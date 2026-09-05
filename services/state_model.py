from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from config import MAX_LIVE_POINTS, PLOT_WINDOW_SECONDS, UI_EVENT_HISTORY_LIMIT
from protocol.air import AirCapabilityMessage, AirSensorStatusMessage
from protocol.common import (
    AirAckResult,
    AirAlignmentState,
    AirCalibrationMode,
    AirCommandPolicy,
    air_sensor_sort_key,
    enum_name,
)


def quat_to_euler_rpy(
    quat: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    w, x, y, z = quat
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0.0 or not math.isfinite(norm):
        return 0.0, 0.0, 0.0
    w, x, y, z = w / norm, x / norm, y / norm, z / norm

    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return roll, pitch, yaw


@dataclass(frozen=True)
class FlightEvent:
    seq: int
    status_id: int
    name: str
    time_ms: int
    arg0: int
    arg1: int
    host_rx_monotonic_ns: int


@dataclass
class UiMessage:
    """Language-neutral UI message rendered by the active I18n instance."""

    key: str = "common.none"
    params: dict[str, object] = field(default_factory=dict)


class EventHistory:
    def __init__(self, limit: int = UI_EVENT_HISTORY_LIMIT) -> None:
        self.limit = max(1, int(limit))
        self._events: deque[FlightEvent] = deque(maxlen=self.limit)
        self.revision = 0

    def append(self, event: FlightEvent) -> None:
        self._events.append(event)
        self.revision += 1

    def clear(self) -> None:
        if self._events:
            self._events.clear()
            self.revision += 1

    def snapshot(self) -> tuple[FlightEvent, ...]:
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)


class LiveFlightPlotBuffer:
    """Bounded display-only velocity/position history for the latest 10 s."""

    def __init__(
        self,
        window_seconds: float = PLOT_WINDOW_SECONDS,
        max_points: int = MAX_LIVE_POINTS,
    ) -> None:
        self.window_seconds = max(0.1, float(window_seconds))
        self.max_points = max(2, int(max_points))
        self.time_s: deque[float] = deque(maxlen=self.max_points)
        self.velocity: tuple[deque[float], deque[float], deque[float]] = tuple(
            deque(maxlen=self.max_points) for _ in range(3)
        )  # type: ignore[assignment]
        self.position: tuple[deque[float], deque[float], deque[float]] = tuple(
            deque(maxlen=self.max_points) for _ in range(3)
        )  # type: ignore[assignment]
        self.latest_time_s: float | None = None
        self.revision = 0
        self.ignored_non_monotonic_samples = 0

    def append(
        self,
        time_s: float,
        velocity: Iterable[float],
        position: Iterable[float],
    ) -> bool:
        time_value = float(time_s)
        vel = tuple(float(value) for value in velocity)
        pos = tuple(float(value) for value in position)
        if len(vel) != 3 or len(pos) != 3 or not math.isfinite(time_value):
            raise ValueError("live plot sample must contain finite time and 3-axis vectors")

        if self.latest_time_s is not None and time_value < self.latest_time_s:
            # Persistent JSON already contains the sample. Ignoring an old
            # display sample keeps the live x-axis ordered and bounded.
            self.ignored_non_monotonic_samples += 1
            return False

        self.latest_time_s = time_value
        self.time_s.append(time_value)
        for axis in range(3):
            self.velocity[axis].append(vel[axis])
            self.position[axis].append(pos[axis])
        self._trim()
        self.revision += 1
        return True

    def _trim(self) -> None:
        if self.latest_time_s is None:
            return
        cutoff = self.latest_time_s - self.window_seconds
        while self.time_s and self.time_s[0] < cutoff:
            self.time_s.popleft()
            for axis in range(3):
                self.velocity[axis].popleft()
                self.position[axis].popleft()

    def clear(self) -> None:
        self.time_s.clear()
        for axis in range(3):
            self.velocity[axis].clear()
            self.position[axis].clear()
        self.latest_time_s = None
        self.revision += 1

    def snapshot(self) -> tuple[list[float], tuple[list[float], ...], tuple[list[float], ...]]:
        return (
            list(self.time_s),
            tuple(list(axis) for axis in self.velocity),
            tuple(list(axis) for axis in self.position),
        )


