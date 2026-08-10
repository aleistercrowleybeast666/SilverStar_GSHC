from __future__ import annotations

import json
import random
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from protocol.air import AirAckMessage, AirCapabilityMessage, AirFlightStateMessage
from protocol.common import AirAckResult, AirCmdId, AirStatusId, GspType
from protocol.gsp_min import GspParser, build_gsp_frame
from protocol.receive_pipeline import ReceivePipeline, protocol_event_log_records
from services.logger import AsyncJsonlLogger
from transport.protocol_worker import ProtocolWorker


def capability_frame(profile: int = 0, seq: int = 1) -> bytes:
    return struct.pack("<BBBBBBBH", 0x12, seq, profile, 1, 7, 7, 16, 2000)


def flight_frame(sequence: int, time_ms: int) -> bytes:
    prefix = struct.pack(
        "<BBIhhhhhhhhhh",
        0x10,
        sequence & 0xFF,
        time_ms,
        100,
        -100,
        2048,
        10,
        20,
        30,
        32767,
        0,
        0,
        0,
    )
    return prefix + struct.pack(
        "<ffffff",
        float(sequence),
        2.0,
        3.0,
        4.0,
        5.0,
        6.0,
    )


def ack_frame(sequence: int, ack_sequence: int) -> bytes:
    return struct.pack(
        "<BBBBBI",
        0x40,
        sequence,
        ack_sequence,
        int(AirCmdId.CAPABILITY_ACK),
        int(AirAckResult.OK),
        100,
    )
def air_rx_gsp(air_frame: bytes, rssi: int = -70, snr_q4: int = 20) -> bytes:
    payload = bytes([rssi & 0xFF, snr_q4 & 0xFF, len(air_frame)]) + air_frame
    return build_gsp_frame(int(GspType.AIR_RX), payload)


def gs_status_gsp(counter: int) -> bytes:
    payload = struct.pack("<BBIIH", 2, 2, counter, counter, 0)
    return build_gsp_frame(int(GspType.GS_STATUS), payload)


