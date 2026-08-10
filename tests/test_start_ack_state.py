from __future__ import annotations

import unittest

from PySide6.QtCore import QObject

from app import Controller, PendingAirCommand, format_air_status_message
from protocol.air import (
    AirAckMessage,
    AirCapabilityMessage,
    AirFlightStateMessage,
    AirPreflightStatusMessage,
    AirStatusMessage,
)
from protocol.common import (
    AirAckResult,
    AirAlignmentState,
    AirCalibrationDiagnosticReason,
    AirCalibrationMode,
    AirCalibrationState,
    AirCmdId,
    AirLifecycleState,
    AirStatusId,
    GspAckResult,
    GspType,
)
from protocol.gsp_min import GspAck
from services.state_model import (
    EventHistory,
    FlightControllerState,
    HandshakeState,
    MissionPhase,
)


class FakeLogger:
    session_active = True
    queue_depth = 0
    queue_max_records = 100
    last_error = ""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def write(self, record: dict) -> None:
        self.records.append(dict(record))


class FakeWorker:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send_bytes(self, payload: bytes) -> None:
        self.sent.append(bytes(payload))


def capability(seq: int = 1, profile: int = 0) -> AirCapabilityMessage:
    return AirCapabilityMessage(
        seq=seq,
        air_profile_id=profile,
        command_policy=1,
        calibration_mode_mask=0x07,
        alignment_capability_mask=0x07,
        accel_full_scale_g=16,
        gyro_full_scale_dps=2000,
    )


def preflight_status(
    *,
    capability_acked: bool,
    completed_face_mask: int = 0,
    calibration_ready: bool = False,
    alignment_ready: bool = False,
    alignment_state: int = int(AirAlignmentState.COLLECTING),
    attitude_ready: bool = True,
    baro_ready: bool = False,
    start_unlocked: bool = False,
    system_ready: bool = False,
    start_block_reason: int = int(AirAckResult.ALIGNMENT_REQUIRED),
    current_face: int = 0xFF,
) -> AirPreflightStatusMessage:
    return AirPreflightStatusMessage(
        seq=7,
        lifecycle_state=int(AirLifecycleState.PREFLIGHT),
        calibration_state=(
            int(AirCalibrationState.READY)
            if calibration_ready
            else int(AirCalibrationState.COLLECTING)
        ),
        calibration_mode=int(AirCalibrationMode.SIX_FACE),
        completed_face_mask=completed_face_mask,
        current_face=current_face,
        alignment_state=alignment_state,
        attitude_ready=attitude_ready,
        gnss_origin_ready=False,
        baro_origin_ready=baro_ready,
        system_ready=system_ready,
        start_unlocked=start_unlocked,
        selftest_passed=True,
        gnss_position_usable=True,
        capability_acked=capability_acked,
        calibration_ready=calibration_ready,
        alignment_ready=alignment_ready,
        start_block_reason=start_block_reason,
    )


def make_controller() -> Controller:
    controller = Controller.__new__(Controller)
    QObject.__init__(controller)
    controller.state = FlightControllerState(
        session_generation=1,
        connected=True,
        connection_text="test",
    )
    controller.events = EventHistory()
    controller.logger = FakeLogger()
    controller.worker = FakeWorker()
    controller.air_seq = 0
    controller.pending_air_cmds = {}
    controller.pending_capability_ack = None
    controller.mission_packet_tracking_active = False
    controller.last_flight_time_ms = None
    controller.received_flight_packets = 0
    controller.estimated_lost_packets = 0
    return controller


def add_pending(controller: Controller, cmd_id: int, seq: int = 9) -> None:
    pending = PendingAirCommand(
        seq=seq,
        cmd_id=cmd_id,
        token=0,
        param0=0,
        param1=0,
        air_frame=b"",
        gsp_frame=b"",
        sent_count=1,
        max_retries=3,
        last_send_monotonic=0.0,
    )
    controller.pending_air_cmds[(seq, cmd_id)] = pending
    controller.state.pending_command_name = cmd_id