@dataclass
class SensorSnapshot:
    source: str = ""
    seq: int | None = None
    time_ms: int | None = None
    accel_raw: tuple[int, int, int] | None = None
    gyro_raw: tuple[int, int, int] | None = None
    accel_mps2: tuple[float, float, float] | None = None
    gyro_radps: tuple[float, float, float] | None = None
    quat_q15: tuple[int, int, int, int] | None = None
    quat: tuple[float, float, float, float] | None = None
    quat_valid: bool = False
    euler_rpy: tuple[float, float, float] | None = None
    velocity_mps: tuple[float, float, float] | None = None
    position_m: tuple[float, float, float] | None = None
    host_rx_monotonic_ns: int | None = None
    revision: int = 0


@dataclass
class CalibrationSnapshot:
    state: int | None = None
    mode: int = 0xFF
    completed_face_mask: int = 0
    current_face: int = 0xFF
    ready: bool = False


@dataclass
class AlignmentSnapshot:
    state: int | None = None
    ready: bool = False


@dataclass
class AlignmentSensorSnapshot:
    """One end-of-Alignment sensor inventory, keyed by protocol index."""

    snapshot_id: int
    expected_total: int | None = None
    frames_by_index: dict[int, AirSensorStatusMessage] = field(default_factory=dict)
    terminal_alignment_state: int | None = None
    duplicate_frames: int = 0
    total_mismatches: int = 0
    revision: int = 0

    @property
    def terminal_received(self) -> bool:
        return self.terminal_alignment_state in {
            int(AirAlignmentState.READY),
            int(AirAlignmentState.FAILED),
        }

    @property
    def complete(self) -> bool:
        return bool(
            self.terminal_received
            and self.expected_total is not None
            and self.expected_total > 0
            and set(self.frames_by_index) == set(range(self.expected_total))
        )

    @property
    def incomplete(self) -> bool:
        return bool(self.terminal_received and not self.complete)

    def ordered_frames(self) -> tuple[AirSensorStatusMessage, ...]:
        return tuple(
            sorted(
                self.frames_by_index.values(),
                key=lambda frame: air_sensor_sort_key(
                    frame.sensor_id, frame.instance_id
                ),
            )
        )


@dataclass(frozen=True)
class SensorSnapshotReceiveResult:
    duplicate_index: bool = False
    total_mismatch: bool = False


@dataclass
class AlignmentSensorSnapshotCache:
    """Bounded accumulators plus the latest terminal Alignment snapshot."""

    accumulators: dict[int, AlignmentSensorSnapshot] = field(default_factory=dict)
    latest_terminal_snapshot: AlignmentSensorSnapshot | None = None
    duplicate_frames: int = 0
    total_mismatches: int = 0
    revision: int = 0
    max_accumulators: int = 8

    def receive(
        self, message: AirSensorStatusMessage
    ) -> SensorSnapshotReceiveResult:
        snapshot_id = message.snapshot_id & 0xFF
        snapshot = self.accumulators.get(snapshot_id)
        if snapshot is None or snapshot.terminal_received:
            snapshot = AlignmentSensorSnapshot(
                snapshot_id=snapshot_id,
                expected_total=message.total,
            )
            self.accumulators[snapshot_id] = snapshot
        duplicate = message.index in snapshot.frames_by_index
        mismatch = (
            snapshot.expected_total is not None
            and snapshot.expected_total != message.total
        )
        if duplicate:
            snapshot.duplicate_frames += 1
            self.duplicate_frames += 1
        if mismatch:
            snapshot.total_mismatches += 1
            self.total_mismatches += 1
        else:
            # Duplicate indices use a consistent latest-frame-wins policy.
            snapshot.frames_by_index[message.index] = message
        snapshot.revision += 1
        self.revision += 1
        self._trim()
        return SensorSnapshotReceiveResult(duplicate, mismatch)

    def terminate(
        self, snapshot_id: int, alignment_state: int
    ) -> AlignmentSensorSnapshot | None:
        if alignment_state not in {
            int(AirAlignmentState.READY),
            int(AirAlignmentState.FAILED),
        }:
            return None
        value = int(snapshot_id) & 0xFF
        if value == 0xFF:
            return None
        snapshot = self.accumulators.get(value)
        if snapshot is None:
            snapshot = AlignmentSensorSnapshot(snapshot_id=value)
            self.accumulators[value] = snapshot
        snapshot.terminal_alignment_state = int(alignment_state)
        snapshot.revision += 1
        self.latest_terminal_snapshot = snapshot
        self.revision += 1
        self._trim()
        return snapshot

    def _trim(self) -> None:
        while len(self.accumulators) > max(1, int(self.max_accumulators)):
            oldest_id = next(iter(self.accumulators))
            if (
                self.latest_terminal_snapshot is not None
                and oldest_id == self.latest_terminal_snapshot.snapshot_id
                and len(self.accumulators) > 1
            ):
                self.accumulators[oldest_id] = self.accumulators.pop(oldest_id)
                continue
            del self.accumulators[oldest_id]


