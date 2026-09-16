from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox

from app import Controller
from services.i18n import I18n
from transport.serial_backend import SerialConfig, SerialWorker, list_serial_port_names
from ui.main_window import MainWindow


@pytest.mark.parametrize("port", ["COM1", "COM9", "COM10", "COM99", "COM100"])
@pytest.mark.parametrize("font_size", [10, 16])
def test_port_text_fits_and_reaches_serial_unchanged(tmp_path, port, font_size):
    application = QApplication.instance() or QApplication([])
    for name in ("segoeui.ttf", "msyh.ttc"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    application.setFont(QFont("Microsoft YaHei", 9))
    window = MainWindow(I18n(QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)))
    try:
        # Ports commonly arrive after the first show (USB hotplug / Refresh).
        window.show()
        application.processEvents()
        with patch("transport.serial_backend.list_ports.comports",
                   return_value=[SimpleNamespace(device=port)]):
            window.set_ports(list_serial_port_names())
        font = QFont("Segoe UI", font_size)
        window.port_combo.setFont(font)
        window.resize(1280, 800)
        window.show()
        application.processEvents()
        combo = window.port_combo
        assert combo.currentText() == port
        assert window.current_port() == port
        assert combo.toolTip() == port
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        rect = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, combo,
        )
        assert rect.width() >= combo.fontMetrics().horizontalAdvance(port)
        combo.showPopup()
        application.processEvents()
        assert combo.view().viewport().width() >= combo.fontMetrics().horizontalAdvance(port)
        combo.hidePopup()
        # Execute the real Controller connection path, replacing only I/O and threads.
        controller = MagicMock()
        controller.window = window
        controller.data_migration_thread = None
        controller.worker = None
        controller.protocol_worker = None
        controller.logger.session_active = False
        controller._connection_generation = 0
        with patch("app.ProtocolWorker"):
            Controller.connect(controller)
        config = controller.link.open.call_args.args[0]
        assert isinstance(config, SerialConfig)
        assert config.port == port
        worker = SerialWorker(config)
        with patch("transport.serial_backend.serial.Serial") as serial_open:
            worker._open_serial()
        assert serial_open.call_args.kwargs["port"] == port
        # Refresh preserves a selection among other port names.
        window.set_ports(["COM2", port, "COM101"])
        assert window.current_port() == port
    finally:
        window.render_timer.stop()
        window.close()
        window.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("scale", ["1.0", "1.5", "2.0"])
@pytest.mark.parametrize("resolution", [(1280, 800), (1920, 1080)])
def test_port_and_connected_status_dpi_geometry(tmp_path, scale, resolution):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, QT_SCALE_FACTOR=scale, QT_QPA_PLATFORM="offscreen",
                       PYTHONDONTWRITEBYTECODE="1", TEMP=str(tmp_path), TMP=str(tmp_path))
    result = subprocess.run(
        [sys.executable, str(root / "tests/validation/port_geometry_probe.py"),
         str(tmp_path), *(str(value) for value in resolution)],
        cwd=root, env=environment, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
