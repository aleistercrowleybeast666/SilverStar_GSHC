"""Causal, session-scoped navigation readiness and live health.

ACKs and Alignment sensor inventories are deliberately not inputs to this model.
Only complete, versioned on-board navigation snapshots can assert readiness.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum, IntFlag


class NavigationApplyResult(str, Enum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    STALE = "STALE"
    WRONG_SESSION = "WRONG_SESSION"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"


class NavigationStartResult(str, Enum):
    ALLOWED = "ALLOWED"
    UNSUPPORTED = "UNSUPPORTED"
    WAITING = "WAITING"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"


class PreparationStep(IntFlag):
    DEVICES = 1
    CALIBRATION = 2
    ATTITUDE = 4
    GNSS_SOLUTION = 8
    GNSS_ORIGIN = 16
    BARO_REFERENCE = 32
    ESTIMATOR = 64


class FusionGroup(IntEnum):
    POS_EN = 0
    POS_U = 1
    VEL_EN = 2
    VEL_U = 3
    BARO_U = 4


@dataclass(frozen=True)
class PreparationSnapshot:
    session: int
    generation: int
    snapshot: int
    required_mask: int
    ready_mask: int
    stage: int
    reason: int
    algorithm_id: int
    progress_completed: int
    progress_total: int
    received_ns: int
    expires_ms: int = 2000


def Navigation_IsNewer(value: int, previous: int, bits: int = 16) -> bool:
    distance = (value - previous) & ((1 << bits) - 1)
    return 0 < distance < (1 << (bits - 1))


@dataclass
class NavigationState:
    """Bounded model; partial/outdated states never become fresh READY."""

    protocol_version: int | None = None
    session: int | None = None
    generation: int | None = None
    preparation: PreparationSnapshot | None = None
    metrics: dict[tuple[int, int], tuple[int, int, int]] = field(default_factory=dict)
    requested_session: int | None = None
    algorithm_id: int | None = None
    group_mask: int = 0
    awaiting_generation: bool = False
    subscription_seq: int | None = None
    capability_seq: int | None = None
    invalidation_reason: str = "UNSUPPORTED"
    revision: int = 0
    wire_sequence: int | None = None
    wire_index: int = 0

    def Navigation_SequenceIndex(self, sequence: int) -> int:
        """Unwrap the shared AIR sequence before comparing sparse individual fields."""
        if self.wire_sequence is None:
            self.wire_sequence = sequence
            self.wire_index = sequence
            return self.wire_index
        delta = (sequence - self.wire_sequence) & 255
        if delta >= 128:
            return self.wire_index + delta - 256
        if delta:
            self.wire_index += delta
            self.wire_sequence = sequence
        return self.wire_index

    def Navigation_Invalidate(self, reason: str = "WAITING") -> None:
        self.preparation = None
        self.metrics.clear()
        self.invalidation_reason = reason
        self.revision += 1

    def Navigation_Request(self, session: int) -> None:
        if not 0 < session <= 65535:
            raise ValueError("navigation session nonce must be nonzero u16")
        self.requested_session = session
        self.session = None
        self.generation = None
        self.protocol_version = None
        self.capability_seq = None
        self.algorithm_id = None
        self.group_mask = 0
        self.awaiting_generation = False
        self.wire_sequence = None
        self.wire_index = 0
        self.Navigation_Invalidate("WAITING")

    def Navigation_Declare(self, version: int, session: int, generation: int = 0, seq: int | None = None) -> NavigationApplyResult:
        if session != self.requested_session:
            return NavigationApplyResult.WRONG_SESSION
        if seq is not None:
            self.Navigation_SequenceIndex(seq)
        if seq is not None and self.capability_seq is not None:
            if seq == self.capability_seq:
                return NavigationApplyResult.DUPLICATE
            if not Navigation_IsNewer(seq, self.capability_seq, 8):
                return NavigationApplyResult.STALE
        if version != 1:
            self.protocol_version = version
            self.Navigation_Invalidate("UNSUPPORTED")
            return NavigationApplyResult.UNSUPPORTED
        if self.session != session or self.protocol_version != version:
            self.Navigation_Invalidate()
            self.generation = generation
        elif self.generation is not None and generation < self.generation:
            return NavigationApplyResult.STALE
        elif generation != self.generation:
            self.Navigation_Invalidate()
            self.generation = generation
            self.awaiting_generation = False
        self.session = session
        self.protocol_version = version
        self.capability_seq = seq
        return NavigationApplyResult.APPLIED

    def Navigation_ApplyPreparation(self, value: PreparationSnapshot) -> NavigationApplyResult:
        if self.protocol_version != 1:
            return NavigationApplyResult.UNSUPPORTED
        if value.session != self.session:
            return NavigationApplyResult.WRONG_SESSION
        self.Navigation_SequenceIndex(value.snapshot)
        if self.awaiting_generation and value.generation == self.generation:
            return NavigationApplyResult.STALE
        if (
            value.required_mask & ~127 or value.ready_mask & ~127
            or value.progress_completed < 0 or value.progress_total < value.progress_completed
            or not 0 < value.expires_ms <= 10000
        ):
            return NavigationApplyResult.INVALID
        if self.generation != value.generation:
            if self.generation is not None and value.generation <= self.generation:
                return NavigationApplyResult.STALE
            self.Navigation_Invalidate()
            self.generation = value.generation
            self.awaiting_generation = False
        if self.preparation is not None:
            if value.snapshot == self.preparation.snapshot:
                return NavigationApplyResult.DUPLICATE
            if not Navigation_IsNewer(value.snapshot, self.preparation.snapshot, 8):
                return NavigationApplyResult.STALE
        self.preparation = value
        self.invalidation_reason = ""
        self.revision += 1
        return NavigationApplyResult.APPLIED

    def Navigation_ApplyMetric(
        self, session: int, generation: int, seq: int, selector: int, value: int, received_ns: int
    ) -> NavigationApplyResult:
        if self.protocol_version != 1:
            return NavigationApplyResult.UNSUPPORTED
        if session != self.session or generation != self.generation:
            return NavigationApplyResult.WRONG_SESSION
        sequence_index = self.Navigation_SequenceIndex(seq)
        group, metric = selector & 7, selector >> 3
        if not ((group <= 4 and metric <= 8) or (group == 7 and metric <= 16)):
            return NavigationApplyResult.UNSUPPORTED
        key = (group, metric)
        previous = self.metrics.get(key)
        if previous is not None:
            if sequence_index == previous[2]:
                return NavigationApplyResult.DUPLICATE
            if sequence_index < previous[2]:
                return NavigationApplyResult.STALE
        self.metrics[key] = (value, received_ns, sequence_index)
        self.revision += 1
        return NavigationApplyResult.APPLIED

    def Navigation_ReadMetric(self, group: int, metric: int, now_ns: int | None = None) -> int | None:
        value = self.metrics.get((group, metric))
        now = time.monotonic_ns() if now_ns is None else now_ns
        ttl_ns = 3_000_000_000 if group <= 4 and metric <= 1 else 10_000_000_000
        if value is None or not 0 <= now - value[1] <= ttl_ns:
            return None
        return value[0]

    def Navigation_StartCheck(self, now_ns: int | None = None) -> NavigationStartResult:
        if self.protocol_version != 1 or self.algorithm_id not in (0, 1, 2):
            return NavigationStartResult.UNSUPPORTED
        value = self.preparation
        if value is None:
            return NavigationStartResult.WAITING
        now = time.monotonic_ns() if now_ns is None else now_ns
        if now < value.received_ns or now - value.received_ns > value.expires_ms * 1_000_000:
            return NavigationStartResult.STALE
        if value.ready_mask & value.required_mask != value.required_mask:
            return NavigationStartResult.INCOMPLETE
        if value.reason:
            return NavigationStartResult.BLOCKED
        return NavigationStartResult.ALLOWED

    def Navigation_GroupAge(self, group: int, now_ns: int | None = None) -> int | None:
        return self.Navigation_MetricAge(group, 1, now_ns)

    def Navigation_MetricAge(self, group: int, metric: int, now_ns: int | None = None) -> int | None:
        value = self.Navigation_ReadMetric(group, metric, now_ns)
        if value is None or value == 65535:
            return None
        now = time.monotonic_ns() if now_ns is None else now_ns
        return value * 100 + (now - self.metrics[(group, metric)][1]) // 1_000_000