class MissionPhase(str, Enum):
    PRE_START = "PRE_START"
    MISSION_ACTIVE = "MISSION_ACTIVE"
    IN_FLIGHT = "IN_FLIGHT"
    RECOVERY = "RECOVERY"
    LANDED = "LANDED"


@dataclass
class MissionPresentationSnapshot:
    """Display-only mission state driven by authoritative protocol events."""

    phase: MissionPhase = MissionPhase.PRE_START
    last_critical_event_name: str = ""
    last_critical_event_time_ms: int | None = None
    last_critical_event_host_monotonic_ns: int | None = None
    parachute_deployed: bool = False


class CalibrationStartResult(str, Enum):
    ALLOWED = "ALLOWED"
    HANDSHAKE_REQUIRED = "HANDSHAKE_REQUIRED"
    UNSUPPORTED_MODE = "UNSUPPORTED_MODE"


class HandshakeState(str, Enum):
    WAITING = "WAITING"
    HANDSHAKING = "HANDSHAKING"
    ACKED = "ACKED"
    ERROR = "ERROR"


@dataclass
class HandshakeDiagnostics:
    """Latest values and counters only; never stores an unbounded history."""

    handshake_state: HandshakeState = HandshakeState.WAITING
    capability_rx: int = 0
    preflight_status_rx: int = 0
    last_air_rx_monotonic_ns: int | None = None
    last_preflight_status_rx_monotonic_ns: int | None = None
    last_capability_seq: int | None = None
    accepted_capability_seq: int | None = None
    last_capability_rx_time: float | None = None
    capability_ack_cmd_seq: int | None = None
    capability_ack_attempts: int = 0
    last_capability_ack_tx_time: float | None = None
    gsp_air_tx_requests: int = 0
    gsp_air_tx_serial_writes: int = 0
    serial_tx_bytes: int = 0
    gsp_air_tx_ack_ok: int = 0
    gsp_air_tx_ack_fail: int = 0
    last_gsp_air_tx_ack_result: int | None = None
    air_ack_rx: int = 0
    air_ack_result: int | None = None
    capability_acked_by_air_ack: bool = False
    capability_acked_by_preflight_status: bool = False
    preflight_status_capability_acked: bool = False
    duplicate_capability_after_ack: int = 0
    last_handshake_error: str = ""


