from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config
from app import Controller, DataMigrationWorker
from services.data_migration import DataMigrationConflictPolicy
from services.logger import AsyncJsonlLogger
from ui.main_window import DataDirectorySelection


class DataPathTests(unittest.TestCase):
    def test_default_data_root_is_d_drive_data_directory(self) -> None:
        self.assertEqual(config.DEFAULT_USER_DATA_ROOT, Path("D:/SilverStar_GSHC_Data"))

    def test_save_user_data_root_persists_and_creates_subdirectories(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            config_dir = base_dir / "config"
            user_paths_file = config_dir / "user_paths.json"
            selected_root = base_dir / "selected"
            with (
                patch.object(config, "CONFIG_DIR", config_dir),
                patch.object(config, "USER_PATHS_FILE", user_paths_file),
            ):
                result = config.save_user_data_root(
                    selected_root,
                    migrate_existing_data=True,
                    previous_data_root=base_dir / "previous",
                    migration_conflict_policy="rename",
                )

            self.assertEqual(result, selected_root)
            self.assertTrue((selected_root / "logs").is_dir())
            self.assertTrue((selected_root / "data").is_dir())
            payload = json.loads(user_paths_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["data_root"], str(selected_root))
            self.assertEqual(payload["logs_dir"], str(selected_root / "logs"))
            self.assertEqual(payload["data_dir"], str(selected_root / "data"))
            self.assertTrue(payload["migration_requested"])
            self.assertEqual(payload["migration_conflict_policy"], "rename")
            self.assertEqual(
                payload["previous_data_root"],
                str(base_dir / "previous"),
            )

    def test_logger_switches_to_selected_directory_only_while_idle(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            logger = AsyncJsonlLogger(base_dir / "old", auto_start_session=False)
            try:
                selected_log_dir = base_dir / "selected" / "logs"
                logger.set_log_dir(selected_log_dir)
                session_path = logger.open_session("test")
                self.assertEqual(session_path.parent, selected_log_dir)
                with self.assertRaises(RuntimeError):
                    logger.set_log_dir(base_dir / "other")
                logger.close_session()
            finally:
                logger.close()

    def test_controller_uses_application_dialog_selection(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            selected_root = Path(temporary_directory) / "selected"
            logger = Mock()
            logger.session_active = False
            window = Mock()
            window.request_data_directory.return_value = DataDirectorySelection(
                data_root=selected_root,
                migrate_existing_data=False,
                conflict_policy=DataMigrationConflictPolicy.RENAME,
            )
            applied: list[
                tuple[
                    Path,
                    bool,
                    Path,
                    DataMigrationConflictPolicy,
                ]
            ] = []
            controller = SimpleNamespace(
                logger=logger,
                protocol_worker=None,
                simulation_thread=None,
                processing_thread=None,
                data_migration_thread=None,
                data_root=Path("D:/SilverStar_GSHC_Data"),
                log_dir=Path("D:/SilverStar_GSHC_Data/logs"),
                data_dir=Path("D:/SilverStar_GSHC_Data/data"),
                window=window,
                _tr=lambda key, **params: key,
                _data_roots_equal=Controller._data_roots_equal,
                _apply_data_root=lambda root, **kwargs: applied.append(
                    (
                        root,
                        kwargs["migrate_existing_data"],
                        kwargs["previous_data_root"],
                        kwargs["migration_conflict_policy"],
                    )
                ),
                _start_data_migration=Mock(),
            )
            Controller.choose_data_root(controller)

            window.request_data_directory.assert_called_once_with(
                controller.data_root
            )
            self.assertEqual(
                applied,
                [
                    (
                        selected_root,
                        False,
                        controller.data_root,
                        DataMigrationConflictPolicy.RENAME,
                    )
                ],
            )

    def test_data_migration_moves_logs_and_results_after_commit(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_log = source_root / "logs" / "session.jsonl"
            source_result = source_root / "data" / "flight" / "summary.txt"
            source_log.parent.mkdir(parents=True)
            source_result.parent.mkdir(parents=True)
            source_log.write_text("log-data", encoding="utf-8")
            source_result.write_text("result-data", encoding="utf-8")

            worker = DataMigrationWorker(source_root, target_root)
            completed: list[tuple[int, int, int, str]] = []
            failures: list[str] = []
            worker.ready_to_commit.connect(lambda: worker.finish_commit(True))
            worker.finished.connect(
                lambda files, size, skipped, warning: completed.append(
                    (files, int(size), skipped, warning)
                )
            )
            worker.failed.connect(failures.append)
            worker.run()

            self.assertFalse(failures)
            self.assertEqual(completed[0][0], 2)
            self.assertEqual(
                (target_root / "logs" / "session.jsonl").read_text(
                    encoding="utf-8"
                ),
                "log-data",
            )
            self.assertEqual(
                (target_root / "data" / "flight" / "summary.txt").read_text(
                    encoding="utf-8"
                ),
                "result-data",
            )
            self.assertFalse(source_log.exists())
            self.assertFalse(source_result.exists())
            self.assertEqual(completed[0][2], 0)

    def test_data_migration_overwrites_by_default_and_removes_source(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_file = source_root / "logs" / "session.jsonl"
            target_file = target_root / "logs" / "session.jsonl"
            source_file.parent.mkdir(parents=True)
            target_file.parent.mkdir(parents=True)
            source_file.write_text("old-data", encoding="utf-8")
            target_file.write_text("different-data", encoding="utf-8")

            worker = DataMigrationWorker(source_root, target_root)
            completed: list[tuple[int, int, int, str]] = []
            failures: list[str] = []
            worker.ready_to_commit.connect(lambda: worker.finish_commit(True))
            worker.finished.connect(
                lambda files, size, skipped, warning: completed.append(
                    (files, int(size), skipped, warning)
                )
            )
            worker.failed.connect(failures.append)
            worker.run()

            self.assertFalse(failures)
            self.assertFalse(source_file.exists())
            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "old-data",
            )
            self.assertEqual(completed[0][0], 1)
            self.assertEqual(completed[0][2], 0)
            self.assertFalse(list(target_root.rglob("*.backup")))
            self.assertFalse(list(target_root.rglob("*.migration")))

    def test_data_migration_fail_policy_preserves_both_files(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_file = source_root / "logs" / "session.jsonl"
            target_file = target_root / "logs" / "session.jsonl"
            source_file.parent.mkdir(parents=True)
            target_file.parent.mkdir(parents=True)
            source_file.write_text("old-data", encoding="utf-8")
            target_file.write_text("different-data", encoding="utf-8")

            worker = DataMigrationWorker(
                source_root,
                target_root,
                DataMigrationConflictPolicy.FAIL,
            )
            failures: list[str] = []
            worker.failed.connect(failures.append)
            worker.run()

            self.assertTrue(failures)
            self.assertTrue(source_file.exists())
            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "different-data",
            )

    def test_data_migration_rename_policy_increments_suffix(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_file = source_root / "logs" / "session.jsonl"
            target_file = target_root / "logs" / "session.jsonl"
            renamed_once = target_root / "logs" / "session (1).jsonl"
            source_file.parent.mkdir(parents=True)
            target_file.parent.mkdir(parents=True)
            source_file.write_text("source-data", encoding="utf-8")
            target_file.write_text("target-data", encoding="utf-8")
            renamed_once.write_text("already-renamed", encoding="utf-8")

            worker = DataMigrationWorker(
                source_root,
                target_root,
                DataMigrationConflictPolicy.RENAME,
            )
            completed: list[tuple[int, int, int, str]] = []
            worker.ready_to_commit.connect(lambda: worker.finish_commit(True))
            worker.finished.connect(
                lambda files, size, skipped, warning: completed.append(
                    (files, int(size), skipped, warning)
                )
            )
            worker.run()

            renamed_twice = target_root / "logs" / "session (2).jsonl"
            self.assertFalse(source_file.exists())
            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "target-data",
            )
            self.assertEqual(
                renamed_once.read_text(encoding="utf-8"),
                "already-renamed",
            )
            self.assertEqual(
                renamed_twice.read_text(encoding="utf-8"),
                "source-data",
            )
            self.assertEqual(completed[0][2], 0)

    def test_data_migration_skip_policy_leaves_conflict_in_old_directory(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_file = source_root / "logs" / "session.jsonl"
            movable_source = source_root / "logs" / "new-session.jsonl"
            target_file = target_root / "logs" / "session.jsonl"
            source_file.parent.mkdir(parents=True)
            target_file.parent.mkdir(parents=True)
            source_file.write_text("source-data", encoding="utf-8")
            movable_source.write_text("move-me", encoding="utf-8")
            target_file.write_text("target-data", encoding="utf-8")

            worker = DataMigrationWorker(
                source_root,
                target_root,
                DataMigrationConflictPolicy.SKIP,
            )
            completed: list[tuple[int, int, int, str]] = []
            worker.ready_to_commit.connect(lambda: worker.finish_commit(True))
            worker.finished.connect(
                lambda files, size, skipped, warning: completed.append(
                    (files, int(size), skipped, warning)
                )
            )
            worker.run()

            self.assertTrue(source_file.exists())
            self.assertFalse(movable_source.exists())
            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "target-data",
            )
            self.assertEqual(
                (target_root / "logs" / "new-session.jsonl").read_text(
                    encoding="utf-8"
                ),
                "move-me",
            )
            self.assertEqual(completed, [(1, 7, 1, "")])

    def test_data_migration_restores_overwritten_file_when_json_commit_fails(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_file = source_root / "logs" / "session.jsonl"
            target_file = target_root / "logs" / "session.jsonl"
            source_file.parent.mkdir(parents=True)
            target_file.parent.mkdir(parents=True)
            source_file.write_text("new-log-data", encoding="utf-8")
            target_file.write_text("previous-target-data", encoding="utf-8")

            worker = DataMigrationWorker(source_root, target_root)
            failures: list[str] = []
            worker.ready_to_commit.connect(
                lambda: worker.finish_commit(False, "JSON write failed")
            )
            worker.failed.connect(failures.append)
            worker.run()

            self.assertEqual(failures, ["JSON write failed"])
            self.assertTrue(source_file.exists())
            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "previous-target-data",
            )
            self.assertFalse(list(target_root.rglob("*.backup")))
            self.assertFalse(list(target_root.rglob("*.migration")))

    def test_cleanup_error_after_commit_keeps_new_directory_active(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_file = source_root / "logs" / "session.jsonl"
            target_file = target_root / "logs" / "session.jsonl"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("log-data", encoding="utf-8")

            worker = DataMigrationWorker(source_root, target_root)
            completed: list[tuple[int, int, int, str]] = []
            failures: list[str] = []
            worker.ready_to_commit.connect(lambda: worker.finish_commit(True))
            worker.finished.connect(
                lambda files, size, skipped, warning: completed.append(
                    (files, int(size), skipped, warning)
                )
            )
            worker.failed.connect(failures.append)
            with patch.object(
                worker,
                "_cleanup_source_files",
                side_effect=OSError("source is locked"),
            ):
                worker.run()

            self.assertFalse(failures)
            self.assertTrue(completed)
            self.assertIn("source is locked", completed[0][3])
            self.assertTrue(source_file.exists())
            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "log-data",
            )

    def test_data_migration_cancel_at_commit_keeps_previous_directory(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            source_root = base_dir / "source"
            target_root = base_dir / "target"
            source_file = source_root / "logs" / "session.jsonl"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("log-data", encoding="utf-8")

            worker = DataMigrationWorker(source_root, target_root)
            cancelled: list[bool] = []

            def cancel_before_commit() -> None:
                worker.request_cancel()
                worker.finish_commit(True)

            worker.ready_to_commit.connect(cancel_before_commit)
            worker.cancelled.connect(lambda: cancelled.append(True))
            worker.run()

            self.assertEqual(cancelled, [True])
            self.assertTrue(source_file.exists())
            self.assertFalse((target_root / "logs" / "session.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
