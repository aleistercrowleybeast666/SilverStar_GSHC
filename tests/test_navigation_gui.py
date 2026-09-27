from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel
from test_ui_workflow import ready_state

from services.i18n import I18n, Language
from services.preferences import Theme
from services.state_model import EventHistory
from ui.main_window import MainWindow


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("theme", list(Theme))
def test_navigation_live_gui_languages_themes_and_start(tmp_path, language, theme):
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "segoeui.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Microsoft YaHei", 9))
    i18n = I18n(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    i18n.set_language(language)
    window = MainWindow(i18n)
    window.resize(1000, 700)
    window.gl_view.hide()
    state = ready_state()
    state.navigation.algorithm_id = 2
    state.navigation.group_mask = 31
    for group in range(5):
        state.navigation.Navigation_ApplyMetric(42, 1, 10 + group, group, 0x22 if group == 0 else 0x11, time.monotonic_ns())
        state.navigation.Navigation_ApplyMetric(42, 1, 20 + group, 8 + group, 3, time.monotonic_ns())
    window.bind_runtime_model(state, EventHistory())
    window.theme_combo.setCurrentIndex(window.theme_combo.findData(theme.value))
    window.show()
    app.processEvents()
    # Theme construction can exceed the real 2 s freshness budget on CI.
    # Simulate the next actual on-board update; never extend the production TTL.
    state.navigation.Navigation_ApplyPreparation(replace(
        state.navigation.preparation, snapshot=2, received_ns=time.monotonic_ns()))
    for group in range(5):
        state.navigation.Navigation_ApplyMetric(42, 1, 30 + group, group, 0x22 if group == 0 else 0x11, time.monotonic_ns())
        state.navigation.Navigation_ApplyMetric(42, 1, 40 + group, 8 + group, 3, time.monotonic_ns())
        for metric in range(2, 9):
            value = 0x0400 if metric == 2 else 10  # R scale is Q8.8: 4.0.
            state.navigation.Navigation_ApplyMetric(42, 1, 50 + metric, metric * 8 + group, value, time.monotonic_ns())
    for metric, value in {0: 4, 1: 125, 2: 250, 3: 50, 4: 1, 5: 0xF03, 6: 6,
                          7: 0, 8: 5, 9: 2000, 10: 7, 11: 17, 12: 5, 13: 12,
                          14: 0, 15: 0, 16: 1600}.items():
        state.navigation.Navigation_ApplyMetric(42, 1, 60 + metric, metric * 8 + 7, value, time.monotonic_ns())
    window.render_state()
    assert window.btn_start.isEnabled()
    assert "ESKF_15" in window.navigation_preparation_panel.summary.text()
    assert len(window.navigation_preparation_panel.steps) == 9
    window.navigation_health_panel.toggle.setChecked(True)
    assert window.navigation_health_panel.group.count() == 5
    assert i18n.tr("navigation.result.2") in window.navigation_health_panel.health.text()
    assert i18n.tr("navigation.imu_flag.1") in window.navigation_health_panel.unavailable.text()
    assert "1.25/2.50" in window.navigation_health_panel.unavailable.text()
    assert "4.00" in window.navigation_health_panel.health.text()
    window.render_timer.stop()
    root = Path(__file__).parent / "joint_rework_20260927" / ("gui-fit-hidpi" if os.environ.get("QT_SCALE_FACTOR") == "2" else "gui-fit")
    root.mkdir(parents=True, exist_ok=True)
    # resize() alone does not prove the layout accepted the requested geometry.
    window.resize(1000, 700)
    app.processEvents()
    assert window.size() == QSize(1000, 700)
    captures = []
    for page_name, page in (("preflight", window.preflight_page), ("flight", window.flight_page)):
        window.pages.setCurrentWidget(page)
        app.processEvents()
        assert window.size() == QSize(1000, 700)
        if page_name == "preflight":
            assert page.widget().width() <= page.viewport().width()
            assert window.navigation_preparation_panel.width() <= page.viewport().width()
            for label in window.navigation_preparation_panel.steps.values():
                assert label.height() >= label.heightForWidth(label.width())
        else:
            assert window.flight_status_scroll.widget().width() <= window.flight_status_scroll.viewport().width()
        frame = window.grab()
        dpr = window.devicePixelRatioF()
        assert frame.size() == QSize(round(1000 * dpr), round(700 * dpr))
        if os.environ.get("QT_SCALE_FACTOR") == "2":
            assert dpr == 2.0
            assert frame.size() == QSize(2000, 1400)
        filename = f"{page_name}_window_{language.value}_{theme.value}.png"
        assert frame.save(str(root / filename))
        captures.append({"page": page_name, "logical_size": [window.width(), window.height()],
                         "pixel_size": [frame.width(), frame.height()], "dpr": dpr, "file": filename})
    (root / f"geometry_{language.value}_{theme.value}.json").write_text(
        json.dumps(captures, indent=2) + "\n", encoding="utf-8")
    mission = window.lbl_mission_state.parentWidget()
    window.flight_status_scroll.ensureWidgetVisible(mission)
    app.processEvents()
    for label in mission.findChildren(QLabel):
        assert label.wordWrap()
        assert label.geometry().left() >= 0
        assert label.geometry().right() < mission.width()
        assert label.height() >= label.heightForWidth(label.width())
    assert mission.grab().save(str(root / f"mission_{language.value}_{theme.value}.png"))
    assert window.grab().save(str(root / f"mission_window_{language.value}_{theme.value}.png"))
    window.flight_status_scroll.verticalScrollBar().setValue(0)
    window.pages.setCurrentWidget(window.flight_page)
    app.processEvents()
    assert window.navigation_health_panel.health.geometry().top() >= window.navigation_health_panel.group.geometry().bottom()
    assert window.navigation_health_panel.grab().save(str(root / f"health_{language.value}_{theme.value}.png"))
    scroll = window.navigation_health_panel.details.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    window.flight_status_scroll.ensureWidgetVisible(window.navigation_health_panel.details)
    app.processEvents()
    assert window.size() == QSize(1000, 700)
    assert window.grab().save(str(root / f"health_details_window_{language.value}_{theme.value}.png"))
    assert window.navigation_health_panel.grab().save(str(root / f"health_details_{language.value}_{theme.value}.png"))
    state.navigation.Navigation_Invalidate()
    window.render_state()
    assert not window.btn_start.isEnabled()
    assert i18n.tr("navigation.WAITING") in window.navigation_preparation_panel.summary.text()
    window.render_timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()
