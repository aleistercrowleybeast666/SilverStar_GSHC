from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from services.preferences import Theme


@dataclass(frozen=True)
class ThemeColors:
    window: str
    panel: str
    base: str
    alternate: str
    text: str
    muted: str
    disabled: str
    border: str
    button: str
    button_hover: str
    highlight: str
    highlighted_text: str
    grid: str
    success: str
    warning: str
    error: str
    plot_curve: str
    mesh_edge: str
    header: str
    header_hover: str
    header_selected: str
    header_text: str
    header_muted: str
    header_control: str
    header_control_text: str
    header_control_border: str


LIGHT_COLORS = ThemeColors(
    window="#f4f6f8",
    panel="#ffffff",
    base="#ffffff",
    alternate="#eef2f6",
    text="#1f2933",
    muted="#66717f",
    disabled="#8a95a3",
    border="#b7c0cc",
    button="#ffffff",
    button_hover="#e8f1fb",
    highlight="#1769aa",
    highlighted_text="#ffffff",
    grid="#7d8996",
    success="#087f46",
    warning="#9a6700",
    error="#c62828",
    plot_curve="#1769aa",
    mesh_edge="#334155",
    header="#123a78",
    header_hover="#245ba0",
    header_selected="#2f6fed",
    header_text="#f8fafc",
    header_muted="#dbeafe",
    header_control="#f8fafc",
    header_control_text="#111827",
    header_control_border="#8fb1df",
)


DARK_COLORS = ThemeColors(
    window="#181b20",
    panel="#20242b",
    base="#111318",
    alternate="#272c35",
    text="#e6e9ef",
    muted="#a7b0bd",
    disabled="#747d89",
    border="#46505f",
    button="#2a3039",
    button_hover="#35404d",
    highlight="#4da3ff",
    highlighted_text="#071019",
    grid="#7c8794",
    success="#4bd58b",
    warning="#f1c75b",
    error="#ff6b6b",
    plot_curve="#65b5ff",
    mesh_edge="#94a3b8",
    header="#0b2447",
    header_hover="#163b6c",
    header_selected="#3b82f6",
    header_text="#f8fafc",
    header_muted="#cbd5e1",
    header_control="#163b6c",
    header_control_text="#f8fafc",
    header_control_border="#4f6f99",
)


def theme_colors(theme: Theme) -> ThemeColors:
    return DARK_COLORS if theme is Theme.DARK else LIGHT_COLORS