@dataclass
class ReceiveHealth:
    serial_rx_bytes: int = 0
    serial_rx_chunks: int = 0
    gsp_frames: int = 0
    gsp_parse_errors: int = 0
    gsp_crc_errors: int = 0
    gsp_resyncs: int = 0
    parser_buffer_size: int = 0
    air_frames: int = 0
    air_parse_errors: int = 0
    protocol_queue_depth: int = 0
    protocol_queue_capacity: int = 0
    ui_mailbox_depth: int = 0
    ui_mailbox_capacity: int = 0
    ui_coalesced_events: int = 0
    logger_queue_depth: int = 0
    logger_queue_capacity: int = 0
    max_processing_lag_ms: float = 0.0
    last_rx_monotonic_ns: int | None = None
    warning: str = ""

    def last_rx_age_ms(self) -> float | None:
        if self.last_rx_monotonic_ns is None:
            return None
        return max(0.0, (time.monotonic_ns() - self.last_rx_monotonic_ns) / 1_000_000.0)

    def is_backlogged(self) -> bool:
        protocol_ratio = (
            self.protocol_queue_depth / self.protocol_queue_capacity
            if self.protocol_queue_capacity > 0
            else 0.0
        )
        logger_ratio = (
            self.logger_queue_depth / self.logger_queue_capacity
            if self.logger_queue_capacity > 0
            else 0.0
        )
        mailbox_ratio = (
            self.ui_mailbox_depth / self.ui_mailbox_capacity
            if self.ui_mailbox_capacity > 0
            else 0.0
        )
        return bool(
            self.warning
            or protocol_ratio >= 0.5
            or logger_ratio >= 0.5
            or mailbox_ratio >= 0.5
        )

    def tooltip(self) -> str:
        age = self.last_rx_age_ms()
        age_text = "—" if age is None else f"{age:.1f}"
        return (
            f"serial_rx_bytes={self.serial_rx_bytes}\n"
            f"serial_rx_chunks={self.serial_rx_chunks}\n"
            f"gsp_frames={self.gsp_frames}\n"
            f"gsp_parse_errors={self.gsp_parse_errors}\n"
            f"gsp_crc_errors={self.gsp_crc_errors}\n"
            f"gsp_resyncs={self.gsp_resyncs}\n"
            f"parser_buffer_size={self.parser_buffer_size}\n"
            f"air_frames={self.air_frames}\n"
            f"air_parse_errors={self.air_parse_errors}\n"
            f"protocol_queue={self.protocol_queue_depth}/{self.protocol_queue_capacity}\n"
            f"ui_mailbox={self.ui_mailbox_depth}/{self.ui_mailbox_capacity}\n"
            f"ui_coalesced_events={self.ui_coalesced_events}\n"
            f"logger_queue={self.logger_queue_depth}/{self.logger_queue_capacity}\n"
            f"last_rx_age_ms={age_text}\n"
            f"max_processing_lag_ms={self.max_processing_lag_ms:.2f}\n"
            f"warning={self.warning or 'none'}"
        )


