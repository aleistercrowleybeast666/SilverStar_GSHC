from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Optional

import serial
from serial import SerialException
from serial.tools import list_ports
from PySide6.QtCore import QThread, Signal, Slot


@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int = 230400
    timeout_s: float = 0.02
    write_timeout_s: float = 0.2
    read_chunk_size: int = 4096
    tx_queue_max_frames: int = 128


def list_serial_port_names() -> list[str]:
    return [p.device for p in list_ports.comports()]


class SerialWorker(QThread):
    bytes_chunk_received = Signal(bytes)
    bytes_sent = Signal(int)
    connection_changed = Signal(bool, str)
    error_occurred = Signal(str)

    def __init__(self, config: SerialConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._serial: Optional[serial.Serial] = None
        self._stop_event = threading.Event()
        self._tx_queue: "queue.Queue[bytes]" = queue.Queue(
            maxsize=max(8, int(config.tx_queue_max_frames))
        )

    def run(self) -> None:
        try:
            self._open_serial()
        except Exception as exc:
            self.connection_changed.emit(False, f"OPEN_FAIL:{exc}")
            self.error_occurred.emit(f"串口打开失败: {exc}")
            return

        self.connection_changed.emit(True, f"{self._config.port}@{self._config.baudrate}")

        try:
            while not self._stop_event.is_set():
                self._flush_tx_queue()
                self._poll_rx()
        except Exception as exc:
            self.error_occurred.emit(f"串口线程异常: {exc}")
        finally:
            self._close_serial()
            self.connection_changed.emit(False, "CLOSED")

    @Slot(bytes)
    def send_bytes(self, payload: bytes) -> None:
        if payload:
            try:
                self._tx_queue.put_nowait(bytes(payload))
            except queue.Full:
                self.error_occurred.emit("串口发送队列已满，命令未入队")

    @Slot()
    def request_stop(self) -> None:
        self._stop_event.set()
        # Abort a pending blocking read promptly when the platform/driver
        # supports it.  The normal 20 ms timeout remains the fallback.
        ser = self._serial
        if ser is not None:
            try:
                ser.cancel_read()
            except (AttributeError, OSError, SerialException):
                pass

    def _open_serial(self) -> None:
        self._serial = serial.Serial(
            port=self._config.port,
            baudrate=self._config.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self._config.timeout_s,
            write_timeout=self._config.write_timeout_s,
        )

    def _close_serial(self) -> None:
        ser = self._serial
        self._serial = None
        if ser is not None:
            try:
                if ser.is_open:
                    ser.close()
            except Exception:
                pass

    def _flush_tx_queue(self) -> None:
        ser = self._serial
        if ser is None or not ser.is_open:
            return
        while True:
            try:
                payload = self._tx_queue.get_nowait()
            except queue.Empty:
                break
            try:
                written = ser.write(payload)
                ser.flush()
                self.bytes_sent.emit(int(written))
            except SerialException as exc:
                self.error_occurred.emit(f"串口发送失败: {exc}")
                self._stop_event.set()
                break

    def _poll_rx(self) -> None:
        ser = self._serial
        if ser is None or not ser.is_open:
            return
        try:
            # Let the short serial timeout coalesce a burst into one chunk.
            # Reading one byte whenever ``in_waiting`` momentarily reached zero
            # produced thousands of cross-thread callbacks per second.
            chunk = ser.read(self._config.read_chunk_size)
        except SerialException as exc:
            self.error_occurred.emit(f"串口接收失败: {exc}")
            self._stop_event.set()
            return
        if chunk:
            self.bytes_chunk_received.emit(bytes(chunk))


class SerialLink:
    def __init__(self) -> None:
        self.worker: Optional[SerialWorker] = None

    def open(self, config: SerialConfig) -> SerialWorker:
        if not self.close():
            raise RuntimeError("previous serial thread is still stopping")
        self.worker = SerialWorker(config)
        self.worker.start()
        return self.worker

    def close(self) -> bool:
        if self.worker is None:
            return True
        worker = self.worker
        worker.request_stop()
        if not worker.wait(5000):
            # Keep the strong reference: destroying a running QThread can
            # terminate the process.  A later close() can retry safely.
            worker.error_occurred.emit("串口线程未在 5 秒内停止")
            return False
        self.worker = None
        return True

    def is_open(self) -> bool:
        return self.worker is not None and self.worker.isRunning()
