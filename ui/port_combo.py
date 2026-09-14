from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QComboBox, QSizePolicy

from ui.touch_scroll import TouchScroll_Enable


class PortComboBox(QComboBox):
    """Keep the font/style-derived contents width even in a crowded header."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumContentsLength(8)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.currentTextChanged.connect(self.setToolTip)
        TouchScroll_Enable(self.view())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def showPopup(self) -> None:
        self.view().setMinimumWidth(self.sizeHint().width())
        super().showPopup()
