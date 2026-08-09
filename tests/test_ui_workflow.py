from __future__ import annotations

import os
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from protocol.air import AirCapabilityMessage
from protocol.common import AirAlignmentState, AirCalibrationMode, AirCalibrationState
from services.i18n import I18n
from services.state_model import EventHistory, FlightControllerState
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