def apply_application_theme(application: QApplication, theme: Theme) -> ThemeColors:
    colors = theme_colors(theme)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors.window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors.base))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.alternate))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.panel))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors.button))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(colors.error))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors.highlight))
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor(colors.highlighted_text),
    )
    for group in (QPalette.ColorGroup.Disabled, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.Text, QColor(colors.disabled))
        palette.setColor(group, QPalette.ColorRole.ButtonText, QColor(colors.disabled))
        palette.setColor(group, QPalette.ColorRole.WindowText, QColor(colors.disabled))
    application.setPalette(palette)
    application.setStyleSheet(
        f"""
        QMainWindow, QDialog {{
            background-color: {colors.window};
            color: {colors.text};
        }}
        QGroupBox {{
            background-color: {colors.panel};
            border: 1px solid {colors.border};
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }}
        QPushButton {{
            background-color: {colors.button};
            border: 1px solid {colors.border};
            border-radius: 4px;
            padding: 5px 9px;
        }}
        QPushButton:hover {{ background-color: {colors.button_hover}; }}
        QPushButton:pressed {{ background-color: {colors.alternate}; }}
        QPushButton:checked {{
            background-color: {colors.highlight};
            color: {colors.highlighted_text};
        }}
        QPushButton:disabled {{
            background-color: {colors.alternate};
            color: {colors.disabled};
            border-color: {colors.border};
        }}
        QPushButton#cancelTaskButton {{
            color: {colors.error};
            border-color: {colors.error};
            font-weight: 600;
        }}
        QPushButton#cancelTaskButton:disabled {{
            color: {colors.disabled};
            border-color: {colors.border};
        }}
        QProgressBar {{
            background-color: {colors.base};
            color: {colors.text};
            border: 1px solid {colors.border};
            border-radius: 6px;
            text-align: center;
            font-weight: 600;
        }}
        QProgressBar::chunk {{
            background-color: {colors.highlight};
            border-radius: 5px;
        }}
        QComboBox, QSpinBox, QPlainTextEdit, QListWidget {{
            background-color: {colors.base};
            color: {colors.text};
            border: 1px solid {colors.border};
            border-radius: 3px;
            selection-background-color: {colors.highlight};
            selection-color: {colors.highlighted_text};
        }}
        QComboBox QAbstractItemView {{
            background-color: {colors.base};
            color: {colors.text};
            selection-background-color: {colors.highlight};
            selection-color: {colors.highlighted_text};
        }}
        QTabWidget::pane {{ border: 1px solid {colors.border}; }}
        QTabBar::tab {{
            background: {colors.alternate};
            border: 1px solid {colors.border};
            padding: 7px 14px;
        }}
        QTabBar::tab:selected {{
            background: {colors.panel};
            border-bottom-color: {colors.panel};
        }}
        QWidget#headerBar {{
            background-color: {colors.header};
            border: 0;
        }}
        QWidget#headerBar QLabel {{
            background: transparent;
            color: {colors.header_text};
        }}
        QLabel#headerTitle {{
            color: {colors.header_text};
            font-size: 16px;
            font-weight: 700;
            padding-right: 8px;
        }}
        QLabel#headerSection {{
            color: {colors.header_muted};
            font-weight: 600;
            padding-right: 4px;
        }}
        QLabel#headerCredit, QLabel#headerVersion {{
            color: {colors.header_muted};
            font-weight: 600;
            padding-left: 4px;
        }}
        QWidget#headerBar QComboBox, QWidget#headerBar QSpinBox {{
            background-color: {colors.header_control};
            color: {colors.header_control_text};
            border: 1px solid {colors.header_control_border};
            border-radius: 3px;
            min-height: 24px;
            padding: 1px 5px;
            selection-background-color: {colors.header_selected};
            selection-color: #ffffff;
        }}
        QWidget#headerBar QPushButton {{
            background-color: {colors.header};
            color: {colors.header_text};
            border: 1px solid {colors.header_control_border};
            border-radius: 3px;
            padding: 5px 9px;
        }}
        QWidget#headerBar QPushButton:hover {{
            background-color: {colors.header_hover};
        }}
        QWidget#headerBar QPushButton:pressed {{
            background-color: {colors.header_selected};
        }}
        QWidget#headerBar QPushButton:disabled {{
            background-color: {colors.header};
            color: {colors.header_muted};
            border-color: {colors.header_hover};
        }}
        QTabWidget#pageTabs::pane {{
            background: {colors.window};
            border: 0;
        }}
        QTabBar#pageNavigation {{
            background: {colors.header};
        }}
        QTabBar#pageNavigation::tab {{
            background: {colors.header};
            color: {colors.header_muted};
            border: 0;
            min-height: 24px;
            padding: 9px 20px;
            font-size: 14px;
            font-weight: 600;
        }}
        QTabBar#pageNavigation::tab:hover {{
            background: {colors.header_hover};
            color: {colors.header_text};
        }}
        QTabBar#pageNavigation::tab:selected {{
            background: {colors.header_selected};
            color: #ffffff;
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {colors.alternate};
            border: none;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {colors.muted};
            border-radius: 4px;
            min-height: 24px;
            min-width: 24px;
        }}
        QToolTip {{
            background-color: {colors.panel};
            color: {colors.text};
            border: 1px solid {colors.border};
        }}
        """
    )
    return colors


__all__ = [
    "DARK_COLORS",
    "LIGHT_COLORS",
    "ThemeColors",
    "apply_application_theme",
    "theme_colors",
]
