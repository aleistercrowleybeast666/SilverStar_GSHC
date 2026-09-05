from __future__ import annotations

import os
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import AIR_CMD_MAX_RETRIES
from protocol.air import (
    AirAckMessage, TOKEN_CALIBRATION, parse_air_frame,
)
from protocol.common import AirAckResult, AirCalibrationMode, AirCalibrationState, AirCmdId, GspType
from protocol.gsp_min import build_gsp_frame
from protocol.receive_pipeline import ReceivePipeline, protocol_event_log_records
from services.i18n import I18n, Language
from services.state_model import CalibrationStartResult, EventHistory, FlightControllerState, HandshakeState
from transport.protocol_worker import ProtocolBatch
from ui.main_window import MainWindow
from test_start_ack_state import capability, make_controller, preflight_status


MASKS = (0x01, 0x03, 0x05, 0x07, 0x81, 0x83, 0x85, 0x87, 0xF9, 0xFB, 0xFD, 0xFF, 0, 0x80)


def handshake(controller, mask, seq=42):
    controller._handle_capability(replace(capability(seq=seq), calibration_mode_mask=mask))
    pending = controller.pending_capability_ack
    assert pending is not None
    controller._handle_ack_message(AirAckMessage(3, pending.command_seq, 5, 0, 100))


def air_rx(air):
    return build_gsp_frame(int(GspType.AIR_RX), bytes((186, 20, len(air))) + air)


@pytest.fixture
def window():
    temporary_directory = TemporaryDirectory()
    application = QApplication.instance() or QApplication([])
    view = MainWindow(I18n(QSettings(os.path.join(temporary_directory.name, "ui.ini"), QSettings.IniFormat)))
    yield view
    view.render_timer.stop()
    view.calibration_dialog.close()
    view.link_details_dialog.close()
    view.close()
    view.deleteLater()
    application.processEvents()
    temporary_directory.cleanup()


@pytest.mark.parametrize("mask", MASKS)
def test_capability_parser_handshake_and_sampling_modes(mask):
    wire = bytes((0x12, 42, 0, 1, mask, 15, 16, 0xD0, 7))
    air, message = parse_air_frame(wire)
    assert air.raw == wire
    assert message.calibration_mode_mask == mask
    assert message.profile_supported
    event = ReceivePipeline().feed(air_rx(wire))[0]
    records = protocol_event_log_records(event)
    record = next(record for record in records if record.get("kind") == "CAPABILITY")
    assert record["calibration_mode_mask"] == mask
    assert not event.air_error

    controller = make_controller()
    controller._handle_capability(message)
    assert not controller.state.sampling_calibration_modes()
    assert controller.pending_capability_ack.air_frame == bytes.fromhex("30 00 05 00 00 00 00 2a 00")
    assert controller.worker.sent == [bytes.fromhex("a5 5a 03 0a 09 30 00 05 00 00 00 00 2a 00 dd c2")]
    controller._handle_ack_message(AirAckMessage(3, 0, 5, 0, 100))
    assert controller.state.sampling_calibration_modes() == tuple(mode for mode in (1, 2) if mask & (1 << mode))
    assert not controller.state.calibration.ready
    assert controller.state.calibration.mode == AirCalibrationMode.NOT_SELECTED


