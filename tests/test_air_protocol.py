from __future__ import annotations

import math
import struct
import unittest

from protocol.air import (
    AIR_CAPABILITY_LEN,
    AIR_FLIGHT_STATE_LEN,
    AIR_PREFLIGHT_STATE_LEN,
    AIR_PREFLIGHT_STATUS_LEN,
    AIR_STATUS_LEN,
    AirCapabilityMessage,
    AirFlightStateMessage,
    AirPreflightStateMessage,
    AirPreflightStatusMessage,
    AirStatusMessage,
    accel_raw_to_mps2,
    build_air_cmd,
    gyro_raw_to_radps,
    parse_air_frame,
)
from protocol.common import AirCmdId, AirStatusId, GspType
from protocol.gsp_min import GspFrame, GsToPcAirFrame, parse_gsp_frame


CAPABILITY_FRAME = bytes.fromhex("12 2A 00 01 07 07 10 D0 07")
PREFLIGHT_STATUS_FRAME = bytes.fromhex("13 02 03 24 15 03 31 7F 00")
PREFLIGHT_STATUS_NOT_SELECTED = bytes.fromhex("13 03 02 F0 00 FF 00 10 0B")
GNSS_AVAILABLE_FRAME = bytes.fromhex("20 03 09 78 56 34 12 01 00")


def inertial_prefix(air_type: int, seq: int = 7, time_ms: int = 1234) -> bytes:
    return struct.pack(
        "<BBIhhhhhhhhhh",
        air_type,
        seq,
        time_ms,
        100,
        -200,
        300,
        -400,
        500,
        -600,
        32767,
        0,
        0,
        0,
    )


