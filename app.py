from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Callable
from uuid import uuid4

from PySide6.QtCore import QObject, QThread, Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from config import (
    APP_EN_NAME,
    APP_ORGANIZATION,
    APP_VERSION,
    APP_WINDOW_TITLE,
    DEFAULT_BAUDRATE,
    USER_DATA_ROOT,
    save_user_data_root,
)
from protocol.air import (
    TOKEN_ALIGNMENT,
    TOKEN_CALIBRATION,
    TOKEN_LOCK,
    TOKEN_START_MISSION,
    TOKEN_UNLOCK,
    AirAckMessage,
    AirCapabilityMessage,
    AirFlightStateMessage,
    AirPreflightStateMessage,
    AirPreflightStatusMessage,
    AirSensorStatusMessage,
    AirStatusMessage,
    accel_raw_to_mps2,
    build_air_cmd,
    gyro_raw_to_radps,
)
from protocol.common import (
    AirAckResult,
    AirAlignmentState,
    AirCalibrationMode,
    AirCalibrationDiagnosticReason,
    AirCalibrationState,
    AirCmdId,
    AirCommandPolicy,
    GspAckResult,
    GspType,
    AirLifecycleState,
    AirStatusId,
    enum_name,
)
from protocol.gsp_min import GsStatus, GspAck, build_pc_to_gs_air_frame
from protocol.receive_pipeline import ProtocolEvent
from services.data_migration import DataMigrationConflictPolicy
from services.i18n import EnumParam, I18n
from services.logger import AsyncJsonlLogger
from services.preferences import ResolvedExportOptions
from services.state_model import (
    CalibrationStartResult,
    EventHistory,
    FlightControllerState,
    FlightEvent,
    HandshakeState,
    MissionPhase,
    SensorSnapshot,
    UiMessage,
    quat_to_euler_rpy,
)
from transport.protocol_worker import ProtocolBatch, ProtocolWorker, ProtocolWorkerDiagnostics
from transport.serial_backend import SerialConfig, SerialLink, list_serial_port_names
from ui.main_window import MainWindow

GS_STATE_MAP = {
    0x00: "IDLE",
    0x01: "INIT_OK",
    0x02: "RX_MODE",
    0x03: "TX_MODE",
    0x04: "ERROR",
}

RADIO_STATE_MAP = {
    0x00: "NOT_INIT",
    0x01: "READY",
    0x02: "RX",
    0x03: "TX",
    0x04: "BUSY",
}

AIR_CMD_ACK_TIMEOUT_MS = 800
AIR_CMD_MAX_RETRIES = 3
AIR_CMD_RETRY_CHECK_MS = 100
FLIGHT_TELEMETRY_PERIOD_MS = 200
CALIBRATION_COMMAND_IDS = frozenset(
    {
        int(AirCmdId.CAL_START),
        int(AirCmdId.CAL_FACE),
        int(AirCmdId.CAL_STOP),
        int(AirCmdId.CAL_RESET),
    }
)


def format_air_status_message(msg: AirStatusMessage, i18n: I18n | None = None) -> str:
    if i18n is None:
        status_text = enum_name(AirStatusId, msg.status_id)
        if msg.status_id == int(AirStatusId.GNSS_POSITION):
            if msg.arg0 == 1:
                status_text += " 定位可用"
            elif msg.arg0 == 0:
                status_text += " 定位不可用"
            else:
                status_text += f" UNKNOWN(arg0={msg.arg0})"
        elif msg.status_id == int(AirStatusId.CALIBRATION_FACE):
            face_names = ("X+", "X-", "Y+", "Y-", "Z+", "Z-")
            face = face_names[msg.arg0] if 0 <= msg.arg0 < len(face_names) else str(msg.arg0)
            status_text += f" {face} {'PASSED' if msg.arg1 == 1 else 'FAILED'}"
        elif msg.status_id == int(AirStatusId.CALIBRATION):
            status_text += f" {enum_name(AirCalibrationState, msg.arg0)}"
        elif msg.status_id == int(AirStatusId.CALIBRATION_DIAGNOSTIC):
            status_text += f" {enum_name(AirCalibrationDiagnosticReason, msg.arg1)} face={msg.arg0}"
        elif msg.status_id == int(AirStatusId.ALIGNMENT):
            status_text += f" {enum_name(AirAlignmentState, msg.arg0)}"
        return f"{status_text} @ {msg.time_ms} ms"

    translator = i18n
    status_text = translator.enum("status", enum_name(AirStatusId, msg.status_id))
    if msg.status_id == int(AirStatusId.GNSS_POSITION):
        if msg.arg0 == 1:
            status_text += " " + translator.tr("event.gnss.usable")
        elif msg.arg0 == 0:
            status_text += " " + translator.tr("event.gnss.unusable")
        else:
            status_text += f" UNKNOWN(arg0={msg.arg0})"
    elif msg.status_id == int(AirStatusId.CALIBRATION_FACE):
        face_names = ("X+", "X-", "Y+", "Y-", "Z+", "Z-")
        face = face_names[msg.arg0] if 0 <= msg.arg0 < len(face_names) else str(msg.arg0)
        status_text += " " + translator.tr(
            "event.detail.face",
            face=face,
            result=translator.tr("event.face.passed" if msg.arg1 == 1 else "event.face.failed"),
        )
    elif msg.status_id == int(AirStatusId.CALIBRATION):
        status_text += " " + translator.enum(
            "calibration_state", enum_name(AirCalibrationState, msg.arg0)
        )
    elif msg.status_id == int(AirStatusId.CALIBRATION_DIAGNOSTIC):
        status_text += " " + translator.enum(
            "calibration_diagnostic_reason",
            enum_name(AirCalibrationDiagnosticReason, msg.arg1),
        )
    elif msg.status_id == int(AirStatusId.ALIGNMENT):
        status_text += " " + translator.enum(
            "alignment_state", enum_name(AirAlignmentState, msg.arg0)
        )
    return f"{status_text} @ {msg.time_ms} ms"


@dataclass
class PendingAirCommand:
    seq: int
    cmd_id: int
    token: int
    param0: int
    param1: int
    air_frame: bytes
    gsp_frame: bytes
    sent_count: int
    max_retries: int
    last_send_monotonic: float
    created_monotonic: float = 0.0
    baseline_completed_face_mask: int = 0


@dataclass
class PendingCapabilityAck:
    capability_seq: int
    air_profile_id: int
    command_seq: int
    air_frame: bytes
    gsp_frame: bytes
    sent_count: int = 0
    max_retries: int = AIR_CMD_MAX_RETRIES
    last_send_monotonic: float = 0.0


class SimulationWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, output_root: Path, seed: int) -> None:
        super().__init__()
        self.output_root = output_root
        self.seed = int(seed)
        self._cancel_event = Event()
        self._output_path: Path | None = None
        self._partial_path: Path | None = None

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def _cleanup_created_files(self) -> str | None:
        errors: list[str] = []
        for path in (self._partial_path, self._output_path):
            if path is None or not path.exists():
                continue
            try:
                path.unlink()
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        return "; ".join(errors) if errors else None

    def run(self) -> None:
        try:
            from processing.fake_log_generator import (
                SimulationCancelledError,
                simulate,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        try:
            self.output_root.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self._output_path = self.output_root / f"sim_validation_{timestamp}.jsonl"
            self._partial_path = self.output_root / f".{self._output_path.name}.partial"

            def generation_progress(done: int, total: int) -> None:
                if self._cancel_event.is_set():
                    raise SimulationCancelledError(
                        "Simulation-data generation was cancelled."
                    )
                scaled = int(round(70.0 * max(0, done) / max(1, total)))
                self.progress.emit(
                    max(0, min(70, scaled)),
                    100,
                    "task.simulation.generating",
                )

            records = simulate(
                seed=self.seed,
                progress=generation_progress,
                cancel_requested=self._cancel_event.is_set,
            )
            if self._cancel_event.is_set():
                raise SimulationCancelledError(
                    "Simulation-data generation was cancelled."
                )

            metadata = {
                "ts": time.time(),
                "host_monotonic_ns": time.monotonic_ns(),
                "dir": "META",
                "layer": "SIMULATION",
                "kind": "SIMULATION_VALIDATION",
                "simulated": True,
                "simulation_label": "SIMULATION_VALIDATION",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "note": "Generated by the upper-computer validation tool; not real flight data.",
            }
            record_total = max(1, len(records))
            update_stride = max(1, record_total // 100)
            with self._partial_path.open("x", encoding="utf-8") as file:
                file.write(json.dumps(metadata, ensure_ascii=False) + "\n")
                for index, record in enumerate(records, start=1):
                    if self._cancel_event.is_set():
                        raise SimulationCancelledError(
                            "Simulation-data generation was cancelled."
                        )
                    item = dict(record)
                    item["simulated"] = True
                    item["simulation_label"] = "SIMULATION_VALIDATION"
                    file.write(json.dumps(item, ensure_ascii=False) + "\n")
                    if index % update_stride == 0 or index == record_total:
                        scaled = 70 + int(round(30.0 * index / record_total))
                        self.progress.emit(
                            max(70, min(100, scaled)),
                            100,
                            "task.simulation.writing",
                        )

            if self._cancel_event.is_set():
                raise SimulationCancelledError(
                    "Simulation-data generation was cancelled."
                )
            self._partial_path.replace(self._output_path)
            if self._cancel_event.is_set():
                raise SimulationCancelledError(
                    "Simulation-data generation was cancelled."
                )
            self.progress.emit(100, 100, "task.simulation.writing")
            self.finished.emit(str(self._output_path))
        except SimulationCancelledError:
            cleanup_error = self._cleanup_created_files()
            if cleanup_error:
                self.failed.emit(f"Unable to clean up cancelled output: {cleanup_error}")
            else:
                self.cancelled.emit()
        except Exception as exc:
            cleanup_error = self._cleanup_created_files()
            detail = str(exc)
            if cleanup_error:
                detail = f"{detail}; cleanup failed: {cleanup_error}"
            self.failed.emit(detail)


class ProcessingWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str, object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(
        self,
        log_path: Path,
        output_root: Path,
        export_options: ResolvedExportOptions,
    ) -> None:
        super().__init__()
        self.log_path = log_path
        self.output_root = output_root
        self.export_options = export_options
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            from processing.flight_log_processor import (
                FlightLogProcessor,
                ProcessingCancelledError,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        try:
            processor = FlightLogProcessor(
                output_root=self.output_root,
                gif_fps=5,
                export_options=self.export_options,
            )

            def progress(done: int, total: int, message: str) -> None:
                self.progress.emit(int(done), int(total), str(message))

            output_dir = processor.process_file(
                self.log_path,
                progress=progress,
                cancel_requested=self._cancel_event.is_set,
            )
            if self._cancel_event.is_set():
                processor.remove_output_dir(output_dir)
                self.cancelled.emit()
            else:
                self.finished.emit(str(output_dir), dict(processor.last_export_errors))
        except ProcessingCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class DataMigrationCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class _DataMigrationFile:
    source: Path
    target: Path
    relative_path: Path
    size: int


@dataclass(frozen=True)
class _DataMigrationTargetChange:
    target: Path
    backup: Path | None


class DataMigrationWorker(QObject):
    planned = Signal(int, object)
    progress = Signal(int, str)
    ready_to_commit = Signal()
    finished = Signal(int, object, int, str)
    cancelled = Signal()
    failed = Signal(str)

    COPY_CHUNK_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        source_root: Path,
        target_root: Path,
        conflict_policy: DataMigrationConflictPolicy = (
            DataMigrationConflictPolicy.OVERWRITE
        ),
    ) -> None:
        super().__init__()
        self.source_root = Path(source_root)
        self.target_root = Path(target_root)
        self.conflict_policy = DataMigrationConflictPolicy(conflict_policy)
        self._cancel_event = Event()
        self._commit_event = Event()
        self._commit_lock = Lock()
        self._commit_approved = False
        self._commit_error = ""

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def finish_commit(self, approved: bool, error: str = "") -> None:
        with self._commit_lock:
            self._commit_approved = bool(approved)
            self._commit_error = str(error)
            self._commit_event.set()

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise DataMigrationCancelledError()

    @staticmethod
    def _is_nested(path: Path, parent: Path) -> bool:
        return path != parent and parent in path.parents

    def _scan_files(self) -> tuple[Path, Path, list[_DataMigrationFile]]:
        source_root = self.source_root.expanduser().resolve(strict=False)
        target_root = self.target_root.expanduser().resolve(strict=False)
        if source_root == target_root:
            raise ValueError("The new data directory is the same as the previous directory")
        if self._is_nested(target_root, source_root) or self._is_nested(
            source_root,
            target_root,
        ):
            raise ValueError(
                "The previous and new data directories cannot contain one another"
            )

        target_root.mkdir(parents=True, exist_ok=True)
        migration_files: list[_DataMigrationFile] = []
        for category in ("logs", "data"):
            category_root = source_root / category
            if not category_root.exists():
                continue
            if not category_root.is_dir():
                raise NotADirectoryError(str(category_root))
            for source_path in category_root.rglob("*"):
                self._check_cancelled()
                if source_path.is_symlink() or not source_path.is_file():
                    continue
                relative_path = source_path.relative_to(source_root)
                migration_files.append(
                    _DataMigrationFile(
                        source=source_path,
                        target=target_root / relative_path,
                        relative_path=relative_path,
                        size=max(0, int(source_path.stat().st_size)),
                    )
                )
        migration_files.sort(key=lambda item: str(item.relative_path).casefold())

        return source_root, target_root, migration_files

    @staticmethod
    def _path_conflicts(path: Path) -> bool:
        return path.exists() or path.is_symlink()

    @staticmethod
    def _path_key(path: Path) -> str:
        return str(path.resolve(strict=False)).casefold()

    @classmethod
    def _renamed_target(
        cls,
        original_target: Path,
        unavailable_keys: set[str],
    ) -> Path:
        suffix = "".join(original_target.suffixes)
        base_name = (
            original_target.name[: -len(suffix)]
            if suffix
            else original_target.name
        )
        index = 1
        while True:
            candidate = original_target.with_name(
                f"{base_name} ({index}){suffix}"
            )
            candidate_key = cls._path_key(candidate)
            if (
                candidate_key not in unavailable_keys
                and not cls._path_conflicts(candidate)
            ):
                return candidate
            index += 1

    def _plan_files(
        self,
        target_root: Path,
        migration_files: list[_DataMigrationFile],
    ) -> tuple[list[_DataMigrationFile], int, set[str]]:
        original_target_keys: set[str] = set()
        for item in migration_files:
            target_key = self._path_key(item.target)
            if target_key in original_target_keys:
                raise FileExistsError(
                    "Multiple source files map to the same destination: "
                    f"{item.relative_path}"
                )
            original_target_keys.add(target_key)

        planned_files: list[_DataMigrationFile] = []
        planned_target_keys: set[str] = set()
        skipped_count = 0
        unavailable_keys = set(original_target_keys)
        for item in migration_files:
            self._check_cancelled()
            resolved_parent = item.target.parent.resolve(strict=False)
            if (
                resolved_parent != target_root
                and target_root not in resolved_parent.parents
            ):
                raise ValueError(
                    "A destination directory link points outside the selected "
                    f"data root: {item.relative_path}"
                )
            target = item.target
            has_conflict = self._path_conflicts(target)
            if has_conflict:
                if self.conflict_policy is DataMigrationConflictPolicy.FAIL:
                    raise FileExistsError(
                        "The new directory already contains "
                        f"{item.relative_path}"
                    )
                if self.conflict_policy is DataMigrationConflictPolicy.SKIP:
                    skipped_count += 1
                    continue
                if self.conflict_policy is DataMigrationConflictPolicy.RENAME:
                    target = self._renamed_target(target, unavailable_keys)
                elif target.is_dir() and not target.is_symlink():
                    raise IsADirectoryError(
                        "A directory blocks the destination file: "
                        f"{item.relative_path}"
                    )

            target_key = self._path_key(target)
            if target_key in planned_target_keys:
                raise FileExistsError(
                    "Multiple source files map to the same destination: "
                    f"{item.relative_path}"
                )
            planned_target_keys.add(target_key)
            unavailable_keys.add(target_key)
            planned_files.append(
                _DataMigrationFile(
                    source=item.source,
                    target=target,
                    relative_path=item.relative_path,
                    size=item.size,
                )
            )

        required_bytes = sum(item.size for item in planned_files)
        free_bytes = shutil.disk_usage(target_root).free
        if required_bytes > free_bytes:
            raise OSError(
                f"Not enough free space in {target_root}: "
                f"need {required_bytes} bytes, available {free_bytes} bytes"
            )
        return planned_files, skipped_count, unavailable_keys

    def _copy_to_temporary(
        self,
        item: _DataMigrationFile,
        advance: Callable[[int, str], None],
    ) -> Path:
        item.target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = item.target.with_name(
            f".{item.target.name}.{uuid4().hex}.migration"
        )
        detail = str(item.relative_path)
        try:
            with item.source.open("rb") as source_file, temporary_path.open(
                "xb"
            ) as target_file:
                copied_bytes = 0
                while True:
                    self._check_cancelled()
                    chunk = source_file.read(self.COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    target_file.write(chunk)
                    copied_bytes += len(chunk)
                    advance(len(chunk), detail)
                target_file.flush()
                os.fsync(target_file.fileno())
            if copied_bytes != item.size:
                raise OSError(
                    f"Source file size changed during migration: {item.source}"
                )
            if item.size == 0:
                advance(1, detail)
            shutil.copystat(item.source, temporary_path, follow_symlinks=False)
            return temporary_path
        except Exception:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @classmethod
    def _install_without_overwrite(
        cls,
        temporary_path: Path,
        target_path: Path,
    ) -> None:
        try:
            try:
                os.link(
                    temporary_path,
                    target_path,
                    follow_symlinks=False,
                )
            except TypeError:
                os.link(temporary_path, target_path)
            return
        except FileExistsError:
            raise
        except OSError:
            if cls._path_conflicts(target_path):
                raise FileExistsError(str(target_path))

        target_created = False
        try:
            with temporary_path.open("rb") as source_file, target_path.open(
                "xb"
            ) as target_file:
                target_created = True
                shutil.copyfileobj(
                    source_file,
                    target_file,
                    length=cls.COPY_CHUNK_BYTES,
                )
                target_file.flush()
                os.fsync(target_file.fileno())
            shutil.copystat(
                temporary_path,
                target_path,
                follow_symlinks=False,
            )
        except Exception:
            if target_created:
                try:
                    target_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    @classmethod
    def _unique_backup_path(cls, target_path: Path) -> Path:
        while True:
            backup_path = target_path.with_name(
                f".{target_path.name}.{uuid4().hex}.backup"
            )
            if not cls._path_conflicts(backup_path):
                return backup_path

    def _install_overwrite(
        self,
        temporary_path: Path,
        target_path: Path,
        target_changes: list[_DataMigrationTargetChange],
    ) -> None:
        while True:
            if not self._path_conflicts(target_path):
                try:
                    self._install_without_overwrite(
                        temporary_path,
                        target_path,
                    )
                except FileExistsError:
                    continue
                target_changes.append(
                    _DataMigrationTargetChange(target_path, None)
                )
                return

            if target_path.is_dir() and not target_path.is_symlink():
                raise IsADirectoryError(
                    f"A directory blocks the destination file: {target_path}"
                )
            backup_path = self._unique_backup_path(target_path)
            try:
                target_path.replace(backup_path)
            except FileNotFoundError:
                continue
            try:
                temporary_path.replace(target_path)
            except Exception:
                try:
                    backup_path.replace(target_path)
                except OSError as rollback_error:
                    raise RuntimeError(
                        "Unable to install the replacement or restore the "
                        f"previous destination: {rollback_error}"
                    ) from rollback_error
                raise
            target_changes.append(
                _DataMigrationTargetChange(target_path, backup_path)
            )
            return

    def _install_file(
        self,
        temporary_path: Path,
        item: _DataMigrationFile,
        original_target: Path,
        unavailable_keys: set[str],
        target_changes: list[_DataMigrationTargetChange],
    ) -> _DataMigrationFile | None:
        if self.conflict_policy is DataMigrationConflictPolicy.OVERWRITE:
            self._install_overwrite(
                temporary_path,
                item.target,
                target_changes,
            )
            return item

        target_path = item.target
        while True:
            try:
                self._install_without_overwrite(
                    temporary_path,
                    target_path,
                )
            except FileExistsError:
                if self.conflict_policy is DataMigrationConflictPolicy.FAIL:
                    raise FileExistsError(
                        "The destination appeared during migration: "
                        f"{target_path}"
                    )
                if self.conflict_policy is DataMigrationConflictPolicy.SKIP:
                    return None
                target_path = self._renamed_target(
                    original_target,
                    unavailable_keys,
                )
                unavailable_keys.add(self._path_key(target_path))
                continue

            target_changes.append(
                _DataMigrationTargetChange(target_path, None)
            )
            if target_path == item.target:
                return item
            return _DataMigrationFile(
                source=item.source,
                target=target_path,
                relative_path=item.relative_path,
                size=item.size,
            )

    def _copy_file(
        self,
        item: _DataMigrationFile,
        original_target: Path,
        advance: Callable[[int, str], None],
        unavailable_keys: set[str],
        target_changes: list[_DataMigrationTargetChange],
    ) -> _DataMigrationFile | None:
        temporary_path = self._copy_to_temporary(item, advance)
        try:
            return self._install_file(
                temporary_path,
                item,
                original_target,
                unavailable_keys,
                target_changes,
            )
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    @classmethod
    def _rollback_target_changes(
        cls,
        target_root: Path,
        target_changes: list[_DataMigrationTargetChange],
    ) -> str:
        errors: list[str] = []
        for change in reversed(target_changes):
            target_path = change.target
            try:
                if target_path.is_dir() and not target_path.is_symlink():
                    raise IsADirectoryError(str(target_path))
                target_path.unlink(missing_ok=True)
                if change.backup is not None:
                    change.backup.replace(target_path)
            except OSError as exc:
                errors.append(f"{target_path}: {exc}")
                continue
            if change.backup is not None:
                continue
            parent = target_path.parent
            while parent != target_root and target_root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        return "\n".join(errors)

    @staticmethod
    def _finalize_target_backups(
        target_changes: list[_DataMigrationTargetChange],
    ) -> str:
        errors: list[str] = []
        for change in target_changes:
            backup_path = change.backup
            if backup_path is None:
                continue
            try:
                if backup_path.is_dir() and not backup_path.is_symlink():
                    raise IsADirectoryError(str(backup_path))
                backup_path.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f"{backup_path}: {exc}")
        return "\n".join(errors)

    @staticmethod
    def _cleanup_source_files(
        source_root: Path,
        migration_files: list[_DataMigrationFile],
    ) -> str:
        errors: list[str] = []
        for item in migration_files:
            try:
                item.source.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f"{item.source}: {exc}")

        for category in ("logs", "data"):
            category_root = source_root / category
            if not category_root.exists() or not category_root.is_dir():
                continue
            directories = [
                path
                for path in category_root.rglob("*")
                if path.is_dir() and not path.is_symlink()
            ]
            for directory in sorted(
                directories,
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            try:
                category_root.rmdir()
            except OSError:
                pass

        if len(errors) > 20:
            omitted = len(errors) - 20
            errors = errors[:20] + [f"... and {omitted} more cleanup errors"]
        return "\n".join(errors)

    def run(self) -> None:
        target_changes: list[_DataMigrationTargetChange] = []
        target_root = self.target_root
        try:
            source_root, target_root, migration_files = self._scan_files()
            planned_files, skipped_count, unavailable_keys = self._plan_files(
                target_root,
                migration_files,
            )
            total_bytes = sum(item.size for item in planned_files)
            total_work = max(
                1,
                sum(max(1, item.size) for item in planned_files),
            )
            completed_work = 0
            self.planned.emit(len(planned_files), total_bytes)

            def advance(amount: int, detail: str) -> None:
                nonlocal completed_work
                completed_work += max(0, int(amount))
                percent = min(94, int(completed_work * 94 / total_work))
                self.progress.emit(percent, detail)

            moved_files: list[_DataMigrationFile] = []
            for item in planned_files:
                self._check_cancelled()
                moved_item = self._copy_file(
                    item,
                    target_root / item.relative_path,
                    advance,
                    unavailable_keys,
                    target_changes,
                )
                if moved_item is None:
                    skipped_count += 1
                else:
                    moved_files.append(moved_item)

            self._check_cancelled()
            self.ready_to_commit.emit()
            while not self._commit_event.wait(0.1):
                self._check_cancelled()
            self._check_cancelled()
            with self._commit_lock:
                commit_approved = self._commit_approved
                commit_error = self._commit_error
            if not commit_approved:
                raise RuntimeError(commit_error or "Unable to switch the data directory")

            warning_parts: list[str] = []
            cleanup_steps = (
                (
                    "Unable to remove destination backups",
                    lambda: self._finalize_target_backups(target_changes),
                ),
                (
                    "Unable to remove some source files",
                    lambda: self._cleanup_source_files(
                        source_root,
                        moved_files,
                    ),
                ),
            )
            for error_prefix, cleanup_step in cleanup_steps:
                try:
                    warning = cleanup_step()
                except Exception as cleanup_error:
                    warning = f"{error_prefix}: {cleanup_error}"
                if warning:
                    warning_parts.append(warning)
            cleanup_warning = "\n".join(warning_parts)
            self.finished.emit(
                len(moved_files),
                sum(item.size for item in moved_files),
                skipped_count,
                cleanup_warning,
            )
        except DataMigrationCancelledError:
            rollback_warning = self._rollback_target_changes(
                target_root,
                target_changes,
            )
            if rollback_warning:
                self.failed.emit(
                    "Migration was cancelled, but some destination changes "
                    f"could not be rolled back:\n{rollback_warning}"
                )
            else:
                self.cancelled.emit()
        except Exception as exc:
            rollback_warning = self._rollback_target_changes(
                target_root,
                target_changes,
            )
            error = str(exc)
            if rollback_warning:
                error = (
                    f"{error}\n\nSome destination changes could not be "
                    f"rolled back:\n{rollback_warning}"
                )
            self.failed.emit(error)


class Controller(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.link = SerialLink()
        self.worker = None
        self.protocol_worker: ProtocolWorker | None = None
        self.data_root = Path(USER_DATA_ROOT)
        self.log_dir = self.data_root / "logs"
        self.data_dir = self.data_root / "data"
        self.logger = AsyncJsonlLogger(self.log_dir, auto_start_session=False)

        self._connection_generation = 0
        self._state_generation = 0
        self.state = FlightControllerState(session_generation=self._next_state_generation())
        self.events = EventHistory()
        self.window.bind_runtime_model(self.state, self.events, select_preflight=True)

        self.air_seq = 0
        self.pending_air_cmds: dict[tuple[int, int], PendingAirCommand] = {}
        self.pending_capability_ack: PendingCapabilityAck | None = None
        self.air_cmd_retry_timer = QTimer(self)
        self.air_cmd_retry_timer.setInterval(AIR_CMD_RETRY_CHECK_MS)
        self.air_cmd_retry_timer.timeout.connect(self._check_air_cmd_timeouts)
        self.air_cmd_retry_timer.start()
        self.protocol_drain_timer = QTimer(self)
        self.protocol_drain_timer.setInterval(20)
        self.protocol_drain_timer.timeout.connect(self._drain_protocol_mailbox)
        self.protocol_drain_timer.start()

        self.processing_thread: QThread | None = None
        self.processing_worker: ProcessingWorker | None = None
        self.processing_cancel_requested = False
        self.simulation_thread: QThread | None = None
        self.simulation_worker: SimulationWorker | None = None
        self.simulation_cancel_requested = False
        self.data_migration_thread: QThread | None = None
        self.data_migration_worker: DataMigrationWorker | None = None
        self.data_migration_cancel_requested = False
        self.pending_data_root: Path | None = None
        self.previous_data_root: Path | None = None
        self.pending_data_migration_conflict_policy = (
            DataMigrationConflictPolicy.OVERWRITE
        )
        self.mission_packet_tracking_active = False
        self.last_flight_time_ms: int | None = None
        self.received_flight_packets = 0
        self.estimated_lost_packets = 0

        self.window.on_refresh_ports = self.refresh_ports
        self.window.on_connect_clicked = self.connect
        self.window.on_disconnect_clicked = self.disconnect
        self.window.on_send_ping = self.send_ping
        self.window.on_send_lock = self.send_lock
        self.window.on_send_unlock = self.send_unlock
        self.window.on_send_start = self.send_start
        self.window.on_cal_start = self.send_cal_start
        self.window.on_cal_face = self.send_cal_face
        self.window.on_cal_stop = self.send_cal_stop
        self.window.on_cal_reset = self.send_cal_reset
        self.window.on_align_start = self.send_align_start
        self.window.on_align_stop = self.send_align_stop
        self.window.on_align_reset = self.send_align_reset
        self.window.on_generate_sim_data = self.generate_sim_validation_data
        self.window.on_cancel_sim_data = self.cancel_sim_validation_data
        self.window.on_process_data = self.choose_and_process_data
        self.window.on_cancel_process_data = self.cancel_processing
        self.window.on_select_data_root = self.choose_data_root
        self.window.on_cancel_data_migration = self.cancel_data_migration
        self.window.on_open_log_dir = self.open_log_dir
        self.window.on_open_data_dir = self.open_data_dir
        self.refresh_ports()

    def _next_state_generation(self) -> int:
        self._state_generation += 1
        return self._state_generation

    def _replace_state(self, *, connected: bool, connection_text: str) -> None:
        self.state = FlightControllerState(
            session_generation=self._next_state_generation(),
            connected=connected,
            connection_text=connection_text,
        )
        self.events = EventHistory()
        self.window.bind_runtime_model(self.state, self.events, select_preflight=True)

    def _set_ui_message(
        self,
        message_attr: str,
        legacy_attr: str,
        key: str,
        **params: object,
    ) -> None:
        message = UiMessage(key, dict(params))
        setattr(self.state, message_attr, message)
        setattr(self.state, legacy_attr, self._translator().format_message(message))

    def _set_connection_message(self, key: str, **params: object) -> None:
        self._set_ui_message("connection_message", "connection_text", key, **params)

    def _set_radio_message(self, key: str, **params: object) -> None:
        self._set_ui_message("radio_message", "radio_hint", key, **params)

    def _set_air_ack_message(self, key: str, **params: object) -> None:
        self._set_ui_message("last_air_ack_message", "last_air_ack", key, **params)

    def _set_gsp_ack_message(self, key: str, **params: object) -> None:
        self._set_ui_message("last_gs_ack_message", "last_gs_ack", key, **params)

    def _tr(self, key: str, **params: object) -> str:
        return self._translator().tr(key, **params)

    def _translator(self) -> I18n:
        window = getattr(self, "window", None)
        translator = getattr(window, "i18n", None)
        if translator is None:
            translator = getattr(self, "_fallback_i18n", None)
        if translator is None:
            translator = I18n()
            self._fallback_i18n = translator
        return translator

    def refresh_ports(self) -> None:
        self.window.set_ports(list_serial_port_names())

    def connect(self) -> None:
        if self.data_migration_thread is not None:
            QMessageBox.information(
                self.window,
                self._tr("app.title"),
                self._tr("message.data_root_busy"),
            )
            return
        port = self.window.current_port()
        baud = self.window.current_baudrate() or DEFAULT_BAUDRATE
        if not port:
            QMessageBox.warning(
                self.window, self._tr("app.title"), self._tr("message.select_port")
            )
            return

        self.disconnect()
        if (
            self.worker is not None
            or self.protocol_worker is not None
            or self.logger.session_active
        ):
            QMessageBox.warning(
                self.window,
                self._tr("app.title"),
                self._tr("message.previous_session_stopping"),
            )
            return
        self._connection_generation += 1
        generation = self._connection_generation
        self._replace_state(connected=False, connection_text=f"正在连接 {port}@{baud}")
        self._set_connection_message("connection.connecting", endpoint=f"{port}@{baud}")
        self.air_seq = 0
        self._clear_pending_air_cmds("重新连接")
        self._clear_capability_ack("重新连接")
        self._clear_mission_packet_stats()

        try:
            self.logger.open_session(
                "serial_connect",
                {"port": port, "baudrate": baud, "pc_connection_generation": generation},
            )
        except Exception as exc:
            self._set_connection_message("connection.error", detail=str(exc))
            self.state.touch()
            QMessageBox.critical(
                self.window,
                self._tr("app.title"),
                self._tr("message.log_open_failed", error=exc),
            )
            return

        self.protocol_worker = ProtocolWorker(generation, self.logger)
        self.protocol_worker.diagnostics_ready.connect(
            self.on_protocol_diagnostics,
            Qt.QueuedConnection,
        )
        self.protocol_worker.warning_occurred.connect(self.on_protocol_warning, Qt.QueuedConnection)
        self.protocol_worker.error_occurred.connect(self.on_error, Qt.QueuedConnection)
        self.protocol_worker.start()

        self.worker = self.link.open(SerialConfig(port=port, baudrate=baud))
        self.worker.bytes_chunk_received.connect(
            self.protocol_worker.enqueue_chunk,
            Qt.DirectConnection,
        )
        self.worker.bytes_sent.connect(
            lambda byte_count, current_generation=generation: self._on_serial_bytes_sent(
                current_generation,
                byte_count,
            )
        )
        self.worker.connection_changed.connect(
            lambda ok, text, current_generation=generation: self._on_serial_connection_changed(
                current_generation,
                ok,
                text,
            )
        )
        self.worker.error_occurred.connect(
            lambda text, current_generation=generation: self._on_serial_error(
                current_generation,
                text,
            )
        )

    def disconnect(self) -> None:
        self._connection_generation += 1
        self._clear_pending_air_cmds("串口断开")
        self._clear_capability_ack("串口断开")
        self._clear_mission_packet_stats()

        if not self.link.close():
            self.worker = self.link.worker
            self.state.connected = False
            self._set_connection_message(
                "connection.error", detail="serial worker shutdown timeout"
            )
            self.state.receive_health.warning = self.state.connection_text
            self.state.touch()
            return
        self.worker = None

        protocol_worker = self.protocol_worker
        if protocol_worker is not None:
            protocol_worker.request_stop()
            if not protocol_worker.wait(15000):
                self.state.connected = False
                self._set_connection_message(
                    "connection.error", detail="protocol worker drain timeout"
                )
                self.state.receive_health.warning = self.state.connection_text
                self.state.touch()
                return
            self.protocol_worker = None

        if self.logger.session_active:
            try:
                self.logger.close_session()
            except Exception as exc:
                self.state.connected = False
                self._set_connection_message(
                    "connection.error", detail=f"logger close failed: {exc}"
                )
                self.state.receive_health.warning = f"日志关闭失败: {exc}"
                self.state.touch()
                return

        self._replace_state(connected=False, connection_text="未连接")

    def shutdown(self) -> None:
        if self.simulation_worker is not None:
            self.simulation_worker.request_cancel()
        if self.processing_worker is not None:
            self.processing_worker.request_cancel()
        if self.data_migration_worker is not None:
            self.data_migration_worker.request_cancel()
        for thread in (
            self.simulation_thread,
            self.processing_thread,
            self.data_migration_thread,
        ):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(30000)
        self.disconnect()
        # A normal disconnect has already drained the producer/consumer chain.
        # If a slow disk or driver exceeded the interactive timeout, make one
        # final bounded attempt while retaining every running QThread object.
        if self.link.worker is not None:
            self.link.close()
            self.worker = self.link.worker
        protocol_worker = self.protocol_worker
        if protocol_worker is not None:
            protocol_worker.request_stop()
            if protocol_worker.wait(30000):
                self.protocol_worker = None
        if self.protocol_worker is None:
            self.logger.close()

    def on_connection_changed(self, ok: bool, text: str) -> None:
        self.state.connected = bool(ok)
        self._set_connection_message(
            "connection.connected" if ok else "connection.disconnected",
            detail=text,
        )
        if not ok:
            self._clear_mission_packet_stats()
        self.state.touch()

    def _on_serial_connection_changed(self, generation: int, ok: bool, text: str) -> None:
        if generation == self._connection_generation:
            self.on_connection_changed(ok, text)

    def _on_serial_error(self, generation: int, text: str) -> None:
        if generation == self._connection_generation:
            self.on_error(text)

    def _on_serial_bytes_sent(self, generation: int, byte_count: int) -> None:
        if generation != self._connection_generation:
            return
        diagnostics = self.state.handshake
        diagnostics.gsp_air_tx_serial_writes += 1
        diagnostics.serial_tx_bytes += max(0, int(byte_count))
        self.state.touch()

    def on_error(self, text: str) -> None:
        self._set_connection_message("connection.error", detail=str(text))
        self.state.receive_health.warning = str(text)
        self.state.touch()

    def on_protocol_warning(self, text: str) -> None:
        self.state.receive_health.warning = str(text)
        self.state.touch()

    def on_protocol_diagnostics(self, diagnostics: ProtocolWorkerDiagnostics) -> None:
        if diagnostics.session_generation != self._connection_generation:
            return
        health = self.state.receive_health
        health.serial_rx_bytes = diagnostics.serial_rx_bytes
        health.serial_rx_chunks = diagnostics.serial_rx_chunks
        health.gsp_frames = diagnostics.gsp_frames
        health.gsp_parse_errors = diagnostics.gsp_parse_errors
        health.gsp_crc_errors = diagnostics.gsp_crc_errors
        health.gsp_resyncs = diagnostics.gsp_resyncs
        health.parser_buffer_size = diagnostics.parser_buffer_size
        health.air_frames = diagnostics.air_frames
        health.air_parse_errors = diagnostics.air_parse_errors
        health.protocol_queue_depth = diagnostics.protocol_queue_depth
        health.protocol_queue_capacity = diagnostics.protocol_queue_capacity
        health.ui_mailbox_depth = diagnostics.ui_mailbox_depth
        health.ui_mailbox_capacity = diagnostics.ui_mailbox_capacity
        health.ui_coalesced_events = diagnostics.ui_coalesced_events
        health.logger_queue_depth = self.logger.queue_depth
        health.logger_queue_capacity = self.logger.queue_max_records
        health.last_rx_monotonic_ns = diagnostics.last_rx_monotonic_ns
        health.max_processing_lag_ms = diagnostics.max_processing_lag_ms
        health.warning = self.logger.last_error or diagnostics.warning
        self.state.touch()

    def _drain_protocol_mailbox(self) -> None:
        worker = self.protocol_worker
        if worker is None:
            return
        batch = worker.take_batch(512)
        if batch is not None:
            self.on_protocol_batch(batch)

    def on_protocol_batch(self, batch: ProtocolBatch) -> None:
        if batch.session_generation != self._connection_generation:
            return
        for event in batch.events:
            if event.rssi_dbm is not None:
                self.state.rssi_dbm = event.rssi_dbm
                self.state.snr_db = event.snr_db
            if isinstance(event.parsed_gsp, GsStatus):
                self._handle_gs_status(event.parsed_gsp)
            elif isinstance(event.parsed_gsp, GspAck):
                self._handle_gsp_ack(event.parsed_gsp)
            if event.air_message is not None:
                self._handle_air_message(event.air_message, event)
        self.state.touch()

    def _handle_gsp_ack(self, ack: GspAck) -> None:
        self._set_gsp_ack_message(
            "gsp.ack",
            type=ack.ack_gsp_type,
            result=enum_name(GspAckResult, ack.result),
            detail=ack.detail,
        )
        if ack.ack_gsp_type != int(GspType.AIR_TX):
            return
        diagnostics = self.state.handshake
        diagnostics.last_gsp_air_tx_ack_result = ack.result
        if ack.result == int(GspAckResult.OK):
            diagnostics.gsp_air_tx_ack_ok += 1
        else:
            diagnostics.gsp_air_tx_ack_fail += 1
            if self.pending_capability_ack is not None and not self.state.capability_acked:
                diagnostics.last_handshake_error = f"GSP_AIR_TX_{enum_name(GspAckResult, ack.result)}"

    def _handle_gs_status(self, status: GsStatus) -> None:
        self.state.gs_state = GS_STATE_MAP.get(status.gs_state, f"0x{status.gs_state:02X}")
        self.state.radio_state = RADIO_STATE_MAP.get(
            status.radio_state,
            f"0x{status.radio_state:02X}",
        )
        self.state.gs_tx_count = status.tx_cnt
        self.state.gs_rx_count = status.rx_cnt
        self.state.gs_crc_error_count = status.crc_err_cnt

    def _next_air_seq(self) -> int:
        value = self.air_seq
        self.air_seq = (self.air_seq + 1) & 0xFF
        return value

    def _cal_start_allowed(self, mode: int) -> bool:
        result = self.state.check_calibration_start(mode)
        if result is CalibrationStartResult.ALLOWED:
            return True
        self._set_radio_message(
            "radio.calibration_handshake_required"
            if result is CalibrationStartResult.HANDSHAKE_REQUIRED
            else "radio.calibration_mode_unsupported"
        )
        self._log(
            {
                "dir": "LOCAL",
                "layer": "AIR_COMMAND_TRANSACTION",
                "kind": "CAL_START_LOCAL_REJECTED",
                "mode": mode,
                "reason": result.value,
                "calibration_mode_mask": (
                    None if self.state.capability is None
                    else self.state.capability.calibration_mode_mask
                ),
            }
        )
        return False

    def _send_air_cmd(
        self,
        cmd_id: int,
        token: int,
        param0: int = 0,
        param1: int = 0,
    ) -> bool:
        if (cmd_id & 0xFF) == int(AirCmdId.CAL_START) and not self._cal_start_allowed(param0):
            return False
        if self.worker is None or not self.state.connected:
            QMessageBox.warning(
                self.window,
                self._tr("app.title"),
                self._tr("message.connect_ground_station_first"),
            )
            return False
        if self.pending_capability_ack is not None:
            self._set_radio_message("radio.capability_priority")
            return False
        if self.pending_air_cmds:
            self._set_radio_message("radio.command_pending")
            return False
        if not self.state.air_command_link_allowed():
            self._set_radio_message("radio.command_not_allowed")
            return False

        seq = self._next_air_seq()
        air_frame = build_air_cmd(seq, cmd_id, token, param0, param1)
        gsp_frame = build_pc_to_gs_air_frame(air_frame)
        pending = PendingAirCommand(
            seq=seq,
            cmd_id=cmd_id & 0xFF,
            token=token & 0xFFFFFFFF,
            param0=param0 & 0xFF,
            param1=param1 & 0xFF,
            air_frame=air_frame,
            gsp_frame=gsp_frame,
            sent_count=0,
            max_retries=AIR_CMD_MAX_RETRIES,
            last_send_monotonic=0.0,
            created_monotonic=time.monotonic(),
            baseline_completed_face_mask=self.state.calibration.completed_face_mask,
        )
        self.pending_air_cmds[(pending.seq, pending.cmd_id)] = pending
        self._transmit_pending_air_cmd(pending, is_retry=False)
        return True

    def _transmit_pending_air_cmd(
        self,
        pending: PendingAirCommand,
        *,
        is_retry: bool,
    ) -> None:
        if pending.cmd_id == int(AirCmdId.CAL_START) and not self._cal_start_allowed(pending.param0):
            self._resolve_pending_air_cmd(pending, "LOCAL_REJECTED", detail="CAL_START gate")
            return
        if self.worker is None:
            return
        self.worker.send_bytes(pending.gsp_frame)
        self.state.handshake.gsp_air_tx_requests += 1
        pending.sent_count += 1
        pending.last_send_monotonic = time.monotonic()
        command_name = enum_name(AirCmdId, pending.cmd_id)
        self.state.pending_command_name = command_name
        self._set_radio_message(
            "radio.command_tx",
            command=command_name,
            action="RETRY" if is_retry else "TX",
            attempt=pending.sent_count,
            total=1 + pending.max_retries,
        )
        self._set_air_ack_message(
            "ack.waiting",
            seq=pending.seq,
            command=command_name,
            attempt=pending.sent_count,
            total=1 + pending.max_retries,
        )
        self._log_tx_command(
            pending.air_frame,
            pending.gsp_frame,
            pending.seq,
            pending.cmd_id,
            pending.token,
            pending.param0,
            pending.param1,
            pending.sent_count,
            is_retry,
        )

    def _update_capability_ack(self, capability: AirCapabilityMessage) -> None:
        pending = self.pending_capability_ack
        if (
            pending is not None
            and pending.capability_seq == capability.seq
            and pending.air_profile_id == capability.air_profile_id
        ):
            return
        if pending is not None:
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "HANDSHAKE",
                    "kind": "CAPABILITY_ACK_REPLACED",
                    "old_capability_seq": pending.capability_seq,
                    "new_capability_seq": capability.seq,
                }
            )

        command_seq = self._next_air_seq()
        air_frame = build_air_cmd(
            command_seq,
            int(AirCmdId.CAPABILITY_ACK),
            0,
            capability.seq,
            capability.air_profile_id,
        )
        self.pending_capability_ack = PendingCapabilityAck(
            capability_seq=capability.seq,
            air_profile_id=capability.air_profile_id,
            command_seq=command_seq,
            air_frame=air_frame,
            gsp_frame=build_pc_to_gs_air_frame(air_frame),
        )
        diagnostics = self.state.handshake
        diagnostics.handshake_state = HandshakeState.HANDSHAKING
        diagnostics.accepted_capability_seq = capability.seq
        diagnostics.capability_ack_cmd_seq = command_seq
        diagnostics.capability_ack_attempts = 0
        diagnostics.last_handshake_error = ""
        self._transmit_capability_ack(is_retry=False)

    def _transmit_capability_ack(self, *, is_retry: bool) -> None:
        pending = self.pending_capability_ack
        if pending is None or self.worker is None:
            return
        self.worker.send_bytes(pending.gsp_frame)
        pending.sent_count += 1
        pending.last_send_monotonic = time.monotonic()
        diagnostics = self.state.handshake
        diagnostics.handshake_state = HandshakeState.HANDSHAKING
        diagnostics.capability_ack_cmd_seq = pending.command_seq
        diagnostics.capability_ack_attempts = pending.sent_count
        diagnostics.last_capability_ack_tx_time = time.time()
        diagnostics.gsp_air_tx_requests += 1
        self.state.pending_command_name = "CAPABILITY_ACK"
        self._set_radio_message(
            "radio.capability_tx",
            action="RETRY" if is_retry else "TX",
            cap_seq=pending.capability_seq,
            cmd_seq=pending.command_seq,
        )
        self._log_tx_command(
            pending.air_frame,
            pending.gsp_frame,
            pending.command_seq,
            int(AirCmdId.CAPABILITY_ACK),
            0,
            pending.capability_seq,
            pending.air_profile_id,
            pending.sent_count,
            is_retry,
        )
        self._log(
            {
                "dir": "TX",
                "layer": "HANDSHAKE",
                "kind": "CAPABILITY_ACK_TX",
                "cmd_seq": pending.command_seq,
                "capability_seq": pending.capability_seq,
                "air_profile_id": pending.air_profile_id,
                "attempt": pending.sent_count,
                "retry": bool(is_retry),
            }
        )

    def _complete_capability_ack(self, source: str) -> None:
        self.pending_capability_ack = None
        diagnostics = self.state.handshake
        diagnostics.handshake_state = HandshakeState.ACKED
        diagnostics.last_handshake_error = ""
        if source == "AIR_ACK":
            diagnostics.capability_acked_by_air_ack = True
        elif source == "PREFLIGHT_STATUS":
            diagnostics.capability_acked_by_preflight_status = True
        self.state.pending_command_name = ""
        self._set_radio_message("radio.capability_complete", source=source)
        self._log(
            {
                "dir": "LOCAL",
                "layer": "HANDSHAKE",
                "kind": "CAPABILITY_HANDSHAKE_COMPLETE",
                "source": source,
            }
        )

    def _check_air_cmd_timeouts(self) -> None:
        now = time.monotonic()
        timeout_s = AIR_CMD_ACK_TIMEOUT_MS / 1000.0

        capability_pending = self.pending_capability_ack
        if (
            capability_pending is not None
            and now - capability_pending.last_send_monotonic >= timeout_s
        ):
            if capability_pending.sent_count <= capability_pending.max_retries:
                self._transmit_capability_ack(is_retry=True)
            else:
                self._log(
                    {
                        "dir": "LOCAL",
                        "layer": "HANDSHAKE",
                        "kind": "CAPABILITY_ACK_TIMEOUT",
                        "capability_seq": capability_pending.capability_seq,
                        "sent_count": capability_pending.sent_count,
                    }
                )
                self.pending_capability_ack = None
                self.state.pending_command_name = ""
                self._set_radio_message("radio.capability_timeout")
                self.state.handshake.handshake_state = HandshakeState.ERROR
                self.state.handshake.last_handshake_error = "CAPABILITY_ACK_TIMEOUT"

        for key, pending in list(self.pending_air_cmds.items()):
            if now - pending.last_send_monotonic < timeout_s:
                continue
            if pending.sent_count <= pending.max_retries:
                self._transmit_pending_air_cmd(pending, is_retry=True)
                continue
            self._resolve_pending_air_cmd(pending, "TIMEOUT")
            self._set_air_ack_message(
                "ack.timeout",
                seq=pending.seq,
                command=enum_name(AirCmdId, pending.cmd_id),
                attempts=pending.sent_count,
            )
            if pending.cmd_id == int(AirCmdId.START_MISSION):
                self.state.last_start_failure_result = None
                self.state.start_transaction_timed_out = True
                self.state.start_snapshot_busy_seen = False
                self._set_radio_message("radio.start_timeout")
            elif pending.cmd_id in CALIBRATION_COMMAND_IDS:
                self._set_radio_message(
                    "radio.calibration_timeout",
                    command=enum_name(AirCmdId, pending.cmd_id),
                )
            else:
                self._set_radio_message("radio.air_ack_timeout")
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "AIR_PARSED",
                    "kind": "ACK_TIMEOUT",
                    "ack_seq": pending.seq,
                    "ack_cmd_id": pending.cmd_id,
                    "sent_count": pending.sent_count,
                }
            )
        self._refresh_pending_command_name()

    def _clear_pending_air_cmds(self, reason: str) -> None:
        pending_values = list(self.pending_air_cmds.values())
        self.pending_air_cmds.clear()
        for pending in pending_values:
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "AIR_PARSED",
                    "kind": "ACK_CANCELLED",
                    "reason": reason,
                    "ack_seq": pending.seq,
                    "ack_cmd_id": pending.cmd_id,
                    "sent_count": pending.sent_count,
                }
            )
        self._refresh_pending_command_name()

    def _clear_capability_ack(self, reason: str) -> None:
        pending = self.pending_capability_ack
        self.pending_capability_ack = None
        if pending is not None:
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "HANDSHAKE",
                    "kind": "CAPABILITY_ACK_CANCELLED",
                    "reason": reason,
                    "capability_seq": pending.capability_seq,
                    "sent_count": pending.sent_count,
                }
            )
        self._refresh_pending_command_name()

    def _refresh_pending_command_name(self) -> None:
        if self.pending_capability_ack is not None:
            self.state.pending_command_name = "CAPABILITY_ACK"
        elif self.pending_air_cmds:
            pending = next(iter(self.pending_air_cmds.values()))
            self.state.pending_command_name = enum_name(AirCmdId, pending.cmd_id)
        else:
            self.state.pending_command_name = ""

    def _find_pending_air_cmd(self, cmd_id: int) -> PendingAirCommand | None:
        target = cmd_id & 0xFF
        return next(
            (pending for pending in self.pending_air_cmds.values() if pending.cmd_id == target),
            None,
        )

    def _pending_calibration_command(self) -> PendingAirCommand | None:
        return next(
            (
                pending
                for pending in self.pending_air_cmds.values()
                if pending.cmd_id in CALIBRATION_COMMAND_IDS
            ),
            None,
        )

    def _resolve_pending_air_cmd(
        self,
        pending: PendingAirCommand,
        resolved_by: str,
        *,
        result: int | None = None,
        detail: str = "",
    ) -> bool:
        key = (pending.seq & 0xFF, pending.cmd_id & 0xFF)
        if self.pending_air_cmds.get(key) is not pending:
            return False
        del self.pending_air_cmds[key]
        self._refresh_pending_command_name()
        age_ms = (
            None
            if pending.created_monotonic <= 0.0
            else max(0.0, (time.monotonic() - pending.created_monotonic) * 1000.0)
        )
        self._log(
            {
                "dir": "LOCAL",
                "layer": "AIR_COMMAND_TRANSACTION",
                "kind": "AIR_COMMAND_TRANSACTION_RESOLVED",
                "command_seq": pending.seq,
                "command_id": pending.cmd_id,
                "command_name": enum_name(AirCmdId, pending.cmd_id),
                "param0": pending.param0,
                "param1": pending.param1,
                "target_mode": (
                    pending.param0 if pending.cmd_id == int(AirCmdId.CAL_START) else None
                ),
                "target_face": (
                    pending.param0 if pending.cmd_id == int(AirCmdId.CAL_FACE) else None
                ),
                "attempts": pending.sent_count,
                "age_ms": age_ms,
                "resolved_by": resolved_by,
                "result": result,
                "result_name": (
                    None if result is None else enum_name(AirAckResult, result)
                ),
                "detail": detail,
            }
        )
        return True

    def _supersede_calibration_pending(self) -> bool:
        pending_values = list(self.pending_air_cmds.values())
        if not pending_values:
            return True
        if any(pending.cmd_id not in CALIBRATION_COMMAND_IDS for pending in pending_values):
            return False
        for pending in pending_values:
            self._resolve_pending_air_cmd(
                pending,
                "SUPERSEDED",
                detail="superseded by a new CAL_START",
            )
        return True

    @staticmethod
    def _cal_start_observed(
        mode: int,
        calibration_state: int,
    ) -> bool:
        if mode == int(AirCalibrationMode.SIX_FACE):
            return calibration_state in {
                int(AirCalibrationState.WAIT_FACE),
                int(AirCalibrationState.COLLECTING),
                int(AirCalibrationState.CHECKING),
                int(AirCalibrationState.READY),
            }
        if mode == int(AirCalibrationMode.ONE_FACE):
            return calibration_state in {
                int(AirCalibrationState.COLLECTING),
                int(AirCalibrationState.CHECKING),
                int(AirCalibrationState.READY),
            }
        return False

    def _resolve_calibration_pending_from_snapshot(
        self, message: AirPreflightStatusMessage
    ) -> None:
        pending = self._pending_calibration_command()
        if pending is None:
            return
        observed = False
        detail = ""
        if pending.cmd_id == int(AirCmdId.CAL_START):
            observed = bool(
                message.calibration_mode == pending.param0
                and self._cal_start_observed(
                    message.calibration_mode,
                    message.calibration_state,
                )
            )
            detail = "CAL_START state/mode observed"
        elif pending.cmd_id == int(AirCmdId.CAL_FACE):
            face_bit = 1 << pending.param0
            baseline_bit = bool(pending.baseline_completed_face_mask & face_bit)
            current_bit = bool(message.completed_face_mask & face_bit)
            face_collecting = bool(
                message.current_face == pending.param0
                and message.calibration_state
                in {
                    int(AirCalibrationState.COLLECTING),
                    int(AirCalibrationState.CHECKING),
                }
            )
            observed = face_collecting or current_bit != baseline_bit
            detail = "CAL_FACE current_face/state or mask transition observed"
        elif pending.cmd_id == int(AirCmdId.CAL_RESET):
            observed = bool(
                message.calibration_mode == int(AirCalibrationMode.NOT_SELECTED)
                and message.calibration_state == int(AirCalibrationState.IDLE)
                and not message.calibration_ready
                and message.completed_face_mask == 0
            )
            detail = "CAL_RESET idle snapshot observed"
        # AIR_PROTOCOL does not define a unique CAL_STOP snapshot state.
        # CAL_STOP therefore remains ACK/timeout based.
        if observed and self._resolve_pending_air_cmd(
            pending, "PREFLIGHT_STATUS", detail=detail
        ):
            self._set_radio_message(
                "radio.calibration_recovered",
                command=enum_name(AirCmdId, pending.cmd_id),
                source="PREFLIGHT_STATUS",
            )

    def _resolve_calibration_pending_from_status(self, message: AirStatusMessage) -> None:
        pending = self._pending_calibration_command()
        if pending is None:
            return
        observed = False
        if (
            pending.cmd_id == int(AirCmdId.CAL_FACE)
            and message.status_id == int(AirStatusId.CALIBRATION_FACE)
        ):
            observed = message.arg0 == pending.param0
        elif (
            pending.cmd_id == int(AirCmdId.CAL_START)
            and message.status_id == int(AirStatusId.CALIBRATION)
        ):
            observed = bool(
                message.arg1 == pending.param0
                and self._cal_start_observed(
                    message.arg1,
                    message.arg0,
                )
            )
        if observed and self._resolve_pending_air_cmd(
            pending,
            "STATUS_EVENT",
            detail=enum_name(AirStatusId, message.status_id),
        ):
            self._set_radio_message(
                "radio.calibration_recovered",
                command=enum_name(AirCmdId, pending.cmd_id),
                source="STATUS_EVENT",
            )

    def send_ping(self) -> None:
        self._send_air_cmd(int(AirCmdId.PING), token=int(time.time()) & 0xFFFFFFFF)

    def send_lock(self) -> None:
        self._send_air_cmd(int(AirCmdId.LOCK), token=TOKEN_LOCK)

    def send_unlock(self) -> None:
        self._send_air_cmd(int(AirCmdId.UNLOCK), token=TOKEN_UNLOCK)

    def send_start(self) -> None:
        if self.state.start_transaction_pending():
            self._set_radio_message("radio.start_in_progress")
            return
        if not self.state.start_button_enabled():
            self._set_radio_message(
                "radio.start_blocked",
                reason=EnumParam("ack_result", self.state.start_block_reason_name()),
            )
            return
        if self._send_air_cmd(
            int(AirCmdId.START_MISSION), token=TOKEN_START_MISSION
        ):
            self.state.last_start_failure_result = None
            self.state.start_transaction_timed_out = False
            self.state.start_snapshot_busy_seen = False
            self._set_radio_message("radio.start_pending")
            pending_start = self._find_pending_air_cmd(int(AirCmdId.START_MISSION))
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "AIR_COMMAND_TRANSACTION",
                    "kind": "START_TRANSACTION_SUBMITTED",
                    "command_seq": (
                        None if pending_start is None else pending_start.seq
                    ),
                }
            )

    def send_cal_start(self, mode: int) -> None:
        if not self._cal_start_allowed(mode):
            return
        mode = int(mode)
        pending_calibration = self._pending_calibration_command()
        if pending_calibration is not None:
            can_supersede = bool(
                self.worker is not None
                and self.pending_capability_ack is None
                and self.state.preflight_command_entry_allowed()
            )
            if not can_supersede or not self._supersede_calibration_pending():
                self._set_radio_message("radio.command_pending")
                return
        self._send_air_cmd(int(AirCmdId.CAL_START), TOKEN_CALIBRATION, mode, 0)

    def send_cal_face(self, face: int) -> None:
        if not 0 <= int(face) <= 5:
            self._set_radio_message("radio.calibration_face_invalid")
            return
        calibration = self.state.calibration
        if (
            calibration.mode != int(AirCalibrationMode.SIX_FACE)
            or calibration.state
            not in {
                int(AirCalibrationState.WAIT_FACE),
                int(AirCalibrationState.READY),
            }
        ):
            self._set_radio_message("radio.calibration_face_unavailable")
            return
        self._send_air_cmd(int(AirCmdId.CAL_FACE), TOKEN_CALIBRATION, int(face), 0)

    def send_cal_stop(self) -> None:
        self._send_air_cmd(int(AirCmdId.CAL_STOP), TOKEN_CALIBRATION)

    def send_cal_reset(self) -> None:
        self._send_air_cmd(int(AirCmdId.CAL_RESET), TOKEN_CALIBRATION)

    def send_align_start(self) -> None:
        if not self.state.calibration.ready:
            self._set_radio_message("radio.alignment_requires_calibration")
            return
        self._send_air_cmd(int(AirCmdId.ALIGN_START), TOKEN_ALIGNMENT)

    def send_align_stop(self) -> None:
        self._send_air_cmd(int(AirCmdId.ALIGN_STOP), TOKEN_ALIGNMENT)

    def send_align_reset(self) -> None:
        self._send_air_cmd(int(AirCmdId.ALIGN_RESET), TOKEN_ALIGNMENT)

    def _handle_air_message(self, message, event: ProtocolEvent | None = None) -> None:
        self.state.handshake.last_air_rx_monotonic_ns = (
            event.host_rx_monotonic_ns if event is not None else time.monotonic_ns()
        )
        if isinstance(message, AirCapabilityMessage):
            self._handle_capability(message, event)
        elif isinstance(message, AirPreflightStatusMessage):
            self._handle_preflight_status(message)
        elif isinstance(message, AirSensorStatusMessage):
            self._handle_sensor_status(message)
        elif isinstance(message, AirPreflightStateMessage):
            self._handle_sensor_message(message, event, source="PREFLIGHT_STATE")
        elif isinstance(message, AirFlightStateMessage):
            self._handle_flight_state(message, event)
        elif isinstance(message, AirStatusMessage):
            self._handle_status_message(message, event)
        elif isinstance(message, AirAckMessage):
            self._handle_ack_message(message)

    def _handle_capability(
        self,
        message: AirCapabilityMessage,
        event: ProtocolEvent | None = None,
    ) -> None:
        diagnostics = self.state.handshake
        diagnostics.capability_rx += 1
        diagnostics.last_capability_seq = message.seq
        diagnostics.last_capability_rx_time = time.time()
        if self.state.capability_acked:
            diagnostics.duplicate_capability_after_ack += 1
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "HANDSHAKE",
                    "kind": "STALE_OR_DUPLICATE_CAPABILITY_AFTER_ACK",
                    "received_capability_seq": message.seq,
                    "accepted_capability_seq": (
                        self.state.capability.seq if self.state.capability is not None else None
                    ),
                    "host_rx_monotonic_ns": (
                        event.host_rx_monotonic_ns if event is not None else None
                    ),
                    "duplicate_count": diagnostics.duplicate_capability_after_ack,
                }
            )
            return

        self.state.capability = message
        self.state.profile_supported = message.profile_supported
        self.state.capability_error = ""
        if not message.profile_supported:
            self._clear_capability_ack("unsupported_profile")
            diagnostics.handshake_state = HandshakeState.ERROR
            diagnostics.last_handshake_error = "UNSUPPORTED_PROFILE"
            self.state.capability_error = "UNSUPPORTED_PROFILE"
            self._set_radio_message("capability.error.UNSUPPORTED_PROFILE")
            return
        if message.accel_full_scale_g <= 0 or message.gyro_full_scale_dps <= 0:
            self._clear_capability_ack("invalid_scale")
            diagnostics.handshake_state = HandshakeState.ERROR
            diagnostics.last_handshake_error = "INVALID_IMU_FULL_SCALE"
            self.state.capability_error = "INVALID_IMU_FULL_SCALE"
            self._set_radio_message("capability.error.INVALID_IMU_FULL_SCALE")
            return
        if message.command_policy not in {
            int(AirCommandPolicy.PREFLIGHT_ONLY),
            int(AirCommandPolicy.MISSION_ALLOWED),
        }:
            self._clear_capability_ack("invalid_command_policy")
            diagnostics.handshake_state = HandshakeState.ERROR
            diagnostics.last_handshake_error = "INVALID_COMMAND_POLICY"
            self.state.capability_error = "INVALID_COMMAND_POLICY"
            self._set_radio_message("capability.error.INVALID_COMMAND_POLICY")
            return
        self._update_capability_ack(message)

    def _handle_preflight_status(self, message: AirPreflightStatusMessage) -> None:
        diagnostics = self.state.handshake
        diagnostics.preflight_status_rx += 1
        diagnostics.last_preflight_status_rx_monotonic_ns = time.monotonic_ns()
        diagnostics.preflight_status_capability_acked = bool(message.capability_acked)
        self._resolve_calibration_pending_from_snapshot(message)
        if (
            self.state.start_transaction_pending()
            and message.start_block_reason == int(AirAckResult.BUSY)
        ):
            if not self.state.start_snapshot_busy_seen:
                pending_start = self._find_pending_air_cmd(int(AirCmdId.START_MISSION))
                self._log(
                    {
                        "dir": "LOCAL",
                        "layer": "AIR_COMMAND_TRANSACTION",
                        "kind": "START_TRANSACTION_PROGRESS",
                        "command_seq": (
                            None if pending_start is None else pending_start.seq
                        ),
                        "observed_by": "PREFLIGHT_STATUS",
                        "snapshot_start_block_reason": int(AirAckResult.BUSY),
                        "snapshot_start_block_reason_name": "BUSY",
                    }
                )
            self.state.start_snapshot_busy_seen = True
        elif self.state.start_transaction_pending():
            self.state.start_snapshot_busy_seen = False
        elif message.start_block_reason == int(AirAckResult.OK):
            self.state.last_start_failure_result = None
            self.state.start_transaction_timed_out = False
        previous_face = self.state.calibration.current_face
        previous_mode = self.state.calibration.mode
        if previous_mode != message.calibration_mode:
            self._clear_calibration_diagnostic()
        elif previous_face != message.current_face:
            diagnostic_face = self.state.latest_calibration_diagnostic_face
            if diagnostic_face != message.current_face:
                self._clear_calibration_diagnostic()
        elif (
            message.calibration_state == int(AirCalibrationState.IDLE)
            and self.state.latest_calibration_diagnostic_reason
        ):
            self._clear_calibration_diagnostic()
        self.state.lifecycle_state = message.lifecycle_state
        self.state.calibration.state = message.calibration_state
        self.state.calibration.mode = message.calibration_mode
        self.state.calibration.completed_face_mask = message.completed_face_mask
        self.state.calibration.current_face = message.current_face
        self.state.calibration.ready = message.calibration_ready
        self.state.alignment.state = message.alignment_state
        self.state.alignment.ready = (
            False
            if message.alignment_state == int(AirAlignmentState.STALE)
            else message.alignment_ready
        )
        if message.alignment_state == int(AirAlignmentState.STALE):
            self._set_radio_message("radio.alignment_stale")
        self.state.system_ready = message.system_ready
        self.state.start_unlocked = message.start_unlocked
        self.state.selftest_passed = message.selftest_passed
        self.state.gnss_position_usable = message.gnss_position_usable
        self.state.start_block_reason = message.start_block_reason
        if message.capability_acked and not self.state.capability_acked:
            self._complete_capability_ack("PREFLIGHT_STATUS")

    def _handle_sensor_status(self, message: AirSensorStatusMessage) -> None:
        result = self.state.alignment_sensor_snapshots.receive(message)
        if result.duplicate_index or result.total_mismatch:
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "SENSOR_SNAPSHOT",
                    "kind": "SENSOR_SNAPSHOT_FRAME_DIAGNOSTIC",
                    "snapshot_id": message.snapshot_id,
                    "index": message.index,
                    "total": message.total,
                    "duplicate_index": result.duplicate_index,
                    "total_mismatch": result.total_mismatch,
                    "duplicate_policy": "LATEST_WINS",
                }
            )

    def _handle_sensor_message(
        self,
        message: AirPreflightStateMessage | AirFlightStateMessage,
        event: ProtocolEvent | None,
        *,
        source: str,
    ) -> None:
        # The controller's accepted Capability is authoritative. A queued,
        # stale Capability may have changed parser-side conversion context,
        # but must never change the live controller session after ACK.
        accel = None
        gyro = None
        if self.state.capability is not None and self.state.capability.profile_supported:
            try:
                accel = accel_raw_to_mps2(
                    message.accel_raw,
                    self.state.capability.accel_full_scale_g,
                )
                gyro = gyro_raw_to_radps(
                    message.gyro_raw,
                    self.state.capability.gyro_full_scale_dps,
                )
            except ValueError:
                accel = None
                gyro = None
        host_ns = event.host_rx_monotonic_ns if event is not None else time.monotonic_ns()
        revision = self.state.sensor.revision + 1
        velocity = message.vel_mps if isinstance(message, AirFlightStateMessage) else None
        position = message.pos_m if isinstance(message, AirFlightStateMessage) else None
        self.state.sensor = SensorSnapshot(
            source=source,
            seq=message.seq,
            time_ms=message.time_ms,
            accel_raw=message.accel_raw,
            gyro_raw=message.gyro_raw,
            accel_mps2=accel,
            gyro_radps=gyro,
            quat_q15=message.quat_q15,
            quat=message.quat,
            quat_valid=message.quat_valid,
            euler_rpy=quat_to_euler_rpy(message.quat),
            velocity_mps=velocity,
            position_m=position,
            host_rx_monotonic_ns=host_ns,
            revision=revision,
        )

    def _handle_flight_state(
        self,
        message: AirFlightStateMessage,
        event: ProtocolEvent | None,
    ) -> None:
        self._mark_mission_started("first_flight_state", message.time_ms)
        if self.state.mission_presentation.phase is MissionPhase.PRE_START:
            self.state.mission_presentation.phase = MissionPhase.MISSION_ACTIVE
        self._handle_sensor_message(message, event, source="FLIGHT_STATE")
        if self.state.mission_first_time_ms is None:
            self.state.mission_first_time_ms = message.time_ms
        self.state.latest_flight_time_ms = message.time_ms
        mission_time_s = max(
            0.0,
            (message.time_ms - self.state.mission_first_time_ms) / 1000.0,
        )
        self.state.live_plot.append(mission_time_s, message.vel_mps, message.pos_m)

        stats = event.packet_stats if event is not None else None
        if stats is None:
            stats = self._track_flight_packet(message.time_ms)
        self.state.received_flight_packets = int(stats["received_flight_packets"])
        self.state.estimated_lost_packets = int(stats["estimated_lost_packets"])
        self.state.expected_flight_packets = int(stats["expected_flight_packets"])
        self.state.packet_loss_rate = float(stats["packet_loss_rate"])

    def _handle_status_message(
        self,
        message: AirStatusMessage,
        event: ProtocolEvent | None,
    ) -> None:
        text = format_air_status_message(message, self._translator())
        self.state.last_status = text
        host_ns = event.host_rx_monotonic_ns if event is not None else time.monotonic_ns()
        self.events.append(
            FlightEvent(
                seq=message.seq,
                status_id=message.status_id,
                name=enum_name(AirStatusId, message.status_id),
                time_ms=message.time_ms,
                arg0=message.arg0,
                arg1=message.arg1,
                host_rx_monotonic_ns=host_ns,
            )
        )
        self._resolve_calibration_pending_from_status(message)

        status_id = message.status_id
        if status_id == int(AirStatusId.BOOT):
            self.state.lifecycle_state = int(AirLifecycleState.BOOT)
        elif status_id == int(AirStatusId.SELFTEST_COMPLETE):
            self.state.selftest_passed = bool(message.arg0)
        elif status_id == int(AirStatusId.MISSION_START):
            self._mark_mission_started("mission_start_status", message.time_ms)
            self._update_mission_presentation(
                MissionPhase.MISSION_ACTIVE,
                "MISSION_START",
                message.time_ms,
                host_ns,
            )
            self.state.lifecycle_state = int(AirLifecycleState.FLIGHT)
        elif status_id == int(AirStatusId.LAUNCH):
            self._update_mission_presentation(
                MissionPhase.IN_FLIGHT, "LAUNCH", message.time_ms, host_ns
            )
            self.state.lifecycle_state = int(AirLifecycleState.FLIGHT)
        elif status_id == int(AirStatusId.PARACHUTE_DEPLOY):
            self._update_mission_presentation(
                MissionPhase.RECOVERY,
                "PARACHUTE_DEPLOY",
                message.time_ms,
                host_ns,
                parachute_deployed=True,
            )
            self.state.lifecycle_state = int(AirLifecycleState.RECOVERY)
        elif status_id == int(AirStatusId.LANDING):
            self._update_mission_presentation(
                MissionPhase.LANDED, "LANDING", message.time_ms, host_ns
            )
            self.state.lifecycle_state = int(AirLifecycleState.LANDED)
        elif status_id == int(AirStatusId.LOCKED):
            self.state.start_unlocked = False
        elif status_id == int(AirStatusId.UNLOCKED):
            self.state.start_unlocked = True
        elif status_id == int(AirStatusId.GNSS_POSITION):
            if message.arg0 in (0, 1):
                self.state.gnss_position_usable = bool(message.arg0)
        elif status_id == int(AirStatusId.ALIGNMENT):
            self.state.alignment.state = message.arg0
            # STATUS gives an immediate indication, but only PREFLIGHT_STATUS
            # is allowed to assert alignment_ready again after STALE.
            if message.arg0 != int(AirAlignmentState.READY):
                self.state.alignment.ready = False
            terminal_snapshot = self.state.alignment_sensor_snapshots.terminate(
                message.arg1, message.arg0
            )
            if terminal_snapshot is not None:
                self._log(
                    {
                        "dir": "LOCAL",
                        "layer": "SENSOR_SNAPSHOT",
                        "kind": "SENSOR_SNAPSHOT_TERMINAL",
                        "snapshot_id": terminal_snapshot.snapshot_id,
                        "alignment_state": message.arg0,
                        "alignment_state_name": enum_name(
                            AirAlignmentState, message.arg0
                        ),
                        "expected_total": terminal_snapshot.expected_total,
                        "received_unique": len(terminal_snapshot.frames_by_index),
                        "complete": terminal_snapshot.complete,
                        "incomplete": terminal_snapshot.incomplete,
                    }
                )
            if message.arg0 == int(AirAlignmentState.STALE):
                self._set_radio_message("radio.alignment_stale")
        elif status_id == int(AirStatusId.CALIBRATION):
            self.state.calibration.state = message.arg0
            self.state.calibration.mode = message.arg1
            self.state.calibration.ready = message.arg0 == int(AirCalibrationState.READY)
        elif status_id == int(AirStatusId.CALIBRATION_FACE):
            if 0 <= message.arg0 <= 5 and message.arg1 == 1:
                self.state.calibration.completed_face_mask |= 1 << message.arg0
        elif status_id == int(AirStatusId.CALIBRATION_DIAGNOSTIC):
            reason = message.arg1
            if reason == int(AirCalibrationDiagnosticReason.NONE):
                self._clear_calibration_diagnostic(time_ms=message.time_ms)
            else:
                self.state.latest_calibration_diagnostic_reason = reason
                self.state.latest_calibration_diagnostic_face = message.arg0
                self.state.latest_calibration_diagnostic_time = message.time_ms

    def _handle_ack_message(self, message: AirAckMessage) -> None:
        self.state.handshake.air_ack_rx += 1
        self.state.handshake.air_ack_result = message.result
        result_name = enum_name(AirAckResult, message.result)
        command_name = enum_name(AirCmdId, message.ack_cmd_id)
        if message.ack_cmd_id == int(AirCmdId.CAPABILITY_ACK):
            diagnostics = self.state.handshake
            diagnostics.air_ack_result = message.result
            pending = self.pending_capability_ack
            matched = pending is not None and message.ack_seq == pending.command_seq
            self._set_air_ack_message(
                "ack.received" if matched else "ack.received_unmatched_capability",
                seq=message.ack_seq,
                command=command_name,
                result=EnumParam("ack_result", result_name),
            )
            if not matched:
                diagnostics.last_handshake_error = "AIR_ACK_SEQUENCE_MISMATCH"
                self._log(
                    {
                        "dir": "LOCAL",
                        "layer": "HANDSHAKE",
                        "kind": "CAPABILITY_ACK_MISMATCH",
                        "ack_seq": message.ack_seq,
                        "expected_cmd_seq": pending.command_seq if pending is not None else None,
                        "result": message.result,
                    }
                )
                return
            if message.result == int(AirAckResult.OK):
                self._complete_capability_ack("AIR_ACK")
            else:
                self.pending_capability_ack = None
                diagnostics.handshake_state = HandshakeState.ERROR
                diagnostics.last_handshake_error = f"AIR_ACK_{result_name}"
                self.state.pending_command_name = ""
                self._set_radio_message(
                    "radio.ack_failed",
                    command="CAPABILITY_ACK",
                    result=EnumParam("ack_result", result_name),
                )
            return

        key = (message.ack_seq & 0xFF, message.ack_cmd_id & 0xFF)
        pending = self.pending_air_cmds.get(key)
        matched = pending is not None
        if pending is not None:
            self._resolve_pending_air_cmd(
                pending,
                "AIR_ACK",
                result=message.result,
            )
        self._set_air_ack_message(
            "ack.received" if matched else "ack.received_unmatched_command",
            seq=message.ack_seq,
            command=command_name,
            result=EnumParam("ack_result", result_name),
        )
        if not matched:
            current = self._find_pending_air_cmd(message.ack_cmd_id)
            self._log(
                {
                    "dir": "LOCAL",
                    "layer": "AIR_COMMAND_TRANSACTION",
                    "kind": "STALE_OR_UNMATCHED_AIR_ACK",
                    "ack_seq": message.ack_seq,
                    "ack_cmd_id": message.ack_cmd_id,
                    "ack_cmd_name": command_name,
                    "result": message.result,
                    "result_name": result_name,
                    "current_pending_seq": None if current is None else current.seq,
                    "mission_started": self.state.mission_started,
                }
            )
            return

        if message.result == int(AirAckResult.OK):
            if message.ack_cmd_id == int(AirCmdId.START_MISSION):
                self._mark_mission_started("start_ack_ok", message.time_ms)
                self._set_radio_message("radio.start_confirmed")
            elif message.ack_cmd_id == int(AirCmdId.LOCK):
                self.state.start_unlocked = False
                self._set_radio_message("radio.lock_confirmed")
            elif message.ack_cmd_id == int(AirCmdId.UNLOCK):
                self.state.start_unlocked = True
                self._set_radio_message("radio.unlock_confirmed")
            elif message.ack_cmd_id in {
                int(AirCmdId.CAL_START),
                int(AirCmdId.CAL_FACE),
                int(AirCmdId.CAL_STOP),
                int(AirCmdId.CAL_RESET),
            }:
                if message.ack_cmd_id in {
                    int(AirCmdId.CAL_START),
                    int(AirCmdId.CAL_FACE),
                    int(AirCmdId.CAL_RESET),
                }:
                    self._clear_calibration_diagnostic()
                self._set_radio_message("radio.calibration_accepted", command=command_name)
            elif message.ack_cmd_id in {
                int(AirCmdId.ALIGN_START),
                int(AirCmdId.ALIGN_STOP),
                int(AirCmdId.ALIGN_RESET),
            }:
                self._set_radio_message("radio.alignment_accepted", command=command_name)
            else:
                self._set_radio_message("radio.ack_ok", command=command_name)
        elif (
            message.ack_cmd_id == int(AirCmdId.START_MISSION)
        ):
            self.state.last_start_failure_result = message.result
            self.state.start_transaction_timed_out = False
            self.state.start_snapshot_busy_seen = False
            if message.result == int(AirAckResult.BUSY):
                self._set_radio_message("radio.start_busy")
            else:
                self._set_radio_message(
                    "radio.start_failed",
                    result=EnumParam("ack_result", result_name),
                )
        elif (
            message.ack_cmd_id == int(AirCmdId.CAL_START)
            and message.result == int(AirAckResult.BAD_PARAM)
        ):
            self._set_radio_message("radio.calibration_bad_param")
        elif (
            message.result == int(AirAckResult.ALREADY_LOCKED)
            and message.ack_cmd_id == int(AirCmdId.LOCK)
        ):
            self.state.start_unlocked = False
            self._set_radio_message("radio.already_locked")
        elif (
            message.result == int(AirAckResult.ALREADY_UNLOCKED)
            and message.ack_cmd_id == int(AirCmdId.UNLOCK)
        ):
            self.state.start_unlocked = True
            self._set_radio_message("radio.already_unlocked")
        else:
            self._set_radio_message(
                "radio.ack_failed",
                command=command_name,
                result=EnumParam("ack_result", result_name),
            )

    def _mark_mission_started(self, source: str, time_ms: int | None = None) -> None:
        first_transition = not self.state.mission_started
        if first_transition:
            self.state.mission_started = True
            self.state.mission_start_source = source
            self.state.last_start_failure_result = None
            self.state.start_transaction_timed_out = False
            self.state.start_snapshot_busy_seen = False
            self._reset_mission_packet_stats(source)
            pending_start = self._find_pending_air_cmd(int(AirCmdId.START_MISSION))
            if pending_start is not None:
                resolved_by = {
                    "mission_start_status": "MISSION_START",
                    "first_flight_state": "FLIGHT_STATE",
                }.get(source, "STATUS_EVENT")
                self._resolve_pending_air_cmd(
                    pending_start,
                    resolved_by,
                    detail=source,
                )
            if (
                self.state.capability is not None
                and self.state.capability.command_policy == int(AirCommandPolicy.PREFLIGHT_ONLY)
            ):
                self._clear_pending_air_cmds("PREFLIGHT_ONLY mission started")
        if time_ms is not None and (
            self.state.mission_first_time_ms is None or source == "mission_start_status"
        ):
            self.state.mission_first_time_ms = int(time_ms)
        self._refresh_pending_command_name()

    def _update_mission_presentation(
        self,
        phase: MissionPhase,
        event_name: str,
        time_ms: int,
        host_monotonic_ns: int,
        *,
        parachute_deployed: bool | None = None,
    ) -> None:
        presentation = self.state.mission_presentation
        presentation.phase = phase
        presentation.last_critical_event_name = event_name
        presentation.last_critical_event_time_ms = int(time_ms)
        presentation.last_critical_event_host_monotonic_ns = int(host_monotonic_ns)
        if parachute_deployed is not None:
            presentation.parachute_deployed = bool(parachute_deployed)

    def _clear_calibration_diagnostic(self, *, time_ms: int | None = None) -> None:
        self.state.latest_calibration_diagnostic_reason = int(
            AirCalibrationDiagnosticReason.NONE
        )
        self.state.latest_calibration_diagnostic_face = 0xFF
        self.state.latest_calibration_diagnostic_time = time_ms

    def _clear_mission_packet_stats(self) -> None:
        self.mission_packet_tracking_active = False
        self.last_flight_time_ms = None
        self.received_flight_packets = 0
        self.estimated_lost_packets = 0

    def _reset_mission_packet_stats(self, source: str) -> None:
        if self.mission_packet_tracking_active:
            return
        self.mission_packet_tracking_active = True
        self.last_flight_time_ms = None
        self.received_flight_packets = 0
        self.estimated_lost_packets = 0
        self._log(
            {
                "dir": "LOCAL",
                "layer": "MISSION",
                "kind": "MISSION_PACKET_TRACKING_START",
                "source": source,
                "expected_period_ms": FLIGHT_TELEMETRY_PERIOD_MS,
            }
        )

    def _track_flight_packet(self, current_time_ms: int) -> dict[str, int | float | bool]:
        lost_since_previous = 0
        time_non_monotonic = False
        if not self.mission_packet_tracking_active:
            self._reset_mission_packet_stats("first_flight_state")
        if self.last_flight_time_ms is None:
            self.last_flight_time_ms = current_time_ms
        else:
            delta_ms = current_time_ms - self.last_flight_time_ms
            if delta_ms > 0:
                expected_steps = max(1, round(delta_ms / FLIGHT_TELEMETRY_PERIOD_MS))
                lost_since_previous = max(0, expected_steps - 1)
                self.last_flight_time_ms = current_time_ms
            else:
                time_non_monotonic = True
        self.received_flight_packets += 1
        self.estimated_lost_packets += lost_since_previous
        expected = self.received_flight_packets + self.estimated_lost_packets
        return {
            "lost_since_previous": lost_since_previous,
            "received_flight_packets": self.received_flight_packets,
            "estimated_lost_packets": self.estimated_lost_packets,
            "expected_flight_packets": expected,
            "packet_loss_rate": self.estimated_lost_packets / expected if expected else 0.0,
            "time_non_monotonic": time_non_monotonic,
        }

    def _log_tx_command(
        self,
        air_frame: bytes,
        gsp_frame: bytes,
        seq: int,
        cmd_id: int,
        token: int,
        param0: int,
        param1: int,
        attempt: int,
        retry: bool,
    ) -> None:
        common = {
            "dir": "TX",
            "seq": seq,
            "cmd_id": cmd_id,
            "cmd_name": enum_name(AirCmdId, cmd_id),
            "attempt": attempt,
            "retry": bool(retry),
        }
        self._log(
            {
                **common,
                "layer": "GSP",
                "kind": "GSP_AIR_TX",
                "raw_hex": gsp_frame.hex(),
                "air_hex": air_frame.hex(),
            }
        )
        self._log(
            {
                **common,
                "layer": "AIR_PARSED",
                "kind": "CMD",
                "token": token,
                "param0": param0,
                "param1": param1,
            }
        )

    def _log(self, record: dict) -> None:
        if not self.logger.session_active:
            return
        try:
            self.logger.write(record)
        except Exception as exc:
            self.state.receive_health.warning = f"日志写入异常: {exc}"

    def _ensure_user_dirs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            QMessageBox.warning(
                self.window,
                self._tr("app.title"),
                self._tr("message.open_folder_failed", folder=folder),
            )

    def open_log_dir(self) -> None:
        self._open_folder(self.log_dir)

    def open_data_dir(self) -> None:
        self._open_folder(self.data_dir)

    def choose_data_root(self) -> None:
        if (
            self.logger.session_active
            or self.protocol_worker is not None
            or self.simulation_thread is not None
            or self.processing_thread is not None
            or self.data_migration_thread is not None
        ):
            QMessageBox.information(
                self.window,
                self._tr("app.title"),
                self._tr("message.data_root_busy"),
            )
            return

        selection = self.window.request_data_directory(self.data_root)
        if selection is None:
            return
        selected_root = Path(selection.data_root).expanduser()
        if self._data_roots_equal(selected_root, self.data_root):
            return

        if selection.migrate_existing_data:
            self._start_data_migration(
                selected_root,
                selection.conflict_policy,
            )
            return

        try:
            self._apply_data_root(
                selected_root,
                migrate_existing_data=False,
                previous_data_root=self.data_root,
                migration_conflict_policy=selection.conflict_policy,
            )
        except Exception as exc:
            QMessageBox.warning(
                self.window,
                self._tr("app.title"),
                self._tr("message.data_root_failed", error=exc),
            )

    @staticmethod
    def _data_roots_equal(left: Path, right: Path) -> bool:
        try:
            return left.resolve(strict=False) == right.resolve(strict=False)
        except OSError:
            return str(left).casefold() == str(right).casefold()

    def _apply_data_root(
        self,
        selected_root: Path,
        *,
        migrate_existing_data: bool,
        previous_data_root: Path,
        migration_conflict_policy: DataMigrationConflictPolicy = (
            DataMigrationConflictPolicy.OVERWRITE
        ),
    ) -> None:
        selected_root = Path(selected_root).expanduser()
        if not selected_root.is_absolute():
            raise ValueError("The data root must be an absolute path")
        selected_log_dir = selected_root / "logs"
        selected_data_dir = selected_root / "data"
        previous_log_dir = self.log_dir

        self.logger.set_log_dir(selected_log_dir)
        try:
            saved_root = save_user_data_root(
                selected_root,
                migrate_existing_data=migrate_existing_data,
                previous_data_root=previous_data_root,
                migration_conflict_policy=migration_conflict_policy.value,
            )
        except Exception:
            self.logger.set_log_dir(previous_log_dir)
            raise

        self.data_root = saved_root
        self.log_dir = saved_root / "logs"
        self.data_dir = saved_root / "data"

    def _start_data_migration(
        self,
        selected_root: Path,
        conflict_policy: DataMigrationConflictPolicy = (
            DataMigrationConflictPolicy.OVERWRITE
        ),
    ) -> None:
        if self.data_migration_thread is not None:
            return
        self.previous_data_root = self.data_root
        self.pending_data_root = Path(selected_root)
        self.pending_data_migration_conflict_policy = (
            DataMigrationConflictPolicy(conflict_policy)
        )
        self.data_migration_cancel_requested = False
        self.window.begin_data_migration(
            self.previous_data_root,
            self.pending_data_root,
        )

        self.data_migration_thread = QThread(self.window)
        self.data_migration_worker = DataMigrationWorker(
            self.previous_data_root,
            self.pending_data_root,
            self.pending_data_migration_conflict_policy,
        )
        self.data_migration_worker.moveToThread(self.data_migration_thread)
        self.data_migration_thread.started.connect(self.data_migration_worker.run)
        self.data_migration_worker.planned.connect(
            self.window.set_data_migration_plan,
            Qt.QueuedConnection,
        )
        self.data_migration_worker.progress.connect(
            self.window.update_data_migration_progress,
            Qt.QueuedConnection,
        )
        self.data_migration_worker.ready_to_commit.connect(
            self._on_data_migration_ready_to_commit,
            Qt.QueuedConnection,
        )
        self.data_migration_worker.finished.connect(
            self._on_data_migration_finished,
            Qt.QueuedConnection,
        )
        self.data_migration_worker.cancelled.connect(
            self._on_data_migration_cancelled,
            Qt.QueuedConnection,
        )
        self.data_migration_worker.failed.connect(
            self._on_data_migration_failed,
            Qt.QueuedConnection,
        )
        self.data_migration_thread.finished.connect(
            self._on_data_migration_thread_finished
        )
        self.data_migration_thread.start()

    def cancel_data_migration(self) -> None:
        if self.data_migration_worker is None:
            return
        self.data_migration_cancel_requested = True
        self.data_migration_worker.request_cancel()
        self.window.mark_data_migration_cancelling()

    def _on_data_migration_ready_to_commit(self) -> None:
        worker = self.data_migration_worker
        selected_root = self.pending_data_root
        previous_root = self.previous_data_root
        if worker is None or selected_root is None or previous_root is None:
            return
        if self.data_migration_cancel_requested:
            worker.request_cancel()
            worker.finish_commit(False)
            return
        self.window.mark_data_migration_committing()
        try:
            self._apply_data_root(
                selected_root,
                migrate_existing_data=True,
                previous_data_root=previous_root,
                migration_conflict_policy=(
                    self.pending_data_migration_conflict_policy
                ),
            )
        except Exception as exc:
            worker.finish_commit(False, str(exc))
        else:
            worker.finish_commit(True)

    def _request_data_migration_thread_stop(self) -> None:
        if self.data_migration_worker is not None:
            self.data_migration_worker.deleteLater()
        if (
            self.data_migration_thread is not None
            and self.data_migration_thread.isRunning()
        ):
            self.data_migration_thread.quit()

    def _on_data_migration_finished(
        self,
        file_count: int,
        _byte_count: object,
        skipped_count: int,
        warning: str,
    ) -> None:
        target_root = self.data_root
        self.window.finish_data_migration_completed(
            file_count,
            target_root,
            warning,
            skipped_count,
        )
        self._request_data_migration_thread_stop()

    def _on_data_migration_cancelled(self) -> None:
        self.window.finish_data_migration_cancelled()
        self._request_data_migration_thread_stop()

    def _on_data_migration_failed(self, error: str) -> None:
        self.window.finish_data_migration_failed(error)
        self._request_data_migration_thread_stop()

    def _on_data_migration_thread_finished(self) -> None:
        self.window.set_data_tools_busy(False)
        self.data_migration_worker = None
        if self.data_migration_thread is not None:
            self.data_migration_thread.deleteLater()
        self.data_migration_thread = None
        self.data_migration_cancel_requested = False
        self.pending_data_root = None
        self.previous_data_root = None
        self.pending_data_migration_conflict_policy = (
            DataMigrationConflictPolicy.OVERWRITE
        )

    def generate_sim_validation_data(self) -> None:
        if self.state.mission_started:
            QMessageBox.information(
                self.window,
                self._tr("app.title"),
                self._tr("message.mission_sim_disabled"),
            )
            return
        if (
            self.simulation_thread is not None
            or self.processing_thread is not None
            or self.data_migration_thread is not None
        ):
            return

        self._ensure_user_dirs()
        self.simulation_cancel_requested = False
        self.window.begin_simulation_task()
        self.simulation_thread = QThread(self.window)
        self.simulation_worker = SimulationWorker(
            self.log_dir,
            int(time.time()) & 0xFFFFFFFF,
        )
        self.simulation_worker.moveToThread(self.simulation_thread)
        self.simulation_thread.started.connect(self.simulation_worker.run)
        self.simulation_worker.progress.connect(
            self._on_simulation_progress,
            Qt.QueuedConnection,
        )
        self.simulation_worker.finished.connect(
            self._on_simulation_finished,
            Qt.QueuedConnection,
        )
        self.simulation_worker.cancelled.connect(
            self._on_simulation_cancelled,
            Qt.QueuedConnection,
        )
        self.simulation_worker.failed.connect(
            self._on_simulation_failed,
            Qt.QueuedConnection,
        )
        self.simulation_thread.finished.connect(self._on_simulation_thread_finished)
        self.simulation_thread.start()

    def _on_simulation_progress(
        self,
        done: int,
        total: int,
        status_key: str,
    ) -> None:
        if self.simulation_cancel_requested:
            return
        self.window.update_simulation_progress(done, total, status_key)

    def cancel_sim_validation_data(self) -> None:
        if self.simulation_worker is None:
            return
        self.simulation_cancel_requested = True
        self.simulation_worker.request_cancel()
        self.window.mark_simulation_cancelling()

    def _remove_simulation_output(self, output_path: Path | str) -> None:
        root = self.log_dir.resolve()
        target = Path(output_path).resolve()
        if (
            target.parent != root
            or not target.name.startswith("sim_validation_")
            or target.suffix.lower() != ".jsonl"
        ):
            raise RuntimeError(f"Refusing to remove unexpected simulation output: {target}")
        if target.exists():
            target.unlink()

    def _request_simulation_thread_stop(self) -> None:
        if self.simulation_worker is not None:
            self.simulation_worker.deleteLater()
        if self.simulation_thread is not None and self.simulation_thread.isRunning():
            self.simulation_thread.quit()

    def _on_simulation_finished(self, output_path: str) -> None:
        if self.simulation_cancel_requested:
            try:
                self._remove_simulation_output(output_path)
            except Exception as exc:
                self.window.finish_simulation_task(
                    "task.simulation.failed",
                    completed=False,
                    error=exc,
                )
            else:
                self.window.finish_simulation_task(
                    "task.simulation.cancelled",
                    completed=False,
                )
        else:
            self.window.finish_simulation_task(
                "task.simulation.completed",
                completed=True,
                path=output_path,
            )
        self.simulation_cancel_requested = False
        self._request_simulation_thread_stop()

    def _on_simulation_cancelled(self) -> None:
        self.window.finish_simulation_task(
            "task.simulation.cancelled",
            completed=False,
        )
        self.simulation_cancel_requested = False
        self._request_simulation_thread_stop()

    def _on_simulation_failed(self, error: str) -> None:
        self.window.finish_simulation_task(
            "task.simulation.failed",
            completed=False,
            error=error,
        )
        self.simulation_cancel_requested = False
        self._request_simulation_thread_stop()

    def _on_simulation_thread_finished(self) -> None:
        self.window.set_data_tools_busy(False)
        self.simulation_worker = None
        if self.simulation_thread is not None:
            self.simulation_thread.deleteLater()
        self.simulation_thread = None

    def choose_and_process_data(self) -> None:
        if self.data_migration_thread is not None:
            return
        if self.state.mission_started:
            QMessageBox.information(
                self.window,
                self._tr("app.title"),
                self._tr("message.mission_processing_disabled"),
            )
            return
        self._ensure_user_dirs()
        log_path_text, _ = QFileDialog.getOpenFileName(
            self.window,
            self._tr("message.choose_log"),
            str(self.log_dir),
            self._tr("message.log_filter"),
        )
        if not log_path_text:
            return
        log_path = Path(log_path_text)
        if log_path.suffix.lower() != ".jsonl" or not log_path.is_file() or log_path.stat().st_size <= 0:
            QMessageBox.warning(
                self.window, self._tr("app.title"), self._tr("message.invalid_log")
            )
            return
        export_options = self.window.request_export_options()
        if export_options is None:
            return
        if self._is_log_already_processed(
            log_path,
            export_options.language_suffix,
        ):
            QMessageBox.information(
                self.window, self._tr("app.title"), self._tr("message.already_processed")
            )
            return
        self._start_processing(log_path, export_options)

    def _is_log_already_processed(
        self,
        log_path: Path,
        language_suffix: str,
    ) -> bool:
        data_dir = self.data_dir
        if not data_dir.exists():
            return False
        try:
            selected = log_path.resolve()
        except OSError:
            selected = log_path
        for manifest_path in data_dir.glob(
            f"*/manifest_{language_suffix.upper()}.json"
        ):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                source = Path(str(manifest.get("source_log", "")))
                if source and (source.resolve() == selected or source.name == log_path.name):
                    return True
            except Exception:
                continue
        return False

    def _start_processing(
        self,
        log_path: Path,
        export_options: ResolvedExportOptions,
    ) -> None:
        if (
            self.processing_thread is not None
            or self.simulation_thread is not None
            or self.data_migration_thread is not None
        ):
            QMessageBox.information(
                self.window, self._tr("app.title"), self._tr("message.processing_running")
            )
            return

        self.processing_cancel_requested = False
        self.window.begin_processing_task()
        self.processing_thread = QThread(self.window)
        self.processing_worker = ProcessingWorker(
            log_path,
            self.data_dir,
            export_options,
        )
        self.processing_worker.moveToThread(self.processing_thread)
        self.processing_thread.started.connect(self.processing_worker.run)
        self.processing_worker.progress.connect(self._on_processing_progress, Qt.QueuedConnection)
        self.processing_worker.finished.connect(self._on_processing_finished, Qt.QueuedConnection)
        self.processing_worker.cancelled.connect(
            self._on_processing_cancelled,
            Qt.QueuedConnection,
        )
        self.processing_worker.failed.connect(self._on_processing_failed, Qt.QueuedConnection)
        self.processing_thread.finished.connect(self._on_processing_thread_finished)
        self.processing_thread.start()

    def _on_processing_progress(self, done: int, total: int, message: str) -> None:
        if self.processing_cancel_requested:
            return
        self.window.update_processing_progress(
            done,
            total,
            "task.processing.running",
            detail=message,
        )

    def cancel_processing(self) -> None:
        if self.processing_worker is None:
            return
        self.processing_cancel_requested = True
        self.processing_worker.request_cancel()
        self.window.mark_processing_cancelling()

    def _request_processing_thread_stop(self) -> None:
        if self.processing_worker is not None:
            self.processing_worker.deleteLater()
        if self.processing_thread is not None and self.processing_thread.isRunning():
            self.processing_thread.quit()

    def _on_processing_finished(
        self,
        output_dir: str,
        export_errors: object,
    ) -> None:
        if self.processing_cancel_requested:
            try:
                from processing.flight_log_processor import FlightLogProcessor

                FlightLogProcessor(output_root=self.data_dir).remove_output_dir(output_dir)
            except Exception as exc:
                self.window.finish_processing_task(
                    "task.processing.failed",
                    completed=False,
                    error=exc,
                )
            else:
                self.window.finish_processing_task(
                    "task.processing.cancelled",
                    completed=False,
                )
            self.processing_cancel_requested = False
            self._request_processing_thread_stop()
            return

        errors = dict(export_errors) if isinstance(export_errors, dict) else {}
        if errors:
            details = "\n".join(
                f"- {name}: {error}" for name, error in errors.items()
            )
            self.window.finish_processing_task(
                "task.processing.partial",
                completed=True,
                path=output_dir,
                errors=details,
            )
        else:
            self.window.finish_processing_task(
                "task.processing.completed",
                completed=True,
                path=output_dir,
            )
        self.processing_cancel_requested = False
        self._request_processing_thread_stop()

    def _on_processing_cancelled(self) -> None:
        self.window.finish_processing_task(
            "task.processing.cancelled",
            completed=False,
        )
        self.processing_cancel_requested = False
        self._request_processing_thread_stop()

    def _on_processing_failed(self, error: str) -> None:
        self.window.finish_processing_task(
            "task.processing.failed",
            completed=False,
            error=error,
        )
        self.processing_cancel_requested = False
        self._request_processing_thread_stop()

    def _on_processing_thread_finished(self) -> None:
        self.window.set_data_tools_busy(False)
        self.processing_worker = None
        if self.processing_thread is not None:
            self.processing_thread.deleteLater()
        self.processing_thread = None


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setApplicationName(APP_EN_NAME)
    app.setApplicationDisplayName(APP_WINDOW_TITLE)
    app.setApplicationVersion(APP_VERSION)
    window = MainWindow()
    controller = Controller(window)
    window.show()
    return_code = app.exec()
    controller.shutdown()
    return return_code


__all__ = [
    "Controller",
    "DataMigrationWorker",
    "PendingAirCommand",
    "PendingCapabilityAck",
    "ProcessingWorker",
    "SimulationWorker",
    "format_air_status_message",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
