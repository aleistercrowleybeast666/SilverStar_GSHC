from __future__ import annotations

import unittest

from PySide6.QtCore import QObject

from app import Controller, PendingAirCommand
from protocol.air import AirAckMessage, AirQuatStateMessage, AirStatusMessage
from protocol.common import AirCmdId, AirStatusId


class FakeWindow:
    def __init__(self) -> None:
        self.mission_calls = 0
        self.last_air_ack = ""
        self.radio_hint = ""
        self.last_status = ""
        self.quat_updates: list[dict] = []
        self.vector_updates: list[tuple[str, tuple[float, float, float]]] = []

    def set_command_state_mission(self) -> None:
        self.mission_calls += 1

    def set_last_air_ack(self, text: str) -> None:
        self.last_air_ack = text

    def set_radio_state_hint(self, text: str) -> None:
        self.radio_hint = text

    def set_last_status(self, text: str) -> None:
        self.last_status = text

    def set_command_state_locked(self) -> None:
        pass

    def set_command_state_unlocked(self) -> None:
        pass

    def update_quat(self, values, *, raw=None, valid=True, source=None) -> None:
        self.quat_updates.append(
            {
                "values": values,
                "raw": raw,
                "valid": valid,
                "source": source,
            }
        )

    def push_vector_sample(self, key: str, values: tuple[float, float, float]) -> None:
        self.vector_updates.append((key, values))


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def write(self, record: dict) -> None:
        self.records.append(record)


def make_controller() -> Controller:
    controller = Controller.__new__(Controller)
    QObject.__init__(controller)
    controller.window = FakeWindow()
    controller.logger = FakeLogger()
    controller.pending_air_cmds = {}
    controller._last_quat_invalid_hint_monotonic = 0.0
    return controller


def add_pending_start(controller: Controller, seq: int = 9) -> None:
    pending = PendingAirCommand(
        seq=seq,
        cmd_id=int(AirCmdId.START_MISSION),
        token=0,
        param0=0,
        param1=0,
        air_frame=b"",
        gsp_frame=b"",
        sent_count=1,
        max_retries=3,
        last_send_monotonic=0.0,
    )
    controller.pending_air_cmds[(pending.seq, pending.cmd_id)] = pending


class StartAckStateTests(unittest.TestCase):
    def test_quat_state_updates_only_quaternion_display(self) -> None:
        controller = make_controller()
        msg = AirQuatStateMessage(
            seq=1,
            time_ms=100,
            quat_q15=(32767, 0, 0, 0),
            quat=(1.0, 0.0, 0.0, 0.0),
            quat_raw_zero=False,
            quat_valid=True,
        )

        controller._handle_air_message(msg)

        self.assertEqual(len(controller.window.quat_updates), 1)
        self.assertEqual(controller.window.quat_updates[0]["source"], "short")
        self.assertEqual(controller.window.vector_updates, [])
        self.assertEqual(controller.logger.records[0]["kind"], "QUAT_STATE")
        self.assertEqual(controller.logger.records[0]["quat_q15"], [32767, 0, 0, 0])
        self.assertTrue(controller.logger.records[0]["quat_valid"])

    def test_status_does_not_enter_mission_before_start_ack(self) -> None:
        controller = make_controller()
        add_pending_start(controller)

        controller._handle_air_message(
            AirStatusMessage(
                seq=1,
                status_id=int(AirStatusId.MISSION_START),
                time_ms=100,
                arg0=0,
                arg1=0,
            )
        )

        self.assertEqual(controller.window.mission_calls, 0)
        self.assertTrue(controller.pending_air_cmds)

    def test_matching_start_ack_ok_enters_mission(self) -> None:
        controller = make_controller()
        add_pending_start(controller, seq=9)

        controller._handle_air_message(
            AirAckMessage(
                seq=2,
                ack_seq=9,
                ack_cmd_id=int(AirCmdId.START_MISSION),
                result=0,
                time_ms=120,
            )
        )

        self.assertEqual(controller.window.mission_calls, 1)
        self.assertFalse(controller.pending_air_cmds)

    def test_start_ack_error_does_not_enter_mission(self) -> None:
        controller = make_controller()
        add_pending_start(controller, seq=9)

        controller._handle_air_message(
            AirAckMessage(
                seq=2,
                ack_seq=9,
                ack_cmd_id=int(AirCmdId.START_MISSION),
                result=5,
                time_ms=120,
            )
        )

        self.assertEqual(controller.window.mission_calls, 0)
        self.assertFalse(controller.pending_air_cmds)


if __name__ == "__main__":
    unittest.main()