class ReceivePipelineStressTests(unittest.TestCase):
    def test_calibration_diagnostic_json_uses_canonical_status_name(self) -> None:
        diagnostic = struct.pack(
            "<BBBIBB",
            0x20,
            8,
            int(AirStatusId.CALIBRATION_DIAGNOSTIC),
            456,
            2,
            4,
        )
        event = ReceivePipeline().feed(air_rx_gsp(diagnostic))[0]
        records = protocol_event_log_records(event)
        status = next(record for record in records if record.get("kind") == "STATUS")
        self.assertEqual(status["status_name"], "CALIBRATION_DIAGNOSTIC")
        self.assertEqual(status["arg0"], 2)
        self.assertEqual(status["arg1"], 4)

    def test_late_capability_is_parsed_without_session_or_logger_side_effects(self) -> None:
        pipeline = ReceivePipeline()
        events = pipeline.feed(
            air_rx_gsp(capability_frame(seq=10))
            + air_rx_gsp(ack_frame(sequence=3, ack_sequence=42))
            + air_rx_gsp(capability_frame(seq=9))
        )

        self.assertIsInstance(events[0].air_message, AirCapabilityMessage)
        self.assertIsInstance(events[1].air_message, AirAckMessage)
        self.assertIsInstance(events[2].air_message, AirCapabilityMessage)
        self.assertFalse(any(hasattr(event, "session_reset") for event in events))

        class NoRolloverLogger:
            def __init__(self) -> None:
                self.records: list[dict] = []

            def write(self, record: dict) -> None:
                self.records.append(record)

            def rollover_session(self, *_args, **_kwargs) -> None:
                raise AssertionError("receive pipeline must not roll over the logger")

        logger = NoRolloverLogger()
        worker = ProtocolWorker(1, logger)  # type: ignore[arg-type]
        worker._persist_events(events)
        self.assertGreater(len(logger.records), 0)

    def test_ten_thousand_interleaved_frames_survive_random_fragmentation(self) -> None:
        frame_count = 10_000
        wire = bytearray(air_rx_gsp(capability_frame()))
        for index in range(frame_count):
            if index % 100 == 0:
                wire.extend(gs_status_gsp(index))
            wire.extend(air_rx_gsp(flight_frame(index, index * 200)))

        random_generator = random.Random(20260809)
        pipeline = ReceivePipeline()
        events = []
        offset = 0
        while offset < len(wire):
            chunk_size = random_generator.randint(1, 197)
            chunk = bytes(wire[offset : offset + chunk_size])
            events.extend(pipeline.feed(chunk))
            offset += len(chunk)

        flight_events = [
            event for event in events if isinstance(event.air_message, AirFlightStateMessage)
        ]
        capability_events = [
            event for event in events if isinstance(event.air_message, AirCapabilityMessage)
        ]
        diagnostics = pipeline.diagnostics()

        self.assertEqual(len(capability_events), 1)
        self.assertEqual(len(flight_events), frame_count)
        self.assertEqual(
            [event.air_message.seq for event in flight_events[:300]],
            [index & 0xFF for index in range(300)],
        )
        self.assertEqual(flight_events[-1].air_message.time_ms, (frame_count - 1) * 200)
        self.assertEqual(flight_events[-1].packet_stats["received_flight_packets"], frame_count)
        self.assertEqual(flight_events[-1].packet_stats["estimated_lost_packets"], 0)
        self.assertEqual(diagnostics.gsp_frames, frame_count + 101)
        self.assertEqual(diagnostics.gsp_crc_errors, 0)
        self.assertEqual(diagnostics.gsp_parse_errors, 0)
        self.assertEqual(diagnostics.air_parse_errors, 0)
        self.assertEqual(diagnostics.parser_buffer_size, 0)

        parsed_records = protocol_event_log_records(flight_events[-1])
        flight_record = next(record for record in parsed_records if record.get("kind") == "FLIGHT_STATE")
        self.assertTrue(flight_record["physical_conversion_valid"])
        self.assertIn("estimated_lost_packets", flight_record)
        self.assertEqual(flight_record["rssi_dbm"], -70)

    def test_unsupported_profile_preserves_raw_frame_without_interpreting_state(self) -> None:
        pipeline = ReceivePipeline()
        events = pipeline.feed(
            air_rx_gsp(capability_frame(profile=9)) + air_rx_gsp(flight_frame(1, 100))
        )

        self.assertIsInstance(events[0].air_message, AirCapabilityMessage)
        self.assertIsNone(events[1].air_message)
        self.assertIn("profile unsupported", events[1].air_error)
        records = protocol_event_log_records(events[1])
        self.assertTrue(any(record.get("kind") == "AIR_RAW" for record in records))
        self.assertTrue(any(record.get("kind") == "AIR_PARSE_ERROR" for record in records))

    def test_gsp_parser_preserves_fragmented_a5_header_byte(self) -> None:
        parser = GspParser()
        frame = gs_status_gsp(1)
        self.assertEqual(parser.feed(b"noise\xA5"), [])
        parsed = parser.feed(frame[1:])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].msg_type, int(GspType.GS_STATUS))
        self.assertEqual(parser.buffer_size, 0)
        self.assertGreaterEqual(parser.diagnostics().resyncs, 1)

    def test_ui_mailbox_is_bounded_and_keeps_latest_display_frame(self) -> None:
        pipeline = ReceivePipeline()
        wire = bytearray(air_rx_gsp(capability_frame()))
        for index in range(600):
            wire.extend(air_rx_gsp(flight_frame(index, index * 200)))
        events = pipeline.feed(bytes(wire))

        with TemporaryDirectory() as temporary_directory:
            logger = AsyncJsonlLogger(Path(temporary_directory))
            worker = ProtocolWorker(1, logger, ui_mailbox_capacity=256)
            worker._publish_events(events)
            self.assertLessEqual(worker.ui_mailbox_depth, 257)
            diagnostics = worker.diagnostics_snapshot()
            self.assertGreater(diagnostics.ui_coalesced_events, 0)

            drained = []
            while True:
                batch = worker.take_batch(128)
                if batch is None:
                    break
                drained.extend(batch.events)
            latest_flight = [
                event for event in drained if isinstance(event.air_message, AirFlightStateMessage)
            ][-1]
            self.assertEqual(latest_flight.air_message.time_ms, 599 * 200)
            logger.close()


class AsyncLoggerTests(unittest.TestCase):
    def test_bounded_logger_drains_in_order_without_silent_drop(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            logger = AsyncJsonlLogger(
                Path(temporary_directory),
                queue_max_records=256,
                batch_records=32,
                flush_interval_s=0.05,
            )
            path = logger.path
            self.assertIsNotNone(path)
            for sequence in range(5000):
                logger.write({"kind": "TEST", "sequence": sequence})
            logger.close()

            lines = path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
            payload_records = [record for record in records if record.get("kind") == "TEST"]
            self.assertEqual(len(payload_records), 5000)
            self.assertEqual(
                [record["sequence"] for record in payload_records],
                list(range(5000)),
            )
            self.assertEqual(logger.records_enqueued, 5000)
            self.assertEqual(logger.records_written, 5001)  # includes SESSION_START
            self.assertLessEqual(logger.max_queue_depth, logger.queue_max_records)

    def test_session_rollover_keeps_records_on_the_correct_side_of_boundary(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            logger = AsyncJsonlLogger(Path(temporary_directory), flush_interval_s=0.02)
            first_path = logger.path
            logger.write({"kind": "BEFORE"})
            second_path = logger.rollover("flight_controller_reboot", {"air_profile_id": 0})
            logger.write({"kind": "AFTER"})
            logger.close()

            first = first_path.read_text(encoding="utf-8")
            second = second_path.read_text(encoding="utf-8")
            self.assertIn('"kind": "BEFORE"', first)
            self.assertNotIn('"kind": "AFTER"', first)
            self.assertIn('"kind": "AFTER"', second)
            self.assertIn('"reason": "flight_controller_reboot"', second)


if __name__ == "__main__":
    unittest.main()
