"""Render the calibration capability matrix without a serial connection."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from protocol.air import AirCapabilityMessage
from services.i18n import I18n, Language
from services.state_model import EventHistory, FlightControllerState
from ui.main_window import MainWindow


def main() -> None:
    root = Path(__file__).resolve().parents[2] / "build/final_alignment_validation"
    root.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    # Qt offscreen may omit the Windows font registry. Load local fonts only
    # for this smoke renderer; production application preferences stay intact.
    for name in ("msyh.ttc", "consola.ttf", "segoeui.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    application.setFont(QFont("Microsoft YaHei", 9))
    with TemporaryDirectory() as settings_dir:
        window = MainWindow(I18n(QSettings(str(Path(settings_dir) / "ui.ini"), QSettings.IniFormat)))
        # Offscreen has no OpenGL surface; verify the unchanged scene via GUI tests.
        window.gl_view.hide()
        window.show()
        report = []
        for language in (Language.ZH_CN, Language.EN_US):
            window.i18n.set_language(language)
            window.retranslate_ui()
            for generation, mask in enumerate((1, 3, 5, 7), start=1):
                state = FlightControllerState(session_generation=generation, connected=True)
                state.capability = AirCapabilityMessage(42, 0, 1, mask, 15, 16, 2000)
                state.capability_acked = True
                state.calibration.mode = 0
                state.calibration.state = 4
                state.calibration.ready = True
                window.bind_runtime_model(state, EventHistory())
                application.processEvents()
                combo = window.calibration_dialog.mode_combo
                modes = [combo.itemData(i) for i in range(combo.count())]
                assert modes == ([] if mask == 1 else [0, *[mode for mode in (1, 2) if mask & (1 << mode)]])
                assert window.btn_cal_reset.isEnabled()
                group = window.lbl_cal_mode.parentWidget()
                path = root / f"calibration_{mask:02x}_{language.value}.png"
                assert group.grab().save(str(path))
                report.append({
                    "mask": mask, "language": language.value, "modes": modes,
                    "start_enabled": window.btn_calibration.isEnabled(),
                    "reset_enabled": window.btn_cal_reset.isEnabled(),
                    "mode": window.lbl_cal_mode.text(), "ready": window.lbl_cal_ready.text(),
                    "screenshot": str(path),
                })
            window._show_calibration_dialog()
            application.processEvents()
            assert window.calibration_dialog.grab().save(str(root / f"calibration_dialog_{language.value}.png"))
            window.calibration_dialog.close()
        QTimer.singleShot(150, application.quit)
        assert application.exec() == 0
        window.render_timer.stop()
        window.close()
        (root / "headless_gui.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Headless GUI smoke passed: 8 mask/language cases, dialog and event loop")


if __name__ == "__main__":
    main()