@pytest.mark.parametrize("mask", MASKS)
@pytest.mark.parametrize("mode", (-1, 0, 1, 2, 3, 7, 255, 257, 258))
def test_controller_gate_covers_public_generic_and_retry_paths(mask, mode):
    for generic in (False, True):
        controller = make_controller()
        handshake(controller, mask)
        controller.worker.sent.clear()
        controller.air_seq = 0
        if generic:
            controller._send_air_cmd(int(AirCmdId.CAL_START), TOKEN_CALIBRATION, mode)
        else:
            controller.send_cal_start(mode)
        supported = mode in (1, 2) and bool(mask & (1 << mode))
        assert len(controller.worker.sent) == int(supported)
        assert bool(controller.pending_air_cmds) == supported
        if supported:
            expected = bytes.fromhex("30 00 07 30 4c 41 43 01 00" if mode == 1 else "30 00 07 30 4c 41 43 02 00")
            pending = controller._find_pending_air_cmd(7)
            assert pending.air_frame == expected
            assert controller.worker.sent[0] == bytes.fromhex(
                "a5 5a 03 0a 09 30 00 07 30 4c 41 43 01 00 38 91"
                if mode == 1 else "a5 5a 03 0a 09 30 00 07 30 4c 41 43 02 00 6b c4"
            )
            # A pending retry must recheck the current build, too.
            controller.state.capability = replace(controller.state.capability, calibration_mode_mask=1)
            pending.last_send_monotonic = -100.0
            controller._check_air_cmd_timeouts()
            assert len(controller.worker.sent) == 1
            assert not controller.pending_air_cmds
        else:
            assert controller.state.check_calibration_start(mode) is CalibrationStartResult.UNSUPPORTED_MODE
            assert controller.logger.records[-1]["kind"] == "CAL_START_LOCAL_REJECTED"


@pytest.mark.parametrize("mode", (0, 1, 2))
@pytest.mark.parametrize("have_capability", (False, True))
def test_no_handshake_never_transmits_cal_start(mode, have_capability):
    controller = make_controller()
    if have_capability:
        controller.state.capability = capability()
    for call in (lambda: controller.send_cal_start(mode), lambda: controller._send_air_cmd(7, TOKEN_CALIBRATION, mode)):
        call()
    assert not controller.worker.sent
    assert not controller.pending_air_cmds
    assert controller.air_seq == 0
    assert controller.state.check_calibration_start(mode) is CalibrationStartResult.HANDSHAKE_REQUIRED


def test_retry_is_rejected_if_handshake_is_lost():
    controller = make_controller()
    handshake(controller, 7)
    controller.send_cal_start(1)
    count = len(controller.worker.sent)
    pending = controller._find_pending_air_cmd(7)
    pending.last_send_monotonic = -100.0
    controller.state.capability_acked = False
    controller._check_air_cmd_timeouts()
    assert len(controller.worker.sent) == count
    assert not controller.pending_air_cmds


@pytest.mark.parametrize("ready", (False, True))
def test_none_snapshot_keeps_real_readiness_without_any_command(ready):
    controller = make_controller()
    handshake(controller, 1)
    controller.worker.sent.clear()
    snapshot = preflight_status(
        capability_acked=True, calibration_mode=0, calibration_ready=ready,
        calibration_state=int(AirCalibrationState.READY if ready else AirCalibrationState.IDLE),
    )
    controller._handle_preflight_status(snapshot)
    controller._check_air_cmd_timeouts()
    assert controller.state.calibration.mode == 0
    assert controller.state.calibration.ready is ready
    assert not controller.worker.sent
    assert not controller.pending_air_cmds
    assert not controller.state.alignment.ready


@pytest.mark.parametrize("mode", (1, 2))
def test_cal_start_ack_acceptance_progress_and_ready_snapshot(mode):
    controller = make_controller()
    handshake(controller, 7)
    controller.send_cal_start(mode)
    pending = controller._find_pending_air_cmd(7)
    controller._handle_ack_message(AirAckMessage(4, pending.seq, 7, 0, 200))
    assert controller.state.radio_message.key == "radio.calibration_accepted"
    assert not controller.state.calibration.ready
    assert not controller.pending_air_cmds
    controller._handle_preflight_status(preflight_status(capability_acked=True, calibration_mode=mode, calibration_state=2))
    assert controller.state.calibration.state == AirCalibrationState.COLLECTING
    assert not controller.state.calibration.ready
    controller._handle_preflight_status(preflight_status(capability_acked=True, calibration_mode=mode, calibration_ready=True))
    assert controller.state.calibration.ready
    assert controller.state.calibration.state == AirCalibrationState.READY


