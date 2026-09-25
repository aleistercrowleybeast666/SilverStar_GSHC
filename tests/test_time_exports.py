from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import matplotlib.pyplot as plt
import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from processing.flight_log_processor import FlightData, FlightLogProcessor, ProcessingCancelledError, TimedVector, main as Processor_Main
from processing.flight_plotter import FlightPlotter, PlotterConfig
from processing.time_ranges import GifPlan_Build, Pages_Build
from services.i18n import I18n, Language
from services.preferences import (
    AppPreferences, ExportItem, ExportTheme, ResolvedExportOptions, Theme,
    resolve_export_theme,
)
from ui.main_window import ExportOptionsDialog, MainWindow


def Data_Build(seconds: float) -> FlightData:
    data = FlightData(0, None, int(seconds * 500), int(seconds * 1000), Path("input.jsonl"))
    data.pos = [TimedVector(float(t), (float(t), 0.0, float(t)), int(t * 1000)) for t in np.arange(0, seconds + 0.1, 0.2)]
    data.vel = [TimedVector(float(t), (1.0, 0.0, 1.0), int(t * 1000)) for t in np.arange(0, seconds + 0.1, 0.2)]
    data.quat = [TimedVector(0.0, (1, 0, 0, 0), 0), TimedVector(seconds, (1, 0, 0, 0), int(seconds * 1000))]
    return data


@pytest.mark.parametrize("seconds,pages,motion,speed", [(10, 1, 300, 1), (30, 1, 900, 1), (31, 2, 900, 31 / 30), (60, 2, 900, 2), (300, 10, 900, 10)])
def test_constant_speed_and_real_gif_hold(tmp_path, seconds, pages, motion, speed):
    data = Data_Build(seconds)
    plan = GifPlan_Build(seconds)
    assert len(list(Pages_Build(seconds))) == pages
    assert plan.motion_frames == motion and plan.speed_factor == speed
    times = np.asarray(plan.Times_Get())
    np.testing.assert_allclose(np.diff(times), speed / 30, atol=1e-12)
    plotter = FlightPlotter(PlotterConfig())
    seen = []
    limits_seen = []
    encoded = []
    plotter.encoding_progress = lambda: encoded.append(1)

    def draw(_renderer, _data, t):
        seen.append(t)
        return Image.new("RGB", (8, 8), (int(t * 10) % 256, 100, 200)).quantize()

    target = tmp_path / "GIF" / "attitude_EN.gif"
    target.parent.mkdir()
    with patch.object(plotter, "_GifFrame_Render", side_effect=draw):
        paths = list(plotter.generate_attitude_motion_gif(data, target, target.parent / "frames"))
    assert len(paths) == motion + 30 == len(encoded)
    assert seen[-1] == seconds
    assert not (target.parent / "frames").exists()
    assert list(target.parent.rglob("*.png")) == []
    np.testing.assert_array_equal(seen[:-1], times)
    metadata = json.loads(target.with_suffix(".json").read_text())
    assert metadata["fps"] == 30 and metadata["hold_frames"] == 30
    assert metadata["source_range_s"] == [0, seconds]
    with Image.open(target) as gif:
        assert gif.n_frames == motion + 30  # Encoded physical frames, not only planned frames.
        durations = []
        hold = []
        for index in range(gif.n_frames):
            gif.seek(index)
            durations.append(gif.info["duration"])
            if index >= motion:
                hold.append(gif.convert("RGB").tobytes())
        assert set(durations) == {30, 40}
        assert sum(durations[motion:]) == 1000
        assert len(set(hold)) == 1
        assert sum(durations) == (motion / 30 + 1) * 1000


def test_gif_clock_uses_source_mission_time():
    data = Data_Build(120)
    plotter = FlightPlotter(PlotterConfig())
    plan = GifPlan_Build(data.duration_s)
    source_time = plan.Times_Get()[450]  # 15 s of playback represents 60 s of flight.
    assert source_time == 60
    renderer = plotter._GifRenderer_Create(data)
    try:
        frame = plotter._GifFrame_Render(renderer, data, source_time)
        frame.close()
        assert renderer["clock"].get_text() == plotter.text("time_value", time=60)
        final_frame = plotter._GifFrame_Render(renderer, data, plan.source_end_s)
        final_frame.close()
        assert renderer["clock"].get_text() == plotter.text("time_value", time=120)
    finally:
        plt.close(renderer["figure"])


def test_defaults_full_custom_and_invalid():
    assert GifPlan_Build(300).source_end_s == 300
    assert GifPlan_Build(300, 30).source_end_s == 30
    assert PlotterConfig().gif_source_duration_s is None
    assert ResolvedExportOptions().gif_source_duration_s is None
    assert list(Pages_Build(61)) == [(0, 30), (30, 60), (60, 61)]
    assert list(Pages_Build(61, None)) == [(0, 61)]
    assert list(Pages_Build(61, 20))[-1] == (60, 61)
    for value in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ResolvedExportOptions(page_duration_s=value)
        with pytest.raises(ValueError):
            ResolvedExportOptions(gif_source_duration_s=value)


@pytest.mark.parametrize("args,expected", [((), None), (("--gif-source-seconds", "30"), 30)])
def test_cli_gif_source_default_and_explicit_range(args, expected):
    with patch("sys.argv", ["flight_log_processor", "input.jsonl", *args]):
        with patch.object(FlightLogProcessor, "process_file", autospec=True, return_value=Path("output")) as process:
            Processor_Main()
    processor = process.call_args.args[0]
    assert processor.export_options.gif_source_duration_s == expected


