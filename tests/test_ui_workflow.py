from __future__ import annotations

import os
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from protocol.air import AirCapabilityMessage
from protocol.common import (
    AirAlignmentState,
    AirCalibrationDiagnosticReason,
    AirCalibrationMode,
    AirCalibrationState,
    AirStatusId,
)
from services.i18n import I18n, Language
from services.state_model import (
    EventHistory,
    FlightControllerState,
    FlightEvent,
    HandshakeState,
    MissionPhase,
)
from ui.main_window import MainWindow


def ready_state(generation: int = 1) -> FlightControllerState:
    state = FlightControllerState(
        session_generation=generation,
        connected=True,
        connection_text="COM_TEST@230400",
    )
    state.capability = AirCapabilityMessage(
        seq=1,
        air_profile_id=0,
        command_policy=1,
        calibration_mode_mask=0x07,
        alignment_capability_mask=0x07,
        accel_full_scale_g=16,
        gyro_full_scale_dps=2000,
    )
    state.profile_supported = True
    state.capability_acked = True
    state.calibration.state = int(AirCalibrationState.READY)
    state.calibration.mode = int(AirCalibrationMode.SIX_FACE)
    state.calibration.completed_face_mask = 0x3F
    state.calibration.ready = True
    state.alignment.state = int(AirAlignmentState.READY)
    state.alignment.attitude_ready = True
    state.alignment.gnss_origin_ready = True
    state.alignment.baro_origin_ready = True
    state.alignment.ready = True
    state.system_ready = True
    state.selftest_passed = True
    state.start_unlocked = True
    state.start_block_reason = 0
    return state


class UiWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        settings = QSettings(
            os.path.join(self.temporary_directory.name, "settings.ini"),
            QSettings.IniFormat,
        )
        self.window = MainWindow(I18n(settings))

    def tearDown(self) -> None:
        self.window.calibration_dialog.close()
        self.window.link_details_dialog.close()
        self.window.close()
        self.temporary_directory.cleanup()

    def test_three_pages_are_always_manually_selectable(self) -> None:
        labels = [self.window.pages.tabText(index) for index in range(self.window.pages.count())]
        self.assertEqual(labels, ["预飞行", "飞行", "后期处理"])
        for page in (
            self.window.preflight_page,
            self.window.flight_page,
            self.window.post_process_page,
        ):
            self.window.pages.setCurrentWidget(page)
            self.assertIs(self.window.pages.currentWidget(), page)

    def test_mission_auto_switches_once_and_does_not_steal_manual_selection(self) -> None:
        state = ready_state(generation=10)
        events = EventHistory()
        self.window.bind_runtime_model(state, events, select_preflight=True)
        state.mission_started = True
        self.window.render_state()
        self.assertIs(self.window.pages.currentWidget(), self.window.flight_page)

        self.window.pages.setCurrentWidget(self.window.preflight_page)
        state.latest_flight_time_ms = 1200
        state.sensor.revision += 1
        state.live_plot.append(0.2, (1, 2, 3), (4, 5, 6))
        self.window.render_state()
        self.assertIs(self.window.pages.currentWidget(), self.window.preflight_page)

        new_state = ready_state(generation=11)
        new_state.mission_started = True
        self.window.bind_runtime_model(new_state, EventHistory(), select_preflight=True)
        self.assertIs(self.window.pages.currentWidget(), self.window.flight_page)

    def test_start_button_is_snapshot_driven(self) -> None:
        state = ready_state()
        self.window.bind_runtime_model(state, EventHistory(), select_preflight=True)
        self.window.render_state()
        self.assertTrue(self.window.btn_start.isEnabled())

        state.alignment.ready = False
        state.start_block_reason = 0x0C
        self.window.render_state()
        self.assertFalse(self.window.btn_start.isEnabled())
        self.assertEqual(self.window.lbl_start_reason.text(), "需要完成初对准")

    def test_alignment_stale_disables_start_and_reenables_alignment(self) -> None:
        state = ready_state()
        state.alignment.state = int(AirAlignmentState.STALE)
        state.alignment.ready = False
        self.window.bind_runtime_model(state, EventHistory(), select_preflight=True)

        self.assertFalse(self.window.btn_start.isEnabled())
        self.assertTrue(self.window.btn_align_start.isEnabled())
        self.assertEqual(self.window.lbl_align_state.text(), "初对准已失效")
        self.assertIn("重新执行初对准", self.window.lbl_align_hint.text())

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        self.assertEqual(self.window.lbl_align_state.text(), "Alignment Stale")
        self.assertEqual(
            self.window.lbl_align_hint.text(),
            "Movement detected after alignment; run alignment again",
        )

    def test_six_face_diagnostic_is_visible_and_localized(self) -> None:
        state = ready_state()
        state.calibration.state = int(AirCalibrationState.COLLECTING)
        state.calibration.ready = False
        state.calibration.current_face = 0
        state.latest_calibration_diagnostic_face = 0
        state.latest_calibration_diagnostic_reason = int(
            AirCalibrationDiagnosticReason.GRAVITY_DIRECTION
        )
        self.window.bind_runtime_model(state, EventHistory())
        self.window.calibration_dialog.render(state)

        self.assertEqual(
            self.window.lbl_cal_issue.text(),
            "X+：当前重力方向与所选面不符",
        )
        self.assertIn("X+：当前重力方向与所选面不符", self.window.calibration_dialog.lbl_issue.text())

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        self.assertEqual(
            self.window.lbl_cal_issue.text(),
            "X+: Current gravity direction does not match the selected face",
        )

    def test_event_history_is_preflight_chronological_and_scrolls_to_bottom(self) -> None:
        state = ready_state()
        events = EventHistory()
        for index in range(40):
            events.append(
                FlightEvent(
                    seq=index,
                    status_id=int(AirStatusId.BOOT),
                    name="BOOT",
                    time_ms=index,
                    arg0=0,
                    arg1=0,
                    host_rx_monotonic_ns=index,
                )
            )
        self.window.show()
        self.window.bind_runtime_model(state, events, select_preflight=True)
        self.application.processEvents()

        self.assertTrue(self.window.preflight_page.isAncestorOf(self.window.event_list))
        self.assertFalse(self.window.flight_page.isAncestorOf(self.window.event_list))
        self.assertIn("0 ms", self.window.event_list.item(0).text())
        self.assertIn("39 ms", self.window.event_list.item(39).text())
        scrollbar = self.window.event_list.verticalScrollBar()
        scrollbar.setValue(scrollbar.minimum())
        events.append(
            FlightEvent(
                seq=40,
                status_id=int(AirStatusId.BOOT),
                name="BOOT",
                time_ms=40,
                arg0=0,
                arg1=0,
                host_rx_monotonic_ns=40,
            )
        )
        self.window.render_state()
        self.application.processEvents()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_air_link_is_short_and_details_keep_full_diagnostics(self) -> None:
        state = ready_state()
        diagnostics = state.handshake
        diagnostics.handshake_state = HandshakeState.HANDSHAKING
        diagnostics.last_capability_seq = 17
        diagnostics.capability_ack_cmd_seq = 29
        diagnostics.capability_ack_attempts = 3
        diagnostics.gsp_air_tx_requests = 4
        diagnostics.gsp_air_tx_ack_ok = 2
        diagnostics.gsp_air_tx_ack_fail = 1
        self.window.bind_runtime_model(state, EventHistory())

        short_text = self.window.lbl_pf_air_link.text()
        self.assertEqual(short_text, "握手中")
        for fragment in ("Cap#", "CMD#", "GSP", "attempt"):
            self.assertNotIn(fragment, short_text)

        self.window.link_details_dialog.render(state)
        details = self.window.link_details_dialog.details_text.toPlainText()
        self.assertIn("最新 Capability seq：17", details)
        self.assertIn("ACK 尝试次数：3", details)
        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        self.window.link_details_dialog.render(state)
        details = self.window.link_details_dialog.details_text.toPlainText()
        self.assertIn("Latest Capability seq: 17", details)
        self.assertIn("Capability ACK CMD seq: 29", details)
        self.assertIn("ACK attempts: 3", details)
        self.assertIn("GSP ACK OK / FAIL: 2 / 1", details)
        self.assertIn("protocol_queue=", details)

    def test_flight_page_mission_state_does_not_infer_launch(self) -> None:
        state = ready_state()
        state.mission_started = True
        state.mission_presentation.phase = MissionPhase.MISSION_ACTIVE
        state.latest_flight_time_ms = 1000
        self.window.bind_runtime_model(state, EventHistory())
        self.assertEqual(
            self.window.lbl_mission_state.text(), "任务已开始 / 等待发射"
        )
        self.assertNotIn("发射", self.window.lbl_mission_last_event.text())

        state.mission_presentation.phase = MissionPhase.IN_FLIGHT
        state.mission_presentation.last_critical_event_name = "LAUNCH"
        state.mission_presentation.last_critical_event_time_ms = 900
        self.window.render_state()
        self.assertEqual(self.window.lbl_mission_state.text(), "飞行中")
        self.assertEqual(self.window.lbl_mission_last_event.text(), "发射")

    def test_calibration_dialog_only_lists_capability_declared_modes(self) -> None:
        state = ready_state()
        state.capability = AirCapabilityMessage(
            seq=1,
            air_profile_id=0,
            command_policy=1,
            calibration_mode_mask=0x05,
            alignment_capability_mask=0x01,
            accel_full_scale_g=16,
            gyro_full_scale_dps=2000,
        )
        self.window.bind_runtime_model(state, EventHistory())
        self.window.calibration_dialog.render(state)
        modes = [
            self.window.calibration_dialog.mode_combo.itemData(index)
            for index in range(self.window.calibration_dialog.mode_combo.count())
        ]
        self.assertEqual(modes, [int(AirCalibrationMode.NONE), int(AirCalibrationMode.SIX_FACE)])

    def test_3d_model_geometry_camera_and_single_view_are_preserved(self) -> None:
        self.assertEqual(self.window.DEFAULT_CAMERA_DISTANCE, 6.5)
        self.assertEqual(self.window.DEFAULT_CAMERA_ELEVATION, 20.0)
        self.assertEqual(self.window.DEFAULT_CAMERA_AZIMUTH, 35.0)
        self.assertEqual(self.window.DEFAULT_CAMERA_CENTER, (0.0, 0.0, 0.9))
        self.assertEqual(self.window.base_vertices.shape, (5, 3))
        self.assertEqual(self.window.base_faces.shape, (6, 3))
        self.assertEqual(len(self.window.body_axis_items), 3)
        self.assertEqual(set(self.window.world_direction_labels), {"E", "W", "N", "S", "U"})
        self.assertEqual(self.window.body_nose_label.text, "NOSE")
        self.assertFalse(hasattr(self.window, "accel_fs_combo"))
        self.assertFalse(hasattr(self.window, "gyro_fs_combo"))


if __name__ == "__main__":
    unittest.main()