@dataclass
class FlightControllerState:
    session_generation: int = 0
    connected: bool = False
    connection_text: str = "未连接"
    connection_message: UiMessage = field(
        default_factory=lambda: UiMessage("connection.not_connected")
    )
    capability: AirCapabilityMessage | None = None
    handshake: HandshakeDiagnostics = field(default_factory=HandshakeDiagnostics)
    profile_supported: bool | None = None
    capability_error: str = ""
    lifecycle_state: int | None = None
    system_ready: bool = False
    selftest_passed: bool = False
    start_unlocked: bool = False
    gnss_position_usable: bool = False
    start_block_reason: int = int(AirAckResult.CAPABILITY_REQUIRED)
    calibration: CalibrationSnapshot = field(default_factory=CalibrationSnapshot)
    latest_calibration_diagnostic_reason: int = 0
    latest_calibration_diagnostic_face: int = 0xFF
    latest_calibration_diagnostic_time: int | None = None
    alignment: AlignmentSnapshot = field(default_factory=AlignmentSnapshot)
    alignment_sensor_snapshots: AlignmentSensorSnapshotCache = field(
        default_factory=AlignmentSensorSnapshotCache
    )
    sensor: SensorSnapshot = field(default_factory=SensorSnapshot)
    live_plot: LiveFlightPlotBuffer = field(default_factory=LiveFlightPlotBuffer)
    receive_health: ReceiveHealth = field(default_factory=ReceiveHealth)
    mission_started: bool = False
    mission_start_source: str = ""
    mission_presentation: MissionPresentationSnapshot = field(
        default_factory=MissionPresentationSnapshot
    )
    pending_command_name: str = ""
    last_start_failure_result: int | None = None
    start_transaction_timed_out: bool = False
    start_snapshot_busy_seen: bool = False
    last_air_ack: str = "—"
    last_air_ack_message: UiMessage = field(default_factory=UiMessage)
    last_gs_ack: str = "—"
    last_gs_ack_message: UiMessage = field(default_factory=UiMessage)
    last_status: str = "—"
    radio_hint: str = "—"
    radio_message: UiMessage = field(default_factory=UiMessage)
    gs_state: str = "—"
    radio_state: str = "—"
    gs_tx_count: int = 0
    gs_rx_count: int = 0
    gs_crc_error_count: int = 0
    rssi_dbm: int | None = None
    snr_db: float | None = None
    received_flight_packets: int = 0
    estimated_lost_packets: int = 0
    expected_flight_packets: int = 0
    packet_loss_rate: float = 0.0
    mission_first_time_ms: int | None = None
    latest_flight_time_ms: int | None = None
    revision: int = 0

    def touch(self) -> None:
        self.revision += 1

    @property
    def capability_acked(self) -> bool:
        """Compatibility view backed by the single authoritative handshake state."""
        return self.handshake.handshake_state is HandshakeState.ACKED

    @capability_acked.setter
    def capability_acked(self, value: bool) -> None:
        self.handshake.handshake_state = (
            HandshakeState.ACKED if value else HandshakeState.WAITING
        )

    def command_policy_name(self) -> str:
        if self.capability is None:
            return "UNKNOWN"
        try:
            return AirCommandPolicy(self.capability.command_policy).name
        except ValueError:
            return f"UNKNOWN({self.capability.command_policy})"

    def start_block_reason_name(self) -> str:
        return enum_name(AirAckResult, self.start_block_reason)

    def air_command_link_allowed(self) -> bool:
        if not self.connected or not self.capability_acked or self.capability is None:
            return False
        if not self.capability.profile_supported:
            return False
        if not self.mission_started:
            return True
        return self.capability.command_policy == int(AirCommandPolicy.MISSION_ALLOWED)

    def preflight_command_entry_allowed(self) -> bool:
        return bool(self.air_command_link_allowed() and not self.mission_started)

    def sampling_calibration_modes(self) -> tuple[AirCalibrationMode, ...]:
        """Supported user sampling flows; independent of calibration readiness."""
        capability = self.capability
        if (
            not self.capability_acked
            or capability is None
            or not capability.profile_supported
        ):
            return ()
        return tuple(
            mode
            for mode in (AirCalibrationMode.ONE_FACE, AirCalibrationMode.SIX_FACE)
            if capability.calibration_mode_mask & (1 << mode)
        )

    def check_calibration_start(self, mode: int) -> CalibrationStartResult:
        if (
            not self.capability_acked
            or self.capability is None
            or not self.capability.profile_supported
        ):
            return CalibrationStartResult.HANDSHAKE_REQUIRED
        if mode not in self.sampling_calibration_modes():
            return CalibrationStartResult.UNSUPPORTED_MODE
        return CalibrationStartResult.ALLOWED

    def start_transaction_pending(self) -> bool:
        return self.pending_command_name == "START_MISSION"

    def calibration_transaction_pending(self) -> bool:
        return self.pending_command_name in {
            "CAL_START",
            "CAL_FACE",
            "CAL_STOP",
            "CAL_RESET",
        }

    def start_prerequisites_ready(self) -> bool:
        return bool(
            self.preflight_command_entry_allowed()
            and self.calibration.ready
            and self.alignment.ready
            and self.system_ready
            and self.start_unlocked
        )

    def start_ready(self) -> bool:
        return bool(
            self.start_prerequisites_ready()
            and not self.pending_command_name
            and self.start_block_reason == int(AirAckResult.OK)
        )

    def start_button_enabled(self) -> bool:
        if self.mission_started or not self.preflight_command_entry_allowed():
            return False
        if self.start_transaction_pending():
            return True
        if self.pending_command_name:
            return False
        if not self.start_prerequisites_ready():
            return False
        return bool(
            self.start_block_reason == int(AirAckResult.OK)
            or self.last_start_failure_result is not None
            or self.start_transaction_timed_out
        )


__all__ = [
    "AlignmentSnapshot",
    "AlignmentSensorSnapshot",
    "AlignmentSensorSnapshotCache",
    "CalibrationSnapshot",
    "CalibrationStartResult",
    "EventHistory",
    "FlightControllerState",
    "FlightEvent",
    "HandshakeDiagnostics",
    "HandshakeState",
    "LiveFlightPlotBuffer",
    "MissionPhase",
    "MissionPresentationSnapshot",
    "ReceiveHealth",
    "SensorSnapshot",
    "SensorSnapshotReceiveResult",
    "UiMessage",
    "quat_to_euler_rpy",
]
