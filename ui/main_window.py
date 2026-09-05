from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCursor, QVector3D
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import (
    APP_WINDOW_TITLE,
    PLOT_REFRESH_INTERVAL_MS,
    PLOT_WINDOW_SECONDS,
)
from protocol.common import (
    AirAckResult,
    AirAlignmentState,
    AirCalibrationDiagnosticReason,
    AirCalibrationMode,
    AirCalibrationModeMask,
    AirCalibrationState,
    AirLifecycleState,
    AirSensorDetailCode,
    AirSensorStatusFlag,
    AirSensorSummaryFlag,
    AirStatusId,
    GspAckResult,
    air_sensor_descriptor,
    enum_name,
)
from services.data_migration import DataMigrationConflictPolicy
from services.i18n import I18n, Language
from services.preferences import (
    ALL_EXPORT_ITEMS,
    AppPreferences,
    ExportItem,
    ExportLanguage,
    ResolvedExportOptions,
    Theme,
    resolve_export_language,
)
from services.state_model import (
    CalibrationStartResult,
    EventHistory,
    FlightControllerState,
    HandshakeState,
    MissionPhase,
)
from ui.theme import ThemeColors, apply_application_theme, theme_colors


def calibration_mode_text(i18n: I18n, state: FlightControllerState) -> str:
    if state.calibration.mode == int(AirCalibrationMode.NONE):
        return i18n.tr("cal.identity")
    return i18n.enum("calibration_mode", enum_name(AirCalibrationMode, state.calibration.mode))


def calibration_capability_text(i18n: I18n, state: FlightControllerState) -> str:
    if not state.capability_acked or state.capability is None:
        return i18n.tr("cal.wait_capability")
    modes = state.sampling_calibration_modes()
    if modes:
        return i18n.tr(
            "cal.sampling_modes",
            modes=" / ".join(i18n.enum("calibration_mode", mode.name) for mode in modes),
        )
    if state.capability.calibration_mode_mask & int(AirCalibrationModeMask.NONE):
        return i18n.tr("cal.identity_only")
    return i18n.tr("cal.no_sampling_modes")


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


def sensor_display_name(i18n: I18n, sensor_id: int) -> str:
    descriptor = air_sensor_descriptor(sensor_id)
    if descriptor is None:
        return i18n.tr("sensor.unknown_name", sensor_id=sensor_id & 0xFF)
    return i18n.enum("sensor", descriptor.canonical_name)


