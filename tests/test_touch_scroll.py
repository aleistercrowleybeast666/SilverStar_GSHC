from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QComboBox, QGraphicsView, QHeaderView,
    QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QScroller,
    QSlider, QSpinBox, QTableWidget, QTextEdit, QTreeWidget,
)

from ui.touch_scroll import TouchScroll_Enable


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("factory", [
    QScrollArea, QListWidget, QTableWidget, QTreeWidget, QPlainTextEdit, QTextEdit,
])
def test_native_gesture_targets_only_the_content_viewport(application, factory):
    widget = factory()
    if isinstance(widget, QAbstractItemView):
        widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    with patch.object(QScroller, "grabGesture", wraps=QScroller.grabGesture) as grab:
        TouchScroll_Enable(widget)
        grab.assert_called_once_with(widget.viewport(), QScroller.ScrollerGestureType.TouchGesture)
    assert QScroller.hasScroller(widget.viewport())
    assert QScroller.grabbedGesture(widget.viewport()).value >= Qt.GestureType.CustomGesture.value
    assert not QScroller.hasScroller(widget)
    if isinstance(widget, QAbstractItemView):
        assert widget.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    widget.close()


@pytest.mark.parametrize("factory", [
    QPushButton, QComboBox, QSpinBox, QSlider, QGraphicsView,
    lambda: QHeaderView(Qt.Orientation.Horizontal),
])
def test_specialized_controls_are_not_page_gesture_targets(application, factory):
    widget = factory()
    TouchScroll_Enable(widget)
    assert not QScroller.hasScroller(widget)
    if isinstance(widget, (QGraphicsView, QHeaderView)):
        assert not QScroller.hasScroller(widget.viewport())
    widget.close()


def test_list_click_selection_survives_touch_registration(application):
    widget = QListWidget()
    widget.addItems([str(row) for row in range(100)])
    TouchScroll_Enable(widget)
    widget.show()
    application.processEvents()
    QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton,
                     pos=widget.visualItemRect(widget.item(2)).center())
    assert widget.currentRow() == 2
    widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())
    assert widget.verticalScrollBar().value() > 0
    widget.close()


def test_window_scroll_coverage_and_graphics_exclusions(application, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from services.i18n import I18n
    from ui.main_window import MainWindow
    window = MainWindow(I18n(QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)))
    try:
        window.show()
        application.processEvents()
        areas = window.findChildren(QScrollArea)
        assert areas
        for area in areas:
            assert QScroller.hasScroller(area.viewport())
        for cls in (QListWidget, QTableWidget, QTreeWidget, QPlainTextEdit, QTextEdit):
            for widget in window.findChildren(cls):
                assert QScroller.hasScroller(widget.viewport()), widget.objectName()
        for cls in (QPushButton, QSpinBox, QSlider, QComboBox, QGraphicsView):
            for widget in window.findChildren(cls):
                assert not QScroller.hasScroller(widget), widget.objectName()
                if isinstance(widget, QGraphicsView):
                    assert not QScroller.hasScroller(widget.viewport())
                    assert not any(area.isAncestorOf(widget) for area in areas)
        for widget in window.findChildren(QSlider):
            assert not any(area.isAncestorOf(widget) for area in areas)
        # pyqtgraph GLViewWidget is a QWidget, not a graphics/scroll view.
        from PySide6.QtWidgets import QWidget
        for widget in window.findChildren(QWidget):
            if any(cls.__name__ == "GLViewWidget" for cls in type(widget).__mro__):
                assert not QScroller.hasScroller(widget)
                assert not any(area.isAncestorOf(widget) for area in areas)
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("kind", ["page", "list", "table", "text"])
def test_native_finger_drag_scrolls_content(application, kind):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    if kind == "page":
        widget = QScrollArea()
        content = QWidget()
        content.setMinimumSize(200, 2000)
        widget.setWidget(content)
    elif kind == "list":
        widget = QListWidget()
        widget.addItems([str(row) for row in range(100)])
    elif kind == "table":
        widget = QTableWidget(100, 2)
    else:
        widget = QPlainTextEdit()
        widget.setReadOnly(True)
        widget.setPlainText("\n".join(str(row) for row in range(100)))
    TouchScroll_Enable(widget)
    widget.resize(260, 260)
    widget.show()
    application.processEvents()
    viewport = widget.viewport()
    device = QTest.createTouchDevice()
    QTest.touchEvent(viewport, device).press(0, QPoint(100, 180), viewport).commit()
    QTest.qWait(50)
    for y in (150, 120, 90, 60):
        QTest.touchEvent(viewport, device).move(0, QPoint(100, y), viewport).commit()
        QTest.qWait(50)
    QTest.touchEvent(viewport, device).release(0, QPoint(100, 60), viewport).commit()
    QTest.qWait(100)
    assert widget.verticalScrollBar().value() > 0
    QScroller.scroller(viewport).stop()
    widget.close()
