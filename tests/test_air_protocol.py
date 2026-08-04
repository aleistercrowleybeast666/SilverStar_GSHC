from __future__ import annotations

import unittest

from protocol.air import (
    AIR_FLIGHT_STATE_LEN,
    AIR_QUAT_STATE_LEN,
    AIR_STATUS_LEN,
    AIR_TYPE_FLIGHT_STATE,
    AirFlightStateMessage,
    AirQuatStateMessage,
    AirStatusMessage,
    parse_air_frame,
)
from protocol.common import AirStatusId, GspType
from protocol.gsp_min import GspFrame, GsToPcAirFrame, parse_gsp_frame


QUAT_VALID_FRAME = bytes.fromhex("11 01 78 56 34 12 FF 7F 00 00 00 00 00 00")
QUAT_ZERO_FRAME = bytes.fromhex("11 02 00 00 00 00 00 00 00 00 00 00 00 00")
GNSS_AVAILABLE_FRAME = bytes.fromhex("20 03 09 78 56 34 12 01 00")
GNSS_UNAVAILABLE_FRAME = bytes.fromhex("20 04 09 79 56 34 12 00 00")


class AirProtocolTests(unittest.TestCase):
    def test_gnss_position_status_fixed_vectors(self) -> None:
        for frame, expected_arg0 in (
            (GNSS_AVAILABLE_FRAME, 1),
            (GNSS_UNAVAILABLE_FRAME, 0),
        ):
            with self.subTest(arg0=expected_arg0):
                air, msg = parse_air_frame(frame)

                self.assertEqual(len(air.raw), AIR_STATUS_LEN)
                self.assertIsInstance(msg, AirStatusMessage)
                self.assertEqual(msg.status_id, int(AirStatusId.GNSS_POSITION))
                self.assertEqual(msg.arg0, expected_arg0)
                self.assertEqual(msg.arg1, 0)

    def test_gnss_position_status_length_remains_nine_bytes(self) -> None:
        self.assertEqual(len(GNSS_AVAILABLE_FRAME), 9)
        with self.assertRaisesRegex(ValueError, "bad AIR_STATUS length"):
            parse_air_frame(GNSS_AVAILABLE_FRAME[:-1])

    def test_quat_state_fixed_vector(self) -> None:
        air, msg = parse_air_frame(QUAT_VALID_FRAME)

        self.assertEqual(air.air_type, 0x11)
        self.assertEqual(len(air.raw), AIR_QUAT_STATE_LEN)
        self.assertIsInstance(msg, AirQuatStateMessage)
        self.assertEqual(msg.seq, 1)
        self.assertEqual(msg.time_ms, 0x12345678)
        self.assertEqual(msg.quat_q15, (32767, 0, 0, 0))
        self.assertTrue(msg.quat_valid)
        self.assertFalse(msg.quat_raw_zero)
        self.assertEqual(msg.quat, (1.0, 0.0, 0.0, 0.0))

    def test_quat_state_raw_zero_is_invalid(self) -> None:
        _air, msg = parse_air_frame(QUAT_ZERO_FRAME)

        self.assertIsInstance(msg, AirQuatStateMessage)
        self.assertEqual(msg.quat_q15, (0, 0, 0, 0))
        self.assertTrue(msg.quat_raw_zero)
        self.assertFalse(msg.quat_valid)

    def test_quat_state_bad_length_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "bad AIR_QUAT_STATE length"):
            parse_air_frame(QUAT_VALID_FRAME[:-1])

    def test_old_flight_state_length_and_parser_remain(self) -> None:
        frame = bytes([AIR_TYPE_FLIGHT_STATE, 7]) + bytes(AIR_FLIGHT_STATE_LEN - 2)
        air, msg = parse_air_frame(frame)

        self.assertEqual(len(air.raw), 50)
        self.assertIsInstance(msg, AirFlightStateMessage)
        self.assertEqual(msg.seq, 7)

    def test_gsp_air_rx_still_starts_air_frame_at_payload_3(self) -> None:
        payload = bytes([0xD8, 0x04, len(QUAT_VALID_FRAME)]) + QUAT_VALID_FRAME
        parsed = parse_gsp_frame(GspFrame(msg_type=int(GspType.AIR_RX), payload=payload))

        self.assertIsInstance(parsed, GsToPcAirFrame)
        self.assertEqual(parsed.rssi_dbm, -40)
        self.assertEqual(parsed.snr_db, 1.0)
        self.assertEqual(parsed.air_frame, QUAT_VALID_FRAME)


if __name__ == "__main__":
    unittest.main()
