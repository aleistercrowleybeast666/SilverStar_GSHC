from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QApplication

from protocol.air import AirCapabilityMessage, AirSensorStatusMessage
from protocol.common import (
    AirAckResult,
    AirAlignmentState,
    AirCalibrationDiagnosticReason,
    AirCalibrationMode,
    AirCalibrationState,
    AirSensorId,
    AirStatusId,
)
from services.data_migration import DataMigrationConflictPolicy
from services.i18n import I18n, Language
from services.preferences import ExportItem, ExportLanguage, Theme
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
        sensor_summary_flags=0x0F,
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
        self.window.sensor_details_dialog.close()
        self.window.export_options_dialog.close()
        self.window.data_directory_dialog.close()
        self.window.data_migration_progress_dialog.close()
        self.window.close()
        self.temporary_directory.cleanup()

    def test_three_pages_are_always_manually_selectable(self) -> None:
        labels = [self.window.pages.tabText(index) for index in range(self.window.pages.count())]
        self.assertEqual(labels, ["预飞行", "飞行", "后期处理"])
        self.assertEqual(self.window.pages.objectName(), "pageTabs")
        self.assertEqual(self.window.pages.tabBar().objectName(), "pageNavigation")
        self.assertTrue(self.window.pages.tabBar().expanding())
        self.assertTrue(self.window.pages.documentMode())
        for page in (
            self.window.preflight_page,
            self.window.flight_page,
            self.window.post_process_page,
        ):
            self.window.pages.setCurrentWidget(page)
            self.assertIs(self.window.pages.currentWidget(), page)

    def test_header_brand_credit_version_and_localized_labels(self) -> None:
        self.assertEqual(self.window.windowTitle(), "SilverStar_GSHC")
        self.assertEqual(self.window.header_bar.objectName(), "headerBar")
        self.assertEqual(self.window.header_title.text(), "SilverStar地面站上位机")
        self.assertEqual(self.window.lbl_language.text(), "语言")
        self.assertEqual(self.window.lbl_theme.text(), "主题")
        self.assertEqual(self.window.header_credit.text(), "辰星引力开发")
        self.assertEqual(
            self.window.header_version.text(),
            "SilverStar_GSHC 0.0.3",
        )
        header_layout = self.window.header_identity_layout
        self.assertLess(
            header_layout.indexOf(self.window.header_version),
            header_layout.indexOf(self.window.header_credit),
        )
        self.assertLess(
            header_layout.indexOf(self.window.header_credit),
            header_layout.indexOf(self.window.lbl_language),
        )
        self.assertLess(
            header_layout.indexOf(self.window.language_combo),
            header_layout.indexOf(self.window.lbl_theme),
        )

        english_index = self.window.language_combo.findData(Language.EN_US.value)
        self.window.language_combo.setCurrentIndex(english_index)
        self.application.processEvents()

        self.assertEqual(self.window.windowTitle(), "SilverStar_GSHC")
        self.assertEqual(
            self.window.header_title.text(),
            "SilverStar Ground Station Host Computer",
        )
        self.assertEqual(self.window.lbl_language.text(), "Language")
        self.assertEqual(self.window.lbl_theme.text(), "Theme")
        self.assertEqual(self.window.header_credit.text(), "by CXYL")
        self.assertEqual(
            self.window.header_version.text(),
            "SilverStar_GSHC 0.0.3",
        )

    def test_post_process_uses_inline_progress_and_cancel_controls(self) -> None:
        self.assertGreaterEqual(self.window.simulation_progress_bar.minimumHeight(), 36)
        self.assertGreaterEqual(self.window.processing_progress_bar.minimumHeight(), 36)
        self.assertFalse(self.window.btn_cancel_sim.isEnabled())
        self.assertFalse(self.window.btn_cancel_processing.isEnabled())
        self.assertEqual(
            self.window.lbl_simulation_progress.text(),
            "尚未生成模拟日志。",
        )
        self.assertEqual(self.window.btn_generate_sim.text(), "生成模拟日志")
        self.assertEqual(self.window.btn_select_data_root.text(), "选择数据目录")
        self.assertEqual(self.window.btn_open_data_dir.text(), "打开结果目录")
        self.assertLess(
            self.window.folder_button_layout.indexOf(self.window.btn_select_data_root),
            self.window.folder_button_layout.indexOf(self.window.btn_open_log_dir),
        )
        self.assertEqual(
            self.window.lbl_processing_progress.text(),
            "尚未开始数据解算。",
        )

        self.window.begin_simulation_task()
        self.window.update_simulation_progress(
            35,
            100,
            "task.simulation.generating",
        )
        self.assertEqual(self.window.simulation_progress_bar.value(), 35)
        self.assertTrue(self.window.btn_cancel_sim.isEnabled())
        self.assertFalse(self.window.btn_generate_sim.isEnabled())
        self.window.mark_simulation_cancelling()
        self.assertFalse(self.window.btn_cancel_sim.isEnabled())
        self.assertIn("取消并清理", self.window.lbl_simulation_progress.text())
        self.window.finish_simulation_task(
            "task.simulation.cancelled",
            completed=False,
        )
        self.assertEqual(self.window.simulation_progress_bar.value(), 0)
        self.assertIn("已清理", self.window.lbl_simulation_progress.text())

        self.window.begin_processing_task()
        self.window.update_processing_progress(
            4,
            8,
            "task.processing.running",
            detail="summary_ZH.txt",
        )
        self.assertEqual(self.window.processing_progress_bar.maximum(), 8)
        self.assertEqual(self.window.processing_progress_bar.value(), 4)
        self.assertIn("summary_ZH.txt", self.window.lbl_processing_progress.text())

        english_index = self.window.language_combo.findData(Language.EN_US.value)
        self.window.language_combo.setCurrentIndex(english_index)
        self.application.processEvents()
        self.assertEqual(self.window.btn_cancel_processing.text(), "Cancel and Clean Up")
        self.assertEqual(self.window.btn_generate_sim.text(), "Generate Simulation Log")
        self.assertEqual(self.window.btn_select_data_root.text(), "Select Data Directory")
        self.assertEqual(self.window.btn_open_data_dir.text(), "Open Results Folder")
        self.assertEqual(
            self.window.lbl_processing_progress.text(),
            "Processing data: summary_ZH.txt",
        )
        self.window.finish_processing_task(
            "task.processing.completed",
            completed=True,
            path="D:/data/result",
        )
        self.assertEqual(self.window.processing_progress_bar.value(), 100)
        self.assertIn("D:/data/result", self.window.lbl_processing_progress.text())

    def test_data_directory_button_opens_application_dialog_with_browse_choice(self) -> None:
        dialog = self.window.data_directory_dialog
        default_root = Path("D:/SilverStar_GSHC_Data")
        dialog.prepare(default_root)
        self.assertEqual(dialog.path_edit.text(), str(default_root))
        self.assertFalse(dialog.chk_migrate_existing.isEnabled())
        self.assertFalse(dialog.conflict_policy_combo.isEnabled())
        self.assertEqual(
            dialog.conflict_policy_combo.currentData(),
            DataMigrationConflictPolicy.OVERWRITE.value,
        )

        changed_root = Path("D:/SilverStar_GSHC_Archive")
        dialog.path_edit.setText(str(changed_root))
        self.assertTrue(dialog.chk_migrate_existing.isEnabled())
        self.assertFalse(dialog.conflict_policy_combo.isEnabled())
        dialog.chk_migrate_existing.setChecked(True)
        self.assertTrue(dialog.conflict_policy_combo.isEnabled())
        selection = dialog.selection()
        self.assertEqual(selection.data_root, changed_root)
        self.assertTrue(selection.migrate_existing_data)
        self.assertEqual(
            selection.conflict_policy,
            DataMigrationConflictPolicy.OVERWRITE,
        )

        browsed_root = "D:/SilverStar_GSHC_Browsed"
        with patch(
            "ui.main_window.QFileDialog.getExistingDirectory",
            return_value=browsed_root,
        ) as browse:
            dialog.btn_browse.click()
        browse.assert_called_once()
        self.assertEqual(dialog.path_edit.text(), browsed_root)

        english_index = self.window.language_combo.findData(Language.EN_US.value)
        self.window.language_combo.setCurrentIndex(english_index)
        self.application.processEvents()
        self.assertEqual(dialog.btn_browse.text(), "Browse…")
        self.assertEqual(dialog.btn_accept.text(), "OK")
        self.assertEqual(
            dialog.conflict_policy_combo.itemText(
                dialog.conflict_policy_combo.findData(
                    DataMigrationConflictPolicy.OVERWRITE.value
                )
            ),
            "Overwrite (Default)",
        )
        self.assertEqual(
            dialog.conflict_policy_combo.itemText(
                dialog.conflict_policy_combo.findData(
                    DataMigrationConflictPolicy.RENAME.value
                )
            ),
            "Rename and append (1)",
        )

    def test_data_migration_progress_uses_separate_modal_dialog(self) -> None:
        dialog = self.window.data_migration_progress_dialog
        self.window.begin_data_migration("D:/Old", "D:/New")
        self.application.processEvents()
        self.assertTrue(dialog.isModal())
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.progress_bar.minimum(), 0)
        self.assertEqual(dialog.progress_bar.maximum(), 0)

        self.window.set_data_migration_plan(4, 4096)
        self.window.update_data_migration_progress(50, "logs/session.jsonl")
        self.assertEqual(dialog.progress_bar.value(), 50)
        self.assertIn("logs/session.jsonl", dialog.lbl_status.text())
        self.window.mark_data_migration_committing()
        self.assertFalse(dialog.btn_action.isEnabled())
        self.window.finish_data_migration_completed(
            4,
            "D:/New",
            skipped_count=2,
        )
        self.assertEqual(dialog.progress_bar.value(), 100)
        self.assertEqual(dialog.btn_action.text(), "关闭")
        self.assertIn("跳过 2 个同名文件", dialog.lbl_status.text())
        dialog.close()

    def test_controller_migration_thread_updates_dialog_and_switches_root(self) -> None:
        from app import Controller

        base_dir = Path(self.temporary_directory.name)
        source_root = base_dir / "source"
        target_root = base_dir / "target"
        source_file = source_root / "logs" / "session.jsonl"
        source_file.parent.mkdir(parents=True)
        source_file.write_text("flight-log", encoding="utf-8")

        controller = Controller(self.window)
        controller.data_root = source_root
        controller.log_dir = source_root / "logs"
        controller.data_dir = source_root / "data"
        controller.logger.set_log_dir(controller.log_dir)
        try:
            with patch(
                "app.save_user_data_root",
                side_effect=lambda root, **_kwargs: Path(root),
            ) as save_root:
                controller._start_data_migration(
                    target_root,
                    DataMigrationConflictPolicy.RENAME,
                )
                deadline = time.monotonic() + 10.0
                while (
                    controller.data_migration_thread is not None
                    and time.monotonic() < deadline
                ):
                    self.application.processEvents()
                    time.sleep(0.01)

            self.assertIsNone(controller.data_migration_thread)
            self.assertEqual(controller.data_root, target_root)
            self.assertFalse(source_file.exists())
            self.assertEqual(
                (target_root / "logs" / "session.jsonl").read_text(
                    encoding="utf-8"
                ),
                "flight-log",
            )
            save_root.assert_called_once()
            self.assertEqual(
                save_root.call_args.kwargs["migration_conflict_policy"],
                DataMigrationConflictPolicy.RENAME.value,
            )
            self.assertEqual(
                self.window.data_migration_progress_dialog.progress_bar.value(),
                100,
            )
        finally:
            controller.shutdown()
            self.window.data_migration_progress_dialog.close()

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
        self.assertEqual(self.window.lbl_start_reason.text(), "不能 START：初对准未完成")

    def test_start_status_distinguishes_pending_snapshot_and_ack_busy(self) -> None:
        state = ready_state()
        self.window.bind_runtime_model(state, EventHistory(), select_preflight=True)

        state.pending_command_name = "START_MISSION"
        state.start_block_reason = int(AirAckResult.BUSY)
        self.window.render_state()
        self.assertTrue(self.window.btn_start.isEnabled())
        self.assertEqual(self.window.btn_start.text(), "等待 START 确认…")
        self.assertEqual(
            self.window.lbl_start_reason.text(),
            "START 请求已发送，等待飞控确认…",
        )
        self.assertEqual(self.window.lbl_pf_start_block.text(), "START 处理中")

        state.pending_command_name = ""
        state.last_start_failure_result = int(AirAckResult.BUSY)
        self.window.render_state()
        self.assertTrue(self.window.btn_start.isEnabled())
        self.assertEqual(self.window.lbl_start_reason.text(), "START 暂时忙，请稍后重试")
        self.assertEqual(self.window.lbl_pf_start_block.text(), "START ACK：飞控忙")

    def test_latest_command_feedback_is_visible_for_ping_result(self) -> None:
        state = ready_state()
        state.radio_message.key = "radio.ack_ok"
        state.radio_message.params = {"command": "PING"}
        self.window.bind_runtime_model(state, EventHistory(), select_preflight=True)
        self.assertIn("PING", self.window.lbl_command_result.text())
        self.assertIn("成功", self.window.lbl_command_result.text())

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        self.assertIn("PING", self.window.lbl_command_result.text())
        self.assertIn("OK", self.window.lbl_command_result.text())

    def test_start_ready_and_locked_text_are_natural_in_both_languages(self) -> None:
        state = ready_state()
        self.window.bind_runtime_model(state, EventHistory(), select_preflight=True)
        self.assertEqual(self.window.lbl_start_reason.text(), "可以 START")

        state.start_unlocked = False
        state.start_block_reason = int(AirAckResult.LOCKED_REQUIRED)
        self.window.render_state()
        self.assertEqual(self.window.lbl_start_reason.text(), "不能 START：需要解锁")

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        self.assertEqual(
            self.window.lbl_start_reason.text(),
            "Cannot START: Unlock required",
        )

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

    def test_completed_six_face_remains_recollectable_and_tracks_snapshots(self) -> None:
        state = ready_state()
        state.calibration.state = int(AirCalibrationState.WAIT_FACE)
        state.calibration.ready = False
        state.calibration.completed_face_mask = 1
        state.calibration.current_face = 0xFF
        selected_faces: list[int] = []
        self.window.calibration_dialog.on_face = selected_faces.append
        self.window.bind_runtime_model(state, EventHistory())
        dialog = self.window.calibration_dialog
        dialog.render(state)

        self.assertTrue(all(button.isEnabled() for button in dialog.face_buttons))
        self.assertEqual(dialog.face_status_labels[0].text(), "✓")
        self.assertEqual(dialog.face_buttons[0].text(), "重新采集 X+")
        self.assertIn("可重新采集", dialog.face_buttons[0].toolTip())

        dialog.face_buttons[0].click()
        self.assertEqual(selected_faces, [0])
        self.assertEqual(state.calibration.completed_face_mask, 1)

        state.calibration.state = int(AirCalibrationState.COLLECTING)
        state.calibration.current_face = 0
        state.calibration.completed_face_mask = 0
        dialog.render(state)
        self.assertFalse(any(button.isEnabled() for button in dialog.face_buttons))
        self.assertIn("正在采集 X+，请等待完成", dialog.lbl_issue.text())
        self.assertEqual(dialog.face_status_labels[0].text(), "●")

        state.calibration.state = int(AirCalibrationState.WAIT_FACE)
        state.calibration.current_face = 0xFF
        state.calibration.completed_face_mask = 1
        dialog.render(state)
        self.assertTrue(dialog.face_buttons[0].isEnabled())
        self.assertEqual(dialog.face_status_labels[0].text(), "✓")

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        self.assertEqual(dialog.face_buttons[0].text(), "Recollect X+")
        self.assertIn("click to recollect", dialog.face_buttons[0].toolTip())

    def test_ready_six_face_allows_single_face_recollection(self) -> None:
        state = ready_state()
        selected_faces: list[int] = []
        self.window.calibration_dialog.on_face = selected_faces.append
        self.window.bind_runtime_model(state, EventHistory())
        dialog = self.window.calibration_dialog
        dialog.render(state)

        self.assertEqual(state.calibration.state, int(AirCalibrationState.READY))
        self.assertEqual(state.calibration.completed_face_mask, 0x3F)
        self.assertTrue(all(button.isEnabled() for button in dialog.face_buttons))
        self.assertTrue(
            all(button.text().startswith("重新采集") for button in dialog.face_buttons)
        )

        dialog.face_buttons[4].click()
        self.assertEqual(selected_faces, [4])
        self.assertEqual(state.calibration.completed_face_mask, 0x3F)

    def test_ready_calibration_keeps_restart_entry_and_mode_selection(self) -> None:
        state = ready_state()
        selected_modes: list[int] = []
        self.window.calibration_dialog.on_start = selected_modes.append
        self.window.bind_runtime_model(state, EventHistory())
        dialog = self.window.calibration_dialog
        dialog.render(state)

        self.assertTrue(self.window.btn_calibration.isEnabled())
        self.assertEqual(self.window.btn_calibration.text(), "重新校准")
        self.assertTrue(dialog.btn_start.isEnabled())
        self.assertEqual(dialog.btn_start.text(), "重新开始所选校准")

        index = dialog.mode_combo.findData(int(AirCalibrationMode.ONE_FACE))
        dialog.mode_combo.setCurrentIndex(index)
        dialog.btn_start.click()
        self.assertEqual(selected_modes, [int(AirCalibrationMode.ONE_FACE)])

        state.pending_command_name = "CAL_FACE"
        dialog.render(state)
        self.assertTrue(dialog.btn_start.isEnabled())
        self.assertFalse(any(button.isEnabled() for button in dialog.face_buttons))

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        self.assertEqual(self.window.btn_calibration.text(), "Restart Calibration")
        self.assertEqual(dialog.btn_start.text(), "Restart Selected Calibration")

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

    def test_sensor_details_are_cached_sorted_and_send_no_air_command(self) -> None:
        state = ready_state()
        arrivals = (
            (0, int(AirSensorId.MAGNETOMETER), 0),
            (1, 0x35, 2),
            (2, int(AirSensorId.GNSS), 0),
            (3, int(AirSensorId.IMU), 1),
        )
        for index, sensor_id, instance_id in arrivals:
            state.alignment_sensor_snapshots.receive(
                AirSensorStatusMessage(
                    seq=index,
                    snapshot_id=6,
                    sensor_id=sensor_id,
                    instance_id=instance_id,
                    status_flags=0xFF,
                    detail_code=0 if sensor_id != 0x35 else 0x7E,
                    index=index,
                    total=len(arrivals),
                )
            )
        state.alignment_sensor_snapshots.terminate(6, int(AirAlignmentState.READY))
        callbacks: list[str] = []
        self.window.on_align_start = lambda: callbacks.append("ALIGN_START")
        self.window.on_align_stop = lambda: callbacks.append("ALIGN_STOP")
        self.window.on_align_reset = lambda: callbacks.append("ALIGN_RESET")
        self.window.on_send_ping = lambda: callbacks.append("PING")
        self.window.bind_runtime_model(state, EventHistory())

        self.window.btn_sensor_details.click()
        self.application.processEvents()

        self.assertEqual(callbacks, [])
        self.assertTrue(self.window.sensor_details_dialog.isVisible())
        details = self.window.sensor_details_dialog.details_text.toPlainText()
        self.assertLess(details.index("IMU #1"), details.index("GNSS #0"))
        self.assertLess(details.index("GNSS #0"), details.index("磁力计 #0"))
        self.assertLess(details.index("磁力计 #0"), details.index("未知传感器 0x35 #2"))
        self.assertIn("未知详情 0x7E", details)
        self.assertIn("#6 · 完整 · 4 个传感器", self.window.lbl_align_snapshot.text())

    def test_stale_sensor_details_identify_last_alignment_snapshot(self) -> None:
        state = ready_state()
        state.alignment_sensor_snapshots.receive(
            AirSensorStatusMessage(1, 3, 1, 0, 0xFF, 0, 0, 1)
        )
        state.alignment_sensor_snapshots.terminate(3, int(AirAlignmentState.READY))
        state.alignment.state = int(AirAlignmentState.STALE)
        state.alignment.ready = False
        self.window.bind_runtime_model(state, EventHistory())
        self.window.sensor_details_dialog.render(state)

        details = self.window.sensor_details_dialog.details_text.toPlainText()
        self.assertIn("上一次 Alignment 终止快照", details)
        self.assertEqual(
            state.alignment_sensor_snapshots.latest_terminal_snapshot.snapshot_id,
            3,
        )

    def test_link_details_preserves_scroll_selection_and_bottom_follow(self) -> None:
        state = ready_state()
        state.receive_health.warning = "\n".join(
            f"diagnostic line {index}" for index in range(160)
        )
        dialog = self.window.link_details_dialog
        dialog.resize(620, 300)
        dialog.show()
        dialog.render(state)
        self.application.processEvents()
        scrollbar = dialog.details_text.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)

        middle = scrollbar.maximum() // 2
        scrollbar.setValue(middle)
        cursor = dialog.details_text.textCursor()
        cursor.setPosition(10)
        cursor.setPosition(25, QTextCursor.KeepAnchor)
        dialog.details_text.setTextCursor(cursor)
        scrollbar.setValue(middle)
        selected = dialog.details_text.textCursor().selectedText()
        revision = dialog.details_text.document().revision()

        dialog.render(state)
        self.assertEqual(dialog.details_text.document().revision(), revision)
        self.assertEqual(dialog.details_text.textCursor().selectedText(), selected)
        self.assertEqual(scrollbar.value(), middle)

        state.receive_health.serial_rx_bytes += 1
        dialog.render(state)
        self.application.processEvents()
        self.assertEqual(scrollbar.value(), middle)
        self.assertEqual(dialog.details_text.textCursor().selectedText(), selected)

        scrollbar.setValue(scrollbar.maximum())
        state.receive_health.serial_rx_bytes += 1
        dialog.render(state)
        self.application.processEvents()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

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
            sensor_summary_flags=0x01,
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
        self.assertIsNone(self.window.mesh_item.opts["shader"])
        self.assertFalse(self.window.mesh_item.opts["computeNormals"])
        self.assertGreater(
            float(self.window.base_colors[:4, :3].mean(axis=1).min()),
            0.36,
        )
        self.assertFalse(hasattr(self.window, "accel_fs_combo"))
        self.assertFalse(hasattr(self.window, "gyro_fs_combo"))

    def test_theme_switch_updates_application_plots_and_3d_background(self) -> None:
        self.assertIs(self.window.theme, Theme.LIGHT)
        light_3d = self.window.gl_view.opts["bgcolor"]
        light_plot = next(iter(self.window.plot_widgets.values())).backgroundBrush().color().name()
        light_faces = self.window.base_colors.tolist()

        dark_index = self.window.theme_combo.findData(Theme.DARK.value)
        self.window.theme_combo.setCurrentIndex(dark_index)
        self.application.processEvents()

        self.assertIs(self.window.theme, Theme.DARK)
        self.assertEqual(self.window.preferences.theme(), Theme.DARK)
        dark_3d = self.window.gl_view.opts["bgcolor"]
        dark_plot = next(iter(self.window.plot_widgets.values())).backgroundBrush().color().name()
        self.assertNotEqual(self.window.base_colors.tolist(), light_faces)
        self.assertNotEqual(dark_3d, light_3d)
        self.assertNotEqual(dark_plot, light_plot)
        self.assertEqual(
            dark_3d,
            pg.glColor(QColor(self.window._theme_colors.base)),
        )
        self.assertEqual(dark_plot, self.window._theme_colors.base)

    def test_export_options_default_all_checked_and_language_is_independent(self) -> None:
        dialog = self.window.export_options_dialog
        dialog.reload_preferences()
        self.assertEqual(
            dialog.language_combo.currentData(),
            ExportLanguage.FOLLOW_UI.value,
        )
        self.assertTrue(
            all(checkbox.isChecked() for checkbox in dialog.item_checkboxes.values())
        )

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )
        dialog.language_combo.setCurrentIndex(
            dialog.language_combo.findData(ExportLanguage.ZH_CN.value)
        )
        dialog.item_checkboxes[ExportItem.ATTITUDE_3D].setChecked(False)
        options = dialog.resolved_options(self.window.i18n.language, self.window.theme)

        self.assertIs(options.language, Language.ZH_CN)
        self.assertEqual(options.language_suffix, "ZH")
        self.assertNotIn(ExportItem.ATTITUDE_3D, options.items)
        self.assertIn(ExportItem.CHARTS, options.items)


if __name__ == "__main__":
    unittest.main()
