from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import (
    LOGGER_BATCH_RECORDS,
    LOGGER_FLUSH_INTERVAL_S,
    LOGGER_QUEUE_MAX_RECORDS,
    LOG_DIR,
)


@dataclass(frozen=True)
class _RecordItem:
    record: dict[str, Any]


@dataclass
class _SessionItem:
    reason: str
    metadata: dict[str, Any]
    close_only: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    path: Path | None = None
    error: str | None = None


@dataclass
class _StopItem:
    done: threading.Event = field(default_factory=threading.Event)


class AsyncJsonlLogger:
    """Ordered, bounded, batched JSONL writer with one dedicated disk thread."""

    def __init__(
        self,
        log_dir: Path | str = LOG_DIR,
        *,
        queue_max_records: int = LOGGER_QUEUE_MAX_RECORDS,
        batch_records: int = LOGGER_BATCH_RECORDS,
        flush_interval_s: float = LOGGER_FLUSH_INTERVAL_S,
        auto_start_session: bool = True,
        warning_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.queue_max_records = max(128, int(queue_max_records))
        self.batch_records = max(1, int(batch_records))
        self.flush_interval_s = max(0.02, float(flush_interval_s))
        self.warning_callback = warning_callback
        self.error_callback = error_callback

        self._queue: queue.Queue[_RecordItem | _SessionItem | _StopItem] = queue.Queue(
            maxsize=self.queue_max_records
        )
        self._state_lock = threading.Lock()
        self._accepting = True
        self._session_active = False
        self._path: Path | None = None
        self._session_index = 0

        self.last_warning = ""
        self.last_error = ""
        self.max_queue_depth = 0
        self.producer_block_count = 0
        self.records_enqueued = 0
        self.records_written = 0

        self._thread = threading.Thread(
            target=self._writer_loop,
            name="AsyncJsonlLogger",
            daemon=True,
        )
        self._thread.start()

        if auto_start_session:
            self.open_session("logger_start")

    @property
    def path(self) -> Path | None:
        with self._state_lock:
            return self._path

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_usage(self) -> float:
        return self.queue_depth / float(self.queue_max_records)

    @property
    def session_active(self) -> bool:
        with self._state_lock:
            return self._session_active

    def set_log_dir(self, log_dir: Path | str) -> None:
        selected_log_dir = Path(log_dir)
        selected_log_dir.mkdir(parents=True, exist_ok=True)
        with self._state_lock:
            if self._session_active or self._queue.unfinished_tasks:
                raise RuntimeError("Cannot change the log directory during an active session")
            self.log_dir = selected_log_dir

    def open_session(
        self,
        reason: str,
        metadata: dict[str, Any] | None = None,
        *,
        timeout_s: float = 10.0,
    ) -> Path:
        self._ensure_accepting()
        request = _SessionItem(reason=str(reason), metadata=dict(metadata or {}))
        self._put_control(request)
        if not request.done.wait(timeout_s):
            raise TimeoutError("JSONL logger session rollover timed out")
        if request.error:
            raise OSError(request.error)
        if request.path is None:
            raise OSError("JSONL logger did not create a session file")
        return request.path

    def close_session(self, *, timeout_s: float = 10.0) -> None:
        if not self._thread.is_alive():
            return
        request = _SessionItem(reason="session_close", metadata={}, close_only=True)
        self._put_control(request)
        if not request.done.wait(timeout_s):
            raise TimeoutError("JSONL logger close_session timed out")
        if request.error:
            raise OSError(request.error)

    def rollover(
        self,
        reason: str,
        metadata: dict[str, Any] | None = None,
        *,
        timeout_s: float = 10.0,
    ) -> Path:
        return self.open_session(reason, metadata, timeout_s=timeout_s)

    def write(self, record: dict[str, Any]) -> None:
        self._ensure_accepting()
        if not self.session_active:
            raise RuntimeError("JSONL logger session is not open")

        item_record = dict(record)
        item_record.setdefault("ts", time.time())
        item_record.setdefault("host_monotonic_ns", time.monotonic_ns())
        item = _RecordItem(item_record)

        depth = self._queue.qsize()
        self.max_queue_depth = max(
            self.max_queue_depth,
            min(self.queue_max_records, depth + 1),
        )
        if depth >= int(self.queue_max_records * 0.8):
            self._warn(
                f"logger queue backlog: {depth}/{self.queue_max_records}; "
                "producer will use bounded backpressure"
            )

        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self.producer_block_count += 1
            self._warn("logger queue full; waiting for disk writer (no record was dropped)")
            while self._thread.is_alive():
                try:
                    self._queue.put(item, timeout=0.25)
                    break
                except queue.Full:
                    continue
            else:
                raise RuntimeError("JSONL writer stopped while producer was backpressured")
        self.records_enqueued += 1

    def wait_until_drained(self, timeout_s: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def close(self, *, timeout_s: float = 15.0) -> None:
        with self._state_lock:
            if not self._accepting:
                return
            self._accepting = False

        stop = _StopItem()
        self._put_control(stop, allow_closed=True)
        if not stop.done.wait(timeout_s):
            self._error("JSONL writer did not stop before timeout")
            return
        self._thread.join(timeout=1.0)

    shutdown = close

    def _ensure_accepting(self) -> None:
        with self._state_lock:
            accepting = self._accepting
        if not accepting or not self._thread.is_alive():
            raise RuntimeError("JSONL logger is closed")

    def _put_control(
        self,
        item: _SessionItem | _StopItem,
        *,
        allow_closed: bool = False,
    ) -> None:
        if not allow_closed:
            self._ensure_accepting()
        while self._thread.is_alive():
            try:
                self._queue.put(item, timeout=0.25)
                return
            except queue.Full:
                self._warn("logger queue full while waiting for a session control operation")
        raise RuntimeError("JSONL writer thread is not running")

    def _make_session_path(self) -> Path:
        self._session_index += 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return self.log_dir / f"fc_session_{stamp}_{self._session_index:02d}.jsonl"

    def _writer_loop(self) -> None:
        fp = None
        batch: list[str] = []
        last_flush = time.monotonic()

        def batch_flush(*, durable: bool = False) -> None:
            nonlocal batch, last_flush
            if fp is None or not batch:
                if fp is not None and durable:
                    fp.flush()
                last_flush = time.monotonic()
                return
            try:
                fp.write("".join(batch))
                fp.flush()
                if durable:
                    # close/session boundaries are the durability boundary; a
                    # per-record fsync would reintroduce the original stall.
                    try:
                        import os

                        os.fsync(fp.fileno())
                    except OSError:
                        pass
                self.records_written += len(batch)
                batch = []
                last_flush = time.monotonic()
            except Exception as exc:
                self._error(f"JSONL file write failed: {exc}")
                batch = []
                last_flush = time.monotonic()

        def file_close() -> None:
            nonlocal fp
            if fp is None:
                return
            batch_flush(durable=True)
            try:
                fp.close()
            except Exception as exc:
                self._error(f"JSONL file close failed: {exc}")
            fp = None
            with self._state_lock:
                self._session_active = False
                self._path = None

        running = True
        while running:
            timeout = max(0.01, self.flush_interval_s - (time.monotonic() - last_flush))
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                batch_flush()
                continue

            try:
                if isinstance(item, _RecordItem):
                    if fp is None:
                        self._error("JSONL record arrived without an active session")
                    else:
                        line = json.dumps(item.record, ensure_ascii=False, default=str) + "\n"
                        batch.append(line)
                        if len(batch) >= self.batch_records:
                            batch_flush()

                elif isinstance(item, _SessionItem):
                    try:
                        file_close()
                        if not item.close_only:
                            path = self._make_session_path()
                            fp = path.open("a", encoding="utf-8", buffering=1024 * 1024)
                            with self._state_lock:
                                self._path = path
                                self._session_active = True
                            item.path = path
                            session_record = {
                                "ts": time.time(),
                                "host_monotonic_ns": time.monotonic_ns(),
                                "dir": "META",
                                "layer": "SESSION",
                                "kind": "SESSION_START",
                                "reason": item.reason,
                                **item.metadata,
                            }
                            batch.append(json.dumps(session_record, ensure_ascii=False, default=str) + "\n")
                            batch_flush()
                    except Exception as exc:
                        item.error = str(exc)
                        self._error(f"JSONL session operation failed: {exc}")
                    finally:
                        item.done.set()

                elif isinstance(item, _StopItem):
                    file_close()
                    running = False
                    item.done.set()
            finally:
                self._queue.task_done()

        file_close()

    def _warn(self, text: str) -> None:
        if text == self.last_warning:
            return
        self.last_warning = text
        if self.warning_callback is not None:
            try:
                self.warning_callback(text)
            except Exception:
                pass

    def _error(self, text: str) -> None:
        self.last_error = text
        if self.error_callback is not None:
            try:
                self.error_callback(text)
            except Exception:
                pass


# Kept as a source-compatible import name for older integrations. Its behavior
# is now asynchronous and batched.
JsonlLogger = AsyncJsonlLogger


__all__ = ["AsyncJsonlLogger", "JsonlLogger"]
