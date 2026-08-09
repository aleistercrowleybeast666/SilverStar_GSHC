from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal, Slot

from config import PROTOCOL_INPUT_QUEUE_MAX_CHUNKS
from protocol.air import (
    AirFlightStateMessage,
    AirPreflightStateMessage,
    AirPreflightStatusMessage,
)
from protocol.receive_pipeline import (
    ProtocolEvent,
    ReceivePipeline,
    protocol_event_log_records,
)
from services.logger import AsyncJsonlLogger


@dataclass(frozen=True)
class ProtocolBatch:
    session_generation: int
    events: tuple[ProtocolEvent, ...]


@dataclass(frozen=True)
class ProtocolWorkerDiagnostics:
    session_generation: int
    serial_rx_bytes: int
    serial_rx_chunks: int
    gsp_frames: int
    gsp_parse_errors: int
    gsp_crc_errors: int
    gsp_resyncs: int
    parser_buffer_size: int
    air_frames: int
    air_parse_errors: int
    protocol_queue_depth: int
    protocol_queue_capacity: int
    ui_mailbox_depth: int
    ui_mailbox_capacity: int
    ui_coalesced_events: int
    last_rx_monotonic_ns: int | None
    max_processing_lag_ms: float
    warning: str


@dataclass(frozen=True)
class _RxChunk:
    payload: bytes
    host_rx_monotonic_ns: int


