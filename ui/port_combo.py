from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QRect, QSize
from PySide6.QtWidgets import QComboBox, QSizePolicy, QStyle, QStyleOptionComboBox

from ui.touch_scroll import TouchScroll_Enable


class PortComboBox(QComboBox):
    """Reserve a measured edit field, including the active style's arrow and padding."""

    def __init__(self) -> None:
        super().__init__()
        self._width_updating = False
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.currentTextChanged.connect(self.setToolTip)
        for signal in (self.model().rowsInserted, self.model().rowsRemoved,
                       self.model().dataChanged, self.model().modelReset):
            signal.connect(self._Width_Update)
        TouchScroll_Enable(self.view())
        self._Width_Update()

    def _Width_Update(self, *_args) -> None:
        if self._width_updating:
            return
        self._width_updating = True
        try:
            metrics = self.fontMetrics()
            texts = ["COM100", self.currentText()]
            texts.extend(self.itemText(index) for index in range(self.count()))
            margin = max(4, math.ceil(2 * self.devicePixelRatioF()))
            required = max(max(metrics.horizontalAdvance(text),
                               metrics.boundingRect(text).width()) for text in texts) + margin
            option = QStyleOptionComboBox()
            self.initStyleOption(option)
            width = self.style().sizeFromContents(
                QStyle.ContentsType.CT_ComboBox, option,
                QSize(required, metrics.height()), self,
            ).width()
            # QSS padding and native subcontrols are measured, not guessed from sizeHint.
            for _ in range(4):
                option.rect = QRect(0, 0, width, max(self.height(), metrics.height() + 8))
                field = self.style().subControlRect(
                    QStyle.ComplexControl.CC_ComboBox, option,
                    QStyle.SubControl.SC_ComboBoxEditField, self,
                )
                deficit = required - field.width()
                if deficit <= 0:
                    break
                width += deficit
            self.setMinimumWidth(width)
            self.updateGeometry()
        finally:
            self._width_updating = False

    def event(self, event) -> bool:
        result = super().event(event)
        if hasattr(self, "_width_updating") and event.type() in (
            QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange,
            QEvent.Type.StyleChange, QEvent.Type.Polish, QEvent.Type.Show,
            QEvent.Type.DevicePixelRatioChange,
        ):
            self._Width_Update()
        return result

    def showPopup(self) -> None:
        self._Width_Update()
        view = self.view()
        width = max(self.minimumWidth(), view.sizeHintForColumn(0))
        width += 2 * view.frameWidth() + self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        view.setMinimumWidth(width)
        super().showPopup()