def sensor_detail_text(i18n: I18n, detail_code: int) -> str:
    try:
        name = AirSensorDetailCode(int(detail_code)).name
    except ValueError:
        return i18n.tr("sensor.unknown_detail", detail_code=detail_code & 0xFF)
    return i18n.enum("sensor_detail", name)


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
        self.setMinimumWidth(780)

        self.on_start: Callable[[int], None] | None = None
        self.on_face: Callable[[int], None] | None = None
        self.on_stop: Callable[[], None] | None = None
        self.on_reset: Callable[[], None] | None = None
        self._last_mode_context: tuple[int, bool, int] | None = None
        self._state: FlightControllerState | None = None

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
        self._last_mode_context = None

    def _start_selected_mode(self) -> None:
        mode = self.mode_combo.currentData()
        if (
            self.btn_start.isEnabled()
            and self._state is not None
            and self._state.preflight_command_entry_allowed()
            and self._state.check_calibration_start(mode) is CalibrationStartResult.ALLOWED
            and self.on_start is not None
        ):
            self.on_start(int(mode))

    def render(self, state: FlightControllerState) -> None:
        self._state = state
        capability = state.capability
        mask = capability.calibration_mode_mask if capability is not None else 0
        context = (state.session_generation, state.capability_acked, mask)
        if context != self._last_mode_context:
            selected = (
                self.mode_combo.currentData()
                if self._last_mode_context is None
                or self._last_mode_context[0] == state.session_generation
                else None
            )
            self.mode_combo.clear()
            for mode in state.calibration_start_modes():
                canonical = mode.name
                self.mode_combo.addItem(
                    self.i18n.tr("cal.default_correction")
                    if mode == AirCalibrationMode.NONE
                    else f"{canonical}（{self.i18n.enum('calibration_mode', canonical)}）",
                    int(mode),
                )
            if selected is not None:
                index = self.mode_combo.findData(selected)
                if index >= 0:
                    self.mode_combo.setCurrentIndex(index)
            self._last_mode_context = context
        self.mode_combo.setEnabled(bool(state.calibration_start_modes()))

        calibration = state.calibration
        mode_name = calibration_mode_text(self.i18n, state)
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
        if not state.calibration_start_modes():
            guidance = calibration_capability_text(self.i18n, state)
        elif calibration.mode == int(AirCalibrationMode.ONE_FACE):
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
            "color: palette(bright-text); font-weight: bold;"
            if state.latest_calibration_diagnostic_reason
            else ""
        )

        preflight_entry_allowed = state.preflight_command_entry_allowed()
        calibration_pending = state.calibration_transaction_pending()
        no_command_pending = not state.pending_command_name
        calibration_idle = bool(
            calibration.mode in (int(AirCalibrationMode.NONE), int(AirCalibrationMode.NOT_SELECTED))
            or calibration.state in (None, int(AirCalibrationState.IDLE))
        )
        self.btn_start.setText(
            self.i18n.tr(
                "button.cal_start" if calibration_idle else "button.cal_restart"
            )
        )
        # Starting/restarting calibration is also the escape hatch for a lost
        # calibration ACK.  The controller supersedes only same-domain pending
        # transactions; unrelated commands remain protected.
        self.btn_start.setEnabled(
            preflight_entry_allowed
            and self.mode_combo.count() > 0
            and (no_command_pending or calibration_pending)
        )
        self.btn_stop.setEnabled(preflight_entry_allowed and no_command_pending)
        self.btn_reset.setEnabled(preflight_entry_allowed and no_command_pending)

        six_face_active = calibration.mode == int(AirCalibrationMode.SIX_FACE)
        waiting_for_face = calibration.state in (
            int(AirCalibrationState.WAIT_FACE),
            int(AirCalibrationState.READY),
        )
        collecting = calibration.state in (
            int(AirCalibrationState.COLLECTING),
            int(AirCalibrationState.CHECKING),
        )
        if collecting:
            if current_face == "—":
                collection_text = self.i18n.tr("cal.collecting_wait_unknown")
            elif calibration.state == int(AirCalibrationState.CHECKING):
                collection_text = self.i18n.tr(
                    "cal.checking_wait", face=current_face
                )
            else:
                collection_text = self.i18n.tr(
                    "cal.collecting_wait", face=current_face
                )
            self.lbl_issue.setText(
                f"{self.i18n.tr('field.current_issue')}: {collection_text}"
                + (f"\n{issue}" if state.latest_calibration_diagnostic_reason else "")
            )
        for face, (status_label, button) in enumerate(
            zip(self.face_status_labels, self.face_buttons)
        ):
            completed = bool(calibration.completed_face_mask & (1 << face))
            active = calibration.current_face == face
            status_label.setText("✓" if completed else ("●" if active else "○"))
            button.setText(
                self.i18n.tr(
                    "button.recollect_face" if completed else "button.collect_face",
                    face=self.FACE_NAMES[face],
                )
            )
            button.setToolTip(
                self.i18n.tr(
                    "cal.face.completed_tooltip"
                    if completed
                    else "cal.face.collect_tooltip"
                )
            )
            # A completed face remains selectable in WAIT_FACE or READY.  Only the
            # flight-controller snapshot/event may clear or restore its check.
            button.setEnabled(
                preflight_entry_allowed
                and no_command_pending
                and six_face_active
                and waiting_for_face
            )


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

    def _set_text_preserving_scroll(self, new_text: str) -> bool:
        if new_text == self.details_text.toPlainText():
            return False
        scrollbar = self.details_text.verticalScrollBar()
        old_value = scrollbar.value()
        old_maximum = scrollbar.maximum()
        was_at_bottom = old_value >= max(0, old_maximum - 1)
        old_cursor = self.details_text.textCursor()
        old_position = old_cursor.position()
        old_anchor = old_cursor.anchor()

        self.details_text.setPlainText(new_text)
        document_length = max(0, self.details_text.document().characterCount() - 1)
        restored_cursor = self.details_text.textCursor()
        restored_cursor.setPosition(
            min(old_anchor, document_length), QTextCursor.MoveAnchor
        )
        restored_cursor.setPosition(
            min(old_position, document_length), QTextCursor.KeepAnchor
        )
        self.details_text.setTextCursor(restored_cursor)
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(old_value, scrollbar.maximum()))
        return True

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
        known_mask = int(
            AirCalibrationModeMask.NONE
            | AirCalibrationModeMask.ONE_FACE
            | AirCalibrationModeMask.SIX_FACE
        )

        def age_text(rx_ns: int | None) -> str:
            if rx_ns is None:
                return none
            return f"{max(0.0, (time_monotonic_ns() - rx_ns) / 1_000_000.0):.0f} ms"

        new_text = self.i18n.tr(
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
                unknown_calibration_bits=(
                    none if capability is None
                    else f"0x{capability.calibration_mode_mask & ~known_mask:02X}"
                ),
                capability_rx=diagnostics.capability_rx,
                preflight_status_rx=diagnostics.preflight_status_rx,
                air_rx_age=age_text(diagnostics.last_air_rx_monotonic_ns),
                preflight_status_age=age_text(diagnostics.last_preflight_status_rx_monotonic_ns),
                last_air_command=self.i18n.format_message(state.last_air_ack_message),
                sensor_flags=(
                    none
                    if capability is None
                    else f"0x{capability.sensor_summary_flags:02X}"
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
        self._set_text_preserving_scroll(new_text)


class SensorDetailsDialog(QDialog):
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
        self.setWindowTitle(self.i18n.tr("dialog.sensor_details.title"))
        self.btn_close.setText(self.i18n.tr("button.close"))

    def render(self, state: FlightControllerState) -> None:
        snapshot = state.alignment_sensor_snapshots.latest_terminal_snapshot
        if snapshot is None:
            text = self.i18n.tr("sensor_snapshot.none")
        else:
            result_name = (
                self.i18n.tr("common.none")
                if snapshot.terminal_alignment_state is None
                else self.i18n.enum(
                    "alignment_state",
                    enum_name(
                        AirAlignmentState, snapshot.terminal_alignment_state
                    ),
                )
            )
            source = self.i18n.tr(
                "sensor_snapshot.source_last"
                if state.alignment.state == int(AirAlignmentState.STALE)
                else "sensor_snapshot.source_alignment"
            )
            expected = (
                self.i18n.tr("common.unknown")
                if snapshot.expected_total is None
                else str(snapshot.expected_total)
            )
            lines = [
                self.i18n.tr("sensor_snapshot.heading"),
                self.i18n.tr("sensor_snapshot.source", source=source),
                self.i18n.tr("sensor_snapshot.id", snapshot_id=snapshot.snapshot_id),
                self.i18n.tr(
                    "sensor_snapshot.complete",
                    value=yes_no_text(self.i18n, snapshot.complete),
                ),
                self.i18n.tr("sensor_snapshot.result", result=result_name),
                self.i18n.tr(
                    "sensor_snapshot.count",
                    received=len(snapshot.frames_by_index),
                    expected=expected,
                ),
            ]
            if snapshot.incomplete:
                lines.append(self.i18n.tr("sensor_snapshot.incomplete"))
            lines.append("")
            for frame in snapshot.ordered_frames():
                lines.append(
                    self.i18n.tr(
                        "sensor_snapshot.sensor_heading",
                        name=sensor_display_name(self.i18n, frame.sensor_id),
                        instance=frame.instance_id,
                    )
                )
                for flag in AirSensorStatusFlag:
                    lines.append(
                        self.i18n.tr(
                            "sensor_snapshot.flag_line",
                            name=self.i18n.tr(f"sensor.flag.{flag.name}"),
                            value=yes_no_text(
                                self.i18n, bool(frame.status_flags & int(flag))
                            ),
                        )
                    )
                lines.append(
                    self.i18n.tr(
                        "sensor_snapshot.detail",
                        detail=sensor_detail_text(self.i18n, frame.detail_code),
                    )
                )
                lines.append(
                    self.i18n.tr(
                        "sensor_snapshot.raw_flags", flags=frame.status_flags
                    )
                )
                lines.append("")
            text = "\n".join(lines).rstrip()
        if text != self.details_text.toPlainText():
            self.details_text.setPlainText(text)


@dataclass(frozen=True)
class DataDirectorySelection:
    data_root: Path
    migrate_existing_data: bool
    conflict_policy: DataMigrationConflictPolicy = (
        DataMigrationConflictPolicy.OVERWRITE
    )


class DataDirectoryDialog(QDialog):
    def __init__(self, i18n: I18n, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._current_root = Path()
        self._selected_root: Path | None = None
        self.setMinimumWidth(680)

        root = QVBoxLayout(self)
        self.lbl_path = QLabel()
        root.addWidget(self.lbl_path)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.btn_browse = QPushButton()
        self.btn_browse.setMinimumWidth(112)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.btn_browse)
        root.addLayout(path_row)

        self.chk_migrate_existing = QCheckBox()
        root.addWidget(self.chk_migrate_existing)
        self.lbl_migration_note = QLabel()
        self.lbl_migration_note.setWordWrap(True)
        root.addWidget(self.lbl_migration_note)

        conflict_row = QHBoxLayout()
        self.lbl_conflict_policy = QLabel()
        self.conflict_policy_combo = QComboBox()
        self.conflict_policy_combo.setMinimumWidth(280)
        conflict_row.addWidget(self.lbl_conflict_policy)
        conflict_row.addWidget(self.conflict_policy_combo, 1)
        root.addLayout(conflict_row)

        self.lbl_error = QLabel()
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setStyleSheet("color: palette(bright-text); font-weight: bold;")
        self.lbl_error.hide()
        root.addWidget(self.lbl_error)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.btn_cancel = QPushButton()
        self.btn_accept = QPushButton()
        self.btn_cancel.setMinimumWidth(104)
        self.btn_accept.setMinimumWidth(104)
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_accept)
        root.addLayout(action_row)

        self.path_edit.textChanged.connect(self._update_controls)
        self.chk_migrate_existing.toggled.connect(self._update_controls)
        self.btn_browse.clicked.connect(self._browse)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_accept.clicked.connect(self.accept)
        self.retranslate_ui()

    @staticmethod
    def _paths_equal(left: Path, right: Path) -> bool:
        try:
            return left.resolve(strict=False) == right.resolve(strict=False)
        except OSError:
            return str(left).casefold() == str(right).casefold()

    def prepare(self, current_root: Path | str) -> None:
        self._current_root = Path(current_root).expanduser()
        self._selected_root = None
        self.path_edit.setText(str(self._current_root))
        self.chk_migrate_existing.setChecked(False)
        overwrite_index = self.conflict_policy_combo.findData(
            DataMigrationConflictPolicy.OVERWRITE.value
        )
        self.conflict_policy_combo.setCurrentIndex(max(0, overwrite_index))
        self.lbl_error.clear()
        self.lbl_error.hide()
        self._update_controls()

    def selection(self) -> DataDirectorySelection:
        selected_root = self._selected_root
        if selected_root is None:
            selected_root = Path(self.path_edit.text().strip()).expanduser()
        policy_value = self.conflict_policy_combo.currentData()
        try:
            conflict_policy = DataMigrationConflictPolicy(policy_value)
        except (TypeError, ValueError):
            conflict_policy = DataMigrationConflictPolicy.OVERWRITE
        return DataDirectorySelection(
            data_root=selected_root,
            migrate_existing_data=(
                self.chk_migrate_existing.isEnabled()
                and self.chk_migrate_existing.isChecked()
            ),
            conflict_policy=conflict_policy,
        )

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("data_directory.dialog.title"))
        self.lbl_path.setText(self.i18n.tr("data_directory.path.label"))
        self.path_edit.setPlaceholderText(
            self.i18n.tr("data_directory.path.placeholder")
        )
        self.btn_browse.setText(self.i18n.tr("button.browse"))
        self.chk_migrate_existing.setText(
            self.i18n.tr("data_directory.migrate_existing")
        )
        self.lbl_migration_note.setText(
            self.i18n.tr("data_directory.migrate_note")
        )
        selected_policy = (
            self.conflict_policy_combo.currentData()
            or DataMigrationConflictPolicy.OVERWRITE.value
        )
        self.lbl_conflict_policy.setText(
            self.i18n.tr("data_directory.conflict.label")
        )
        self.conflict_policy_combo.clear()
        for policy in DataMigrationConflictPolicy:
            self.conflict_policy_combo.addItem(
                self.i18n.tr(f"data_directory.conflict.{policy.value}"),
                policy.value,
            )
        selected_index = self.conflict_policy_combo.findData(selected_policy)
        if selected_index < 0:
            selected_index = self.conflict_policy_combo.findData(
                DataMigrationConflictPolicy.OVERWRITE.value
            )
        self.conflict_policy_combo.setCurrentIndex(max(0, selected_index))
        self.btn_cancel.setText(self.i18n.tr("button.cancel"))
        self.btn_accept.setText(self.i18n.tr("button.confirm"))

    def _browse(self) -> None:
        initial_path = self.path_edit.text().strip() or str(self._current_root)
        selected_path = QFileDialog.getExistingDirectory(
            self,
            self.i18n.tr("data_directory.browse.title"),
            initial_path,
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected_path:
            self.path_edit.setText(selected_path)

    def _update_controls(self) -> None:
        path_text = self.path_edit.text().strip()
        changed = False
        if path_text:
            try:
                changed = not self._paths_equal(
                    Path(path_text).expanduser(),
                    self._current_root,
                )
            except (OSError, ValueError):
                changed = True
        self.chk_migrate_existing.setEnabled(changed)
        if not changed:
            self.chk_migrate_existing.setChecked(False)
        conflict_enabled = (
            changed
            and self.chk_migrate_existing.isEnabled()
            and self.chk_migrate_existing.isChecked()
        )
        self.lbl_conflict_policy.setEnabled(conflict_enabled)
        self.conflict_policy_combo.setEnabled(conflict_enabled)
        self.btn_accept.setEnabled(bool(path_text))
        self.lbl_error.hide()

    def accept(self) -> None:
        path_text = self.path_edit.text().strip()
        if not path_text:
            self.lbl_error.setText(self.i18n.tr("data_directory.error.empty"))
            self.lbl_error.show()
            return
        selected_root = Path(path_text).expanduser()
        if not selected_root.is_absolute():
            self.lbl_error.setText(self.i18n.tr("data_directory.error.absolute"))
            self.lbl_error.show()
            return
        self._selected_root = selected_root
        super().accept()


class DataMigrationProgressDialog(QDialog):
    def __init__(self, i18n: I18n, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.on_cancel: Callable[[], None] | None = None
        self._active = False
        self._cancel_requested = False
        self._status_key = "data_migration.scanning"
        self._status_params: dict[str, object] = {}
        self.setMinimumWidth(620)
        self.setModal(True)

        root = QVBoxLayout(self)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(34)
        root.addWidget(self.progress_bar)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.btn_action = QPushButton()
        action_row.addWidget(self.btn_action)
        root.addLayout(action_row)
        self.btn_action.clicked.connect(self._action_clicked)
        self.retranslate_ui()

    @staticmethod
    def _format_bytes(byte_count: int) -> str:
        value = float(max(0, int(byte_count)))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024.0 or unit == "TB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} TB"

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("data_migration.dialog.title"))
        self.lbl_status.setText(
            self.i18n.tr(self._status_key, **self._status_params)
        )
        self.btn_action.setText(
            self.i18n.tr(
                "button.cancel_migration" if self._active else "button.close"
            )
        )

    def begin(self, source_root: Path | str, target_root: Path | str) -> None:
        self._active = True
        self._cancel_requested = False
        self._status_key = "data_migration.scanning"
        self._status_params = {
            "source": str(source_root),
            "target": str(target_root),
        }
        self.progress_bar.setRange(0, 0)
        self.btn_action.setEnabled(True)
        self.retranslate_ui()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_plan(self, file_count: int, byte_count: int) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._status_key = "data_migration.preparing"
        self._status_params = {
            "files": int(file_count),
            "size": self._format_bytes(byte_count),
        }
        self.retranslate_ui()

    def update_progress(self, percent: int, detail: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(max(0, min(100, int(percent))))
        self._status_key = "data_migration.copying"
        self._status_params = {"detail": detail}
        self.retranslate_ui()

    def mark_committing(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(max(95, self.progress_bar.value()))
        self._status_key = "data_migration.committing"
        self._status_params = {}
        self.btn_action.setEnabled(False)
        self.retranslate_ui()

    def mark_cancelling(self) -> None:
        self._status_key = "data_migration.cancelling"
        self._status_params = {}
        self.btn_action.setEnabled(False)
        self.retranslate_ui()

    def finish_completed(
        self,
        file_count: int,
        target_root: Path | str,
        warning: str = "",
        skipped_count: int = 0,
    ) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        if skipped_count and warning:
            self._status_key = "data_migration.completed_skipped_warning"
            self._status_params = {
                "files": int(file_count),
                "skipped": int(skipped_count),
                "target": str(target_root),
                "warning": warning,
            }
        elif skipped_count:
            self._status_key = "data_migration.completed_skipped"
            self._status_params = {
                "files": int(file_count),
                "skipped": int(skipped_count),
                "target": str(target_root),
            }
        elif warning:
            self._status_key = "data_migration.completed_warning"
            self._status_params = {
                "files": int(file_count),
                "target": str(target_root),
                "warning": warning,
            }
        else:
            self._status_key = "data_migration.completed"
            self._status_params = {
                "files": int(file_count),
                "target": str(target_root),
            }
        self._finish()

    def finish_cancelled(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._status_key = "data_migration.cancelled"
        self._status_params = {}
        self._finish()

    def finish_failed(self, error: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._status_key = "data_migration.failed"
        self._status_params = {"error": error}
        self._finish()

    def _finish(self) -> None:
        self._active = False
        self._cancel_requested = False
        self.btn_action.setEnabled(True)
        self.retranslate_ui()

    def _action_clicked(self) -> None:
        if not self._active:
            self.accept()
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.mark_cancelling()
        if self.on_cancel is not None:
            self.on_cancel()

    def reject(self) -> None:
        if self._active:
            self._action_clicked()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._active:
            event.ignore()
            self._action_clicked()
            return
        super().closeEvent(event)


class ExportOptionsDialog(QDialog):
    def __init__(
        self,
        i18n: I18n,
        preferences: AppPreferences,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.preferences = preferences
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        language_row = QHBoxLayout()
        self.lbl_language = QLabel()
        self.language_combo = QComboBox()
        language_row.addWidget(self.lbl_language)
        language_row.addWidget(self.language_combo, 1)
        root.addLayout(language_row)

        self.items_group = QGroupBox()
        items_layout = QVBoxLayout(self.items_group)
        self.item_checkboxes: dict[ExportItem, QCheckBox] = {}
        for item in ALL_EXPORT_ITEMS:
            checkbox = QCheckBox()
            checkbox.toggled.connect(self._update_accept_enabled)
            items_layout.addWidget(checkbox)
            self.item_checkboxes[item] = checkbox
        selection_row = QHBoxLayout()
        self.btn_select_all = QPushButton()
        self.btn_select_none = QPushButton()
        selection_row.addWidget(self.btn_select_all)
        selection_row.addWidget(self.btn_select_none)
        selection_row.addStretch(1)
        items_layout.addLayout(selection_row)
        root.addWidget(self.items_group)

        self.lbl_suffix_note = QLabel()
        self.lbl_suffix_note.setWordWrap(True)
        root.addWidget(self.lbl_suffix_note)
        self.lbl_required = QLabel()
        self.lbl_required.setStyleSheet(
            "color: palette(bright-text); font-weight: bold;"
        )
        self.lbl_required.setWordWrap(True)
        root.addWidget(self.lbl_required)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.btn_cancel = QPushButton()
        self.btn_accept = QPushButton()
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_accept)
        root.addLayout(action_row)

        self.btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
        self.btn_select_none.clicked.connect(lambda: self._set_all_checked(False))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_accept.clicked.connect(self.accept)
        self.retranslate_ui()
        self.reload_preferences()

    def retranslate_ui(self) -> None:
        selected_language = self.language_combo.currentData()
        self.setWindowTitle(self.i18n.tr("export.dialog.title"))
        self.lbl_language.setText(self.i18n.tr("export.language.label"))
        self.language_combo.clear()
        for language in ExportLanguage:
            self.language_combo.addItem(
                self.i18n.tr(f"export.language.{language.value}"),
                language.value,
            )
        if selected_language is not None:
            index = self.language_combo.findData(selected_language)
            if index >= 0:
                self.language_combo.setCurrentIndex(index)
        self.items_group.setTitle(self.i18n.tr("export.items.title"))
        for item, checkbox in self.item_checkboxes.items():
            checkbox.setText(self.i18n.tr(f"export.item.{item.value}"))
        self.btn_select_all.setText(self.i18n.tr("button.select_all"))
        self.btn_select_none.setText(self.i18n.tr("button.select_none"))
        self.btn_cancel.setText(self.i18n.tr("button.cancel"))
        self.btn_accept.setText(self.i18n.tr("button.export_start"))
        self.lbl_suffix_note.setText(self.i18n.tr("export.suffix.note"))
        self.lbl_required.setText(self.i18n.tr("export.items.required"))
        self._update_accept_enabled()

    def reload_preferences(self) -> None:
        language = self.preferences.export_language()
        index = self.language_combo.findData(language.value)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        selected = self.preferences.export_items()
        for item, checkbox in self.item_checkboxes.items():
            checkbox.setChecked(item in selected)
        self._update_accept_enabled()

    def _set_all_checked(self, checked: bool) -> None:
        for checkbox in self.item_checkboxes.values():
            checkbox.setChecked(checked)
        self._update_accept_enabled()

    def _update_accept_enabled(self) -> None:
        has_selection = any(
            checkbox.isChecked() for checkbox in self.item_checkboxes.values()
        )
        self.btn_accept.setEnabled(has_selection)
        self.lbl_required.setVisible(not has_selection)

    def resolved_options(
        self,
        ui_language: Language,
        theme: Theme,
    ) -> ResolvedExportOptions:
        export_language = ExportLanguage(str(self.language_combo.currentData()))
        items = frozenset(
            item
            for item, checkbox in self.item_checkboxes.items()
            if checkbox.isChecked()
        )
        self.preferences.set_export_language(export_language)
        self.preferences.set_export_items(items)
        return ResolvedExportOptions(
            language=resolve_export_language(export_language, ui_language),
            theme=theme,
            items=items,
        )


class MainWindow(QMainWindow):
    DEFAULT_CAMERA_DISTANCE = 6.5
    DEFAULT_CAMERA_ELEVATION = 20.0
    DEFAULT_CAMERA_AZIMUTH = 35.0
    DEFAULT_CAMERA_CENTER = (0.0, 0.0, 0.9)
    ROCKET_FACE_COLOR_HEX = {
        Theme.LIGHT: (
            "#ff5a5f",
            "#22c55e",
            "#3b82f6",
            "#f5b942",
            "#a8b3c2",
            "#7f8b9d",
        ),
        Theme.DARK: (
            "#ff7b86",
            "#4ade80",
            "#60a5fa",
            "#facc15",
            "#cbd5e1",
            "#94a3b8",
        ),
    }

    def __init__(self, i18n: I18n | None = None) -> None:
        super().__init__()
        self.i18n = i18n or I18n()
        self.preferences = AppPreferences(self.i18n.settings)
        self.theme = self.preferences.theme()
        self._theme_colors: ThemeColors = theme_colors(self.theme)
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
        self.on_cancel_sim_data: Callable[[], None] | None = None
        self.on_process_data: Callable[[], None] | None = None
        self.on_cancel_process_data: Callable[[], None] | None = None
        self.on_select_data_root: Callable[[], None] | None = None
        self.on_cancel_data_migration: Callable[[], None] | None = None
        self.on_open_log_dir: Callable[[], None] | None = None
        self.on_open_data_dir: Callable[[], None] | None = None
        self.on_language_changed: Callable[[], None] | None = None

        self._state: FlightControllerState | None = None
        self._events: EventHistory | None = None
        self._data_tools_busy = False
        self._simulation_task_active = False
        self._simulation_cancel_enabled = False
        self._simulation_status_key = "task.simulation.idle"
        self._simulation_status_params: dict[str, object] = {}
        self._processing_task_active = False
        self._processing_cancel_enabled = False
        self._processing_status_key = "task.processing.idle"
        self._processing_status_params: dict[str, object] = {}
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
        )

        self._build_ui()
        self._build_3d_scene()
        self._build_plots()
        self._apply_theme(self.theme, persist=False)
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

    def _on_theme_changed(self, index: int) -> None:
        theme_value = self.theme_combo.itemData(index)
        if theme_value is None:
            return
        selected = Theme(str(theme_value))
        if selected is self.theme:
            return
        self._apply_theme(selected, persist=True)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("app.title"))
        for widget, key, params in self._translation_bindings:
            text = self.i18n.tr(key, **params)
            if isinstance(widget, QGroupBox):
                widget.setTitle(text)
            else:
                widget.setText(text)  # type: ignore[attr-defined]
        self._render_data_task_status()
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
        self.theme_combo.setItemText(
            self.theme_combo.findData(Theme.LIGHT.value),
            self.i18n.tr("theme.light"),
        )
        self.theme_combo.setItemText(
            self.theme_combo.findData(Theme.DARK.value),
            self.i18n.tr("theme.dark"),
        )
        theme_index = self.theme_combo.findData(self.theme.value)
        if theme_index >= 0 and theme_index != self.theme_combo.currentIndex():
            blocked = self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(theme_index)
            self.theme_combo.blockSignals(blocked)
        self._set_3d_camera_unlocked(self.btn_toggle_camera_lock.isChecked())
        self.calibration_dialog.retranslate_ui()
        if self._state is not None:
            # Dynamic face/restart labels depend on the current canonical
            # calibration snapshot, even while the dialog is hidden.
            self.calibration_dialog.render(self._state)
        self.link_details_dialog.retranslate_ui()
        self.sensor_details_dialog.retranslate_ui()
        self.export_options_dialog.retranslate_ui()
        self.data_directory_dialog.retranslate_ui()
        self.data_migration_progress_dialog.retranslate_ui()
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
        self.pages.setObjectName("pageTabs")
        self.pages.setDocumentMode(True)
        self.pages.setUsesScrollButtons(False)
        self.pages.tabBar().setObjectName("pageNavigation")
        self.pages.tabBar().setExpanding(True)
        self.pages.tabBar().setDrawBase(False)
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
        self.sensor_details_dialog = SensorDetailsDialog(self.i18n, self)
        self.export_options_dialog = ExportOptionsDialog(
            self.i18n,
            self.preferences,
            self,
        )
        self.data_directory_dialog = DataDirectoryDialog(self.i18n, self)
        self.data_migration_progress_dialog = DataMigrationProgressDialog(
            self.i18n,
            self,
        )
        self.data_migration_progress_dialog.on_cancel = (
            lambda: self.on_cancel_data_migration
            and self.on_cancel_data_migration()
        )

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("headerBar")
        self.header_bar = bar
        root_layout = QVBoxLayout(bar)
        root_layout.setContentsMargins(10, 5, 10, 5)
        root_layout.setSpacing(3)
        connection_layout = QHBoxLayout()
        connection_layout.setSpacing(4)
        identity_layout = QHBoxLayout()
        identity_layout.setSpacing(4)
        self.header_connection_layout = connection_layout
        self.header_identity_layout = identity_layout

        self.header_title = QLabel()
        self.header_title.setObjectName("headerTitle")
        self._bind_text(self.header_title, "app.brand")
        connection_layout.addWidget(self.header_title)
        self.header_connection = QLabel()
        self.header_connection.setObjectName("headerSection")
        self._bind_text(self.header_connection, "group.connection")
        connection_layout.addWidget(self.header_connection)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumContentsLength(8)
        self.btn_refresh = QPushButton()
        self._bind_text(self.btn_refresh, "button.refresh_ports")
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(9600, 2_000_000)
        self.baud_spin.setValue(230400)
        self.baud_spin.setFixedWidth(88)
        self.btn_connect = QPushButton()
        self.btn_disconnect = QPushButton()
        self._bind_text(self.btn_connect, "button.connect")
        self._bind_text(self.btn_disconnect, "button.disconnect")
        self.conn_label = QLabel()
        self._configure_dynamic_label(self.conn_label, 110, show_tooltip=True)

        self.lbl_port_name = QLabel()
        self._bind_text(self.lbl_port_name, "field.port")
        connection_layout.addWidget(self.lbl_port_name)
        connection_layout.addWidget(self.port_combo)
        connection_layout.addWidget(self.btn_refresh)
        self.lbl_baud_name = QLabel()
        self._bind_text(self.lbl_baud_name, "field.baudrate")
        connection_layout.addWidget(self.lbl_baud_name)
        connection_layout.addWidget(self.baud_spin)
        connection_layout.addWidget(self.btn_connect)
        connection_layout.addWidget(self.btn_disconnect)
        connection_layout.addWidget(self.conn_label)
        connection_layout.addStretch(1)
        root_layout.addLayout(connection_layout)
        identity_layout.addStretch(1)

        self.header_version = QLabel()
        self.header_version.setObjectName("headerVersion")
        self._bind_text(self.header_version, "app.product_version")
        identity_layout.addWidget(self.header_version)
        self.header_credit = QLabel()
        self.header_credit.setObjectName("headerCredit")
        self._bind_text(self.header_credit, "app.credit")
        identity_layout.addWidget(self.header_credit)

        self.lbl_language = QLabel()
        self._bind_text(self.lbl_language, "language.label")
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", Language.ZH_CN.value)
        self.language_combo.addItem("English", Language.EN_US.value)
        self.language_combo.setMinimumWidth(88)
        identity_layout.addWidget(self.lbl_language)
        identity_layout.addWidget(self.language_combo)

        self.lbl_theme = QLabel()
        self._bind_text(self.lbl_theme, "theme.label")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("", Theme.LIGHT.value)
        self.theme_combo.addItem("", Theme.DARK.value)
        self.theme_combo.setMinimumWidth(70)
        identity_layout.addWidget(self.lbl_theme)
        identity_layout.addWidget(self.theme_combo)
        root_layout.addLayout(identity_layout)

        self.btn_refresh.clicked.connect(lambda: self.on_refresh_ports and self.on_refresh_ports())
        self.btn_connect.clicked.connect(lambda: self.on_connect_clicked and self.on_connect_clicked())
        self.btn_disconnect.clicked.connect(
            lambda: self.on_disconnect_clicked and self.on_disconnect_clicked()
        )
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        return bar

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
        self.lbl_cal_mode.setWordWrap(True)
        self.lbl_cal_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addLayout(grid)
        self.lbl_cal_capability = QLabel()
        self.lbl_cal_capability.setWordWrap(True)
        layout.addWidget(self.lbl_cal_capability)
        actions = QHBoxLayout()
        self.btn_calibration = QPushButton()
        self._bind_text(self.btn_calibration, "button.calibration")
        self.btn_calibration.setStyleSheet(self._cmd_button_style)
        actions.addWidget(self.btn_calibration)
        self.btn_cal_reset = QPushButton()
        self._bind_text(self.btn_cal_reset, "button.reset")
        self.btn_cal_reset.setStyleSheet(self._cmd_button_style)
        self.btn_cal_reset.clicked.connect(lambda: self.on_cal_reset and self.on_cal_reset())
        actions.addWidget(self.btn_cal_reset)
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
        self.lbl_align_snapshot = QLabel("—")
        self.lbl_align_hint = QLabel("—")
        self.lbl_align_hint.setWordWrap(True)
        self._add_value_pair(grid, 0, "field.state", self.lbl_align_state, 125, True)
        self._add_value_pair(grid, 1, "field.ready", self.lbl_align_ready, 125, True)
        self._add_value_pair(
            grid, 2, "field.sensor_snapshot", self.lbl_align_snapshot, 125, True
        )
        self._add_value_pair(
            grid, 3, "field.alignment_hint", self.lbl_align_hint, 125, True
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
        self.btn_sensor_details = QPushButton()
        self._bind_text(self.btn_align_start, "button.align_start")
        self._bind_text(self.btn_align_stop, "button.stop")
        self._bind_text(self.btn_align_reset, "button.reset")
        self._bind_text(self.btn_sensor_details, "button.sensor_details")
        for button in (
            self.btn_align_start,
            self.btn_align_stop,
            self.btn_align_reset,
            self.btn_sensor_details,
        ):
            button.setStyleSheet(self._cmd_button_style)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.btn_align_start.clicked.connect(lambda: self.on_align_start and self.on_align_start())
        self.btn_align_stop.clicked.connect(lambda: self.on_align_stop and self.on_align_stop())
        self.btn_align_reset.clicked.connect(lambda: self.on_align_reset and self.on_align_reset())
        self.btn_sensor_details.clicked.connect(self._show_sensor_details)
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
        self.lbl_pf_gnss_present = QLabel("—")
        self.lbl_pf_gnss_usable = QLabel("—")
        self._add_value_pair(
            grid, 0, "field.sensor_present", self.lbl_pf_gnss_present, 120, True
        )
        self._add_value_pair(grid, 1, "field.position_usable", self.lbl_pf_gnss_usable, 120, True)
        self.lbl_gnss_note = QLabel()
        self._bind_text(self.lbl_gnss_note, "note.gnss_profile")
        self.lbl_gnss_note.setWordWrap(True)
        grid.addWidget(self.lbl_gnss_note, 2, 0, 1, 2)
        return box

    def _build_preflight_command_panel(self) -> QWidget:
        box = QGroupBox()
        self._bind_text(box, "group.preflight_commands")
        layout = QVBoxLayout(box)
        command_row = QHBoxLayout()
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
            command_row.addWidget(button)
        command_row.addSpacing(16)
        self.lbl_start_reason = QLabel()
        self._bind_text(self.lbl_start_reason, "start.wait_capability")
        self._configure_dynamic_label(self.lbl_start_reason, 360, show_tooltip=True)
        command_row.addWidget(self.lbl_start_reason, 1)
        layout.addLayout(command_row)

        feedback_row = QHBoxLayout()
        self.lbl_command_result_name = QLabel()
        self._bind_text(self.lbl_command_result_name, "field.last_command_result")
        self.lbl_command_result_name.setMinimumWidth(80)
        self.lbl_command_result = QLabel()
        self._bind_text(self.lbl_command_result, "common.none")
        self._configure_dynamic_label(self.lbl_command_result, 520, show_tooltip=True)
        feedback_row.addWidget(self.lbl_command_result_name)
        feedback_row.addWidget(self.lbl_command_result, 1)
        layout.addLayout(feedback_row)

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
        layout.setSpacing(10)
        self.btn_generate_sim = QPushButton()
        self.btn_process_data = QPushButton()
        self.btn_select_data_root = QPushButton()
        self.btn_open_log_dir = QPushButton()
        self.btn_open_data_dir = QPushButton()
        self.btn_cancel_sim = QPushButton()
        self.btn_cancel_processing = QPushButton()
        self._bind_text(self.btn_generate_sim, "button.generate_sim")
        self._bind_text(self.btn_process_data, "button.process_data")
        self._bind_text(self.btn_select_data_root, "button.select_data_root")
        self._bind_text(self.btn_open_log_dir, "button.open_logs")
        self._bind_text(self.btn_open_data_dir, "button.open_data")
        self._bind_text(self.btn_cancel_sim, "button.cancel_and_clean")
        self._bind_text(self.btn_cancel_processing, "button.cancel_and_clean")

        for button in (self.btn_generate_sim, self.btn_process_data):
            button.setMinimumHeight(42)
            button.setStyleSheet(self._cmd_button_style)
        for button in (self.btn_cancel_sim, self.btn_cancel_processing):
            button.setObjectName("cancelTaskButton")
            button.setMinimumHeight(36)
            button.setEnabled(False)

        self.lbl_simulation_progress = QLabel()
        self.lbl_simulation_progress.setWordWrap(True)
        self.simulation_progress_bar = QProgressBar()
        self.simulation_progress_bar.setObjectName("simulationProgress")
        self.simulation_progress_bar.setRange(0, 100)
        self.simulation_progress_bar.setValue(0)
        self.simulation_progress_bar.setMinimumHeight(36)
        simulation_progress_row = QHBoxLayout()
        simulation_progress_row.addWidget(self.simulation_progress_bar, 1)
        simulation_progress_row.addWidget(self.btn_cancel_sim)

        self.lbl_processing_progress = QLabel()
        self.lbl_processing_progress.setWordWrap(True)
        self.processing_progress_bar = QProgressBar()
        self.processing_progress_bar.setObjectName("processingProgress")
        self.processing_progress_bar.setRange(0, 100)
        self.processing_progress_bar.setValue(0)
        self.processing_progress_bar.setMinimumHeight(36)
        processing_progress_row = QHBoxLayout()
        processing_progress_row.addWidget(self.processing_progress_bar, 1)
        processing_progress_row.addWidget(self.btn_cancel_processing)

        layout.addWidget(self.btn_generate_sim)
        layout.addWidget(self.lbl_simulation_progress)
        layout.addLayout(simulation_progress_row)
        layout.addSpacing(18)
        layout.addWidget(self.btn_process_data)
        layout.addWidget(self.lbl_processing_progress)
        layout.addLayout(processing_progress_row)
        layout.addSpacing(18)

        folder_row = QHBoxLayout()
        self.folder_button_layout = folder_row
        for button in (
            self.btn_select_data_root,
            self.btn_open_log_dir,
            self.btn_open_data_dir,
        ):
            button.setMinimumHeight(42)
            button.setStyleSheet(self._cmd_button_style)
            folder_row.addWidget(button, 1)
        layout.addLayout(folder_row)
        layout.addStretch(1)
        root.addWidget(box, 1)
        self.btn_generate_sim.clicked.connect(
            lambda: self.on_generate_sim_data and self.on_generate_sim_data()
        )
        self.btn_cancel_sim.clicked.connect(
            lambda: self.on_cancel_sim_data and self.on_cancel_sim_data()
        )
        self.btn_process_data.clicked.connect(lambda: self.on_process_data and self.on_process_data())
        self.btn_cancel_processing.clicked.connect(
            lambda: self.on_cancel_process_data and self.on_cancel_process_data()
        )
        self.btn_select_data_root.clicked.connect(
            lambda: self.on_select_data_root and self.on_select_data_root()
        )
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
        if (
            self._state is None
            or not self._state.preflight_command_entry_allowed()
            or not self._state.calibration_start_modes()
        ):
            return
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

    def _show_sensor_details(self) -> None:
        if self._state is not None:
            self.sensor_details_dialog.render(self._state)
        self.sensor_details_dialog.show()
        self.sensor_details_dialog.raise_()
        self.sensor_details_dialog.activateWindow()

    def request_export_options(self) -> ResolvedExportOptions | None:
        self.export_options_dialog.reload_preferences()
        if self.export_options_dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.export_options_dialog.resolved_options(
            self.i18n.language,
            self.theme,
        )

    def request_data_directory(
        self,
        current_root: Path | str,
    ) -> DataDirectorySelection | None:
        self.data_directory_dialog.prepare(current_root)
        if self.data_directory_dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.data_directory_dialog.selection()

    def _apply_theme(self, theme: Theme, *, persist: bool) -> None:
        self.theme = theme
        application = QApplication.instance()
        if application is not None:
            self._theme_colors = apply_application_theme(application, theme)
        else:
            self._theme_colors = theme_colors(theme)
        if persist:
            self.preferences.set_theme(theme)
        self._apply_3d_theme()
        self._apply_plot_theme()
        if self._state is not None:
            self.render_state()

    def _rocket_face_colors(self) -> np.ndarray:
        return np.asarray(
            [
                QColor(color).getRgbF()
                for color in self.ROCKET_FACE_COLOR_HEX[self.theme]
            ],
            dtype=float,
        )

    def _apply_3d_theme(self) -> None:
        if not hasattr(self, "gl_view"):
            return
        colors = self._theme_colors
        self.gl_view.setBackgroundColor(QColor(colors.base))
        if hasattr(self, "ground_grid"):
            self.ground_grid.setColor(QColor(colors.grid))
        if hasattr(self, "world_direction_labels"):
            direction_colors = {
                "E": colors.error,
                "W": colors.text,
                "N": colors.success,
                "S": colors.text,
                "U": colors.highlight,
            }
            for name, label in self.world_direction_labels.items():
                label.setData(color=QColor(direction_colors[name]))
        if hasattr(self, "body_nose_label"):
            self.body_nose_label.setData(color=QColor(colors.text))
        if hasattr(self, "base_colors"):
            self.base_colors = self._rocket_face_colors()
        if hasattr(self, "mesh_item"):
            self._apply_quat_to_mesh(self.latest_quat)
            self.mesh_item.setMeshData(
                meshdata=self.mesh_item.opts["meshdata"],
                edgeColor=pg.glColor(QColor(colors.mesh_edge)),
            )
        self.gl_view.update()

    def _apply_plot_theme(self) -> None:
        if not hasattr(self, "plot_widgets"):
            return
        colors = self._theme_colors
        for key, plot_widget in self.plot_widgets.items():
            plot_widget.setBackground(colors.base)
            for axis_name in ("left", "bottom"):
                axis = plot_widget.getAxis(axis_name)
                axis.setPen(pg.mkPen(colors.border))
                axis.setTextPen(pg.mkPen(colors.text))
            self.curves[key].setPen(pg.mkPen(colors.plot_curve, width=2))

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

    def begin_data_migration(
        self,
        source_root: Path | str,
        target_root: Path | str,
    ) -> None:
        self.set_data_tools_busy(True)
        self.data_migration_progress_dialog.begin(source_root, target_root)

    def set_data_migration_plan(self, file_count: int, byte_count: int) -> None:
        self.data_migration_progress_dialog.set_plan(file_count, byte_count)

    def update_data_migration_progress(self, percent: int, detail: str) -> None:
        self.data_migration_progress_dialog.update_progress(percent, detail)

    def mark_data_migration_committing(self) -> None:
        self.data_migration_progress_dialog.mark_committing()

    def mark_data_migration_cancelling(self) -> None:
        self.data_migration_progress_dialog.mark_cancelling()

    def finish_data_migration_completed(
        self,
        file_count: int,
        target_root: Path | str,
        warning: str = "",
        skipped_count: int = 0,
    ) -> None:
        self.data_migration_progress_dialog.finish_completed(
            file_count,
            target_root,
            warning,
            skipped_count,
        )

    def finish_data_migration_cancelled(self) -> None:
        self.data_migration_progress_dialog.finish_cancelled()

    def finish_data_migration_failed(self, error: str) -> None:
        self.data_migration_progress_dialog.finish_failed(error)

    def set_data_tools_busy(self, busy: bool) -> None:
        self._data_tools_busy = bool(busy)
        self._render_data_tool_buttons()

    @staticmethod
    def _set_progress_value(progress_bar: QProgressBar, done: int, total: int) -> None:
        total = max(0, int(total))
        if total == 0:
            progress_bar.setRange(0, 0)
            return
        done = max(0, min(int(done), total))
        progress_bar.setRange(0, total)
        progress_bar.setValue(done)

    def _render_data_task_status(self) -> None:
        if not hasattr(self, "lbl_simulation_progress"):
            return
        self.lbl_simulation_progress.setText(
            self.i18n.tr(self._simulation_status_key, **self._simulation_status_params)
        )
        self.lbl_processing_progress.setText(
            self.i18n.tr(self._processing_status_key, **self._processing_status_params)
        )

    def begin_simulation_task(self) -> None:
        self._simulation_task_active = True
        self._simulation_cancel_enabled = True
        self._simulation_status_key = "task.simulation.preparing"
        self._simulation_status_params = {}
        self._set_progress_value(self.simulation_progress_bar, 0, 100)
        self.set_data_tools_busy(True)
        self._render_data_task_status()

    def update_simulation_progress(
        self,
        done: int,
        total: int,
        status_key: str,
        **params: object,
    ) -> None:
        self._simulation_status_key = status_key
        self._simulation_status_params = dict(params)
        self._set_progress_value(self.simulation_progress_bar, done, total)
        self._render_data_task_status()

    def mark_simulation_cancelling(self) -> None:
        self._simulation_cancel_enabled = False
        self._simulation_status_key = "task.simulation.cancelling"
        self._simulation_status_params = {}
        self._render_data_tool_buttons()
        self._render_data_task_status()

    def finish_simulation_task(
        self,
        status_key: str,
        *,
        completed: bool,
        **params: object,
    ) -> None:
        self._simulation_task_active = False
        self._simulation_cancel_enabled = False
        self._simulation_status_key = status_key
        self._simulation_status_params = dict(params)
        self._set_progress_value(self.simulation_progress_bar, 100 if completed else 0, 100)
        self.set_data_tools_busy(False)
        self._render_data_task_status()

    def begin_processing_task(self) -> None:
        self._processing_task_active = True
        self._processing_cancel_enabled = True
        self._processing_status_key = "task.processing.preparing"
        self._processing_status_params = {}
        self._set_progress_value(self.processing_progress_bar, 0, 100)
        self.set_data_tools_busy(True)
        self._render_data_task_status()

    def update_processing_progress(
        self,
        done: int,
        total: int,
        status_key: str,
        **params: object,
    ) -> None:
        self._processing_status_key = status_key
        self._processing_status_params = dict(params)
        self._set_progress_value(self.processing_progress_bar, done, total)
        self._render_data_task_status()

    def mark_processing_cancelling(self) -> None:
        self._processing_cancel_enabled = False
        self._processing_status_key = "task.processing.cancelling"
        self._processing_status_params = {}
        self._render_data_tool_buttons()
        self._render_data_task_status()

    def finish_processing_task(
        self,
        status_key: str,
        *,
        completed: bool,
        **params: object,
    ) -> None:
        self._processing_task_active = False
        self._processing_cancel_enabled = False
        self._processing_status_key = status_key
        self._processing_status_params = dict(params)
        self._set_progress_value(self.processing_progress_bar, 100 if completed else 0, 100)
        self.set_data_tools_busy(False)
        self._render_data_task_status()

    def _render_data_tool_buttons(self) -> None:
        mission = bool(self._state and self._state.mission_started)
        high_load_enabled = not self._data_tools_busy and not mission
        self.btn_generate_sim.setEnabled(high_load_enabled)
        self.btn_process_data.setEnabled(high_load_enabled)
        self.btn_cancel_sim.setEnabled(
            self._simulation_task_active and self._simulation_cancel_enabled
        )
        self.btn_cancel_processing.setEnabled(
            self._processing_task_active and self._processing_cancel_enabled
        )
        self.btn_select_data_root.setEnabled(
            high_load_enabled and not bool(self._state and self._state.connected)
        )
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
        if state.start_transaction_pending():
            start_block_text = self.i18n.tr("start.in_progress_short")
        elif state.last_start_failure_result == int(AirAckResult.BUSY):
            start_block_text = self.i18n.tr("start.ack_busy_short")
        else:
            start_block_text = self.i18n.enum(
                "ack_result", state.start_block_reason_name()
            )
        self.lbl_pf_start_block.setText(start_block_text)
        self.lbl_pf_lock.setText(
            self.i18n.tr("common.unlocked" if state.start_unlocked else "common.locked")
        )

        calibration = state.calibration
        self.lbl_cal_mode.setText(calibration_mode_text(self.i18n, state))
        self.lbl_cal_capability.setText(calibration_capability_text(self.i18n, state))
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
        sensor_snapshot = (
            state.alignment_sensor_snapshots.latest_terminal_snapshot
        )
        if sensor_snapshot is None:
            sensor_snapshot_text = self.i18n.tr("sensor_snapshot.none_short")
            sensor_snapshot_style = "unknown"
        elif sensor_snapshot.complete:
            sensor_snapshot_text = self.i18n.tr(
                "sensor_snapshot.complete_short",
                snapshot_id=sensor_snapshot.snapshot_id,
                total=sensor_snapshot.expected_total,
            )
            sensor_snapshot_style = "ready"
        else:
            sensor_snapshot_text = self.i18n.tr(
                "sensor_snapshot.incomplete_short",
                snapshot_id=sensor_snapshot.snapshot_id,
                received=len(sensor_snapshot.frames_by_index),
                expected=(
                    "?"
                    if sensor_snapshot.expected_total is None
                    else sensor_snapshot.expected_total
                ),
            )
            sensor_snapshot_style = "error"
        self.lbl_align_snapshot.setText(sensor_snapshot_text)
        self._set_semantic_style(self.lbl_align_snapshot, sensor_snapshot_style)
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
        sensor_summary_flags = state.capability.sensor_summary_flags if state.capability else None
        self.lbl_pf_gnss_present.setText(
            self.i18n.tr("common.unknown")
            if sensor_summary_flags is None
            else yes_no(
                bool(sensor_summary_flags & int(AirSensorSummaryFlag.GNSS_PRESENT))
            )
        )
        self.lbl_pf_gnss_usable.setText(yes_no(state.gnss_position_usable))

        health = state.receive_health
        health_text = self.i18n.tr("common.backlog" if health.is_backlogged() else "common.normal")
        health_style = (
            f"color: {self._theme_colors.error}; font-weight: bold;"
            if health.is_backlogged()
            else ""
        )
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
        # Refresh hidden controls too: a new handshake must replace the old build's modes.
        self.calibration_dialog.render(state)
        if not state.calibration_start_modes():
            self.calibration_dialog.close()
        if self.link_details_dialog.isVisible():
            self.link_details_dialog.render(state)
        if self.sensor_details_dialog.isVisible():
            self.sensor_details_dialog.render(state)

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
            "air_link.connected": self._theme_colors.success,
            "air_link.handshaking": self._theme_colors.warning,
            "air_link.downlink_wait": self._theme_colors.warning,
            "air_link.unsupported": self._theme_colors.error,
            "air_link.error": self._theme_colors.error,
            "air_link.not_detected": self._theme_colors.muted,
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

    def _set_semantic_style(self, label: QLabel, state: str) -> None:
        color = {
            "ready": self._theme_colors.success,
            "waiting": self._theme_colors.warning,
            "error": self._theme_colors.error,
            "unknown": self._theme_colors.muted,
        }.get(state, self._theme_colors.muted)
        label.setStyleSheet(f"color: {color}; font-weight: bold;")

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
        command_feedback = self.i18n.format_message(state.radio_message)
        self._set_dynamic_label_text(self.lbl_command_result, command_feedback)
        self.lbl_command_result.setToolTip(command_feedback)
        link_allowed = state.air_command_link_allowed()
        preflight_allowed = link_allowed and not state.pending_command_name
        self.btn_ping.setEnabled(link_allowed and not state.pending_command_name)
        self.btn_lock.setEnabled(preflight_allowed and state.start_unlocked)
        self.btn_unlock.setEnabled(preflight_allowed and not state.start_unlocked)
        self.btn_start.setEnabled(state.start_button_enabled())
        self.btn_start.setText(
            self.i18n.tr(
                "button.start_waiting"
                if state.start_transaction_pending()
                else "button.start"
            )
        )
        calibration_idle = bool(
            state.calibration.mode in (int(AirCalibrationMode.NONE), int(AirCalibrationMode.NOT_SELECTED))
            or state.calibration.state in (None, int(AirCalibrationState.IDLE))
        )
        self.btn_calibration.setText(
            self.i18n.tr(
                "button.calibration_start"
                if calibration_idle
                else "button.calibration_restart"
            )
        )
        # Opening the dialog must remain available through READY and through a
        # lost calibration ACK; sending still obeys domain-safe controller rules.
        self.btn_calibration.setEnabled(
            state.preflight_command_entry_allowed() and bool(state.calibration_start_modes())
        )
        self.btn_cal_reset.setEnabled(
            state.preflight_command_entry_allowed() and not state.pending_command_name
        )
        self.btn_align_start.setEnabled(preflight_allowed and state.calibration.ready)
        self.btn_align_stop.setEnabled(preflight_allowed)
        self.btn_align_reset.setEnabled(preflight_allowed)

        semantic_state = "error"
        if state.mission_started:
            reason = self.i18n.tr("start.mission_started")
            semantic_state = "ready"
        elif state.start_transaction_pending():
            reason = self.i18n.tr("start.pending")
            semantic_state = "waiting"
        elif state.capability_error:
            blocker = self.i18n.tr(f"capability.error.{state.capability_error}")
            reason = self.i18n.tr("start.cannot", reason=blocker)
        elif state.profile_supported is False:
            blocker = self.i18n.tr(
                "capability.unsupported",
                profile=state.capability.air_profile_id if state.capability else "?",
            )
            reason = self.i18n.tr("start.cannot", reason=blocker)
        elif not state.capability_acked:
            reason = self.i18n.tr(
                "start.cannot", reason=self.i18n.tr("start.block.capability")
            )
        elif state.pending_command_name:
            reason = self.i18n.tr(
                "start.other_pending", command=state.pending_command_name
            )
            semantic_state = "waiting"
        elif state.last_start_failure_result == int(AirAckResult.BUSY):
            reason = self.i18n.tr("start.busy_retry")
            semantic_state = "waiting"
        elif state.last_start_failure_result is not None:
            failure = self.i18n.enum(
                "ack_result",
                enum_name(AirAckResult, state.last_start_failure_result),
            )
            reason = self.i18n.tr("start.failed_retry", reason=failure)
        elif state.start_transaction_timed_out:
            reason = self.i18n.tr("start.timeout_retry")
            semantic_state = "waiting"
        elif not state.calibration.ready:
            reason = self.i18n.tr(
                "start.cannot", reason=self.i18n.tr("start.block.calibration")
            )
        elif not state.alignment.ready:
            reason = self.i18n.tr(
                "start.cannot", reason=self.i18n.tr("start.block.alignment")
            )
        elif not state.system_ready:
            reason = self.i18n.tr(
                "start.cannot", reason=self.i18n.tr("start.block.system")
            )
        elif not state.start_unlocked:
            reason = self.i18n.tr(
                "start.cannot", reason=self.i18n.tr("start.block.unlock")
            )
        elif state.start_ready():
            reason = self.i18n.tr("start.ready")
            semantic_state = "ready"
        else:
            blocker_key = (
                "start.block.busy"
                if state.start_block_reason == int(AirAckResult.BUSY)
                else None
            )
            blocker = (
                self.i18n.tr(blocker_key)
                if blocker_key is not None
                else self.i18n.enum("ack_result", state.start_block_reason_name())
            )
            reason = self.i18n.tr("start.cannot", reason=blocker)
        self._set_dynamic_label_text(self.lbl_start_reason, reason)
        self._set_semantic_style(self.lbl_start_reason, semantic_state)
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

        colors = self._rocket_face_colors()

        self.base_vertices = verts.copy()
        self.base_faces = faces.copy()
        self.base_colors = colors.copy()

        mesh_data = gl.MeshData(vertexes=verts, faces=faces, faceColors=colors)
        self.mesh_item = gl.GLMeshItem(
            meshdata=mesh_data,
            smooth=False,
            computeNormals=False,
            drawEdges=True,
            edgeColor=(1, 1, 1, 1),
            shader=None,
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
