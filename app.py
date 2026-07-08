from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog

from config import (
    ACCEL_FULL_SCALE_G,
    APP_NAME,
    DATA_DIR,
    DEFAULT_BAUDRATE,
    GYRO_FULL_SCALE_DPS,
    LOG_DIR,
    STANDARD_GRAVITY_MPS2,
)
from protocol.air import (
    AirAckMessage,
    AirCmdId,
    AirFlightStateMessage,
    AirStatusId,
    build_air_cmd,
    parse_air_frame,
)
from protocol.common import GspType
from protocol.gsp_min import GspParser, build_pc_to_gs_air_frame, parse_gsp_frame
from services.logger import JsonlLogger
from transport.serial_backend import SerialConfig, SerialLink, list_serial_port_names
from ui.main_window import MainWindow

GS_STATE_MAP = {
    0x00: "IDLE",
    0x01: "INIT_OK",
    0x02: "RX_MODE",
    0x03: "TX_MODE",
    0x04: "ERROR",
}

RADIO_STATE_MAP = {
    0x00: "NOT_INIT",
    0x01: "READY",
    0x02: "RX",
    0x03: "TX",
    0x04: "BUSY",
}

STATUS_MAP = {
    AirStatusId.BOOT: "BOOT",
    AirStatusId.SELFTEST_OK: "SELFTEST_OK",
    AirStatusId.MISSION_START: "MISSION_START",
    AirStatusId.LAUNCH: "LAUNCH",
    AirStatusId.PARACHUTE_DEPLOY: "PARACHUTE_DEPLOY",
    AirStatusId.LANDING: "LANDING",
    AirStatusId.LOCKED: "LOCKED",
    AirStatusId.UNLOCKED: "UNLOCKED",
}

ACK_RESULT_MAP = {
    0x00: "OK",
    0x01: "BAD_LEN",
    0x02: "BAD_CMD",
    0x03: "BAD_TOKEN",
    0x04: "BUSY",
    0x05: "REJECTED",
    0x06: "BAD_STATE",
    0x07: "LOCKED_REQUIRED",
    0x08: "ALREADY_LOCKED",
    0x09: "ALREADY_UNLOCKED",
}


# AIR_CMD 是低频人工命令。这里按协议建议实现：等待 AIR_ACK，超时重发。
# MAX_RETRIES 表示“重发次数”，所以总发送次数 = 1 + MAX_RETRIES。
AIR_CMD_ACK_TIMEOUT_MS = 800
AIR_CMD_MAX_RETRIES = 3
AIR_CMD_RETRY_CHECK_MS = 100


@dataclass
class PendingAirCommand:
    seq: int
    cmd_id: int
    token: int
    param0: int
    param1: int
    air_frame: bytes
    gsp_frame: bytes
    sent_count: int
    max_retries: int
    last_send_monotonic: float


class ProcessingWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, log_path: Path, output_root: Path) -> None:
        super().__init__()
        self.log_path = log_path
        self.output_root = output_root

    def run(self) -> None:
        try:
            from processing.flight_log_processor import FlightLogProcessor

            processor = FlightLogProcessor(output_root=self.output_root, gif_fps=5)

            def progress(done: int, total: int, msg: str) -> None:
                self.progress.emit(int(done), int(total), str(msg))

            out_dir = processor.process_file(self.log_path, progress=progress)
            self.finished.emit(str(out_dir))
        except Exception as exc:
            self.failed.emit(str(exc))


