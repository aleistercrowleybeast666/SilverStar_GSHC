from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from processing.flight_log_processor import (
    STATUS_GNSS_POSITION,
    STATUS_LANDING,
    STATUS_MISSION_START,
    STATUS_NAME,
    FlightData,
    FlightLogProcessor,
    calculate_packet_loss_stats,
)
from processing.fake_log_generator import quat_to_q15, simulate
from processing.flight_plotter import FlightPlotter
from services.state_model import LiveFlightPlotBuffer


class PacketLossStatsTests(unittest.TestCase):
    def test_status_lookup_is_not_confused_by_gnss_events(self) -> None:
        processor = FlightLogProcessor()
        events = [
            (STATUS_GNSS_POSITION, 50, 0, 0),
            (STATUS_MISSION_START, 100, 0, 0),
            (STATUS_GNSS_POSITION, 200, 1, 0),
            (STATUS_LANDING, 500, 0, 0),
        ]
        self.assertEqual(STATUS_NAME[STATUS_GNSS_POSITION], "GNSS_POSITION")
        self.assertEqual(processor._first_status_time(events, STATUS_MISSION_START), 100)
        self.assertEqual(processor._first_status_time(events, STATUS_LANDING, after_ms=100), 500)

    def test_continuous_and_missing_slots(self) -> None:
        continuous = calculate_packet_loss_stats([0, 200, 400, 600, 800], landing_ms=None)
        missing = calculate_packet_loss_stats([0, 200, 800], landing_ms=None)
        self.assertEqual((continuous.expected_packets, continuous.lost_packets), (5, 0))
        self.assertEqual((missing.expected_packets, missing.lost_packets), (5, 2))

    def test_loss_is_bucketed_and_duplicates_are_deduplicated(self) -> None:
        received = [time_ms for time_ms in range(0, 2000, 200) if time_ms not in {400, 600, 1400}]
        stats = calculate_packet_loss_stats(received + [200, 201], landing_ms=1999)
        self.assertEqual(stats.loss_per_second, [(0, 2), (1, 1)])

    def test_landing_defines_observable_window(self) -> None:
        stats = calculate_packet_loss_stats([0, 200], landing_ms=600)
        self.assertEqual((stats.received_packets, stats.expected_packets, stats.lost_packets), (2, 4, 2))
        self.assertEqual(stats.loss_window_end_basis, "landing")

    def test_summary_and_manifest_include_packet_loss(self) -> None:
        stats = calculate_packet_loss_stats([0, 200, 600], landing_ms=600)
        data = FlightData(
            mission_start_ms=0,
            landing_ms=600,
            parachute_ms=None,
            end_ms=600,
            source_log=Path("flight.jsonl"),
            packet_loss=stats,
            air_profile_id=0,
            command_policy=1,
            accel_full_scale_g=16,
            gyro_full_scale_dps=2000,
        )
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            processor = FlightLogProcessor(output_root=root)
            processor._write_summary(data, root / "summary.txt")
            processor._write_manifest(data, root / "manifest.json", data.source_log)
            FlightPlotter().plot_packet_loss_per_second(
                root / "packet_loss.png",
                stats.loss_per_second,
            )
            summary = (root / "summary.txt").read_text(encoding="utf-8")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("lost_packets: 1", summary)
            self.assertEqual(manifest["packet_loss"]["lost_packets"], 1)
            self.assertEqual(manifest["capability"]["air_profile_id"], 0)


class LiveWindowTests(unittest.TestCase):
    def test_thirty_seconds_of_samples_retain_only_latest_ten_seconds(self) -> None:
        live = LiveFlightPlotBuffer(window_seconds=10.0, max_points=1000)
        persisted: list[dict] = []
        for sample in range(151):
            time_s = sample * 0.2
            velocity = (time_s, time_s + 1.0, time_s + 2.0)
            position = (time_s * 2.0, 0.0, 0.0)
            live.append(time_s, velocity, position)
            persisted.append({"time_s": time_s, "vel_mps": velocity, "pos_m": position})

        times, velocity, position = live.snapshot()
        self.assertAlmostEqual(times[0], 20.0, places=6)
        self.assertAlmostEqual(times[-1], 30.0, places=6)
        self.assertEqual(len(times), 51)
        self.assertEqual(len(velocity[0]), len(times))
        self.assertEqual(len(position[0]), len(times))
        self.assertEqual(len(persisted), 151, "display trimming must not trim persistent records")

    def test_defensive_point_limit_is_also_bounded(self) -> None:
        live = LiveFlightPlotBuffer(window_seconds=10.0, max_points=20)
        for sample in range(100):
            live.append(sample / 100.0, (0, 0, 0), (0, 0, 0))
        self.assertEqual(len(live.time_s), 20)


