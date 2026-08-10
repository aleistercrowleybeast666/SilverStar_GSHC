from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QVector3D
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import PLOT_REFRESH_INTERVAL_MS, PLOT_WINDOW_SECONDS
from protocol.common import (
    AirAckResult,
    AirAlignmentCapability,
    AirAlignmentState,
    AirCalibrationDiagnosticReason,
    AirCalibrationMode,
    AirCalibrationModeMask,
    AirCalibrationState,
    AirLifecycleState,
    AirStatusId,
    GspAckResult,
    enum_name,
)
from services.i18n import I18n, Language
from services.state_model import (
    EventHistory,
    FlightControllerState,
    HandshakeState,
    MissionPhase,
)


def calibration_diagnostic_text(i18n: I18n, state: FlightControllerState) -> str:
    reason_value = state.latest_calibration_diagnostic_reason
    if reason_value == int(AirCalibrationDiagnosticReason.NONE):
        return i18n.tr("common.none")
    reason_name = enum_name(AirCalibrationDiagnosticReason, reason_value)
    reason_text = i18n.enum("calibration_diagnostic_reason", reason_name)
    face = state.latest_calibration_diagnostic_face
    if (
        state.calibration.mode == int(AirCalibrationMode.SIX_FACE)
        and 0 <= face < len(CalibrationDialog.FACE_NAMES)
    ):
        return i18n.tr(
            "diagnostic.face_issue",
            face=CalibrationDialog.FACE_NAMES[face],
            reason=reason_text,
        )
    return reason_text