def test_real_61s_png_pages_and_full_text(tmp_path):
    data = Data_Build(61)
    options = ResolvedExportOptions(items=frozenset({ExportItem.CHARTS, ExportItem.PROCESSED_DATA, ExportItem.SESSION_INFO}))
    processor = FlightLogProcessor(tmp_path, export_options=options)
    progress = []
    with patch.object(processor, "_read_jsonl", return_value=[]), patch.object(processor, "_extract_flight_data", return_value=data):
        output = processor.process_file(data.source_log, progress=lambda *v: progress.append(v))
    assert not processor.last_export_errors
    files = list(output.rglob("*.png"))
    assert len(files) == 21
    assert {p.parent.name for p in files} == {"Position", "Velocity", "Attitude", "Sensors", "Diagnostics"}
    for category in ("Position", "Velocity"):
        assert len(list((output / category).glob("*.png"))) == 3
    assert any("000060.000-000061.000" in p.name for p in files)
    assert "61.000" in (output / "processed_data_EN.txt").read_text()
    manifest = json.loads((output / "manifest_EN.json").read_text())
    assert manifest["export"]["pages_s"] == [[0, 30], [30, 60], [60, 61]]
    assert manifest["export"]["gif"]["source_range_s"] == [0, 61]
    assert manifest["export"]["theme_mode"] == "follow_ui"
    assert manifest["export"]["resolved_theme"] == "light"
    assert progress[-1][0] == progress[-1][1] == 23
    assert data.duration_s == 61 and data.pos[-1].time_s == 61


def test_gif_cancel_during_encoding_removes_only_current_output(tmp_path):
    keep = tmp_path / "existing"; keep.mkdir(); (keep / "keep.txt").write_text("keep")
    data = Data_Build(1)
    processor = FlightLogProcessor(tmp_path, export_options=ResolvedExportOptions(items=frozenset({ExportItem.ATTITUDE_3D})))
    state = {"cancel": False}
    def progress(_done, _total, name):
        if name == "GIF encode":
            state["cancel"] = True
    def draw(*_args):
        return Image.new("RGB", (2, 2), "blue").quantize()
    with patch.object(processor, "_read_jsonl", return_value=[]), patch.object(processor, "_extract_flight_data", return_value=data), patch.object(FlightPlotter, "_GifFrame_Render", side_effect=draw):
        with pytest.raises(ProcessingCancelledError):
            processor.process_file(data.source_log, progress=progress, cancel_requested=lambda: state["cancel"])
    assert list(tmp_path.iterdir()) == [keep]


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("theme", list(Theme))
def test_export_dialog_range_controls_1000_700(qtbot, tmp_path, language, theme):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    QApplication.setFont(QFont("Microsoft YaHei", 9))
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    i18n = I18n(settings); i18n.set_language(language)
    window = MainWindow(i18n)
    qtbot.addWidget(window)
    window._apply_theme(theme, persist=False)
    window.resize(1000, 700)
    window.gl_view.hide()
    window.show()
    dialog = window.export_options_dialog
    dialog.resize(680, 640)
    dialog.show()
    qtbot.wait(40)
    assert dialog._Range_Value("page") == 30
    assert dialog._Range_Value("gif") is None
    for kind in ("page", "gif"):
        combo = dialog.range_combos[kind]
        assert combo.count() == 7
        combo.setCurrentIndex(combo.findData("custom"))
        spin = dialog.range_custom[kind]
        assert spin.isVisible() and spin.isEnabled()
        spin.setValue(61)
        assert dialog._Range_Value(kind) == 61
        assert combo.geometry().right() < combo.parentWidget().width()
        assert spin.geometry().right() < spin.parentWidget().width()
    options = dialog.resolved_options(language, theme)
    assert options.page_duration_s == options.gif_source_duration_s == 61
    dialog.grab().save(str(tmp_path / f"export_{language.value}_{theme.value}.png"))
    window.grab().save(str(tmp_path / f"main_{language.value}_{theme.value}.png"))
    dialog.close()


@pytest.mark.parametrize("ui_theme,mode,expected", [
    (Theme.LIGHT, ExportTheme.FOLLOW_UI, Theme.LIGHT),
    (Theme.DARK, ExportTheme.FOLLOW_UI, Theme.DARK),
    (Theme.DARK, ExportTheme.LIGHT, Theme.LIGHT),
    (Theme.LIGHT, ExportTheme.DARK, Theme.DARK),
])
def test_export_theme_resolution_and_persisted_mode(qtbot, tmp_path, ui_theme, mode, expected):
    settings = QSettings(str(tmp_path / "theme.ini"), QSettings.Format.IniFormat)
    preferences = AppPreferences(settings)
    preferences.set_export_theme(mode)
    assert preferences.export_theme() is mode
    assert resolve_export_theme(mode, ui_theme) is expected
    dialog = ExportOptionsDialog(I18n(settings), preferences)
    qtbot.addWidget(dialog)
    assert dialog.export_theme_combo.currentData() == mode.value
    options = dialog.resolved_options(Language.EN_US, ui_theme)
    assert options.theme_mode is mode
    assert options.theme is expected
    dialog.close()