class Controller(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.link = SerialLink()
        self.worker = None

        self.gsp_parser = GspParser()
        self.logger = JsonlLogger()

        self.air_seq = 0
        self.pending_air_cmds: dict[tuple[int, int], PendingAirCommand] = {}
        self.air_cmd_retry_timer = QTimer(self)
        self.air_cmd_retry_timer.setInterval(AIR_CMD_RETRY_CHECK_MS)
        self.air_cmd_retry_timer.timeout.connect(self._check_air_cmd_timeouts)
        self.air_cmd_retry_timer.start()

        self.processing_thread: QThread | None = None
        self.processing_worker: ProcessingWorker | None = None
        self.processing_dialog: QProgressDialog | None = None
        self._processing_success_out_dir: str | None = None
        self._processing_error_text: str | None = None

        self.window.on_refresh_ports = self.refresh_ports
        self.window.on_connect_clicked = self.connect
        self.window.on_disconnect_clicked = self.disconnect
        self.window.on_send_ping = self.send_ping
        self.window.on_send_lock = self.send_lock
        self.window.on_send_unlock = self.send_unlock
        self.window.on_send_start = self.send_start
        self.window.on_generate_sim_data = self.generate_sim_validation_data
        self.window.on_process_data = self.choose_and_process_data
        self.window.on_open_log_dir = self.open_log_dir
        self.window.on_open_data_dir = self.open_data_dir

        self.refresh_ports()

    def refresh_ports(self) -> None:
        self.window.set_ports(list_serial_port_names())

    def connect(self) -> None:
        port = self.window.current_port()
        baud = self.window.current_baudrate() or DEFAULT_BAUDRATE

        if not port:
            QMessageBox.warning(self.window, APP_NAME, "请先选择串口")
            return

        self.disconnect()

        self.gsp_parser = GspParser()
        self.air_seq = 0
        self._clear_pending_air_cmds("重新连接")
        self.window.reset_runtime_display()

        self.worker = self.link.open(SerialConfig(port=port, baudrate=baud))
        self.worker.bytes_chunk_received.connect(self.on_bytes_received)
        self.worker.connection_changed.connect(self.on_connection_changed)
        self.worker.error_occurred.connect(self.on_error)

    def disconnect(self) -> None:
        self._clear_pending_air_cmds("串口断开")
        self.link.close()
        self.worker = None
        self.window.set_connection_status("未连接")

    def on_connection_changed(self, ok: bool, text: str) -> None:
        self.window.set_connection_status(text if ok else f"断开: {text}")

    def on_error(self, text: str) -> None:
        self.window.set_connection_status(text)

    def _next_air_seq(self) -> int:
        value = self.air_seq
        self.air_seq = (self.air_seq + 1) & 0xFF
        return value

    def _send_air_cmd(self, cmd_id: int, token: int, param0: int = 0, param1: int = 0) -> None:
        if self.worker is None:
            QMessageBox.warning(self.window, APP_NAME, "请先连接地面站串口")
            return

        if self.pending_air_cmds:
            self.window.set_radio_state_hint("已有 AIR_CMD 等待 ACK，暂不发送新命令")
            return

        seq = self._next_air_seq()
        air = build_air_cmd(seq, cmd_id, token, param0, param1)
        gsp = build_pc_to_gs_air_frame(air)

        pending = PendingAirCommand(
            seq=seq,
            cmd_id=cmd_id & 0xFF,
            token=token & 0xFFFFFFFF,
            param0=param0 & 0xFF,
            param1=param1 & 0xFF,
            air_frame=air,
            gsp_frame=gsp,
            sent_count=0,
            max_retries=AIR_CMD_MAX_RETRIES,
            last_send_monotonic=0.0,
        )
        self.pending_air_cmds[(pending.seq, pending.cmd_id)] = pending
        self._transmit_pending_air_cmd(pending, is_retry=False)

    def _transmit_pending_air_cmd(self, pending: PendingAirCommand, *, is_retry: bool) -> None:
        if self.worker is None:
            return

        self.worker.send_bytes(pending.gsp_frame)
        pending.sent_count += 1
        pending.last_send_monotonic = time.monotonic()

        action = "RETRY" if is_retry else "TX"
        self.window.set_radio_state_hint(
            f"AIR_CMD {action}: seq={pending.seq} cmd=0x{pending.cmd_id:02X} "
            f"attempt={pending.sent_count}/{1 + pending.max_retries}"
        )
        self.window.set_last_air_ack(
            f"等待 ACK: seq={pending.seq} cmd=0x{pending.cmd_id:02X} "
            f"attempt={pending.sent_count}/{1 + pending.max_retries}"
        )

        self.logger.write(
            {
                "ts": time.time(),
                "dir": "TX",
                "layer": "GSP",
                "kind": "GSP_AIR_TX",
                "retry": bool(is_retry),
                "air_seq": pending.seq,
                "air_cmd_id": pending.cmd_id,
                "attempt": pending.sent_count,
                "max_attempts": 1 + pending.max_retries,
                "air_hex": pending.air_frame.hex(),
            }
        )

    def _check_air_cmd_timeouts(self) -> None:
        if not self.pending_air_cmds:
            return

        now = time.monotonic()
        timeout_s = AIR_CMD_ACK_TIMEOUT_MS / 1000.0

        for key, pending in list(self.pending_air_cmds.items()):
            if now - pending.last_send_monotonic < timeout_s:
                continue

            if pending.sent_count <= pending.max_retries:
                self._transmit_pending_air_cmd(pending, is_retry=True)
                continue

            del self.pending_air_cmds[key]
            self.window.set_last_air_ack(
                f"ACK超时: seq={pending.seq} cmd=0x{pending.cmd_id:02X} "
                f"已发送{pending.sent_count}次"
            )
            self.window.set_radio_state_hint("AIR_CMD ACK超时")
            self.logger.write(
                {
                    "ts": time.time(),
                    "dir": "RX",
                    "layer": "AIR_PARSED",
                    "kind": "ACK_TIMEOUT",
                    "ack_seq": pending.seq,
                    "ack_cmd_id": pending.cmd_id,
                    "sent_count": pending.sent_count,
                }
            )

    def _clear_pending_air_cmds(self, reason: str) -> None:
        if not self.pending_air_cmds:
            return

        pending_list = list(self.pending_air_cmds.values())
        self.pending_air_cmds.clear()
        for pending in pending_list:
            self.logger.write(
                {
                    "ts": time.time(),
                    "dir": "LOCAL",
                    "layer": "AIR_PARSED",
                    "kind": "ACK_CANCELLED",
                    "reason": reason,
                    "ack_seq": pending.seq,
                    "ack_cmd_id": pending.cmd_id,
                    "sent_count": pending.sent_count,
                }
            )

    def _on_air_cmd_ack_result(self, msg: AirAckMessage) -> None:
        key = (msg.ack_seq & 0xFF, msg.ack_cmd_id & 0xFF)
        pending = self.pending_air_cmds.pop(key, None)
        matched = pending is not None

        result = ACK_RESULT_MAP.get(msg.result, f"0x{msg.result:02X}")
        suffix = "" if matched else "，未匹配到本机待应答命令"
        self.window.set_last_air_ack(
            f"ack_seq={msg.ack_seq} cmd=0x{msg.ack_cmd_id:02X} result={result}{suffix}"
        )

        if not matched:
            return

        self.window.set_radio_state_hint("AIR_CMD 已收到 ACK")

        # 只有在收到对应 ACK 后才改变命令按钮状态，避免“发送失败但界面已切状态”。
        if msg.result == 0x00:
            if msg.ack_cmd_id == int(AirCmdId.LOCK):
                self.window.set_command_state_locked()
            elif msg.ack_cmd_id == int(AirCmdId.UNLOCK):
                self.window.set_command_state_unlocked()
            elif msg.ack_cmd_id == int(AirCmdId.START_MISSION):
                self.window.set_command_state_mission()
        elif msg.result == 0x08 and msg.ack_cmd_id == int(AirCmdId.LOCK):
            self.window.set_command_state_locked()
        elif msg.result == 0x09 and msg.ack_cmd_id == int(AirCmdId.UNLOCK):
            self.window.set_command_state_unlocked()

    def send_ping(self) -> None:
        self._send_air_cmd(int(AirCmdId.PING), token=int(time.time()) & 0xFFFFFFFF)

    def send_lock(self) -> None:
        self._send_air_cmd(int(AirCmdId.LOCK), token=0xC33CA55A)

    def send_unlock(self) -> None:
        self._send_air_cmd(int(AirCmdId.UNLOCK), token=0x55AA6996)

    def send_start(self) -> None:
        self._send_air_cmd(int(AirCmdId.START_MISSION), token=0xA55A3CC3)

    def _ensure_user_dirs(self) -> None:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

    def _open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))
        if not ok:
            QMessageBox.warning(self.window, APP_NAME, f"无法打开文件夹：\n{folder}")

    def open_log_dir(self) -> None:
        self._open_folder(Path(LOG_DIR))

    def open_data_dir(self) -> None:
        self._open_folder(Path(DATA_DIR))

    def generate_sim_validation_data(self) -> None:
        try:
            from processing.fake_log_generator import simulate

            self._ensure_user_dirs()
            log_dir = Path(LOG_DIR)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = log_dir / f"sim_validation_{ts}.jsonl"

            records = simulate(seed=int(time.time()) & 0xFFFFFFFF)
            meta = {
                "ts": time.time(),
                "dir": "META",
                "layer": "SIMULATION",
                "kind": "SIMULATION_VALIDATION",
                "simulated": True,
                "simulation_label": "SIMULATION_VALIDATION",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "note": "This log was generated by the upper-computer simulation validation button, not real flight data.",
            }

            with out_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for r in records:
                    r = dict(r)
                    r["simulated"] = True
                    r["simulation_label"] = "SIMULATION_VALIDATION"
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

            QMessageBox.information(self.window, APP_NAME, f"模拟验证数据生成成功：\n{out_path}")
        except Exception as exc:
            QMessageBox.critical(self.window, APP_NAME, f"模拟验证数据生成失败：\n{exc}")

    def choose_and_process_data(self) -> None:
        self._ensure_user_dirs()
        log_dir = Path(LOG_DIR)
        path_str, _ = QFileDialog.getOpenFileName(
            self.window,
            "选择要处理的数据日志",
            str(log_dir),
            "JSONL日志 (*.jsonl);;所有文件 (*.*)",
        )
        if not path_str:
            return

        log_path = Path(path_str)
        if log_path.suffix.lower() != ".jsonl":
            QMessageBox.warning(self.window, APP_NAME, "文件有问题：请选择 .jsonl 日志文件。")
            return
        if not log_path.exists() or not log_path.is_file():
            QMessageBox.warning(self.window, APP_NAME, "文件有问题：文件不存在或不是普通文件。")
            return
        if log_path.stat().st_size <= 0:
            QMessageBox.warning(self.window, APP_NAME, "文件有问题：文件为空。")
            return

        if self._is_log_already_processed(log_path):
            QMessageBox.information(self.window, APP_NAME, "该数据已经处理过，data目录中已有对应结果。")
            return

        self._start_processing(log_path)

    def _is_log_already_processed(self, log_path: Path) -> bool:
        data_dir = Path(DATA_DIR)
        if not data_dir.exists():
            return False

        selected_name = log_path.name
        try:
            selected_resolved = str(log_path.resolve())
        except OSError:
            selected_resolved = str(log_path)

        for manifest in data_dir.glob("*/manifest.json"):
            try:
                obj = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue

            source = str(obj.get("source_log", ""))
            if not source:
                continue

            if source == selected_resolved or source == str(log_path):
                return True

            try:
                if Path(source).resolve() == log_path.resolve():
                    return True
            except Exception:
                pass

            if Path(source).name == selected_name:
                return True

        return False

    def _start_processing(self, log_path: Path) -> None:
        if self.processing_thread is not None:
            QMessageBox.information(self.window, APP_NAME, "当前已有数据处理任务在运行。")
            return

        self.window.set_data_tools_busy(True)
        self._processing_success_out_dir = None
        self._processing_error_text = None

        self.processing_dialog = QProgressDialog("正在处理数据...", "", 0, 100, self.window)
        self.processing_dialog.setCancelButton(None)
        self.processing_dialog.setWindowTitle("数据处理")
        self.processing_dialog.setWindowModality(Qt.WindowModal)
        self.processing_dialog.setMinimumDuration(0)
        self.processing_dialog.setValue(0)
        self.processing_dialog.show()

        self.processing_thread = QThread(self.window)
        self.processing_worker = ProcessingWorker(log_path, Path(DATA_DIR))
        self.processing_worker.moveToThread(self.processing_thread)

        self.processing_thread.started.connect(self.processing_worker.run)
        self.processing_worker.progress.connect(self._on_processing_progress, Qt.QueuedConnection)
        self.processing_worker.finished.connect(self._on_processing_finished, Qt.QueuedConnection)
        self.processing_worker.failed.connect(self._on_processing_failed, Qt.QueuedConnection)
        self.processing_thread.finished.connect(self._on_processing_thread_finished)

        self.processing_thread.start()

    def _on_processing_progress(self, done: int, total: int, msg: str) -> None:
        if self.processing_dialog is None:
            return
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        self.processing_dialog.setMaximum(total)
        self.processing_dialog.setValue(done)
        self.processing_dialog.setLabelText(f"正在处理数据...\n{msg}")

    def _request_processing_thread_stop(self) -> None:
        """
        Stop and clean the processing QThread explicitly.

        Do not connect ProcessingWorker.finished(str) directly to QThread.quit()
        or QObject.deleteLater(). In PySide, signal-to-slot connections where
        the signal has extra arguments and the slot has no arguments can behave
        inconsistently for Qt C++ slots. The previous code could reach 100%
        progress while QThread.finished was never emitted, so the success dialog
        was never shown and the UI remained in a busy state.
        """
        if self.processing_worker is not None:
            self.processing_worker.deleteLater()

        if self.processing_thread is not None and self.processing_thread.isRunning():
            self.processing_thread.quit()

    def _on_processing_finished(self, out_dir: str) -> None:
        self._processing_success_out_dir = str(out_dir)
        self._processing_error_text = None

        if self.processing_dialog is not None:
            self.processing_dialog.setValue(self.processing_dialog.maximum())
            self.processing_dialog.close()
            self.processing_dialog = None

        self.window.set_data_tools_busy(False)
        self._request_processing_thread_stop()

        # Controller is now a QObject living in the GUI thread, and the worker
        # signal is connected with Qt.QueuedConnection. It is safe to show the
        # completion popup here.
        QMessageBox.information(self.window, APP_NAME, f"数据处理成功：\n{out_dir}")

    def _on_processing_failed(self, error: str) -> None:
        self._processing_success_out_dir = None
        self._processing_error_text = str(error)

        if self.processing_dialog is not None:
            self.processing_dialog.close()
            self.processing_dialog = None

        self.window.set_data_tools_busy(False)
        self._request_processing_thread_stop()

        QMessageBox.warning(self.window, APP_NAME, f"文件有问题，无法处理：\n{error}")

    def _on_processing_thread_finished(self) -> None:
        self.window.set_data_tools_busy(False)
        self.processing_worker = None
        if self.processing_thread is not None:
            self.processing_thread.deleteLater()
        self.processing_thread = None
        self._processing_success_out_dir = None
        self._processing_error_text = None

    def on_bytes_received(self, data: bytes) -> None:
        for frame in self.gsp_parser.feed(data):
            parsed = parse_gsp_frame(frame)

            self.logger.write(
                {
                    "ts": time.time(),
                    "dir": "RX",
                    "layer": "GSP",
                    "msg_type": int(frame.msg_type),
                    "payload_hex": frame.payload.hex(),
                }
            )

            if frame.msg_type == GspType.GS_STATUS and parsed is not None:
                self.window.update_gs_status(
                    GS_STATE_MAP.get(parsed.gs_state, hex(parsed.gs_state)),
                    RADIO_STATE_MAP.get(parsed.radio_state, hex(parsed.radio_state)),
                    parsed.tx_cnt,
                    parsed.rx_cnt,
                    parsed.crc_err_cnt,
                )

            elif frame.msg_type == GspType.AIR_RX and parsed is not None:
                self.window.update_link_quality(parsed.rssi_dbm, parsed.snr_db)

                try:
                    air_frame, msg = parse_air_frame(parsed.air_frame)
                except Exception as exc:
                    self.logger.write(
                        {
                            "ts": time.time(),
                            "dir": "RX",
                            "layer": "AIR",
                            "error": str(exc),
                            "air_hex": parsed.air_frame.hex(),
                        }
                    )
                    continue

                self.logger.write(
                    {
                        "ts": time.time(),
                        "dir": "RX",
                        "layer": "AIR",
                        "air_type": int(air_frame.air_type),
                        "seq": int(air_frame.seq),
                        "payload_hex": air_frame.payload.hex(),
                    }
                )

                self._handle_air_message(msg)

            elif frame.msg_type == GspType.ACK and parsed is not None:
                self.window.set_last_gs_ack(
                    f"type=0x{parsed.ack_gsp_type:02X} result={parsed.result} detail={parsed.detail}"
                )

    def _handle_air_message(self, msg) -> None:
        from protocol.air import AirAckMessage, AirFlightStateMessage, AirStatusMessage

        if isinstance(msg, AirFlightStateMessage):
            accel = self._accel_raw_to_mps2(msg.accel_raw)
            gyro = self._gyro_raw_to_radps(msg.gyro_raw)

            self.window.push_vector_sample("accel", accel)
            self.window.push_vector_sample("gyro", gyro)
            self.window.update_quat(msg.quat)
            self.window.push_vector_sample("vel", msg.vel_mps)
            self.window.push_vector_sample("pos", msg.pos_m)

            self.logger.write(
                {
                    "ts": time.time(),
                    "dir": "RX",
                    "layer": "AIR_PARSED",
                    "kind": "FLIGHT_STATE",
                    "seq": msg.seq,
                    "time_ms": msg.time_ms,
                    "accel_raw": list(msg.accel_raw),
                    "gyro_raw": list(msg.gyro_raw),
                    "quat_q15": list(msg.quat_q15),
                    "quat": list(msg.quat),
                    "accel_mps2": list(accel),
                    "gyro_radps": list(gyro),
                    "accel_full_scale_g": self.window.current_accel_full_scale_g(),
                    "gyro_full_scale_dps": self.window.current_gyro_full_scale_dps(),
                    "vel_mps": list(msg.vel_mps),
                    "pos_m": list(msg.pos_m),
                }
            )

        elif isinstance(msg, AirStatusMessage):
            text = STATUS_MAP.get(msg.status_id, f"0x{msg.status_id:02X}")
            self.window.set_last_status(f"{text} @ {msg.time_ms} ms")

            if msg.status_id == AirStatusId.LOCKED:
                self.window.set_command_state_locked()
            elif msg.status_id == AirStatusId.UNLOCKED:
                self.window.set_command_state_unlocked()
            elif msg.status_id in (
                AirStatusId.MISSION_START,
                AirStatusId.LAUNCH,
                AirStatusId.PARACHUTE_DEPLOY,
                AirStatusId.LANDING,
            ):
                self.window.set_command_state_mission()

            self.logger.write(
                {
                    "ts": time.time(),
                    "dir": "RX",
                    "layer": "AIR_PARSED",
                    "kind": "STATUS",
                    "seq": msg.seq,
                    "status_id": int(msg.status_id),
                    "time_ms": msg.time_ms,
                    "arg0": msg.arg0,
                    "arg1": msg.arg1,
                }
            )

        elif isinstance(msg, AirAckMessage):
            self._on_air_cmd_ack_result(msg)

            self.logger.write(
                {
                    "ts": time.time(),
                    "dir": "RX",
                    "layer": "AIR_PARSED",
                    "kind": "ACK",
                    "seq": msg.seq,
                    "ack_seq": msg.ack_seq,
                    "ack_cmd_id": msg.ack_cmd_id,
                    "result": msg.result,
                    "time_ms": msg.time_ms,
                }
            )

    def _accel_raw_to_mps2(self, raw: tuple[int, int, int]) -> tuple[float, float, float]:
        full_scale_g = self.window.current_accel_full_scale_g()
        scale = full_scale_g * STANDARD_GRAVITY_MPS2 / 32768.0
        return raw[0] * scale, raw[1] * scale, raw[2] * scale

    def _gyro_raw_to_radps(self, raw: tuple[int, int, int]) -> tuple[float, float, float]:
        full_scale_dps = self.window.current_gyro_full_scale_dps()
        scale = full_scale_dps * 3.141592653589793 / 180.0 / 32768.0
        return raw[0] * scale, raw[1] * scale, raw[2] * scale


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    controller = Controller(window)

    window.show()

    rc = app.exec()

    controller.disconnect()
    controller.logger.close()

    return rc
