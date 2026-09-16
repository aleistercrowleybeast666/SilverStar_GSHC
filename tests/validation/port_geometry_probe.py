"""Isolated Qt process: style edit rect, popup, connected status and screenshots."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QSettings, Qt, QRect
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox, QStyleOptionSpinBox
from services.i18n import I18n
from ui.main_window import MainWindow

def Probe_Run(output: Path, width: int, height: int, baseline: bool = False) -> None:
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    for filename in ("segoeui.ttf", "msyh.ttc"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Microsoft YaHei", 16))
    window_type = MainWindow
    if baseline:
        import subprocess
        import types
        legacy_port = types.ModuleType("legacy_port")
        exec(subprocess.check_output(["git", "show", "HEAD:ui/port_combo.py"], text=True), legacy_port.__dict__)
        legacy_window = types.ModuleType("legacy_window")
        sys.modules[legacy_window.__name__] = legacy_window
        exec(subprocess.check_output(["git", "show", "HEAD:ui/main_window.py"], text=True, encoding="utf-8"), legacy_window.__dict__)
        legacy_window.PortComboBox = legacy_port.PortComboBox
        window_type = legacy_window.MainWindow
    window = window_type(I18n(QSettings(str(output / "ui.ini"), QSettings.Format.IniFormat)))
    window.resize(width, height)
    window.show()
    window.port_combo.setFont(QFont("Segoe UI", 16))
    window.conn_label.setFont(QFont("Segoe UI", 16))
    rows = []
    for port in ("COM1", "COM9", "COM10", "COM99", "COM100"):
        window.set_ports([port])
        status = port + "@230400"
        window.set_connection_status(status)
        app.processEvents()
        if not baseline:
            spin = window.baud_spin
            spin_option = QStyleOptionSpinBox()
            spin.initStyleOption(spin_option)
            spin_field = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,
                spin_option, QStyle.SubControl.SC_SpinBoxEditField, spin)
            assert spin_field.width() >= spin.fontMetrics().horizontalAdvance(spin.text())
        combo = window.port_combo
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        field = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,
                    option, QStyle.SubControl.SC_ComboBoxEditField, combo)
        metrics = combo.fontMetrics()
        required = max(metrics.horizontalAdvance(port), metrics.boundingRect(port).width())
        if not baseline:
            assert field.width() >= required, (port, field.width(), required)
        if not baseline:
            assert combo.minimumWidth() > 0
        combo.showPopup()
        app.processEvents()
        popup = combo.view().viewport().width()
        if not baseline:
            assert popup >= required
        if port == "COM100":
            combo.view().window().grab().save(str(output / "popup.png"))
        combo.hidePopup()
        label = window.conn_label
        # QLabel contents minus its documented margin/indent, including word wrap.
        available = label.contentsRect().adjusted(label.margin(), label.margin(),
                                                   -label.margin(), -label.margin())
        indent = label.indent()
        if indent < 0:
            indent = label.fontMetrics().horizontalAdvance("x") // 2 if label.frameWidth() else 0
        available.adjust(indent, 0, 0, 0)
        bounds = label.fontMetrics().boundingRect(QRect(0, 0, available.width(), 10000),
                    int(label.alignment()) | (int(Qt.TextFlag.TextWordWrap) if label.wordWrap() else 0), status)
        if not baseline:
            assert bounds.width() <= available.width(), (status, bounds, available)
            assert bounds.height() <= available.height(), (status, bounds, available)
        assert label.text() == status
        rows.append(dict(port=port, required=required, edit=field.width(), popup=popup,
                         status_width=available.width(), status_height=available.height(),
                         text_width=bounds.width(), text_height=bounds.height()))
    window.grab().save(str(output / "window.png"))
    (output / "geometry.json").write_text(json.dumps(dict(scale=window.devicePixelRatioF(),
        requested=[width,height], actual=[window.width(),window.height()], rows=rows), indent=2))
    window.render_timer.stop()
    window.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("output", type=Path)
    parser.add_argument("width", type=int)
    parser.add_argument("height", type=int)
    args = parser.parse_args()
    Probe_Run(args.output, args.width, args.height, args.baseline)