def flight_state(time_ms: int = 1000) -> AirFlightStateMessage:
    return AirFlightStateMessage(
        seq=20,
        time_ms=time_ms,
        accel_raw=(0, 0, 2048),
        gyro_raw=(0, 0, 0),
        quat_q15=(32767, 0, 0, 0),
        quat=(1.0, 0.0, 0.0, 0.0),
        quat_raw_zero=False,
        quat_valid=True,
        vel_mps=(1.0, 2.0, 3.0),
        pos_m=(4.0, 5.0, 6.0),
    )


class CapabilityHandshakeTests(unittest.TestCase):
    def test_latest_capability_sequence_replaces_pending_ack(self) -> None:
        controller = make_controller()
        controller._handle_capability(capability(seq=10))
        first_command_seq = controller.pending_capability_ack.command_seq

        controller._handle_capability(capability(seq=11))

        self.assertEqual(controller.pending_capability_ack.capability_seq, 11)
        self.assertNotEqual(controller.pending_capability_ack.command_seq, first_command_seq)
        self.assertEqual(len(controller.worker.sent), 2)
        self.assertEqual(controller.state.handshake.last_capability_seq, 11)
        self.assertEqual(controller.state.handshake.accepted_capability_seq, 11)
        self.assertEqual(controller.state.handshake.capability_ack_attempts, 1)
        tx_records = [
            item for item in controller.logger.records if item.get("kind") == "CAPABILITY_ACK_TX"
        ]
        self.assertEqual(tx_records[-1]["capability_seq"], 11)
        self.assertEqual(tx_records[-1]["cmd_seq"], controller.pending_capability_ack.command_seq)

    def test_matching_ack_completes_handshake(self) -> None:
        controller = make_controller()
        controller._handle_capability(capability(seq=10))
        command_seq = controller.pending_capability_ack.command_seq

        controller._handle_ack_message(
            AirAckMessage(
                seq=2,
                ack_seq=command_seq,
                ack_cmd_id=int(AirCmdId.CAPABILITY_ACK),
                result=int(AirAckResult.OK),
                time_ms=100,
            )
        )

        self.assertTrue(controller.state.capability_acked)
        self.assertIsNone(controller.pending_capability_ack)
        self.assertIs(controller.state.handshake.handshake_state, HandshakeState.ACKED)
        self.assertTrue(controller.state.handshake.capability_acked_by_air_ack)

    def test_wrong_ack_sequence_never_completes_handshake(self) -> None:
        controller = make_controller()
        controller._handle_capability(capability(seq=10))
        expected = controller.pending_capability_ack.command_seq

        controller._handle_ack_message(
            AirAckMessage(
                seq=2,
                ack_seq=(expected + 1) & 0xFF,
                ack_cmd_id=int(AirCmdId.CAPABILITY_ACK),
                result=int(AirAckResult.OK),
                time_ms=100,
            )
        )

        self.assertFalse(controller.state.capability_acked)
        self.assertIsNotNone(controller.pending_capability_ack)
        self.assertIs(controller.state.handshake.handshake_state, HandshakeState.HANDSHAKING)
        self.assertEqual(
            controller.state.handshake.last_handshake_error,
            "AIR_ACK_SEQUENCE_MISMATCH",
        )

    def test_late_capability_after_ack_is_diagnostic_only(self) -> None:
        controller = make_controller()
        controller._handle_capability(capability(seq=10))
        command_seq = controller.pending_capability_ack.command_seq
        controller._handle_ack_message(
            AirAckMessage(
                seq=2,
                ack_seq=command_seq,
                ack_cmd_id=int(AirCmdId.CAPABILITY_ACK),
                result=int(AirAckResult.OK),
                time_ms=100,
            )
        )
        accepted = controller.state.capability
        generation = controller.state.session_generation
        sent_count = len(controller.worker.sent)

        controller._handle_capability(capability(seq=9, profile=9))

        self.assertIs(controller.state.capability, accepted)
        self.assertEqual(controller.state.session_generation, generation)
        self.assertTrue(controller.state.capability_acked)
        self.assertIsNone(controller.pending_capability_ack)
        self.assertEqual(len(controller.worker.sent), sent_count)
        self.assertEqual(controller.state.handshake.duplicate_capability_after_ack, 1)
        self.assertTrue(
            any(
                item.get("kind") == "STALE_OR_DUPLICATE_CAPABILITY_AFTER_ACK"
                for item in controller.logger.records
            )
        )

    def test_gsp_air_tx_ack_is_counted_but_not_used_as_air_handshake_ack(self) -> None:
        controller = make_controller()
        controller._handle_capability(capability(seq=10))

        controller._handle_gsp_ack(
            GspAck(
                ack_gsp_type=int(GspType.AIR_TX),
                result=int(GspAckResult.OK),
                detail=0,
            )
        )

        self.assertEqual(controller.state.handshake.gsp_air_tx_ack_ok, 1)
        self.assertFalse(controller.state.capability_acked)
        self.assertIsNotNone(controller.pending_capability_ack)

    def test_snapshot_recovers_lost_capability_ack(self) -> None:
        controller = make_controller()
        controller._handle_capability(capability(seq=10))

        controller._handle_preflight_status(preflight_status(capability_acked=True))

        self.assertTrue(controller.state.capability_acked)
        self.assertIsNone(controller.pending_capability_ack)
        self.assertTrue(controller.state.handshake.capability_acked_by_preflight_status)

    def test_unsupported_profile_is_never_acknowledged(self) -> None:
        controller = make_controller()
        controller._handle_capability(capability(seq=1, profile=9))

        self.assertFalse(controller.state.profile_supported)
        self.assertFalse(controller.state.capability_acked)
        self.assertIsNone(controller.pending_capability_ack)
        self.assertEqual(controller.worker.sent, [])
        self.assertEqual(controller.state.capability_error, "UNSUPPORTED_PROFILE")
        self.assertIs(controller.state.handshake.handshake_state, HandshakeState.ERROR)


