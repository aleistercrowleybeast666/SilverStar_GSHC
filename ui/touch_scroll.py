from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QPlainTextEdit,
    QScrollArea,
    QScroller,
    QTextEdit,
    QWidget,
)


def TouchScroll_Enable(widget: QWidget) -> None:
    """Register only ordinary content viewports; leave mouse and graphics gestures alone."""
    if isinstance(widget, QHeaderView) or not isinstance(
        widget, (QScrollArea, QAbstractItemView, QPlainTextEdit, QTextEdit)
    ):
        return
    QScroller.grabGesture(widget.viewport(), QScroller.ScrollerGestureType.TouchGesture)


def TouchScroll_Wrap(content: QWidget) -> QScrollArea:
    """Make a form scrollable without changing the widgets inside it."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    TouchScroll_Enable(scroll)
    return scroll
