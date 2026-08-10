from __future__ import annotations

import os
import struct
import unittest
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from protocol.common import AirStatusId, GspType
from protocol.gsp_min import build_gsp_frame
from protocol.receive_pipeline import ReceivePipeline, protocol_event_log_records
from services.i18n import I18n, Language, translation_keys_match
from services.state_model import EventHistory, FlightControllerState, FlightEvent, HandshakeState
from ui.main_window import MainWindow


def capability_gsp() -> bytes:
    air = struct.pack("<BBBBBBBH", 0x12, 7, 0, 1, 7, 7, 16, 2000)
    payload = bytes([(-70) & 0xFF, 20, len(air)]) + air
    return build_gsp_frame(int(GspType.AIR_RX), payload)


class RuntimeI18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.settings_path = os.path.join(self.temporary_directory.name, "settings.ini")
        self.settings = QSettings(self.settings_path, QSettings.IniFormat)
        self.i18n = I18n(self.settings)
        self.window = MainWindow(self.i18n)

    def tearDown(self) -> None:
        self.window.calibration_dialog.close()
        self.window.close()
        self.temporary_directory.cleanup()

    def test_translation_tables_have_identical_keys(self) -> None:
        self.assertTrue(translation_keys_match())

    def test_locked_required_means_unlock_required_in_both_languages(self) -> None:
        self.assertEqual(
            self.i18n.enum("ack_result", "LOCKED_REQUIRED"), "需要解锁"
        )
        self.i18n.set_language(Language.EN_US)
        self.assertEqual(
            self.i18n.enum("ack_result", "LOCKED_REQUIRED"), "Unlock Required"
        )

    def test_live_switch_retranslates_current_state_and_event_history(self) -> None:
        state = FlightControllerState(session_generation=3, connected=True)
        state.handshake.handshake_state = HandshakeState.ACKED
        events = EventHistory()
        events.append(
            FlightEvent(
                seq=1,
                status_id=int(AirStatusId.GNSS_POSITION),
                name="GNSS_POSITION",
                time_ms=123,
                arg0=1,
                arg1=0,
                host_rx_monotonic_ns=1,
            )
        )
        events.append(
            FlightEvent(
                seq=2,
                status_id=int(AirStatusId.CALIBRATION_DIAGNOSTIC),
                name="CALIBRATION_DIAGNOSTIC",
                time_ms=124,
                arg0=0,
                arg1=4,
                host_rx_monotonic_ns=2,
            )
        )
        self.window.bind_runtime_model(state, events, select_preflight=True)
        state_identity = id(self.window._state)
        gl_identity = id(self.window.gl_view)

        self.assertEqual(self.window.pages.tabText(0), "预飞行")
        self.assertEqual(self.window.lbl_pf_air_link.text(), "已连接")
        self.assertIn("定位可用", self.window.event_list.item(0).text())
        self.assertIn("当前重力方向", self.window.event_list.item(1).text())

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.EN_US.value)
        )

        self.assertEqual(self.window.pages.tabText(0), "Preflight")
        self.assertEqual(self.window.btn_connect.text(), "Connect")
        self.assertEqual(self.window.lbl_pf_air_link.text(), "Connected")
        self.assertIn("Position Usable", self.window.event_list.item(0).text())
        self.assertIn("Current gravity direction", self.window.event_list.item(1).text())
        self.assertEqual(id(self.window._state), state_identity)
        self.assertEqual(id(self.window.gl_view), gl_identity)
        self.assertIs(state.handshake.handshake_state, HandshakeState.ACKED)
        self.assertEqual(set(self.window.world_direction_labels), {"E", "W", "N", "S", "U"})
        self.assertEqual(self.window.body_nose_label.text, "NOSE")

        self.window.language_combo.setCurrentIndex(
            self.window.language_combo.findData(Language.ZH_CN.value)
        )
        self.assertEqual(self.window.pages.tabText(0), "预飞行")
        self.assertIn("定位可用", self.window.event_list.item(0).text())

    def test_language_persists_in_qsettings(self) -> None:
        self.i18n.set_language(Language.EN_US)
        restored = I18n(QSettings(self.settings_path, QSettings.IniFormat))
        self.assertIs(restored.language, Language.EN_US)

    def test_language_switch_does_not_change_protocol_json_fields(self) -> None:
        event = ReceivePipeline().feed(capability_gsp(), host_rx_monotonic_ns=123)[0]
        before = protocol_event_log_records(event)
        self.i18n.set_language(Language.EN_US)
        after = protocol_event_log_records(event)

        def stable(records: list[dict]) -> list[dict]:
            return [{key: value for key, value in item.items() if key != "ts"} for item in records]

        self.assertEqual(stable(before), stable(after))
        capability_record = next(item for item in after if item.get("kind") == "CAPABILITY")
        self.assertEqual(capability_record["kind"], "CAPABILITY")
        self.assertEqual(capability_record["command_policy_name"], "PREFLIGHT_ONLY")


if __name__ == "__main__":
    unittest.main()