class PreflightStateTests(unittest.TestCase):
    def test_calibration_diagnostic_updates_and_none_clears_without_failing(self) -> None:
        controller = make_controller()
        controller.state.calibration.state = int(AirCalibrationState.COLLECTING)
        controller.state.calibration.mode = int(AirCalibrationMode.SIX_FACE)
        controller._handle_status_message(
            AirStatusMessage(
                seq=1,
                status_id=int(AirStatusId.CALIBRATION_DIAGNOSTIC),
                time_ms=100,
                arg0=2,
                arg1=int(AirCalibrationDiagnosticReason.GRAVITY_DIRECTION),
            ),
            None,
        )

        self.assertEqual(
            controller.state.latest_calibration_diagnostic_reason,
            int(AirCalibrationDiagnosticReason.GRAVITY_DIRECTION),
        )
        self.assertEqual(controller.state.latest_calibration_diagnostic_face, 2)
        self.assertEqual(controller.state.calibration.state, int(AirCalibrationState.COLLECTING))

        controller._handle_status_message(
            AirStatusMessage(
                seq=2,
                status_id=int(AirStatusId.CALIBRATION_DIAGNOSTIC),
                time_ms=120,
                arg0=0xFF,
                arg1=int(AirCalibrationDiagnosticReason.NONE),
            ),
            None,
        )
        self.assertEqual(
            controller.state.latest_calibration_diagnostic_reason,
            int(AirCalibrationDiagnosticReason.NONE),
        )
        self.assertEqual(controller.state.latest_calibration_diagnostic_face, 0xFF)
        self.assertEqual(controller.state.calibration.state, int(AirCalibrationState.COLLECTING))

    def test_new_six_face_snapshot_clears_old_diagnostic(self) -> None:
        controller = make_controller()
        controller.state.calibration.mode = int(AirCalibrationMode.SIX_FACE)
        controller.state.calibration.current_face = 2
        controller.state.latest_calibration_diagnostic_reason = int(
            AirCalibrationDiagnosticReason.VARIANCE
        )
        controller.state.latest_calibration_diagnostic_face = 2

        controller._handle_preflight_status(
            preflight_status(capability_acked=True, current_face=3)
        )

        self.assertEqual(
            controller.state.latest_calibration_diagnostic_reason,
            int(AirCalibrationDiagnosticReason.NONE),
        )

    def test_calibration_ack_ok_does_not_mark_face_passed(self) -> None:
        controller = make_controller()
        controller.state.capability = capability()
        controller.state.capability_acked = True
        controller.state.latest_calibration_diagnostic_reason = int(
            AirCalibrationDiagnosticReason.VARIANCE
        )
        add_pending(controller, int(AirCmdId.CAL_FACE), seq=9)

        controller._handle_ack_message(
            AirAckMessage(
                seq=1,
                ack_seq=9,
                ack_cmd_id=int(AirCmdId.CAL_FACE),
                result=int(AirAckResult.OK),
                time_ms=10,
            )
        )

        self.assertEqual(controller.state.calibration.completed_face_mask, 0)
        self.assertEqual(
            controller.state.latest_calibration_diagnostic_reason,
            int(AirCalibrationDiagnosticReason.NONE),
        )
        self.assertEqual(controller.state.radio_message.key, "radio.calibration_accepted")

    def test_face_event_and_snapshot_both_authoritatively_complete_faces(self) -> None:
        controller = make_controller()
        controller._handle_status_message(
            AirStatusMessage(
                seq=1,
                status_id=int(AirStatusId.CALIBRATION_FACE),
                time_ms=100,
                arg0=2,
                arg1=1,
            ),
            None,
        )
        self.assertEqual(controller.state.calibration.completed_face_mask, 1 << 2)

        controller._handle_preflight_status(
            preflight_status(capability_acked=True, completed_face_mask=0x3F)
        )
        self.assertEqual(controller.state.calibration.completed_face_mask, 0x3F)

    def test_alignment_ack_does_not_complete_but_snapshot_does(self) -> None:
        controller = make_controller()
        controller.state.capability = capability()
        controller.state.capability_acked = True
        add_pending(controller, int(AirCmdId.ALIGN_START), seq=4)
        controller._handle_ack_message(
            AirAckMessage(
                seq=1,
                ack_seq=4,
                ack_cmd_id=int(AirCmdId.ALIGN_START),
                result=0,
                time_ms=10,
            )
        )
        self.assertFalse(controller.state.alignment.ready)

        controller._handle_preflight_status(
            preflight_status(
                capability_acked=True,
                calibration_ready=True,
                alignment_ready=False,
                attitude_ready=True,
                baro_ready=False,
            )
        )
        self.assertFalse(controller.state.alignment.ready)
        controller._handle_preflight_status(
            preflight_status(
                capability_acked=True,
                calibration_ready=True,
                alignment_ready=True,
                alignment_state=int(AirAlignmentState.READY),
                baro_ready=True,
                system_ready=True,
                start_unlocked=True,
                start_block_reason=0,
            )
        )
        self.assertTrue(controller.state.alignment.ready)

    def test_alignment_stale_requires_a_later_ready_snapshot(self) -> None:
        controller = make_controller()
        controller.state.alignment.state = int(AirAlignmentState.READY)
        controller.state.alignment.ready = True
        controller._handle_status_message(
            AirStatusMessage(
                seq=1,
                status_id=int(AirStatusId.ALIGNMENT),
                time_ms=200,
                arg0=int(AirAlignmentState.STALE),
                arg1=0x07,
            ),
            None,
        )
        self.assertEqual(controller.state.alignment.state, int(AirAlignmentState.STALE))
        self.assertFalse(controller.state.alignment.ready)

        controller._handle_status_message(
            AirStatusMessage(
                seq=2,
                status_id=int(AirStatusId.ALIGNMENT),
                time_ms=220,
                arg0=int(AirAlignmentState.READY),
                arg1=0x07,
            ),
            None,
        )
        self.assertFalse(controller.state.alignment.ready)

        controller._handle_preflight_status(
            preflight_status(
                capability_acked=True,
                calibration_ready=True,
                alignment_ready=True,
                alignment_state=int(AirAlignmentState.READY),
            )
        )
        self.assertTrue(controller.state.alignment.ready)