@pytest.mark.parametrize("result", (AirAckResult.BAD_PARAM, AirAckResult.BAD_STATE, AirAckResult.BUSY))
def test_rejected_cal_start_keeps_capability_and_never_falls_back(result):
    controller = make_controller()
    handshake(controller, 7)
    accepted = controller.state.capability
    controller.send_cal_start(1)
    pending = controller._find_pending_air_cmd(7)
    sent_count = len(controller.worker.sent)
    controller._handle_ack_message(AirAckMessage(4, pending.seq, 7, int(result), 200))
    for _ in range(8):
        controller._check_air_cmd_timeouts()
    assert not controller.pending_air_cmds
    assert len(controller.worker.sent) == sent_count
    assert controller.state.capability is accepted
    assert controller.state.connected
    assert controller.state.handshake.handshake_state is HandshakeState.ACKED
    assert controller.state.handshake.air_ack_result == result
    assert not controller.state.calibration.ready
    assert controller.state.radio_message.key == (
        "radio.calibration_bad_param" if result is AirAckResult.BAD_PARAM else "radio.ack_failed"
    )


def test_cal_start_timeout_is_bounded_and_retains_capability():
    controller = make_controller()
    handshake(controller, 3)
    controller.worker.sent.clear()
    accepted = controller.state.capability
    controller.send_cal_start(1)
    pending = controller._find_pending_air_cmd(7)
    for _ in range(AIR_CMD_MAX_RETRIES + 5):
        pending.last_send_monotonic = -100.0
        controller._check_air_cmd_timeouts()
    assert len(controller.worker.sent) == AIR_CMD_MAX_RETRIES + 1
    assert len(set(controller.worker.sent)) == 1
    assert not controller.pending_air_cmds
    assert controller.state.capability is accepted
    assert controller.state.capability_acked
    assert controller.state.radio_message.key == "radio.calibration_timeout"


def test_alignment_and_reset_golden_commands_remain_available_for_identity():
    for method, expected_air, expected_gsp in (
        ("send_cal_reset", "30 00 0a 30 4c 41 43 00 00", "a5 5a 03 0a 09 30 00 0a 30 4c 41 43 00 00 03 48"),
        ("send_align_start", "30 00 0b 47 49 4c 41 00 00", "a5 5a 03 0a 09 30 00 0b 47 49 4c 41 00 00 0e 09"),
    ):
        controller = make_controller()
        handshake(controller, 1)
        controller.worker.sent.clear()
        controller.air_seq = 0
        controller.state.calibration.ready = True
        getattr(controller, method)()
        pending = next(iter(controller.pending_air_cmds.values()))
        assert pending.air_frame == bytes.fromhex(expected_air)
        assert controller.worker.sent == [bytes.fromhex(expected_gsp)]


@pytest.mark.parametrize("mask", MASKS)
def test_gui_only_offers_current_build_sampling_modes(window, mask):
    controller = make_controller()
    handshake(controller, mask)
    state = controller.state
    window.bind_runtime_model(state, EventHistory())
    dialog = window.calibration_dialog
    expected = [mode for mode in (1, 2) if mask & (1 << mode)]
    assert [dialog.mode_combo.itemData(i) for i in range(dialog.mode_combo.count())] == expected
    assert window.btn_calibration.isEnabled() is bool(expected)
    assert dialog.btn_start.isEnabled() is bool(expected)
    assert window.btn_cal_reset.isEnabled()
    assert dialog.mode_combo.findData(0) == -1
    calls = []
    window.on_cal_start = calls.append
    dialog.mode_combo.addItem("injected NONE", 0)
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.count() - 1)
    dialog._start_selected_mode()
    assert not calls
    if not expected:
        window._show_calibration_dialog()
        assert not dialog.isVisible()
    window.on_cal_reset = lambda: calls.append("RESET")
    window.btn_cal_reset.click()
    assert calls == ["RESET"]


@pytest.mark.parametrize("ready", (False, True))
def test_none_gui_uses_identity_text_and_real_ready_in_both_languages(window, ready):
    controller = make_controller()
    handshake(controller, 1)
    controller._handle_preflight_status(preflight_status(capability_acked=True, calibration_mode=0, calibration_ready=ready))
    window.bind_runtime_model(controller.state, EventHistory())
    assert window.btn_calibration.text() == "开始校准"
    assert window.lbl_cal_mode.text() == "单位校正（未执行单面/六面采样校准）"
    assert window.lbl_cal_ready.text() == ("是" if ready else "否")
    assert window.lbl_cal_capability.text() == "本工程不执行采样校准，使用单位校正（NONE）"
    assert window.btn_align_start.isEnabled() is ready
    assert not window.btn_calibration.isEnabled()
    window.i18n.set_language(Language.EN_US)
    window.retranslate_ui()
    assert window.btn_calibration.text() == "Start Calibration"
    assert window.lbl_cal_mode.text() == "Identity correction (no one-face/six-face sampling calibration)"
    assert window.lbl_cal_ready.text() == ("Yes" if ready else "No")
    assert "identity correction (NONE)" in window.lbl_cal_capability.text()
    assert not window.calibration_dialog.isVisible()