class ProtocolWorker(QThread):
    """Consumes raw chunks, parses GSP/AIR, and persists records off the GUI thread."""

    diagnostics_ready = Signal(object)
    warning_occurred = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        session_generation: int,
        logger: AsyncJsonlLogger,
        *,
        queue_capacity: int = PROTOCOL_INPUT_QUEUE_MAX_CHUNKS,
        ui_mailbox_capacity: int = 4096,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.session_generation = int(session_generation)
        self.logger = logger
        self.queue_capacity = max(16, int(queue_capacity))
        self._queue: queue.Queue[_RxChunk | None] = queue.Queue(maxsize=self.queue_capacity)
        self.ui_mailbox_capacity = max(256, int(ui_mailbox_capacity))
        self._mailbox: deque[ProtocolEvent] = deque()
        self._mailbox_latest: dict[type, ProtocolEvent] = {}
        self._mailbox_lock = threading.Lock()
        self._ui_coalesced_events = 0
        self._accepting = True
        self._accept_lock = threading.Lock()
        self._pipeline = ReceivePipeline()
        self._serial_rx_bytes = 0
        self._serial_rx_chunks = 0
        self._last_rx_monotonic_ns: int | None = None
        self._max_processing_lag_ms = 0.0
        self._warning = ""
        self._last_queue_warning_ns = 0

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def ui_mailbox_depth(self) -> int:
        with self._mailbox_lock:
            return len(self._mailbox) + len(self._mailbox_latest)

    def take_batch(self, max_events: int = 512) -> ProtocolBatch | None:
        limit = max(1, int(max_events))
        selected: list[ProtocolEvent] = []
        with self._mailbox_lock:
            while self._mailbox and len(selected) < limit:
                selected.append(self._mailbox.popleft())

            if len(selected) < limit and self._mailbox_latest:
                latest = sorted(
                    self._mailbox_latest.values(),
                    key=lambda event: event.host_rx_monotonic_ns,
                )
                self._mailbox_latest.clear()
                remaining = limit - len(selected)
                selected.extend(latest[:remaining])
                for event in latest[remaining:]:
                    self._mailbox_latest[type(event.air_message)] = event

        if not selected:
            return None
        return ProtocolBatch(self.session_generation, tuple(selected))

    @Slot(bytes)
    def enqueue_chunk(self, data: bytes) -> None:
        self.enqueue_chunk_with_timestamp(data, time.monotonic_ns())

    def enqueue_chunk_with_timestamp(self, data: bytes, host_rx_monotonic_ns: int) -> None:
        if not data:
            return
        with self._accept_lock:
            accepting = self._accepting
        if not accepting:
            self.error_occurred.emit("协议接收线程已停止，拒绝了迟到的串口数据")
            return

        chunk = _RxChunk(bytes(data), int(host_rx_monotonic_ns))
        self._serial_rx_bytes += len(chunk.payload)
        self._serial_rx_chunks += 1
        self._last_rx_monotonic_ns = chunk.host_rx_monotonic_ns

        depth = self._queue.qsize()
        if depth >= int(self.queue_capacity * 0.75):
            self._queue_warning(f"protocol queue backlog: {depth}/{self.queue_capacity}")

        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            self._queue_warning("protocol queue full; serial reader is applying bounded backpressure")
            # Formal AIR data is never silently discarded. In the abnormal case
            # the serial producer waits for the independent parser, not for GUI
            # rendering or disk I/O.
            while True:
                with self._accept_lock:
                    if not self._accepting:
                        return
                try:
                    self._queue.put(chunk, timeout=0.05)
                    break
                except queue.Full:
                    continue

    def request_stop(self) -> None:
        with self._accept_lock:
            if not self._accepting:
                return
            self._accepting = False
        while self.isRunning():
            try:
                self._queue.put(None, timeout=0.1)
                break
            except queue.Full:
                continue

    def run(self) -> None:
        last_diagnostics_ns = 0
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    break
                events = self._pipeline.feed(item.payload, item.host_rx_monotonic_ns)
                processed_ns = time.monotonic_ns()
                lag_ms = max(0.0, (processed_ns - item.host_rx_monotonic_ns) / 1_000_000.0)
                self._max_processing_lag_ms = max(self._max_processing_lag_ms, lag_ms)

                if events:
                    self._persist_events(events)
                    self._publish_events(events)

                now_ns = time.monotonic_ns()
                if (
                    self._queue.qsize() < int(self.queue_capacity * 0.25)
                    and self.ui_mailbox_depth < int(self.ui_mailbox_capacity * 0.25)
                ):
                    self._warning = ""
                if now_ns - last_diagnostics_ns >= 250_000_000:
                    last_diagnostics_ns = now_ns
                    self.diagnostics_ready.emit(self.diagnostics_snapshot())
            except Exception as exc:
                self.error_occurred.emit(f"协议接收线程异常: {exc}")
            finally:
                self._queue.task_done()

        self.diagnostics_ready.emit(self.diagnostics_snapshot())

    def _publish_events(self, events: list[ProtocolEvent]) -> None:
        with self._mailbox_lock:
            for event in events:
                if len(self._mailbox) < self.ui_mailbox_capacity:
                    self._mailbox.append(event)
                    continue

                if self._is_ui_coalescible(event):
                    key = type(event.air_message)
                    self._mailbox_latest[key] = event
                    self._ui_coalesced_events += 1
                    continue

                replace_index = next(
                    (
                        index
                        for index, queued_event in enumerate(self._mailbox)
                        if self._is_ui_coalescible(queued_event)
                    ),
                    None,
                )
                if replace_index is not None:
                    del self._mailbox[replace_index]
                    self._mailbox.append(event)
                    self._ui_coalesced_events += 1
                else:
                    # All formal data was already persisted. This only bounds
                    # the 200-entry live event presentation under an extreme
                    # critical-event storm, and the diagnostic makes it explicit.
                    self._mailbox.popleft()
                    self._mailbox.append(event)
                    self._ui_coalesced_events += 1
                    self._warning = "UI mailbox saturated; old display events were coalesced"

    @staticmethod
    def _is_ui_coalescible(event: ProtocolEvent) -> bool:
        return isinstance(
            event.air_message,
            (AirPreflightStateMessage, AirFlightStateMessage, AirPreflightStatusMessage),
        )

    def _persist_events(self, events: list[ProtocolEvent]) -> None:
        for event in events:
            for record in protocol_event_log_records(event):
                self.logger.write(record)

    def diagnostics_snapshot(self) -> ProtocolWorkerDiagnostics:
        core = self._pipeline.diagnostics()
        return ProtocolWorkerDiagnostics(
            session_generation=self.session_generation,
            serial_rx_bytes=self._serial_rx_bytes,
            serial_rx_chunks=self._serial_rx_chunks,
            gsp_frames=core.gsp_frames,
            gsp_parse_errors=core.gsp_parse_errors,
            gsp_crc_errors=core.gsp_crc_errors,
            gsp_resyncs=core.gsp_resyncs,
            parser_buffer_size=core.parser_buffer_size,
            air_frames=core.air_frames,
            air_parse_errors=core.air_parse_errors,
            protocol_queue_depth=self._queue.qsize(),
            protocol_queue_capacity=self.queue_capacity,
            ui_mailbox_depth=self.ui_mailbox_depth,
            ui_mailbox_capacity=self.ui_mailbox_capacity,
            ui_coalesced_events=self._ui_coalesced_events,
            last_rx_monotonic_ns=self._last_rx_monotonic_ns,
            max_processing_lag_ms=self._max_processing_lag_ms,
            warning=self._warning,
        )

    def _queue_warning(self, text: str) -> None:
        self._warning = text
        now_ns = time.monotonic_ns()
        if now_ns - self._last_queue_warning_ns < 1_000_000_000:
            return
        self._last_queue_warning_ns = now_ns
        self.warning_occurred.emit(text)


__all__ = ["ProtocolBatch", "ProtocolWorker", "ProtocolWorkerDiagnostics"]