class MissionRecoveryTests(unittest.TestCase):
    def test_first_flight_state_recovers_lost_start_ack(self) -> None:
        controller = make_controller()
        controller.state.capability = capability()
        controller.state.capability_acked = True
        add_pending(controller, int(AirCmdId.START_MISSION), seq=9)

        controller._handle_flight_state(flight_state(1000), None)

        self.assertTrue(controller.state.mission_started)
        self.assertFalse(controller.pending_air_cmds)
        self.assertEqual(controller.state.mission_start_source, "first_flight_state")
        self.assertEqual(list(controller.state.live_plot.time_s), [0.0])
        self.assertIs(
            controller.state.mission_presentation.phase,
            MissionPhase.MISSION_ACTIVE,
        )
        self.assertEqual(controller.state.mission_presentation.last_critical_event_name, "")

    def test_start_ack_ok_enters_mission(self) -> None:
        controller = make_controller()
        controller.state.capability = capability()
        controller.state.capability_acked = True
        add_pending(controller, int(AirCmdId.START_MISSION), seq=9)

        controller._handle_ack_message(
            AirAckMessage(
                seq=2,
                ack_seq=9,
                ack_cmd_id=int(AirCmdId.START_MISSION),
                result=0,
                time_ms=900,
            )
        )

        self.assertTrue(controller.state.mission_started)
        self.assertEqual(controller.state.mission_first_time_ms, 900)
        self.assertIs(
            controller.state.mission_presentation.phase, MissionPhase.PRE_START
        )

    def test_mission_state_follows_only_authoritative_key_events(self) -> None:
        controller = make_controller()
        transitions = (
            (AirStatusId.MISSION_START, MissionPhase.MISSION_ACTIVE, False),
            (AirStatusId.LAUNCH, MissionPhase.IN_FLIGHT, False),
            (AirStatusId.PARACHUTE_DEPLOY, MissionPhase.RECOVERY, True),
            (AirStatusId.LANDING, MissionPhase.LANDED, True),
        )
        for index, (status_id, phase, deployed) in enumerate(transitions):
            controller._handle_status_message(
                AirStatusMessage(
                    seq=index,
                    status_id=int(status_id),
                    time_ms=1000 + index * 100,
                    arg0=0,
                    arg1=0,
                ),
                None,
            )
            self.assertIs(controller.state.mission_presentation.phase, phase)
            self.assertEqual(
                controller.state.mission_presentation.last_critical_event_name,
                status_id.name,
            )
            self.assertEqual(
                controller.state.mission_presentation.parachute_deployed, deployed
            )

    def test_all_status_events_enter_bounded_history(self) -> None:
        controller = make_controller()
        for index in range(250):
            controller._handle_status_message(
                AirStatusMessage(
                    seq=index & 0xFF,
                    status_id=int(AirStatusId.GNSS_POSITION),
                    time_ms=index,
                    arg0=index & 1,
                    arg1=0,
                ),
                None,
            )
        self.assertEqual(len(controller.events), 200)

    def test_gnss_status_format(self) -> None:
        message = AirStatusMessage(
            seq=1,
            status_id=int(AirStatusId.GNSS_POSITION),
            time_ms=123,
            arg0=1,
            arg1=0,
        )
        self.assertEqual(format_air_status_message(message), "GNSS_POSITION 定位可用 @ 123 ms")


if __name__ == "__main__":
    unittest.main()
