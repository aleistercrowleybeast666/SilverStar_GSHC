from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import matplotlib.pyplot as plt
from matplotlib.colors import to_hex

from processing.flight_log_processor import (
    FlightData,
    FlightLogProcessor,
    ProcessingCancelledError,
)
from processing.flight_plotter import (
    DARK_PLOT_COLORS,
    FlightPlotter,
    PlotterConfig,
)
from services.i18n import Language
from services.preferences import ExportItem, ResolvedExportOptions, Theme


def minimal_flight_data() -> FlightData:
    return FlightData(
        mission_start_ms=0,
        landing_ms=None,
        parachute_ms=None,
        end_ms=1000,
        source_log=Path("flight.jsonl"),
    )


class ExportProcessingTests(unittest.TestCase):
    def test_cancellation_removes_the_current_partial_output_directory(self) -> None:
        options = ResolvedExportOptions(
            language=Language.ZH_CN,
            theme=Theme.LIGHT,
            items=frozenset({ExportItem.PROCESSED_DATA}),
        )
        data = minimal_flight_data()
        cancel_state = {"requested": False}

        def write_then_cancel(_data: FlightData, path: Path) -> None:
            path.write_text("partial", encoding="utf-8")
            cancel_state["requested"] = True

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            processor = FlightLogProcessor(
                output_root=root,
                export_options=options,
            )
            with (
                patch.object(processor, "_read_jsonl", return_value=[]),
                patch.object(processor, "_extract_flight_data", return_value=data),
                patch.object(
                    processor,
                    "_write_processed_data",
                    side_effect=write_then_cancel,
                ),
            ):
                with self.assertRaises(ProcessingCancelledError):
                    processor.process_file(
                        data.source_log,
                        cancel_requested=lambda: cancel_state["requested"],
                    )

            self.assertEqual(list(root.iterdir()), [])

    def test_language_suffix_and_partial_chart_failure_are_isolated(self) -> None:
        options = ResolvedExportOptions(
            language=Language.ZH_CN,
            theme=Theme.DARK,
            items=frozenset(
                {
                    ExportItem.SUMMARY,
                    ExportItem.CHARTS,
                    ExportItem.SESSION_INFO,
                }
            ),
        )
        data = minimal_flight_data()

        def vector_export(path: Path, *_args, **_kwargs) -> None:
            if path.name.startswith("accel_"):
                raise RuntimeError("intentional chart failure")
            path.write_bytes(b"chart")

        def chart_export(path: Path, *_args, **_kwargs) -> None:
            path.write_bytes(b"chart")

        with TemporaryDirectory() as temporary_directory:
            processor = FlightLogProcessor(
                output_root=Path(temporary_directory),
                export_options=options,
            )
            with (
                patch.object(processor, "_read_jsonl", return_value=[]),
                patch.object(processor, "_extract_flight_data", return_value=data),
                patch.object(FlightPlotter, "plot_vector_figure", side_effect=vector_export),
                patch.object(FlightPlotter, "plot_link_quality", side_effect=chart_export),
                patch.object(
                    FlightPlotter,
                    "plot_packet_loss_per_second",
                    side_effect=chart_export,
                ),
            ):
                output_dir = processor.process_file(data.source_log)

            self.assertTrue((output_dir / "summary_ZH.txt").is_file())
            self.assertTrue((output_dir / "velocity_ZH.png").is_file())
            self.assertFalse((output_dir / "accel_ZH.png").exists())
            self.assertIn("accel_ZH.png", processor.last_export_errors)
            self.assertFalse(any(output_dir.glob("*_EN.*")))
            self.assertTrue(
                (output_dir / "summary_ZH.txt")
                .read_text(encoding="utf-8")
                .startswith("飞行摘要")
            )

            manifest = json.loads(
                (output_dir / "manifest_ZH.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["export"]["language"], "zh_CN")
            self.assertEqual(manifest["export"]["theme"], "dark")
            self.assertEqual(manifest["export"]["filename_language_suffix"], "ZH")
            self.assertIn(
                "accel_ZH.png",
                manifest["export"]["partial_failures"],
            )

    def test_3d_frame_names_include_export_language_suffix(self) -> None:
        plotter = FlightPlotter(
            PlotterConfig(
                language=Language.EN_US,
                theme=Theme.LIGHT,
                filename_suffix="EN",
            )
        )
        data = minimal_flight_data()
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            def draw_frame(path: Path, *_args, **_kwargs) -> None:
                path.write_bytes(b"frame")

            with (
                patch.object(plotter, "_draw_gif_frame", side_effect=draw_frame),
                patch.object(plotter, "_append_final_hold_frames", return_value=[]),
                patch.object(plotter, "_save_gif_with_real_timing"),
            ):
                frame_paths = list(
                    plotter.generate_attitude_motion_gif(
                        data,
                        root / "attitude_motion_EN.gif",
                        root / "gif_frames_EN",
                    )
                )

            self.assertEqual(len(frame_paths), 1)
            self.assertTrue(frame_paths[0].name.endswith("_EN.png"))


class ExportedFigureTests(unittest.TestCase):
    def _render_3d_metadata(self, language: Language) -> tuple[list[str], str, list[str]]:
        plotter = FlightPlotter(
            PlotterConfig(
                language=language,
                theme=Theme.DARK,
                filename_suffix="ZH" if language is Language.ZH_CN else "EN",
            )
        )
        captured: dict[str, object] = {}

        def capture(fig, _path: Path, *, dpi=None) -> None:
            del dpi
            plotter._style_figure(fig)
            captured["figure"] = fig

        with patch.object(plotter, "_save_figure", side_effect=capture):
            plotter._draw_gif_frame(
                Path("unused.png"),
                minimal_flight_data(),
                0.0,
                (1.0, 0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                ((-1.0, 1.0), (-1.0, 1.0), (-1.0, 1.0)),
            )

        fig = captured["figure"]
        assert hasattr(fig, "axes")
        titles = [axis.get_title() for axis in fig.axes]
        labels = [
            label
            for axis in fig.axes
            for label in (
                axis.get_xlabel(),
                axis.get_ylabel(),
                axis.get_zlabel(),
            )
        ]
        background = to_hex(fig.get_facecolor())
        plt.close(fig)
        return titles, background, labels

    def test_exported_3d_text_language_and_dark_background_switch(self) -> None:
        zh_titles, zh_background, zh_labels = self._render_3d_metadata(Language.ZH_CN)
        en_titles, en_background, en_labels = self._render_3d_metadata(Language.EN_US)

        self.assertTrue(any("姿态" in title for title in zh_titles))
        self.assertTrue(any("三维轨迹" in title for title in zh_titles))
        self.assertTrue(any("Attitude" in title for title in en_titles))
        self.assertTrue(any("3D Trajectory" in title for title in en_titles))
        self.assertTrue(any("东向" in label for label in zh_labels))
        self.assertTrue(any("East" in label for label in en_labels))
        self.assertEqual(zh_background, DARK_PLOT_COLORS.figure)
        self.assertEqual(en_background, DARK_PLOT_COLORS.figure)


if __name__ == "__main__":
    unittest.main()
