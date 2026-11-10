from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from modules.cleanup_manager import CleanupManager, detect_iis_cleanup_tasks, detect_service_cleanup_tasks, normalize_extensions, normalize_folder_names
from modules.cleanup_detection_worker import CleanupDetectionWorker
from modules.service_backup import ServiceInfo
from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


class CleanupManagerTests(unittest.TestCase):
    def test_normalize_extensions(self) -> None:
        self.assertEqual(normalize_extensions("json, .LOG;txt, json"), [".json", ".log", ".txt"])
        with self.assertRaises(ValueError):
            normalize_extensions("*")

    def test_detects_log_folders_only_for_selected_iis_apps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            selected_path = root / "Selected"
            ignored_path = root / "Ignored"
            (selected_path / "logs").mkdir(parents=True)
            (selected_path / "nested" / "Exceptions").mkdir(parents=True)
            (ignored_path / "logs").mkdir(parents=True)
            applications = [
                {"site": "Site", "path": "/Selected", "physical_path": str(selected_path)},
                {"site": "Site", "path": "/Ignored", "physical_path": str(ignored_path)},
            ]

            tasks = detect_iis_cleanup_tasks(applications, {"Site|/Selected"})

            self.assertEqual({Path(task["path"]).name for task in tasks}, {"logs", "Exceptions"})
            self.assertTrue(all(task["source"] == "IIS Auto Detect" for task in tasks))

    def test_detects_custom_cleanup_folder_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Archive").mkdir()
            (root / "logs").mkdir()
            applications = [{"site": "Site", "path": "/App", "physical_path": str(root)}]

            tasks = detect_iis_cleanup_tasks(applications, {"Site|/App"}, "Archive")

            self.assertEqual([Path(task["path"]).name for task in tasks], ["Archive"])
            self.assertEqual(normalize_folder_names(" Exceptions, logs; Archive "), {"exceptions", "logs", "archive"})

    def test_cleanup_detection_worker_returns_detected_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "logs").mkdir()
            worker = CleanupDetectionWorker("iis", {"Site|/App"}, "logs")
            worker_results: list[list[dict]] = []
            worker.finished.connect(worker_results.append)
            with patch("modules.cleanup_detection_worker.IISManager") as iis_manager:
                iis_manager.return_value.list_applications.return_value = [
                    {"site": "Site", "path": "/App", "physical_path": str(root)}
                ]
                worker.run()

            self.assertEqual(len(worker_results), 1)
            self.assertEqual(Path(worker_results[0][0]["path"]).name, "logs")

    def test_detects_cleanup_folders_from_selected_service_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Exception").mkdir()
            executable = root / "service.exe"
            executable.write_text("", encoding="utf-8")
            services = [ServiceInfo("Svc", "Service", str(executable), "Running")]

            tasks = detect_service_cleanup_tasks(services, {"Svc"}, "Exception")

            self.assertEqual(len(tasks), 1)
            self.assertEqual(Path(tasks[0]["path"]).name, "Exception")
            self.assertEqual(tasks[0]["source"], "Service Auto Detect")

    def test_cleanup_deletes_matching_files_and_preserves_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested"
            nested.mkdir()
            (root / "app.log").write_text("log", encoding="utf-8")
            (nested / "error.JSON").write_text("json", encoding="utf-8")
            (root / "keep.csv").write_text("csv", encoding="utf-8")

            results = CleanupManager().cleanup_tasks([{
                "name": "Logs",
                "path": str(root),
                "extensions": [".log", ".json"],
                "enabled": True,
            }])

            self.assertEqual(results[0]["deleted_files"], 2)
            self.assertFalse((root / "app.log").exists())
            self.assertFalse((nested / "error.JSON").exists())
            self.assertTrue((root / "keep.csv").exists())
            self.assertTrue(nested.exists())

    def test_cleanup_with_empty_extensions_deletes_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested"
            nested.mkdir()
            (root / "app.log").write_text("log", encoding="utf-8")
            (nested / "keep.csv").write_text("csv", encoding="utf-8")
            (nested / "README").write_text("text", encoding="utf-8")

            results = CleanupManager().cleanup_tasks([{
                "name": "All files",
                "path": str(root),
                "extensions": [],
                "enabled": True,
            }])

            self.assertEqual(results[0]["deleted_files"], 3)
            self.assertEqual(list(root.rglob("*")), [nested])

    def test_cleanup_skips_active_windows_files_without_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            active_log = Path(temp_dir) / "active.log"
            active_log.write_text("still in use", encoding="utf-8")
            sharing_violation = OSError(13, "The process cannot access the file", str(active_log), 32)

            with patch.object(Path, "unlink", side_effect=sharing_violation):
                result = CleanupManager().cleanup_tasks([{
                    "name": "Logs", "path": temp_dir, "extensions": [".log"], "enabled": True,
                }])[0]

            self.assertEqual(result["status"], "Completed")
            self.assertEqual(result["in_use_files"], 1)
            self.assertEqual(result["failed_files"], 0)
            self.assertIn("1 active files skipped", result["detail"])

    def test_runner_executes_cleanup_before_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data = deepcopy(DEFAULT_CONFIG)
            data["general"]["backup_root_folder"] = temp_dir
            data["folder_backup"]["folders"] = [
                {"name": "Test", "path": temp_dir, "enabled": True}
            ]
            runner = ChecklistRunner(ConfigManager(data=data))
            calls: list[str] = []
            runner.prepare_sql_backup = lambda: calls.append("prepare")
            runner.cleanup_logs = lambda *args: calls.append("cleanup")
            runner.backup_iis = lambda *args: calls.append("iis")
            runner.backup_services = lambda *args: calls.append("services")
            runner.backup_sql = lambda *args: calls.append("sql")
            runner.backup_folders = lambda *args: calls.append("folders")
            runner._compute_directory_stats = lambda *args: (1024, 1)
            runner.generate_report = lambda *args: calls.append("report")

            report = runner.run()

            self.assertEqual(report["status"], "Completed")
            self.assertEqual(calls, ["prepare", "cleanup", "iis", "services", "sql", "folders", "report"])

    def test_runner_cleanup_only_does_not_run_backups(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["cleanup"] = {"enabled": True, "tasks": [{"enabled": True}]}
        runner = ChecklistRunner(ConfigManager(data=data))
        calls: list[str] = []
        runner.cleanup_logs = lambda *args: calls.append("cleanup")
        runner.backup_iis = lambda *args: calls.append("iis")

        report = runner.run_cleanup_only()

        self.assertEqual(report["status"], "Completed")
        self.assertEqual(calls, ["cleanup"])


if __name__ == "__main__":
    unittest.main()