class AttitudeGLViewWidget(gl.GLViewWidget):
    """3D attitude view that can lock mouse-driven camera movement."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._camera_locked = True

    def set_camera_locked(self, locked: bool) -> None:
        self._camera_locked = bool(locked)

    def camera_locked(self) -> bool:
        return self._camera_locked

    def mouseMoveEvent(self, event) -> None:
        if self._camera_locked:
            event.accept()
            return
        super().mouseMoveEvent(event)


class CalibrationDialog(QDialog):
    FACE_NAMES = ("X+", "X-", "Y+", "Y-", "Z+", "Z-")

    def __init__(self, i18n: I18n, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setMinimumWidth(560)

        self.on_start: Callable[[int], None] | None = None
        self.on_face: Callable[[int], None] | None = None
        self.on_stop: Callable[[], None] | None = None
        self.on_reset: Callable[[], None] | None = None
        self._last_mode_mask: int | None = None

        root = QVBoxLayout(self)
        mode_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.btn_start = QPushButton()
        self.lbl_supported_modes = QLabel()
        mode_row.addWidget(self.lbl_supported_modes)
        mode_row.addWidget(self.mode_combo, 1)
        mode_row.addWidget(self.btn_start)
        root.addLayout(mode_row)

        self.lbl_state = QLabel()
        self.lbl_state.setWordWrap(True)
        root.addWidget(self.lbl_state)

        self.lbl_issue = QLabel()
        self.lbl_issue.setWordWrap(True)
        root.addWidget(self.lbl_issue)

        self.faces_group = QGroupBox()
        face_grid = QGridLayout(self.faces_group)
        self.face_status_labels: list[QLabel] = []
        self.face_buttons: list[QPushButton] = []
        for face, name in enumerate(self.FACE_NAMES):
            status = QLabel("○")
            button = QPushButton()
            button.clicked.connect(
                lambda _checked=False, value=face: self.on_face and self.on_face(value)
            )
            face_grid.addWidget(QLabel(name), face, 0)
            face_grid.addWidget(status, face, 1)
            face_grid.addWidget(button, face, 2)
            self.face_status_labels.append(status)
            self.face_buttons.append(button)
        face_grid.setColumnStretch(3, 1)
        root.addWidget(self.faces_group)

        action_row = QHBoxLayout()
        self.btn_stop = QPushButton()
        self.btn_reset = QPushButton()
        self.btn_close = QPushButton()
        action_row.addWidget(self.btn_stop)
        action_row.addWidget(self.btn_reset)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_close)
        root.addLayout(action_row)

        self.btn_start.clicked.connect(self._start_selected_mode)
        self.btn_stop.clicked.connect(lambda: self.on_stop and self.on_stop())
        self.btn_reset.clicked.connect(lambda: self.on_reset and self.on_reset())
        self.btn_close.clicked.connect(self.close)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("cal.dialog.title"))
        self.lbl_supported_modes.setText(self.i18n.tr("cal.supported_modes"))
        self.btn_start.setText(self.i18n.tr("button.cal_start"))
        self.faces_group.setTitle(self.i18n.tr("cal.faces_title"))
        self.btn_stop.setText(self.i18n.tr("button.stop"))
        self.btn_reset.setText(self.i18n.tr("button.reset"))
        self.btn_close.setText(self.i18n.tr("button.close"))
        self.lbl_state.setText(self.i18n.tr("cal.wait_status"))
        self.lbl_issue.setText(
            f"{self.i18n.tr('field.current_issue')}: {self.i18n.tr('common.none')}"
        )
        for face, button in enumerate(self.face_buttons):
            button.setText(self.i18n.tr("button.collect_face", face=self.FACE_NAMES[face]))
        self._last_mode_mask = None

    def _start_selected_mode(self) -> None:
        mode = self.mode_combo.currentData()
        if mode is not None and self.on_start is not None:
            self.on_start(int(mode))

    def render(self, state: FlightControllerState) -> None:
        capability = state.capability
        mask = capability.calibration_mode_mask if capability is not None else 0
        if mask != self._last_mode_mask:
            selected = self.mode_combo.currentData()
            self.mode_combo.clear()
            for mode in (
                int(AirCalibrationMode.NONE),
                int(AirCalibrationMode.ONE_FACE),
                int(AirCalibrationMode.SIX_FACE),
            ):
                if mask & (1 << mode):
                    canonical = enum_name(AirCalibrationMode, mode)
                    self.mode_combo.addItem(
                        f"{canonical}（{self.i18n.enum('calibration_mode', canonical)}）",
                        mode,
                    )
            if selected is not None:
                index = self.mode_combo.findData(selected)
                if index >= 0:
                    self.mode_combo.setCurrentIndex(index)
            self._last_mode_mask = mask

        calibration = state.calibration
        mode_name = self.i18n.enum(
            "calibration_mode", enum_name(AirCalibrationMode, calibration.mode)
        )
        state_name = (
            self.i18n.tr("common.wait")
            if calibration.state is None
            else self.i18n.enum(
                "calibration_state", enum_name(AirCalibrationState, calibration.state)
            )
        )
        current_face = (
            "—"
            if calibration.current_face == 0xFF or calibration.current_face >= len(self.FACE_NAMES)
            else self.FACE_NAMES[calibration.current_face]
        )
        if calibration.mode == int(AirCalibrationMode.ONE_FACE):
            guidance = self.i18n.tr("cal.guidance.one_face")
        elif calibration.mode == int(AirCalibrationMode.SIX_FACE):
            guidance = self.i18n.tr("cal.guidance.six_face")
        elif calibration.mode == int(AirCalibrationMode.NONE):
            guidance = self.i18n.tr("cal.guidance.none")
        else:
            guidance = self.i18n.tr("cal.guidance.select")
        self.lbl_state.setText(
            self.i18n.tr(
                "cal.status",
                mode=mode_name,
                state=state_name,
                face=current_face,
                ready=self.i18n.tr("common.yes" if calibration.ready else "common.no"),
                guidance=guidance,
            )
        )
        issue = calibration_diagnostic_text(self.i18n, state)
        self.lbl_issue.setText(f"{self.i18n.tr('field.current_issue')}: {issue}")
        self.lbl_issue.setStyleSheet(
            "color: #e04b4b; font-weight: bold;"
            if state.latest_calibration_diagnostic_reason
            else ""
        )

        command_ready = bool(
            state.air_command_link_allowed()
            and not state.pending_command_name
        )
        self.btn_start.setEnabled(command_ready and self.mode_combo.count() > 0)
        self.btn_stop.setEnabled(command_ready)
        self.btn_reset.setEnabled(command_ready)

        six_face_active = calibration.mode == int(AirCalibrationMode.SIX_FACE)
        for face, (status_label, button) in enumerate(
            zip(self.face_status_labels, self.face_buttons)
        ):
            completed = bool(calibration.completed_face_mask & (1 << face))
            active = calibration.current_face == face
            status_label.setText("✓" if completed else ("●" if active else "○"))
            button.setEnabled(command_ready and six_face_active and not completed)


class LinkDetailsDialog(QDialog):
    def __init__(self, i18n: I18n, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setMinimumSize(620, 560)
        layout = QVBoxLayout(self)
        self.details_text = QPlainTextEdit()
        self.details_text.setFont(QFont("Consolas"))
        self.details_text.setReadOnly(True)
        layout.addWidget(self.details_text, 1)
        self.btn_close = QPushButton()
        self.btn_close.clicked.connect(self.close)
        layout.addWidget(self.btn_close, 0, Qt.AlignRight)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("dialog.link_details.title"))
        self.btn_close.setText(self.i18n.tr("button.close"))

    def render(self, state: FlightControllerState) -> None:
        diagnostics = state.handshake
        none = self.i18n.tr("common.none")

        def optional(value: object | None) -> object:
            return none if value is None else value

        air_result = (
            none
            if diagnostics.air_ack_result is None
            else self.i18n.enum(
                "ack_result", enum_name(AirAckResult, diagnostics.air_ack_result)
            )
        )
        gsp_result = (
            none
            if diagnostics.last_gsp_air_tx_ack_result is None
            else self.i18n.enum(
                "gsp_ack_result",
                enum_name(GspAckResult, diagnostics.last_gsp_air_tx_ack_result),
            )
        )
        rssi = none if state.rssi_dbm is None else f"{state.rssi_dbm} dBm"
        snr = none if state.snr_db is None else f"{state.snr_db:.2f} dB"
        capability = state.capability
        self.details_text.setPlainText(
            self.i18n.tr(
                "link_details.body",
                handshake_state=self.i18n.tr(
                    f"handshake.{diagnostics.handshake_state.value}"
                ),
                profile=none if capability is None else capability.air_profile_id,
                accel=none if capability is None else capability.accel_full_scale_g,
                gyro=none if capability is None else capability.gyro_full_scale_dps,
                calibration_mask=(
                    none
                    if capability is None
                    else f"0x{capability.calibration_mode_mask:02X}"
                ),
                alignment_mask=(
                    none
                    if capability is None
                    else f"0x{capability.alignment_capability_mask:02X}"
                ),
                policy=self.i18n.enum(
                    "command_policy", state.command_policy_name()
                ),
                cap_seq=optional(diagnostics.last_capability_seq),
                accepted_cap_seq=optional(diagnostics.accepted_capability_seq),
                cmd_seq=optional(diagnostics.capability_ack_cmd_seq),
                attempts=diagnostics.capability_ack_attempts,
                requests=diagnostics.gsp_air_tx_requests,
                serial_writes=diagnostics.gsp_air_tx_serial_writes,
                serial_bytes=diagnostics.serial_tx_bytes,
                gsp_ok=diagnostics.gsp_air_tx_ack_ok,
                gsp_fail=diagnostics.gsp_air_tx_ack_fail,
                gsp_result=gsp_result,
                air_result=air_result,
                air_ack_rx=diagnostics.air_ack_rx,
                pstatus=yes_no_text(
                    self.i18n, diagnostics.preflight_status_capability_acked
                ),
                by_air_ack=yes_no_text(
                    self.i18n, diagnostics.capability_acked_by_air_ack
                ),
                by_pstatus=yes_no_text(
                    self.i18n, diagnostics.capability_acked_by_preflight_status
                ),
                duplicates=diagnostics.duplicate_capability_after_ack,
                error=diagnostics.last_handshake_error or none,
                gs_state=self.i18n.enum("gs_state", state.gs_state),
                radio_state=self.i18n.enum("radio_state", state.radio_state),
                gs_tx=state.gs_tx_count,
                gs_rx=state.gs_rx_count,
                gs_crc=state.gs_crc_error_count,
                rssi=rssi,
                snr=snr,
                receive_health=state.receive_health.tooltip(),
            )
        )


class MainWindow(QMainWindow):
    DEFAULT_CAMERA_DISTANCE = 6.5
    DEFAULT_CAMERA_ELEVATION = 20.0
    DEFAULT_CAMERA_AZIMUTH = 35.0
    DEFAULT_CAMERA_CENTER = (0.0, 0.0, 0.9)

    def __init__(self, i18n: I18n | None = None) -> None:
        super().__init__()
        self.i18n = i18n or I18n()
        self._translation_bindings: list[tuple[object, str, dict[str, object]]] = []
        self.resize(1760, 960)

        self.on_refresh_ports: Callable[[], None] | None = None
        self.on_connect_clicked: Callable[[], None] | None = None
        self.on_disconnect_clicked: Callable[[], None] | None = None
        self.on_send_ping: Callable[[], None] | None = None
        self.on_send_lock: Callable[[], None] | None = None
        self.on_send_unlock: Callable[[], None] | None = None
        self.on_send_start: Callable[[], None] | None = None
        self.on_cal_start: Callable[[int], None] | None = None
        self.on_cal_face: Callable[[int], None] | None = None
        self.on_cal_stop: Callable[[], None] | None = None
        self.on_cal_reset: Callable[[], None] | None = None
        self.on_align_start: Callable[[], None] | None = None
        self.on_align_stop: Callable[[], None] | None = None
        self.on_align_reset: Callable[[], None] | None = None
        self.on_generate_sim_data: Callable[[], None] | None = None
        self.on_process_data: Callable[[], None] | None = None
        self.on_open_log_dir: Callable[[], None] | None = None
        self.on_open_data_dir: Callable[[], None] | None = None
        self.on_language_changed: Callable[[], None] | None = None

        self._state: FlightControllerState | None = None
        self._events: EventHistory | None = None
        self._data_tools_busy = False
        self._last_sensor_revision = -1
        self._last_plot_revision = -1
        self._last_event_revision = -1
        self._auto_switched_session_generation: int | None = None
        self.latest_quat = (1.0, 0.0, 0.0, 0.0)
        self.latest_euler = (0.0, 0.0, 0.0)

        self._dynamic_value_font = QFont("Consolas")
        self._dynamic_value_font.setStyleHint(QFont.StyleHint.Monospace)
        self._dynamic_value_font.setFixedPitch(True)
        self._compact_label_font = QFont()
        self._compact_label_font.setPointSize(9)
        self._compact_value_font = QFont("Consolas")
        self._compact_value_font.setStyleHint(QFont.StyleHint.Monospace)
        self._compact_value_font.setFixedPitch(True)
        self._compact_value_font.setPointSize(9)

        self._cmd_button_style = (
            "QPushButton { padding: 4px 8px; }"
            "QPushButton:disabled { background-color: #2b2b2b; color: #8a8a8a; "
            "border: 1px solid #555555; }"
        )

        self._build_ui()
        self._build_3d_scene()
        self._build_plots()
        self.retranslate_ui()

        self.render_timer = QTimer(self)
        self.render_timer.setInterval(PLOT_REFRESH_INTERVAL_MS)
        self.render_timer.timeout.connect(self.render_state)
        self.render_timer.start()

    def bind_runtime_model(
        self,
        state: FlightControllerState,
        events: EventHistory,
        *,
        select_preflight: bool = False,
    ) -> None:
        self._state = state
        self._events = events
        self._last_sensor_revision = -1
        self._last_plot_revision = -1
        self._last_event_revision = -1
        if select_preflight:
            self.pages.setCurrentWidget(self.preflight_page)
        self.render_state()

    def _bind_text(self, widget: object, key: str, **params: object) -> object:
        self._translation_bindings.append((widget, key, params))
        return widget

    def _on_language_changed(self, index: int) -> None:
        language = self.language_combo.itemData(index)
        if language is not None and self.i18n.set_language(str(language)):
            self.retranslate_ui()
            if self.on_language_changed is not None:
                self.on_language_changed()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("app.title"))
        for widget, key, params in self._translation_bindings:
            text = self.i18n.tr(key, **params)
            if isinstance(widget, QGroupBox):
                widget.setTitle(text)
            else:
                widget.setText(text)  # type: ignore[attr-defined]
        self.pages.setTabText(self.pages.indexOf(self.preflight_page), self.i18n.tr("page.preflight"))
        self.pages.setTabText(self.pages.indexOf(self.flight_page), self.i18n.tr("page.flight"))
        self.pages.setTabText(
            self.pages.indexOf(self.post_process_page), self.i18n.tr("page.post_process")
        )
        language_index = self.language_combo.findData(self.i18n.language.value)
        if language_index >= 0 and language_index != self.language_combo.currentIndex():
            blocked = self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(language_index)
            self.language_combo.blockSignals(blocked)
        self._set_3d_camera_unlocked(self.btn_toggle_camera_lock.isChecked())
        self.calibration_dialog.retranslate_ui()
        self.link_details_dialog.retranslate_ui()
        self._retranslate_plots()
        self._last_sensor_revision = -1
        self._last_event_revision = -1
        self._last_plot_revision = -1
        self.render_state()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self._build_top_bar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.gl_view = AttitudeGLViewWidget()
        self.gl_view.setMinimumSize(360, 360)
        self.gl_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self.gl_view, 1)

        camera_controls = QWidget()
        camera_layout = QHBoxLayout(camera_controls)
        camera_layout.setContentsMargins(0, 4, 0, 0)
        self.btn_toggle_camera_lock = QPushButton()
        self.btn_toggle_camera_lock.setCheckable(True)
        self.btn_reset_camera = QPushButton()
        self._bind_text(self.btn_reset_camera, "camera.reset")
        camera_layout.addWidget(self.btn_toggle_camera_lock)
        camera_layout.addWidget(self.btn_reset_camera)
        camera_layout.addStretch(1)
        left_layout.addWidget(camera_controls)
        self.btn_toggle_camera_lock.toggled.connect(self._set_3d_camera_unlocked)
        self.btn_reset_camera.clicked.connect(self._reset_3d_camera)
        splitter.addWidget(left)

        self.pages = QTabWidget()
        self.preflight_page = self._build_preflight_page()
        self.flight_page = self._build_flight_page()
        self.post_process_page = self._build_post_process_page()
        self.pages.addTab(self.preflight_page, "")
        self.pages.addTab(self.flight_page, "")
        self.pages.addTab(self.post_process_page, "")
        splitter.addWidget(self.pages)
        splitter.setSizes([420, 1340])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.calibration_dialog = CalibrationDialog(self.i18n, self)
        self.calibration_dialog.on_start = lambda mode: self.on_cal_start and self.on_cal_start(mode)
        self.calibration_dialog.on_face = lambda face: self.on_cal_face and self.on_cal_face(face)
        self.calibration_dialog.on_stop = lambda: self.on_cal_stop and self.on_cal_stop()
        self.calibration_dialog.on_reset = lambda: self.on_cal_reset and self.on_cal_reset()
        self.link_details_dialog = LinkDetailsDialog(self.i18n, self)

    def _build_top_bar(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.connection")
        layout = QHBoxLayout(box)
        self.port_combo = QComboBox()
        self.port_combo.setMinimumContentsLength(12)
        self.btn_refresh = QPushButton()
        self._bind_text(self.btn_refresh, "button.refresh_ports")
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(9600, 2_000_000)
        self.baud_spin.setValue(230400)
        self.baud_spin.setFixedWidth(100)
        self.btn_connect = QPushButton()
        self.btn_disconnect = QPushButton()
        self._bind_text(self.btn_connect, "button.connect")
        self._bind_text(self.btn_disconnect, "button.disconnect")
        self.conn_label = QLabel()
        self._configure_dynamic_label(self.conn_label, 260, show_tooltip=True)

        self.lbl_port_name = QLabel()
        self._bind_text(self.lbl_port_name, "field.port")
        layout.addWidget(self.lbl_port_name)
        layout.addWidget(self.port_combo)
        layout.addWidget(self.btn_refresh)
        self.lbl_baud_name = QLabel()
        self._bind_text(self.lbl_baud_name, "field.baudrate")
        layout.addWidget(self.lbl_baud_name)
        layout.addWidget(self.baud_spin)
        layout.addWidget(self.btn_connect)
        layout.addWidget(self.btn_disconnect)
        layout.addWidget(self.conn_label)
        layout.addStretch(1)
        self.lbl_language = QLabel()
        self._bind_text(self.lbl_language, "language.label")
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", Language.ZH_CN.value)
        self.language_combo.addItem("English", Language.EN_US.value)
        layout.addWidget(self.lbl_language)
        layout.addWidget(self.language_combo)

        self.btn_refresh.clicked.connect(lambda: self.on_refresh_ports and self.on_refresh_ports())
        self.btn_connect.clicked.connect(lambda: self.on_connect_clicked and self.on_connect_clicked())
        self.btn_disconnect.clicked.connect(
            lambda: self.on_disconnect_clicked and self.on_disconnect_clicked()
        )
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        return box

    def _build_preflight_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        summary_row = QHBoxLayout()
        summary_row.addWidget(self._build_preflight_system_panel(), 1)
        summary_row.addWidget(self._build_calibration_panel(), 1)
        summary_row.addWidget(self._build_alignment_panel(), 1)
        root.addLayout(summary_row)

        detail_row = QHBoxLayout()
        detail_row.addWidget(self._build_preflight_sensor_panel(), 2)
        detail_row.addWidget(self._build_preflight_gnss_panel(), 1)
        root.addLayout(detail_row)
        root.addWidget(self._build_preflight_command_panel())
        root.addWidget(self._build_event_panel(), 1)
        return page

    def _build_preflight_system_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.system")
        grid = QGridLayout(box)
        self.lbl_pf_lifecycle = QLabel("—")
        self.lbl_pf_system_ready = QLabel("—")
        self.lbl_pf_selftest = QLabel("—")
        self.lbl_pf_air_link = QLabel("—")
        self.lbl_pf_start_block = QLabel("CAPABILITY_REQUIRED")
        self.lbl_pf_lock = QLabel("—")
        for row, (name, label) in enumerate(
            (
                ("field.lifecycle", self.lbl_pf_lifecycle),
                ("field.system_ready", self.lbl_pf_system_ready),
                ("field.selftest", self.lbl_pf_selftest),
                ("field.air_link", self.lbl_pf_air_link),
                ("field.lock_state", self.lbl_pf_lock),
                ("field.start_block", self.lbl_pf_start_block),
            )
        ):
            self._add_value_pair(grid, row, name, label, 150, True)
        self.btn_link_details = QPushButton()
        self._bind_text(self.btn_link_details, "button.details")
        self.btn_link_details.clicked.connect(self._show_link_details)
        grid.addWidget(self.btn_link_details, 3, 2)
        return box

    def _build_calibration_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.calibration")
        layout = QVBoxLayout(box)
        grid = QGridLayout()
        self.lbl_cal_mode = QLabel("NOT_SELECTED")
        self.lbl_cal_state = QLabel("—")
        self.lbl_cal_ready = QLabel("NO")
        self.lbl_cal_face = QLabel("—")
        self.lbl_cal_progress = QLabel("○ ○ ○ ○ ○ ○")
        self.lbl_cal_issue = QLabel("—")
        self.lbl_cal_issue.setWordWrap(True)
        for row, (name, label) in enumerate(
            (
                ("field.mode", self.lbl_cal_mode),
                ("field.state", self.lbl_cal_state),
                ("field.ready", self.lbl_cal_ready),
                ("field.current_face", self.lbl_cal_face),
                ("field.six_face", self.lbl_cal_progress),
                ("field.current_issue", self.lbl_cal_issue),
            )
        ):
            self._add_value_pair(grid, row, name, label, 130, True)
        self.lbl_cal_issue.setWordWrap(True)
        self.lbl_cal_issue.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addLayout(grid)
        actions = QHBoxLayout()
        self.btn_calibration = QPushButton()
        self._bind_text(self.btn_calibration, "button.calibration")
        self.btn_calibration.setStyleSheet(self._cmd_button_style)
        actions.addWidget(self.btn_calibration)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.btn_calibration.clicked.connect(self._show_calibration_dialog)
        return box

    def _build_alignment_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.alignment")
        layout = QVBoxLayout(box)
        grid = QGridLayout()
        self.lbl_align_state = QLabel("—")
        self.lbl_align_ready = QLabel("NO")
        self.lbl_align_attitude = QLabel("—")
        self.lbl_align_gnss = QLabel("—")
        self.lbl_align_baro = QLabel("—")
        self.lbl_align_hint = QLabel("—")
        self.lbl_align_hint.setWordWrap(True)
        self._add_value_pair(grid, 0, "field.state", self.lbl_align_state, 125, True)
        self._add_value_pair(grid, 1, "field.ready", self.lbl_align_ready, 125, True)
        self.lbl_align_attitude_name = self._add_value_pair(
            grid, 2, "field.attitude", self.lbl_align_attitude, 125, True
        )
        self.lbl_align_gnss_name = self._add_value_pair(
            grid, 3, "field.gnss_origin", self.lbl_align_gnss, 125, True
        )
        self.lbl_align_baro_name = self._add_value_pair(
            grid, 4, "field.baro_origin", self.lbl_align_baro, 125, True
        )
        self._add_value_pair(
            grid, 5, "field.alignment_hint", self.lbl_align_hint, 125, True
        )
        self.lbl_align_hint.setWordWrap(True)
        self.lbl_align_hint.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addLayout(grid)
        actions = QHBoxLayout()
        self.btn_align_start = QPushButton()
        self.btn_align_stop = QPushButton()
        self.btn_align_reset = QPushButton()
        self._bind_text(self.btn_align_start, "button.align_start")
        self._bind_text(self.btn_align_stop, "button.stop")
        self._bind_text(self.btn_align_reset, "button.reset")
        for button in (self.btn_align_start, self.btn_align_stop, self.btn_align_reset):
            button.setStyleSheet(self._cmd_button_style)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.btn_align_start.clicked.connect(lambda: self.on_align_start and self.on_align_start())
        self.btn_align_stop.clicked.connect(lambda: self.on_align_stop and self.on_align_stop())
        self.btn_align_reset.clicked.connect(lambda: self.on_align_reset and self.on_align_reset())
        return box

    def _build_preflight_sensor_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.sensor_preflight")
        grid = QGridLayout(box)
        self.lbl_pf_accel = QLabel()
        self.lbl_pf_gyro = QLabel()
        self._bind_text(self.lbl_pf_accel, "capability.waiting")
        self._bind_text(self.lbl_pf_gyro, "capability.waiting")
        self.lbl_pf_quat_raw = QLabel("W: —  X: —  Y: —  Z: —")
        self.lbl_pf_quat = QLabel("W: —  X: —  Y: —  Z: —")
        self.lbl_pf_euler = QLabel("R: —  P: —  Y: —")
        for row, (name, label) in enumerate(
            (
                ("field.accel_mps2", self.lbl_pf_accel),
                ("field.gyro_radps", self.lbl_pf_gyro),
                ("field.quat_raw", self.lbl_pf_quat_raw),
                ("field.quat_wxyz", self.lbl_pf_quat),
                ("field.euler_rpy_rad", self.lbl_pf_euler),
            )
        ):
            self._add_value_pair(grid, row, name, label, 420, True)
        return box

    def _build_preflight_gnss_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.gnss")
        grid = QGridLayout(box)
        self.lbl_pf_gnss_usable = QLabel("—")
        self.lbl_pf_gnss_origin = QLabel("—")
        self._add_value_pair(grid, 0, "field.position_usable", self.lbl_pf_gnss_usable, 120, True)
        self._add_value_pair(grid, 1, "field.origin_ready", self.lbl_pf_gnss_origin, 120, True)
        self.lbl_gnss_note = QLabel()
        self._bind_text(self.lbl_gnss_note, "note.gnss_profile")
        self.lbl_gnss_note.setWordWrap(True)
        grid.addWidget(self.lbl_gnss_note, 2, 0, 1, 2)
        return box

    def _build_preflight_command_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.preflight_commands")
        layout = QHBoxLayout(box)
        self.btn_ping = QPushButton()
        self.btn_lock = QPushButton()
        self.btn_unlock = QPushButton()
        self.btn_start = QPushButton()
        self._bind_text(self.btn_ping, "button.ping")
        self._bind_text(self.btn_lock, "button.lock")
        self._bind_text(self.btn_unlock, "button.unlock")
        self._bind_text(self.btn_start, "button.start")
        for button in (self.btn_ping, self.btn_lock, self.btn_unlock, self.btn_start):
            button.setStyleSheet(self._cmd_button_style)
            button.setMinimumWidth(100)
            button.setMinimumHeight(34)
            layout.addWidget(button)
        layout.addSpacing(16)
        self.lbl_start_reason_name = QLabel()
        self._bind_text(self.lbl_start_reason_name, "field.start_reason")
        layout.addWidget(self.lbl_start_reason_name)
        self.lbl_start_reason = QLabel()
        self._bind_text(self.lbl_start_reason, "start.wait_capability")
        self._configure_dynamic_label(self.lbl_start_reason, 360, show_tooltip=True)
        layout.addWidget(self.lbl_start_reason, 1)
        self.btn_ping.clicked.connect(lambda: self.on_send_ping and self.on_send_ping())
        self.btn_lock.clicked.connect(lambda: self.on_send_lock and self.on_send_lock())
        self.btn_unlock.clicked.connect(lambda: self.on_send_unlock and self.on_send_unlock())
        self.btn_start.clicked.connect(lambda: self.on_send_start and self.on_send_start())
        return box

    def _build_flight_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(self._build_flight_status_panel(), 1)
        top.addWidget(self._build_flight_data_panel(), 2)
        top.addWidget(self._build_mission_state_panel(), 2)
        root.addLayout(top)
        root.addWidget(self._build_plot_panel(), 1)
        return page

    def _build_flight_status_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.important_status")
        grid = QGridLayout(box)
        self.lbl_flight_lifecycle = QLabel("—")
        self.lbl_mission_time = QLabel("—")
        self.lbl_rssi = QLabel("— dBm")
        self.lbl_snr = QLabel("— dB")
        self.lbl_packet_loss = QLabel("0 / 0 (0.00%)")
        self.lbl_last_packet_age = QLabel("— ms")
        self.lbl_flight_processing = QLabel("NORMAL")
        for row, (name, label) in enumerate(
            (
                ("field.lifecycle", self.lbl_flight_lifecycle),
                ("field.mission_time", self.lbl_mission_time),
                ("field.rssi", self.lbl_rssi),
                ("field.snr", self.lbl_snr),
                ("field.packet_loss", self.lbl_packet_loss),
                ("field.last_packet_age", self.lbl_last_packet_age),
                ("field.pc_processing", self.lbl_flight_processing),
            )
        ):
            self._add_value_pair(grid, row, name, label, 150, True)
        return box

    def _build_flight_data_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.current_data")
        grid = QGridLayout(box)
        self.lbl_flight_accel = QLabel("—")
        self.lbl_flight_gyro = QLabel("—")
        self.lbl_flight_quat = QLabel("—")
        self.lbl_flight_euler = QLabel("—")
        self.lbl_flight_vel = QLabel("—")
        self.lbl_flight_pos = QLabel("—")
        for row, (name, label) in enumerate(
            (
                ("field.accel_xyz", self.lbl_flight_accel),
                ("field.gyro_xyz", self.lbl_flight_gyro),
                ("field.quaternion", self.lbl_flight_quat),
                ("field.euler_rpy", self.lbl_flight_euler),
                ("field.velocity_enu", self.lbl_flight_vel),
                ("field.position_enu", self.lbl_flight_pos),
            )
        ):
            self._add_value_pair(grid, row, name, label, 300, True)
        return box

    def _build_event_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.event_history")
        layout = QVBoxLayout(box)
        self.event_list = QListWidget()
        self.event_list.setFont(self._compact_value_font)
        layout.addWidget(self.event_list)
        return box

    def _build_mission_state_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.mission_state")
        grid = QGridLayout(box)
        self.lbl_mission_state = QLabel("—")
        self.lbl_mission_last_event = QLabel("—")
        self.lbl_mission_event_elapsed = QLabel("—")
        self.lbl_mission_parachute = QLabel("—")
        for row, (name, label) in enumerate(
            (
                ("field.mission_state", self.lbl_mission_state),
                ("field.last_key_event", self.lbl_mission_last_event),
                ("field.event_elapsed", self.lbl_mission_event_elapsed),
                ("field.parachute_status", self.lbl_mission_parachute),
            )
        ):
            self._add_value_pair(grid, row, name, label, 220, True)
        return box

    def _build_plot_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.live_plots", seconds=PLOT_WINDOW_SECONDS)
        self.plot_layout = QGridLayout(box)
        self.plot_layout.setHorizontalSpacing(8)
        self.plot_layout.setVerticalSpacing(8)
        return box

    def _build_post_process_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        box = QGroupBox()
        self._bind_text(box, "group.post_process")
        layout = QVBoxLayout(box)
        self.btn_generate_sim = QPushButton()
        self.btn_process_data = QPushButton()
        self.btn_open_log_dir = QPushButton()
        self.btn_open_data_dir = QPushButton()
        self._bind_text(self.btn_generate_sim, "button.generate_sim")
        self._bind_text(self.btn_process_data, "button.process_data")
        self._bind_text(self.btn_open_log_dir, "button.open_logs")
        self._bind_text(self.btn_open_data_dir, "button.open_data")
        for button in (
            self.btn_generate_sim,
            self.btn_process_data,
            self.btn_open_log_dir,
            self.btn_open_data_dir,
        ):
            button.setMinimumHeight(42)
            button.setStyleSheet(self._cmd_button_style)
            layout.addWidget(button)
        layout.addStretch(1)
        root.addWidget(box)
        root.addStretch(1)
        self.btn_generate_sim.clicked.connect(
            lambda: self.on_generate_sim_data and self.on_generate_sim_data()
        )
        self.btn_process_data.clicked.connect(lambda: self.on_process_data and self.on_process_data())
        self.btn_open_log_dir.clicked.connect(
            lambda: self.on_open_log_dir and self.on_open_log_dir()
        )
        self.btn_open_data_dir.clicked.connect(
            lambda: self.on_open_data_dir and self.on_open_data_dir()
        )
        return page

    def _configure_dynamic_label(
        self,
        label: QLabel,
        min_width: int,
        *,
        show_tooltip: bool = False,
        compact: bool = True,
    ) -> None:
        label.setMinimumWidth(min_width)
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("showFullTextToolTip", show_tooltip)
        label.setFont(self._compact_value_font if compact else self._dynamic_value_font)

    def _set_dynamic_label_text(self, label: QLabel, text: str) -> None:
        label.setText(text)
        if label.property("showFullTextToolTip"):
            label.setToolTip("" if text in ("", "—") else text)

    def _add_value_pair(
        self,
        grid: QGridLayout,
        row: int,
        name_key: str,
        value_label: QLabel,
        value_min_width: int,
        value_tooltip: bool,
    ) -> QLabel:
        name_label = QLabel()
        self._bind_text(name_label, name_key)
        name_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        name_label.setFont(self._compact_label_font)
        self._configure_dynamic_label(
            value_label,
            value_min_width,
            show_tooltip=value_tooltip,
        )
        grid.addWidget(name_label, row, 0)
        grid.addWidget(value_label, row, 1)
        grid.setColumnStretch(1, 1)
        return name_label

    def _show_calibration_dialog(self) -> None:
        if self._state is not None:
            self.calibration_dialog.render(self._state)
        self.calibration_dialog.show()
        self.calibration_dialog.raise_()
        self.calibration_dialog.activateWindow()

    def _show_link_details(self) -> None:
        if self._state is not None:
            self.link_details_dialog.render(self._state)
        self.link_details_dialog.show()
        self.link_details_dialog.raise_()
        self.link_details_dialog.activateWindow()

    def set_ports(self, ports: list[str]) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current:
            index = self.port_combo.findText(current)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

    def current_port(self) -> str:
        return self.port_combo.currentText().strip()

    def current_baudrate(self) -> int:
        return int(self.baud_spin.value())

    def set_connection_status(self, text: str) -> None:
        self._set_dynamic_label_text(self.conn_label, text)

    def set_data_tools_busy(self, busy: bool) -> None:
        self._data_tools_busy = bool(busy)
        self._render_data_tool_buttons()

    def _render_data_tool_buttons(self) -> None:
        mission = bool(self._state and self._state.mission_started)
        high_load_enabled = not self._data_tools_busy and not mission
        self.btn_generate_sim.setEnabled(high_load_enabled)
        self.btn_process_data.setEnabled(high_load_enabled)
        # Merely opening the folders is not a high-load operation.
        self.btn_open_log_dir.setEnabled(True)
        self.btn_open_data_dir.setEnabled(True)

    def render_state(self) -> None:
        state = self._state
        if state is None:
            return

        self.set_connection_status(self.i18n.format_message(state.connection_message))
        lifecycle_name = (
            "UNKNOWN"
            if state.lifecycle_state is None
            else enum_name(AirLifecycleState, state.lifecycle_state)
        )
        lifecycle = (
            "—" if state.lifecycle_state is None else self.i18n.enum("lifecycle", lifecycle_name)
        )
        yes_no = lambda value: self.i18n.tr("common.yes" if value else "common.no")

        self.lbl_pf_lifecycle.setText(lifecycle)
        self.lbl_flight_lifecycle.setText(lifecycle)
        self.lbl_pf_system_ready.setText(yes_no(state.system_ready))
        self.lbl_pf_selftest.setText(
            self.i18n.tr("common.pass" if state.selftest_passed else "common.not_ready")
        )
        self._set_semantic_style(
            self.lbl_pf_system_ready, "ready" if state.system_ready else "waiting"
        )
        self._set_semantic_style(
            self.lbl_pf_selftest, "ready" if state.selftest_passed else "waiting"
        )
        self._render_air_link(state)
        self.lbl_pf_start_block.setText(
            self.i18n.enum("ack_result", state.start_block_reason_name())
        )
        self.lbl_pf_lock.setText(
            self.i18n.tr("common.unlocked" if state.start_unlocked else "common.locked")
        )

        calibration = state.calibration
        self.lbl_cal_mode.setText(
            self.i18n.enum("calibration_mode", enum_name(AirCalibrationMode, calibration.mode))
        )
        self.lbl_cal_state.setText(
            "—"
            if calibration.state is None
            else self.i18n.enum(
                "calibration_state", enum_name(AirCalibrationState, calibration.state)
            )
        )
        self.lbl_cal_ready.setText(yes_no(calibration.ready))
        self._set_semantic_style(
            self.lbl_cal_ready,
            "error"
            if calibration.state == int(AirCalibrationState.FAILED)
            else "ready"
            if calibration.ready
            else "waiting",
        )
        face_names = CalibrationDialog.FACE_NAMES
        self.lbl_cal_face.setText(
            "—"
            if calibration.current_face == 0xFF or calibration.current_face >= len(face_names)
            else face_names[calibration.current_face]
        )
        self.lbl_cal_progress.setText(
            " ".join(
                f"{face_names[face]}{'✓' if calibration.completed_face_mask & (1 << face) else '○'}"
                for face in range(6)
            )
        )
        self.lbl_cal_issue.setText(calibration_diagnostic_text(self.i18n, state))
        self._set_semantic_style(
            self.lbl_cal_issue,
            "error" if state.latest_calibration_diagnostic_reason else "unknown",
        )

        alignment = state.alignment
        self.lbl_align_state.setText(
            "—"
            if alignment.state is None
            else self.i18n.enum(
                "alignment_state", enum_name(AirAlignmentState, alignment.state)
            )
        )
        self.lbl_align_ready.setText(yes_no(alignment.ready))
        self._set_semantic_style(
            self.lbl_align_ready,
            "error"
            if alignment.state
            in (int(AirAlignmentState.FAILED), int(AirAlignmentState.STALE))
            else "ready"
            if alignment.ready
            else "waiting",
        )
        capability_mask = state.capability.alignment_capability_mask if state.capability else 0
        self._render_alignment_source(
            self.lbl_align_attitude_name,
            self.lbl_align_attitude,
            capability_mask,
            int(AirAlignmentCapability.ATTITUDE),
            alignment.attitude_ready,
        )
        self._render_alignment_source(
            self.lbl_align_gnss_name,
            self.lbl_align_gnss,
            capability_mask,
            int(AirAlignmentCapability.GNSS_ORIGIN),
            alignment.gnss_origin_ready,
        )
        self._render_alignment_source(
            self.lbl_align_baro_name,
            self.lbl_align_baro,
            capability_mask,
            int(AirAlignmentCapability.BARO_ORIGIN),
            alignment.baro_origin_ready,
        )
        alignment_stale = alignment.state == int(AirAlignmentState.STALE)
        self.lbl_align_hint.setText(
            self.i18n.tr("alignment.stale.hint")
            if alignment_stale
            else self.i18n.tr("common.none")
        )
        self._set_semantic_style(
            self.lbl_align_state,
            "error" if alignment_stale else "ready" if alignment.ready else "waiting",
        )
        self._set_semantic_style(
            self.lbl_align_hint, "error" if alignment_stale else "unknown"
        )
        self.lbl_pf_gnss_usable.setText(yes_no(state.gnss_position_usable))
        self.lbl_pf_gnss_origin.setText(
            self.i18n.tr("common.not_supported")
            if not (capability_mask & int(AirAlignmentCapability.GNSS_ORIGIN))
            else yes_no(alignment.gnss_origin_ready)
        )

        health = state.receive_health
        health_text = self.i18n.tr("common.backlog" if health.is_backlogged() else "common.normal")
        health_style = "color: #ff5555; font-weight: bold;" if health.is_backlogged() else ""
        for label in (self.lbl_flight_processing,):
            label.setText(health_text)
            label.setStyleSheet(health_style)
            label.setToolTip(health.tooltip())

        self.lbl_rssi.setText("— dBm" if state.rssi_dbm is None else f"{state.rssi_dbm} dBm")
        self.lbl_snr.setText("— dB" if state.snr_db is None else f"{state.snr_db:.2f} dB")
        self.lbl_packet_loss.setText(
            f"{state.estimated_lost_packets} / {state.expected_flight_packets} "
            f"({state.packet_loss_rate * 100.0:.2f}%)"
        )
        age_ms = (
            None
            if state.sensor.host_rx_monotonic_ns is None
            else max(
                0.0,
                (time_monotonic_ns() - state.sensor.host_rx_monotonic_ns) / 1_000_000.0,
            )
        )
        self.lbl_last_packet_age.setText("— ms" if age_ms is None else f"{age_ms:.1f} ms")
        if state.latest_flight_time_ms is None or state.mission_first_time_ms is None:
            self.lbl_mission_time.setText("—")
        else:
            mission_s = max(0.0, (state.latest_flight_time_ms - state.mission_first_time_ms) / 1000.0)
            self.lbl_mission_time.setText(f"{mission_s:.1f} s")

        self._render_sensor(state)
        self._render_mission_state(state)
        self._render_commands(state)
        self._render_events()
        self._render_plots(state)
        self._render_data_tool_buttons()
        if self.calibration_dialog.isVisible():
            self.calibration_dialog.render(state)
        if self.link_details_dialog.isVisible():
            self.link_details_dialog.render(state)

        if (
            state.mission_started
            and self._auto_switched_session_generation != state.session_generation
        ):
            self.pages.setCurrentWidget(self.flight_page)
            self._auto_switched_session_generation = state.session_generation

    def _render_air_link(self, state: FlightControllerState) -> None:
        diagnostics = state.handshake
        handshake_state = diagnostics.handshake_state
        if state.profile_supported is False or state.capability_error == "UNSUPPORTED_PROFILE":
            key = "air_link.unsupported"
        elif handshake_state is HandshakeState.ERROR:
            key = "air_link.error"
        elif handshake_state is HandshakeState.ACKED:
            key = "air_link.connected"
        elif handshake_state is HandshakeState.HANDSHAKING:
            key = "air_link.handshaking"
        elif diagnostics.last_capability_seq is not None or state.receive_health.air_frames > 0:
            key = "air_link.downlink_wait"
        else:
            key = "air_link.not_detected"
        self.lbl_pf_air_link.setText(self.i18n.tr(key))
        self.lbl_pf_air_link.setToolTip("")
        color = {
            "air_link.connected": "#35b96f",
            "air_link.handshaking": "#d7a928",
            "air_link.downlink_wait": "#d7a928",
            "air_link.unsupported": "#e04b4b",
            "air_link.error": "#e04b4b",
            "air_link.not_detected": "#888888",
        }[key]
        self.lbl_pf_air_link.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _render_mission_state(self, state: FlightControllerState) -> None:
        presentation = state.mission_presentation
        self.lbl_mission_state.setText(
            self.i18n.enum("mission_phase", presentation.phase.value)
        )
        self._set_semantic_style(
            self.lbl_mission_state,
            "waiting"
            if presentation.phase is MissionPhase.PRE_START
            else "ready",
        )
        if presentation.last_critical_event_name:
            self.lbl_mission_last_event.setText(
                self.i18n.enum("status", presentation.last_critical_event_name)
            )
        else:
            self.lbl_mission_last_event.setText(self.i18n.tr("mission.no_key_event"))

        event_time = presentation.last_critical_event_time_ms
        event_host_ns = presentation.last_critical_event_host_monotonic_ns
        latest_times = [
            value
            for value in (state.latest_flight_time_ms, state.sensor.time_ms, event_time)
            if value is not None
        ]
        if event_host_ns is not None:
            elapsed_s = max(0.0, (time_monotonic_ns() - event_host_ns) / 1_000_000_000.0)
            self.lbl_mission_event_elapsed.setText(
                self.i18n.tr("mission.elapsed", seconds=elapsed_s)
            )
        elif event_time is None or not latest_times:
            self.lbl_mission_event_elapsed.setText(self.i18n.tr("common.none"))
        else:
            elapsed_s = max(0.0, (max(latest_times) - event_time) / 1000.0)
            self.lbl_mission_event_elapsed.setText(
                self.i18n.tr("mission.elapsed", seconds=elapsed_s)
            )
        self.lbl_mission_parachute.setText(
            self.i18n.tr(
                "mission.parachute_deployed"
                if presentation.parachute_deployed
                else "mission.parachute_not_deployed"
            )
        )

    @staticmethod
    def _set_semantic_style(label: QLabel, state: str) -> None:
        color = {
            "ready": "#35b96f",
            "waiting": "#d7a928",
            "error": "#e04b4b",
            "unknown": "#888888",
        }.get(state, "#888888")
        label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _render_alignment_source(
        self,
        name_label: QLabel,
        label: QLabel,
        capability_mask: int,
        source_bit: int,
        ready: bool,
    ) -> None:
        supported = bool(capability_mask & source_bit)
        name_label.setVisible(supported)
        label.setVisible(supported)
        if supported:
            label.setText(self.i18n.tr("common.ready" if ready else "common.wait"))

    def _render_sensor(self, state: FlightControllerState) -> None:
        sensor = state.sensor
        if sensor.revision == self._last_sensor_revision:
            return
        self._last_sensor_revision = sensor.revision

        accel_text = self._format_vector(sensor.accel_mps2, "X", "Y", "Z")
        gyro_text = self._format_vector(sensor.gyro_radps, "X", "Y", "Z")
        if sensor.accel_mps2 is None and sensor.accel_raw is not None:
            accel_text = self.i18n.tr("sensor.wait_capability_raw", raw=sensor.accel_raw)
        if sensor.gyro_radps is None and sensor.gyro_raw is not None:
            gyro_text = self.i18n.tr("sensor.wait_capability_raw", raw=sensor.gyro_raw)
        quat_raw_text = self._format_int_vector(sensor.quat_q15, ("W", "X", "Y", "Z"))
        quat_text = self._format_vector(sensor.quat, "W", "X", "Y", "Z")
        if sensor.quat is not None:
            quat_text += "  " + self.i18n.tr(
                "sensor.quat_valid" if sensor.quat_valid else "sensor.quat_invalid"
            )
        euler_text = self._format_vector(sensor.euler_rpy, "R", "P", "Y")

        self.lbl_pf_accel.setText(accel_text)
        self.lbl_pf_gyro.setText(gyro_text)
        self.lbl_pf_quat_raw.setText(quat_raw_text)
        self.lbl_pf_quat.setText(quat_text)
        self.lbl_pf_euler.setText(euler_text)
        self.lbl_flight_accel.setText(accel_text)
        self.lbl_flight_gyro.setText(gyro_text)
        self.lbl_flight_quat.setText(quat_text)
        self.lbl_flight_euler.setText(euler_text)
        self.lbl_flight_vel.setText(self._format_vector(sensor.velocity_mps, "E", "N", "U"))
        self.lbl_flight_pos.setText(self._format_vector(sensor.position_m, "E", "N", "U"))

        if sensor.quat is not None:
            self.latest_quat = sensor.quat
            if sensor.euler_rpy is not None:
                self.latest_euler = sensor.euler_rpy
            if sensor.quat_valid:
                self._apply_quat_to_mesh(sensor.quat)

    def _render_commands(self, state: FlightControllerState) -> None:
        link_allowed = state.air_command_link_allowed()
        preflight_allowed = link_allowed and not state.pending_command_name
        self.btn_ping.setEnabled(link_allowed and not state.pending_command_name)
        self.btn_lock.setEnabled(preflight_allowed and state.start_unlocked)
        self.btn_unlock.setEnabled(preflight_allowed and not state.start_unlocked)
        self.btn_start.setEnabled(state.start_ready())
        self.btn_calibration.setEnabled(preflight_allowed)
        self.btn_align_start.setEnabled(preflight_allowed and state.calibration.ready)
        self.btn_align_stop.setEnabled(preflight_allowed)
        self.btn_align_reset.setEnabled(preflight_allowed)

        if state.capability_error:
            reason = self.i18n.tr(f"capability.error.{state.capability_error}")
        elif state.profile_supported is False:
            reason = self.i18n.tr(
                "capability.unsupported",
                profile=state.capability.air_profile_id if state.capability else "?",
            )
        elif not state.capability_acked:
            reason = self.i18n.tr("start.wait_capability")
        elif state.pending_command_name:
            reason = self.i18n.tr("start.wait_pending", command=state.pending_command_name)
        elif not state.calibration.ready:
            reason = self.i18n.enum("ack_result", "CALIBRATION_REQUIRED")
        elif not state.alignment.ready:
            reason = self.i18n.enum("ack_result", "ALIGNMENT_REQUIRED")
        elif not state.system_ready:
            reason = self.i18n.enum("ack_result", "SYSTEM_NOT_READY")
        elif not state.start_unlocked:
            reason = self.i18n.enum("ack_result", "LOCKED_REQUIRED")
        else:
            reason = self.i18n.enum("ack_result", state.start_block_reason_name())
        self._set_dynamic_label_text(self.lbl_start_reason, reason)
        self.lbl_start_reason.setToolTip(
            self.i18n.tr(
                "start.tooltip",
                reason=reason,
                hint=self.i18n.format_message(state.radio_message),
                ack=self.i18n.format_message(state.last_air_ack_message),
            )
        )

    def _render_events(self) -> None:
        if self._events is None or self._events.revision == self._last_event_revision:
            return
        self._last_event_revision = self._events.revision
        self.event_list.clear()
        for event in self._events.snapshot():
            self.event_list.addItem(self._format_event(event))
        self.event_list.scrollToBottom()

    def _format_event(self, event) -> str:
        name = self.i18n.enum("status", event.name)
        detail = ""
        if event.status_id == int(AirStatusId.GNSS_POSITION):
            value = (
                self.i18n.tr("event.gnss.usable")
                if event.arg0 == 1
                else self.i18n.tr("event.gnss.unusable")
                if event.arg0 == 0
                else f"UNKNOWN({event.arg0})"
            )
            detail = self.i18n.tr("event.detail.gnss", value=value)
        elif event.status_id == int(AirStatusId.CALIBRATION_FACE):
            faces = CalibrationDialog.FACE_NAMES
            face = faces[event.arg0] if 0 <= event.arg0 < len(faces) else str(event.arg0)
            result = self.i18n.tr(
                "event.face.passed" if event.arg1 == 1 else "event.face.failed"
            )
            detail = self.i18n.tr("event.detail.face", face=face, result=result)
        elif event.status_id == int(AirStatusId.CALIBRATION):
            detail = self.i18n.tr(
                "event.detail.calibration",
                state=self.i18n.enum(
                    "calibration_state", enum_name(AirCalibrationState, event.arg0)
                ),
            )
        elif event.status_id == int(AirStatusId.ALIGNMENT):
            detail = self.i18n.tr(
                "event.detail.alignment",
                state=self.i18n.enum(
                    "alignment_state", enum_name(AirAlignmentState, event.arg0)
                ),
            )
        elif event.status_id == int(AirStatusId.CALIBRATION_DIAGNOSTIC):
            reason = self.i18n.enum(
                "calibration_diagnostic_reason",
                enum_name(AirCalibrationDiagnosticReason, event.arg1),
            )
            if 0 <= event.arg0 < len(CalibrationDialog.FACE_NAMES):
                detail = self.i18n.tr(
                    "diagnostic.face_issue",
                    face=CalibrationDialog.FACE_NAMES[event.arg0],
                    reason=reason,
                )
            else:
                detail = reason
        if detail:
            name = f"{name} {detail}"
        return self.i18n.tr(
            "event.line",
            time_ms=event.time_ms,
            name=name,
            arg0=event.arg0,
            arg1=event.arg1,
        )

    def _render_plots(self, state: FlightControllerState) -> None:
        plot = state.live_plot
        if plot.revision == self._last_plot_revision:
            return
        self._last_plot_revision = plot.revision
        times, velocity, position = plot.snapshot()
        values = {"vel": velocity, "pos": position}
        for (key, axis), curve in self.curves.items():
            curve.setData(times, values[key][axis])
            if times:
                latest = times[-1]
                self.plot_widgets[(key, axis)].setXRange(
                    latest - PLOT_WINDOW_SECONDS,
                    latest,
                    padding=0,
                )

    def refresh_plots(self) -> None:
        if self._state is not None:
            self._last_plot_revision = -1
            self._render_plots(self._state)

    def clear_vector_series(self) -> None:
        if self._state is not None:
            self._state.live_plot.clear()
            self.refresh_plots()

    @staticmethod
    def _format_vector(values, *labels: str) -> str:
        if values is None:
            return "—"
        return "  ".join(f"{label}:{float(value):.4f}" for label, value in zip(labels, values))

    @staticmethod
    def _format_int_vector(values, labels: tuple[str, ...]) -> str:
        if values is None:
            return "—"
        return "  ".join(f"{label}:{int(value)}" for label, value in zip(labels, values))

    def _build_3d_scene(self) -> None:
        self._reset_3d_camera()

        self.ground_grid = gl.GLGridItem()
        self.ground_grid.setSize(8, 8)
        self.ground_grid.setSpacing(1, 1)
        self.gl_view.addItem(self.ground_grid)

        world_origin = np.array([0.0, 0.0, 0.0], dtype=float)
        world_axis_endpoints = np.array(
            [
                [3.2, 0.0, 0.0],
                [0.0, 3.2, 0.0],
                [0.0, 0.0, 3.2],
            ],
            dtype=float,
        )
        axis_colors = (
            (1.0, 0.20, 0.20, 1.0),
            (0.20, 1.0, 0.30, 1.0),
            (0.25, 0.55, 1.0, 1.0),
        )

        self.world_axis_items = []
        for endpoint, color in zip(world_axis_endpoints, axis_colors):
            axis_item = gl.GLLinePlotItem(
                pos=np.vstack((world_origin, endpoint)),
                color=color,
                width=5.0,
                antialias=True,
                mode="lines",
            )
            self.gl_view.addItem(axis_item)
            self.world_axis_items.append(axis_item)

        world_label_font = QFont()
        world_label_font.setPointSize(15)
        world_label_font.setBold(True)
        world_label_specs = (
            ("E", (3.4, 0.0, 0.05), "#ff4d4d"),
            ("W", (-3.4, 0.0, 0.05), "#ffdddd"),
            ("N", (0.0, 3.4, 0.05), "#4dff66"),
            ("S", (0.0, -3.4, 0.05), "#ddffdd"),
            ("U", (0.0, 0.0, 3.4), "#66a3ff"),
        )
        self.world_direction_labels = {}
        for text, position, color in world_label_specs:
            label = gl.GLTextItem(
                pos=position,
                text=text,
                color=color,
                font=world_label_font,
            )
            self.gl_view.addItem(label)
            self.world_direction_labels[text] = label

        verts = np.array(
            [
                [-0.35, -0.35, 0.0],
                [0.35, -0.35, 0.0],
                [0.35, 0.35, 0.0],
                [-0.35, 0.35, 0.0],
                [0.00, 0.00, 2.2],
            ],
            dtype=float,
        )

        faces = np.array(
            [
                [0, 1, 4],
                [1, 2, 4],
                [2, 3, 4],
                [3, 0, 4],
                [0, 1, 2],
                [0, 2, 3],
            ],
            dtype=np.uint32,
        )

        colors = np.array(
            [
                [1.00, 0.25, 0.25, 1.00],
                [0.20, 0.80, 0.35, 1.00],
                [0.20, 0.55, 1.00, 1.00],
                [1.00, 0.85, 0.20, 1.00],
                [0.45, 0.45, 0.45, 1.00],
                [0.35, 0.35, 0.35, 1.00],
            ],
            dtype=float,
        )

        self.base_vertices = verts.copy()
        self.base_faces = faces.copy()
        self.base_colors = colors.copy()

        mesh_data = gl.MeshData(vertexes=verts, faces=faces, faceColors=colors)
        self.mesh_item = gl.GLMeshItem(
            meshdata=mesh_data,
            smooth=False,
            drawEdges=True,
            edgeColor=(1, 1, 1, 1),
            shader="shaded",
        )
        self.gl_view.addItem(self.mesh_item)

        self.body_axis_origin = np.array([0.0, 0.0, 0.0], dtype=float)
        self.body_axis_endpoints = np.array(
            [
                [1.1, 0.0, 0.0],
                [0.0, 1.1, 0.0],
                [0.0, 0.0, 1.6],
            ],
            dtype=float,
        )
        self.body_nose_label_position = np.array([0.0, 0.0, 2.45], dtype=float)

        self.body_axis_items = []
        for endpoint, color in zip(self.body_axis_endpoints, axis_colors):
            axis_item = gl.GLLinePlotItem(
                pos=np.vstack((self.body_axis_origin, endpoint)),
                color=color,
                width=3.5,
                antialias=True,
                mode="lines",
            )
            self.gl_view.addItem(axis_item)
            self.body_axis_items.append(axis_item)

        body_label_font = QFont()
        body_label_font.setPointSize(12)
        body_label_font.setBold(True)
        body_label_specs = (
            ("Xb", "#ff4d4d"),
            ("Yb", "#4dff66"),
            ("Zb", "#66a3ff"),
        )
        self.body_axis_labels = []
        for endpoint, (text, color) in zip(self.body_axis_endpoints, body_label_specs):
            label = gl.GLTextItem(
                pos=endpoint * 1.10,
                text=text,
                color=color,
                font=body_label_font,
            )
            self.gl_view.addItem(label)
            self.body_axis_labels.append(label)

        self.body_nose_label = gl.GLTextItem(
            pos=self.body_nose_label_position,
            text="NOSE",
            color="#f2f2f2",
            font=body_label_font,
        )
        self.gl_view.addItem(self.body_nose_label)

    def _set_3d_camera_unlocked(self, unlocked: bool) -> None:
        """Toggle mouse rotation and panning while preserving wheel zoom."""
        self.gl_view.set_camera_locked(not unlocked)
        self.btn_toggle_camera_lock.setText(
            self.i18n.tr("camera.lock" if unlocked else "camera.unlock")
        )

    def _reset_3d_camera(self) -> None:
        """Restore the shared default camera position and observation center."""
        self.gl_view.setCameraPosition(
            pos=QVector3D(*self.DEFAULT_CAMERA_CENTER),
            distance=self.DEFAULT_CAMERA_DISTANCE,
            elevation=self.DEFAULT_CAMERA_ELEVATION,
            azimuth=self.DEFAULT_CAMERA_AZIMUTH,
        )

    def _update_body_frame_items(self, rot: np.ndarray) -> None:
        """Apply the rocket rotation matrix to its body axes and labels."""
        rotated_origin = self.body_axis_origin @ rot.T
        rotated_endpoints = self.body_axis_endpoints @ rot.T

        for axis_item, endpoint in zip(self.body_axis_items, rotated_endpoints):
            axis_item.setData(pos=np.vstack((rotated_origin, endpoint)))

        for label, endpoint in zip(self.body_axis_labels, rotated_endpoints):
            label.setData(pos=endpoint * 1.10)

        rotated_nose_label_position = self.body_nose_label_position @ rot.T
        self.body_nose_label.setData(pos=rotated_nose_label_position)

    def _build_plots(self) -> None:
        pg.setConfigOptions(antialias=True)
        titles = [
            ("plot.velocity_e", "vel", 0),
            ("plot.velocity_n", "vel", 1),
            ("plot.velocity_u", "vel", 2),
            ("plot.position_e", "pos", 0),
            ("plot.position_n", "pos", 1),
            ("plot.position_u", "pos", 2),
        ]
        self.curves = {}
        self.plot_widgets = {}
        self.plot_title_keys = {}
        for index, (title_key, key, axis) in enumerate(titles):
            row, column = divmod(index, 3)
            plot_widget = pg.PlotWidget(title=self.i18n.tr(title_key))
            plot_widget.setMinimumSize(240, 190)
            plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            plot_widget.showGrid(x=True, y=True, alpha=0.3)
            plot_widget.setLabel("bottom", self.i18n.tr("plot.mission_time"))
            plot_widget.disableAutoRange(axis=pg.ViewBox.XAxis)
            plot_widget.getAxis("left").setWidth(72)
            plot_widget.getAxis("bottom").setHeight(32)
            curve = plot_widget.plot(pen=pg.mkPen(width=2))
            self.plot_layout.addWidget(plot_widget, row, column)
            self.curves[(key, axis)] = curve
            self.plot_widgets[(key, axis)] = plot_widget
            self.plot_title_keys[(key, axis)] = title_key

    def _retranslate_plots(self) -> None:
        if not hasattr(self, "plot_widgets"):
            return
        for key, plot_widget in self.plot_widgets.items():
            plot_widget.setTitle(self.i18n.tr(self.plot_title_keys[key]))
            plot_widget.setLabel("bottom", self.i18n.tr("plot.mission_time"))

    def _normalize_quat(
        self,
        quat: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float] | None:
        w, x, y, z = quat
        norm = (w * w + x * x + y * y + z * z) ** 0.5
        if norm == 0:
            return None
        return w / norm, x / norm, y / norm, z / norm

    def _apply_quat_to_mesh(self, quat: tuple[float, float, float, float]) -> None:
        normalized = self._normalize_quat(quat)
        if normalized is None or not np.all(np.isfinite(normalized)):
            return

        w, x, y, z = normalized
        rot = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ],
            dtype=float,
        )
        rotated = self.base_vertices @ rot.T
        mesh = gl.MeshData(
            vertexes=rotated,
            faces=self.base_faces,
            faceColors=self.base_colors,
        )
        self.mesh_item.setMeshData(meshdata=mesh)
        self._update_body_frame_items(rot)


def time_monotonic_ns() -> int:
    # Kept as a tiny indirection so GUI age rendering can be deterministic in
    # tests without changing the state model.
    import time

    return time.monotonic_ns()


def yes_no_text(i18n: I18n, value: bool) -> str:
    return i18n.tr("common.yes" if value else "common.no")


__all__ = ["AttitudeGLViewWidget", "CalibrationDialog", "MainWindow"]