class AirProtocolTests(unittest.TestCase):
    def test_capability_fixed_vector(self) -> None:
        air, message = parse_air_frame(CAPABILITY_FRAME)

        self.assertEqual(len(air.raw), AIR_CAPABILITY_LEN)
        self.assertIsInstance(message, AirCapabilityMessage)
        self.assertEqual(message.seq, 0x2A)
        self.assertEqual(message.air_profile_id, 0)
        self.assertEqual(message.command_policy, 1)
        self.assertEqual(message.calibration_mode_mask, 0x07)
        self.assertEqual(message.alignment_capability_mask, 0x07)
        self.assertEqual(message.accel_full_scale_g, 16)
        self.assertEqual(message.gyro_full_scale_dps, 2000)
        self.assertTrue(message.profile_supported)

    def test_unknown_profile_is_reported_without_guessing_compatibility(self) -> None:
        _air, message = parse_air_frame(bytes.fromhex("12 01 09 01 07 07 10 D0 07"))

        self.assertIsInstance(message, AirCapabilityMessage)
        self.assertEqual(message.air_profile_id, 9)
        self.assertFalse(message.profile_supported)

    def test_preflight_status_bit_fields(self) -> None:
        air, message = parse_air_frame(PREFLIGHT_STATUS_FRAME)

        self.assertEqual(len(air.raw), AIR_PREFLIGHT_STATUS_LEN)
        self.assertIsInstance(message, AirPreflightStatusMessage)
        self.assertEqual(message.lifecycle_state, 3)
        self.assertEqual(message.calibration_state, 4)
        self.assertEqual(message.calibration_mode, 2)
        self.assertEqual(message.completed_face_mask, 0x15)
        self.assertEqual(message.current_face, 3)
        self.assertEqual(message.alignment_state, 1)
        self.assertTrue(message.attitude_ready)
        self.assertTrue(message.gnss_origin_ready)
        self.assertFalse(message.baro_origin_ready)
        self.assertTrue(message.system_ready)
        self.assertTrue(message.start_unlocked)
        self.assertTrue(message.capability_acked)
        self.assertTrue(message.calibration_ready)
        self.assertTrue(message.alignment_ready)
        self.assertEqual(message.start_block_reason, 0)

    def test_preflight_status_restores_not_selected_mode(self) -> None:
        _air, message = parse_air_frame(PREFLIGHT_STATUS_NOT_SELECTED)
        self.assertIsInstance(message, AirPreflightStatusMessage)
        self.assertEqual(message.calibration_mode, 0xFF)
        self.assertFalse(message.system_ready)
        self.assertTrue(message.capability_acked)

    def test_preflight_and_flight_share_exact_prefix_parser(self) -> None:
        preflight_frame = inertial_prefix(0x11)
        flight_frame = preflight_frame[:1].replace(b"\x11", b"\x10") + preflight_frame[1:]
        flight_frame += struct.pack("<ffffff", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

        _air, preflight = parse_air_frame(preflight_frame)
        _air, flight = parse_air_frame(flight_frame)

        self.assertEqual(len(preflight_frame), AIR_PREFLIGHT_STATE_LEN)
        self.assertEqual(len(flight_frame), AIR_FLIGHT_STATE_LEN)
        self.assertIsInstance(preflight, AirPreflightStateMessage)
        self.assertIsInstance(flight, AirFlightStateMessage)
        for field in ("seq", "time_ms", "accel_raw", "gyro_raw", "quat_q15", "quat"):
            self.assertEqual(getattr(preflight, field), getattr(flight, field))
        self.assertEqual(flight.vel_mps, (1.0, 2.0, 3.0))
        self.assertEqual(flight.pos_m, (4.0, 5.0, 6.0))

    def test_capability_scales_are_the_only_physical_conversion_source(self) -> None:
        accel = accel_raw_to_mps2((32767, 0, -32768), 16)
        gyro = gyro_raw_to_radps((32767, 0, -32768), 2000)

        self.assertAlmostEqual(accel[0], 16 * 9.80665 * 32767 / 32768, places=6)
        self.assertAlmostEqual(accel[2], -16 * 9.80665, places=6)
        self.assertAlmostEqual(gyro[0], math.radians(2000) * 32767 / 32768, places=6)
        self.assertAlmostEqual(gyro[2], -math.radians(2000), places=6)

    def test_status_and_command_lengths_remain_nine_bytes(self) -> None:
        air, status = parse_air_frame(GNSS_AVAILABLE_FRAME)
        self.assertEqual(len(air.raw), AIR_STATUS_LEN)
        self.assertIsInstance(status, AirStatusMessage)
        self.assertEqual(status.status_id, int(AirStatusId.GNSS_POSITION))
        self.assertEqual(status.arg0, 1)

        command = build_air_cmd(5, int(AirCmdId.CAL_FACE), 0x43414C30, 4, 0)
        self.assertEqual(len(command), 9)
        self.assertEqual(command[2], int(AirCmdId.CAL_FACE))

    def test_all_fixed_lengths_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "bad AIR_CAPABILITY length"):
            parse_air_frame(CAPABILITY_FRAME[:-1])
        with self.assertRaisesRegex(ValueError, "bad AIR_PREFLIGHT_STATUS length"):
            parse_air_frame(PREFLIGHT_STATUS_FRAME + b"\x00")
        with self.assertRaisesRegex(ValueError, "bad AIR_PREFLIGHT_STATE length"):
            parse_air_frame(inertial_prefix(0x11)[:-1])

    def test_gsp_air_rx_still_starts_air_frame_at_payload_three(self) -> None:
        payload = bytes([0xD8, 0x04, len(CAPABILITY_FRAME)]) + CAPABILITY_FRAME
        parsed = parse_gsp_frame(GspFrame(msg_type=int(GspType.AIR_RX), payload=payload))

        self.assertIsInstance(parsed, GsToPcAirFrame)
        self.assertEqual(parsed.rssi_dbm, -40)
        self.assertEqual(parsed.snr_db, 1.0)
        self.assertEqual(parsed.air_frame, CAPABILITY_FRAME)


if __name__ == "__main__":
    unittest.main()
