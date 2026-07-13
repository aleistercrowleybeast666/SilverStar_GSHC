from __future__ import annotations

import unittest

from PySide6.QtCore import QObject

from app import Controller, PendingAirCommand
from protocol.air import AirAckMessage, AirFlightStateMessage, AirQuatStateMessage, AirStatusMessage
from protocol.common import AirCmdId, AirStatusId


class FakeWindow:
    def __init__(self) -> None:
        self.mission_calls = 0
        self.last_air_ack = ""
        self.radio_hint = ""
        self.last_status = ""
        self.connection_status = ""
        self.quat_updates: list[dict] = []
        self.vector_updates: list[tuple[str, tuple[float, float, float], float]] = []

    def set_command_state_mission(self) -> None:
        self.mission_calls += 1

    def set_last_air_ack(self, text: str) -> None:
        self.last_air_ack = text

    def set_radio_state_hint(self, text: str) -> None:
        self.radio_hint = text

    def set_last_status(self, text: str) -> None:
        self.last_status = text

    def set_connection_status(self, text: str) -> None:
        self.connection_status = text

    def set_command_state_locked(self) -> None:
        pass

    def set_command_state_unlocked(self) -> None:
        pass

    def current_accel_full_scale_g(self) -> float:
        return 16.0

    def current_gyro_full_scale_dps(self) -> float:
        return 2000.0

    def update_quat(self, values, *, raw=None, valid=True, source=None) -> None:
        self.quat_updates.append(
            {
                "values": values,
                "raw": raw,
                "valid": valid,
                "source": source,
            }
        )

    def push_vector_sample(self, key: str, values: tuple[float, float, float], time_s: float) -> None:
        self.vector_updates.append((key, values, time_s))


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
    controller.mission_packet_tracking_active = False
    controller.last_flight_time_ms = None
    controller.received_flight_packets = 0
    controller.estimated_lost_packets = 0
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
        self.assertTrue(controller.mission_packet_tracking_active)

        controller._handle_air_message(
            AirStatusMessage(
                seq=2,
                status_id=int(AirStatusId.MISSION_START),
                time_ms=100,
                arg0=0,
                arg1=0,
            )
        )
        tracking_start_records = [
            record for record in controller.logger.records
            if record.get("kind") == "MISSION_PACKET_TRACKING_START"
        ]
        self.assertEqual(len(tracking_start_records), 1)

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
        self.assertTrue(controller.mission_packet_tracking_active)
        self.assertEqual(controller.logger.records[0]["kind"], "MISSION_PACKET_TRACKING_START")

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

    def test_flight_state_uses_mission_time_and_logs_packet_loss(self) -> None:
        controller = make_controller()
        controller._reset_mission_packet_stats("test")
        msg_args = {
            "accel_raw": (0, 0, 0),
            "gyro_raw": (0, 0, 0),
            "quat_q15": (32767, 0, 0, 0),
            "quat": (1.0, 0.0, 0.0, 0.0),
            "quat_raw_zero": False,
            "quat_valid": True,
            "vel_mps": (1.0, 2.0, 3.0),
            "pos_m": (4.0, 5.0, 6.0),
        }

        controller._handle_air_message(AirFlightStateMessage(seq=200, time_ms=0, **msg_args))
        controller._handle_air_message(AirFlightStateMessage(seq=201, time_ms=400, **msg_args))

        self.assertEqual(controller.window.vector_updates[-2], ("vel", (1.0, 2.0, 3.0), 0.4))
        self.assertEqual(controller.window.vector_updates[-1], ("pos", (4.0, 5.0, 6.0), 0.4))
        flight_record = controller.logger.records[-1]
        self.assertEqual(flight_record["seq"], 201)
        self.assertEqual(flight_record["time_ms"], 400)
        self.assertEqual(flight_record["lost_since_previous"], 1)
        self.assertEqual(flight_record["estimated_lost_packets"], 1)
        self.assertEqual(flight_record["expected_flight_packets"], 3)

    def test_non_monotonic_time_does_not_move_tracking_baseline_back(self) -> None:
        controller = make_controller()
        controller._reset_mission_packet_stats("test")

        first = controller._track_flight_packet(400)
        out_of_order = controller._track_flight_packet(200)
        next_packet = controller._track_flight_packet(600)

        self.assertFalse(first["time_non_monotonic"])
        self.assertTrue(out_of_order["time_non_monotonic"])
        self.assertEqual(next_packet["lost_since_previous"], 0)
        self.assertEqual(controller.estimated_lost_packets, 0)

    def test_connection_drop_clears_packet_tracking(self) -> None:
        controller = make_controller()
        controller._reset_mission_packet_stats("test")
        controller._track_flight_packet(0)

        controller.on_connection_changed(False, "lost")

        self.assertFalse(controller.mission_packet_tracking_active)
        self.assertIsNone(controller.last_flight_time_ms)
        self.assertEqual(controller.received_flight_packets, 0)
        self.assertEqual(controller.estimated_lost_packets, 0)


if __name__ == "__main__":
    unittest.main()