def test_reconnect_and_latest_handshake_rebuild_hidden_gui_controls(window):
    controller = make_controller()
    for generation, mask in enumerate((7, 1, 3, 5, 7), start=1):
        controller.state = FlightControllerState(session_generation=generation, connected=True)
        window.bind_runtime_model(controller.state, EventHistory())
        assert window.calibration_dialog.mode_combo.count() == 0
        controller._handle_capability(replace(capability(seq=10), calibration_mode_mask=7))
        controller._handle_capability(replace(capability(seq=11), calibration_mode_mask=mask))
        pending = controller.pending_capability_ack
        _, cmd = parse_air_frame(pending.air_frame)
        assert (cmd.param0, cmd.param1) == (11, 0)
        controller._handle_ack_message(AirAckMessage(1, pending.command_seq, 5, 0, 100))
        window.render_state()
        dialog = window.calibration_dialog
        assert [dialog.mode_combo.itemData(i) for i in range(dialog.mode_combo.count())] == [m for m in (1, 2) if mask & (1 << m)]
        # Stale broadcasts after success stay diagnostic only, per existing session policy.
        accepted = controller.state.capability
        controller._handle_capability(replace(capability(seq=9), calibration_mode_mask=7))
        assert controller.state.capability is accepted


def test_downlink_pause_does_not_hide_gsp_progress_or_erase_handshake(window):
    controller = make_controller()
    controller._connection_generation = 1
    pipeline = ReceivePipeline()
    def deliver(wire):
        controller.on_protocol_batch(ProtocolBatch(1, tuple(pipeline.feed(wire))))
    deliver(air_rx(bytes.fromhex("12 2a 00 01 87 0f 10 d0 07")))
    pending = controller.pending_capability_ack
    deliver(air_rx(bytes((0x40, 1, pending.command_seq, 5, 0, 100, 0, 0, 0))))
    deliver(air_rx(bytes.fromhex("13 02 02 12 00 ff 00 10 0b")))
    controller.send_cal_start(1)
    controller._on_serial_bytes_sent(1, 16)
    deliver(build_gsp_frame(4, bytes((3, 0, 0))))
    deliver(build_gsp_frame(1, bytes.fromhex("02 02 09 00 00 00 04 00 00 00 00 00")))
    diagnostics = controller.state.handshake
    last_air = diagnostics.last_air_rx_monotonic_ns
    assert diagnostics.capability_rx == 1
    assert diagnostics.preflight_status_rx == 1
    assert diagnostics.gsp_air_tx_requests == 2
    assert diagnostics.gsp_air_tx_serial_writes == 1
    assert diagnostics.gsp_air_tx_ack_ok == 1
    assert diagnostics.air_ack_rx == 1
    assert controller.state.gs_tx_count == 9
    assert controller.state.gs_rx_count == 4
    assert controller.state.capability_acked
    window.i18n.set_language(Language.EN_US)
    window.retranslate_ui()
    with patch("ui.main_window.time_monotonic_ns", return_value=last_air + 5_000_000_000):
        window.link_details_dialog.render(controller.state)
    details = window.link_details_dialog.details_text.toPlainText()
    assert "Unknown calibration capability bits (diagnostic only): 0x80" in details
    assert "Last AIR downlink age: 5000 ms" in details
    assert "PREFLIGHT_STATUS RX / last snapshot age: 1 /" in details
    assert "GS TX / RX / CRC: 9 / 4 / 0" in details
    assert "RSSI / SNR: -70 dBm / 5.00 dB" in details
    assert "GSP AIR_TX requests: 2" in details
