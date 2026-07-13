from __future__ import annotations

import json
import unittest
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from processing.flight_log_processor import FlightData, FlightLogProcessor, calculate_packet_loss_stats
from processing.flight_plotter import FlightPlotter
from ui.main_window import MainWindow


class FakeCurve:
    def __init__(self) -> None:
        self.x: list[float] = []
        self.y: list[float] = []

    def setData(self, x: list[float], y: list[float]) -> None:
        self.x = x
        self.y = y


class FakePlotWidget:
    def __init__(self) -> None:
        self.x_range: tuple[float, float, int] | None = None

    def setXRange(self, minimum: float, maximum: float, *, padding: int) -> None:
        self.x_range = minimum, maximum, padding


class FakePlotWindow:
    def __init__(self) -> None:
        self.max_points = 3
        self.series_time = {"vel": deque(maxlen=3), "pos": deque(maxlen=3)}
        self.series = {
            "vel": [deque(maxlen=3) for _ in range(3)],
            "pos": [deque(maxlen=3) for _ in range(3)],
        }
        self.curves = {(key, axis): FakeCurve() for key in self.series for axis in range(3)}
        self.plot_widgets = {(key, axis): FakePlotWidget() for key in self.series for axis in range(3)}

    def refresh_plots(self) -> None:
        MainWindow.refresh_plots(self)  # type: ignore[arg-type]


class PacketLossStatsTests(unittest.TestCase):
    def test_continuous_time_slots_have_no_loss(self) -> None:
        stats = calculate_packet_loss_stats([0, 200, 400, 600, 800], landing_ms=None)

        self.assertEqual((stats.received_packets, stats.expected_packets, stats.lost_packets), (5, 5, 0))

    def test_one_missing_time_slot(self) -> None:
        stats = calculate_packet_loss_stats([0, 200, 600, 800], landing_ms=None)

        self.assertEqual((stats.received_packets, stats.expected_packets, stats.lost_packets), (4, 5, 1))

    def test_two_missing_time_slots(self) -> None:
        stats = calculate_packet_loss_stats([0, 200, 800], landing_ms=None)

        self.assertEqual((stats.received_packets, stats.expected_packets, stats.lost_packets), (3, 5, 2))

    def test_loss_is_bucketed_per_second_with_zero_buckets(self) -> None:
        received = [time_ms for time_ms in range(0, 2000, 200) if time_ms not in {400, 600, 1400}]
        stats = calculate_packet_loss_stats(received, landing_ms=1999)

        self.assertEqual(stats.loss_per_second, [(0, 2), (1, 1)])

    def test_duplicate_times_and_ticks_are_deduplicated(self) -> None:
        stats = calculate_packet_loss_stats([0, 200, 200, 201, 400], landing_ms=None)

        self.assertEqual((stats.received_packets, stats.expected_packets, stats.lost_packets), (3, 3, 0))

    def test_sequence_is_not_an_input_to_packet_loss(self) -> None:
        continuous = calculate_packet_loss_stats([0, 200, 400], landing_ms=None)
        skipped = calculate_packet_loss_stats([0, 200, 600], landing_ms=None)

        self.assertEqual(continuous.lost_packets, 0)
        self.assertEqual(skipped.lost_packets, 1)

    def test_landing_defines_observable_loss_window(self) -> None:
        stats = calculate_packet_loss_stats([0, 200], landing_ms=600)

        self.assertEqual((stats.received_packets, stats.expected_packets, stats.lost_packets), (2, 4, 2))
        self.assertEqual(stats.loss_window_end_basis, "landing")

    def test_negative_time_is_ignored(self) -> None:
        stats = calculate_packet_loss_stats([-200, 0, 200], landing_ms=None)

        self.assertEqual((stats.received_packets, stats.expected_packets, stats.lost_packets), (2, 2, 0))

    def test_summary_manifest_and_plot_include_packet_loss(self) -> None:
        stats = calculate_packet_loss_stats([0, 200, 600], landing_ms=600)
        data = FlightData(
            mission_start_ms=0,
            landing_ms=600,
            parachute_ms=None,
            end_ms=600,
            source_log=Path("flight.jsonl"),
            packet_loss=stats,
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            processor = FlightLogProcessor(output_root=root)
            summary_path = root / "summary.txt"
            manifest_path = root / "manifest.json"
            plot_path = root / "packet_loss_per_second.png"

            processor._write_summary(data, summary_path)
            processor._write_manifest(data, manifest_path, data.source_log)
            FlightPlotter().plot_packet_loss_per_second(plot_path, stats.loss_per_second)

            summary = summary_path.read_text(encoding="utf-8")
            manifest = manifest_path.read_text(encoding="utf-8")
            self.assertIn("lost_packets: 1", summary)
            self.assertIn("loss_window_end_basis: landing", summary)
            self.assertIn('"lost_packets": 1', manifest)
            self.assertIn('"loss_window_end_basis": "landing"', manifest)
            self.assertGreater(plot_path.stat().st_size, 0)

    def test_realtime_plot_uses_time_axis_and_history_limit(self) -> None:
        window = FakePlotWindow()
        for time_s in (0.0, 0.2, 0.4, 0.6):
            window.series_time["vel"].append(time_s)
            for axis in range(3):
                window.series["vel"][axis].append(time_s + axis)

        MainWindow.refresh_plots(window)  # type: ignore[arg-type]

        curve = window.curves[("vel", 0)]
        self.assertEqual(curve.x, [0.2, 0.4, 0.6])
        self.assertEqual(curve.y, [0.2, 0.4, 0.6])
        self.assertEqual(window.plot_widgets[("vel", 0)].x_range, (0.2, 0.6, 0))

        MainWindow.clear_vector_series(window)  # type: ignore[arg-type]
        self.assertEqual(list(window.series_time["vel"]), [])
        self.assertEqual(list(window.series["vel"][0]), [])

    def test_process_file_generates_existing_and_packet_loss_outputs(self) -> None:
        records = [
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "STATUS",
                "status_id": 0x03,
                "time_ms": 0,
            }
        ]
        for seq, time_ms in enumerate((0, 200, 600), start=1):
            records.append(
                {
                    "dir": "RX",
                    "layer": "AIR_PARSED",
                    "kind": "FLIGHT_STATE",
                    "seq": seq,
                    "time_ms": time_ms,
                    "accel_mps2": [0.0, 0.0, 9.8],
                    "gyro_radps": [0.0, 0.0, 0.0],
                    "quat": [1.0, 0.0, 0.0, 0.0],
                    "quat_valid": True,
                    "vel_mps": [0.0, 0.0, 1.0],
                    "pos_m": [0.0, 0.0, time_ms / 1000.0],
                }
            )
        records.append(
            {
                "dir": "RX",
                "layer": "AIR_PARSED",
                "kind": "STATUS",
                "status_id": 0x06,
                "time_ms": 600,
            }
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = root / "flight.jsonl"
            log_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            processor = FlightLogProcessor(output_root=root / "output")

            with patch.object(FlightPlotter, "generate_attitude_motion_gif", return_value=iter(())):
                output_dir = processor.process_file(log_path)

            for filename in (
                "accel.png",
                "gyro.png",
                "euler.png",
                "velocity.png",
                "position.png",
                "link_quality.png",
                "packet_loss_per_second.png",
            ):
                self.assertGreater((output_dir / filename).stat().st_size, 0)
            self.assertIn("lost_packets: 1", (output_dir / "summary.txt").read_text(encoding="utf-8"))
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["packet_loss"]["lost_packets"], 1)


if __name__ == "__main__":
    unittest.main()