class PostProcessingTests(unittest.TestCase):
    @staticmethod
    def _raw_gsp_air_record(air_frame: bytes) -> dict:
        payload = bytes([(-70) & 0xFF, 20, len(air_frame)]) + air_frame
        return {
            "dir": "RX",
            "layer": "GSP",
            "msg_type": 0x02,
            "payload_hex": payload.hex(),
        }

    def test_fake_log_covers_the_complete_profile_zero_workflow(self) -> None:
        records = simulate(duration_s=1.0, seed=42)
        kinds = {record.get("kind") for record in records}
        status_ids = {
            int(record["status_id"])
            for record in records
            if record.get("kind") == "STATUS"
        }

        self.assertTrue(
            {
                "CAPABILITY",
                "PREFLIGHT_STATUS",
                "PREFLIGHT_STATE",
                "STATUS",
                "FLIGHT_STATE",
            }.issubset(kinds)
        )
        self.assertIn(0x0A, status_ids)  # ALIGNMENT
        self.assertIn(0x0B, status_ids)  # CALIBRATION
        self.assertIn(0x0C, status_ids)  # CALIBRATION_FACE
        self.assertEqual(quat_to_q15((-1.0, 0.0, 0.0, 0.0))[0], -32768)

    def test_mixed_log_recovers_raw_capability_and_preflight_metadata(self) -> None:
        capability = struct.pack("<BBBBBBBH", 0x12, 1, 0, 1, 7, 3, 8, 1000)
        preflight_status = bytes([0x13, 2, 3, 0x24, 0x3F, 0xFF, 0x33, 0x7F, 0])
        records = [
            self._raw_gsp_air_record(capability),
            self._raw_gsp_air_record(preflight_status),
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "STATUS",
                "status_id": STATUS_MISSION_START,
                "time_ms": 1000,
                "arg0": 0,
                "arg1": 0,
            },
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "FLIGHT_STATE",
                "seq": 3,
                "time_ms": 1000,
                "accel_mps2": [0.0, 0.0, 9.8],
                "gyro_radps": [0.0, 0.0, 0.0],
                "quat": [1.0, 0.0, 0.0, 0.0],
                "quat_valid": True,
                "vel_mps": [0.0, 0.0, 0.0],
                "pos_m": [0.0, 0.0, 0.0],
            },
        ]

        data = FlightLogProcessor()._extract_flight_data(records, Path("mixed.jsonl"))

        self.assertEqual(data.air_profile_id, 0)
        self.assertEqual(data.accel_full_scale_g, 8)
        self.assertEqual(data.gyro_full_scale_dps, 1000)
        self.assertEqual(data.final_preflight_lifecycle, 3)
        self.assertEqual(data.calibration_final_state, 4)
        self.assertEqual(data.alignment_final_state, 3)
        self.assertTrue(data.gnss_position_usable_before_start)
        self.assertEqual(len(data.accel), 1, "paired GSP records must not duplicate parsed telemetry")

    def test_profile_zero_log_generates_outputs_and_preflight_manifest(self) -> None:
        records = [
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "CAPABILITY",
                "seq": 1,
                "air_profile_id": 0,
                "command_policy": 1,
                "calibration_mode_mask": 7,
                "alignment_capability_mask": 7,
                "accel_full_scale_g": 16,
                "gyro_full_scale_dps": 2000,
            },
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "PREFLIGHT_STATUS",
                "seq": 2,
                "lifecycle_state": 3,
                "calibration_state": 4,
                "calibration_mode": 2,
                "alignment_state": 3,
                "gnss_position_usable": True,
            },
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "STATUS",
                "status_id": STATUS_MISSION_START,
                "time_ms": 1000,
                "arg0": 0,
                "arg1": 0,
            },
        ]
        for sequence, time_ms in enumerate((1000, 1200, 1600), start=3):
            records.append(
                {
                    "dir": "RX",
                    "layer": "AIR_PARSED",
                    "kind": "FLIGHT_STATE",
                    "seq": sequence,
                    "time_ms": time_ms,
                    "accel_raw": [0, 0, 2048],
                    "gyro_raw": [0, 0, 0],
                    "accel_full_scale_g": 16,
                    "gyro_full_scale_dps": 2000,
                    "accel_mps2": [0.0, 0.0, 9.8],
                    "gyro_radps": [0.0, 0.0, 0.0],
                    "quat_q15": [32767, 0, 0, 0],
                    "quat": [1.0, 0.0, 0.0, 0.0],
                    "quat_valid": True,
                    "vel_mps": [0.0, 0.0, 1.0],
                    "pos_m": [0.0, 0.0, (time_ms - 1000) / 1000.0],
                    "rssi_dbm": -70,
                    "snr_db": 5.0,
                }
            )
        records.append(
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "STATUS",
                "status_id": STATUS_LANDING,
                "time_ms": 1600,
                "arg0": 0,
                "arg1": 0,
            }
        )

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "flight.jsonl"
            log_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            processor = FlightLogProcessor(output_root=root / "output")
            with patch.object(FlightPlotter, "generate_attitude_motion_gif", return_value=iter(())):
                output_dir = processor.process_file(log_path)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["protocol"], "SilverStar_0.0.8_AIR_PROFILE_COMPACT_V0")
            self.assertEqual(manifest["capability"]["air_profile_id"], 0)
            self.assertEqual(manifest["preflight"]["calibration_final_state"], 4)
            self.assertEqual(manifest["packet_loss"]["lost_packets"], 1)
            self.assertGreater((output_dir / "velocity.png").stat().st_size, 0)

    def test_first_flight_state_is_valid_fallback_start(self) -> None:
        records = [
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "FLIGHT_STATE",
                "seq": 1,
                "time_ms": 5000,
                "accel_mps2": [0, 0, 0],
                "gyro_radps": [0, 0, 0],
                "quat": [1, 0, 0, 0],
                "quat_valid": True,
                "vel_mps": [0, 0, 0],
                "pos_m": [0, 0, 0],
            }
        ]
        processor = FlightLogProcessor()
        data = processor._extract_flight_data(records, Path("fallback.jsonl"))
        self.assertEqual(data.mission_start_ms, 5000)
        self.assertTrue(any("first FLIGHT_STATE" in warning for warning in data.warnings))


if __name__ == "__main__":
    unittest.main()
