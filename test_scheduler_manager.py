from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from modules.scheduler_manager import SchedulerManager, apply_task_scheduler_best_effort, config_for_default_schedule, delete_previous_automated_backups, enqueue_default_checklist_if_due, run_scheduled_checklist, scheduled_run_is_due
from core.scheduled_jobs import ScheduledJobStore
from core.job_queue import PersistentJobQueue
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


class SchedulerManagerTests(unittest.TestCase):
    def test_task_scheduler_access_denied_is_non_fatal_for_service_schedule(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["enabled"] = True
        config = ConfigManager(data=data)

        with patch.object(
            SchedulerManager,
            "register_tasks",
            side_effect=RuntimeError("Task Scheduler update failed.\nAccess denied"),
        ):
            warning = apply_task_scheduler_best_effort(config)

        self.assertIn("MiChecklistService will still run", warning)
        self.assertIn("Access denied", warning)

    def test_service_queues_one_full_bootstrap_when_no_successful_backup_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data = deepcopy(DEFAULT_CONFIG)
            data["schedule"]["enabled"] = True
            manager = ConfigManager(data=data)
            queue = PersistentJobQueue(Path(temp_dir) / "jobs.json")
            with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=[]):
                job_id = enqueue_default_checklist_if_due(manager, queue)
                duplicate = enqueue_default_checklist_if_due(manager, queue)

            self.assertIsNotNone(job_id)
            self.assertIsNone(duplicate)
            job = queue.load()[0]
            self.assertTrue(job["payload"]["include_all"])
            self.assertEqual(job["payload"]["schedule_kind"], "default")

    def test_service_bootstraps_when_history_points_to_a_missing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data = deepcopy(DEFAULT_CONFIG)
            data["schedule"]["enabled"] = True
            data["general"]["backup_root_folder"] = str(Path(temp_dir) / "backups")
            manager = ConfigManager(data=data)
            queue = PersistentJobQueue(Path(temp_dir) / "jobs.json")
            history = [{
                "status": "Completed", "date": "2026-09-27T02:00:00",
                "backup_path": str(Path(temp_dir) / "missing-backup"),
            }]

            with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=history):
                job_id = enqueue_default_checklist_if_due(
                    manager, queue, datetime(2026, 9, 28, 1, 0),
                )

            self.assertIsNotNone(job_id)
            self.assertTrue(queue.load()[0]["payload"]["include_all"])

    def test_custom_item_schedules_are_excluded_from_default_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data = deepcopy(DEFAULT_CONFIG)
            data["folder_backup"]["folders"] = [
                {"name": "Default", "path": "C:/default", "enabled": True},
                {"name": "Monthly", "path": "C:/monthly", "enabled": True},
            ]
            data["services"]["selected_services"] = ["DefaultSvc", "MonthlySvc"]
            data["iis"]["selected_sites"] = ["Default Site", "Monthly Site"]
            data["iis"]["selected_apps"] = ["Default Site|/Default", "Monthly Site|/Monthly"]
            manager = ConfigManager(data=data)
            store = ScheduledJobStore(Path(temp_dir) / "schedules.json")
            store.add("Monthly folder", "folder_backup", {"path": "C:/monthly"}, {"frequency": "monthly"})
            store.add("Monthly service", "service_backup", {"name": "MonthlySvc"}, {"frequency": "monthly"})
            store.add("Monthly site", "iis_backup", {"name": "Monthly Site", "kind": "site"}, {"frequency": "monthly"})
            store.add("Monthly app", "iis_backup", {"key": "Monthly Site|/Monthly", "kind": "application"}, {"frequency": "monthly"})

            scheduled = config_for_default_schedule(manager, store)

            self.assertEqual([item["name"] for item in scheduled.get("folder_backup", "folders")], ["Default"])
            self.assertEqual(scheduled.get("services", "selected_services"), ["DefaultSvc"])
            self.assertEqual(scheduled.get("iis", "selected_sites"), ["Default Site"])
            self.assertEqual(scheduled.get("iis", "selected_apps"), ["Default Site|/Default"])

    def test_weekly_registration_script_uses_weekly_trigger(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "weekly"
        data["schedule"]["weekday"] = "Sunday"
        data["schedule"]["start_time"] = "02:00"
        manager = SchedulerManager(ConfigManager(data=data))

        script = manager.build_registration_script()

        self.assertIn("--scheduled-run", script)
        self.assertIn("-UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest", script)
        self.assertIn("-WorkingDirectory", script)
        self.assertIn("-DontStopIfGoingOnBatteries -AllowStartIfOnBatteries", script)
        self.assertIn("MiChecklist - Automated Run", script)
        self.assertIn("New-ScheduledTaskTrigger -Weekly -DaysOfWeek 'Sunday' -At '02:00'", script)
        self.assertNotIn("--startup-run", script)
        self.assertIn("Unregister-ScheduledTask -TaskName 'MiChecklist - Automated Run'", script)
        self.assertIn("Unregister-ScheduledTask -TaskName 'MiChecklist - Background'", script)
        self.assertIn("Unregister-ScheduledTask -TaskName 'MiChecklist - Logon Schedule Check'", script)
        self.assertIn("Unregister-ScheduledTask -TaskName 'MiChecklist - Tray'", script)
        self.assertIn("Unregister-ScheduledTask -TaskName 'MiChecklist - Restart Test'", script)
        self.assertNotIn("Register-ScheduledTask -TaskName 'MiChecklist - Background'", script)
        self.assertNotIn("New-ScheduledTaskTrigger -Daily", script)
        self.assertNotIn("New-ScheduledTaskTrigger -AtStartup", script)
        self.assertNotIn("New-ScheduledTaskTrigger -AtLogOn", script)
        self.assertNotIn("--tray", script)
        self.assertNotIn("-LogonType Interactive", script)

    def test_daily_registration_script_uses_daily_trigger(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "daily"
        data["schedule"]["start_time"] = "03:30"
        manager = SchedulerManager(ConfigManager(data=data))

        script = manager.build_registration_script()

        self.assertIn("New-ScheduledTaskTrigger -Daily -At '03:30'", script)
        self.assertNotIn("New-ScheduledTaskTrigger -Weekly", script)
        self.assertNotIn("New-ScheduledTaskTrigger -AtLogOn", script)

    def test_after_restart_registration_script_uses_logon_trigger(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "after_restart"
        manager = SchedulerManager(ConfigManager(data=data))

        script = manager.build_registration_script()

        self.assertIn("New-ScheduledTaskTrigger -AtStartup", script)
        self.assertIn("$trigger.Delay = 'PT2M'", script)
        self.assertNotIn("New-ScheduledTaskTrigger -Daily", script)
        self.assertNotIn("New-ScheduledTaskTrigger -Weekly", script)

    def test_start_when_available_can_be_disabled(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["run_missed_schedule"] = False
        manager = SchedulerManager(ConfigManager(data=data))

        script = manager.build_registration_script()

        self.assertNotIn("-StartWhenAvailable", script)

    def test_every_n_days_uses_daily_wake_with_overlap_protection(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "days_after_last_backup"
        data["schedule"]["days_interval"] = 3
        data["schedule"]["start_time"] = "04:15"
        manager = SchedulerManager(ConfigManager(data=data))

        script = manager.build_registration_script()

        self.assertIn("New-ScheduledTaskTrigger -Daily -At '04:15'", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("-RestartCount 2", script)

    def test_registration_script_can_add_restart_test_task(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["test_run_after_restart"] = True
        manager = SchedulerManager(ConfigManager(data=data))

        script = manager.build_registration_script()

        self.assertIn("MiChecklist - Restart Test", script)
        self.assertIn("--scheduled-test-run", script)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", script)

    def test_latest_valid_backup_is_the_default_retention_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = root / "Automatedbackup_old"
            current = root / "Automatedbackup_current"
            old.mkdir()
            current.mkdir()
            (root / "KeepMe").mkdir()
            data = deepcopy(DEFAULT_CONFIG)
            data["general"]["backup_root_folder"] = str(root)
            manager = ConfigManager(data=data)

            self.assertEqual(delete_previous_automated_backups(manager, keep_path=current), 1)
            self.assertFalse(old.exists())
            self.assertTrue(current.exists())
            self.assertTrue((root / "KeepMe").exists())

    def test_latest_valid_backup_policy_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = root / "Automatedbackup_old"
            old.mkdir()
            data = deepcopy(DEFAULT_CONFIG)
            data["general"]["backup_root_folder"] = str(root)
            data["schedule"]["delete_previous_automated_backups"] = False

            self.assertEqual(delete_previous_automated_backups(ConfigManager(data=data)), 0)
            self.assertTrue(old.exists())

    def test_retention_requires_a_new_successful_backup_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = root / "Automatedbackup_old"
            old.mkdir()
            data = deepcopy(DEFAULT_CONFIG)
            data["general"]["backup_root_folder"] = str(root)

            self.assertEqual(delete_previous_automated_backups(ConfigManager(data=data)), 0)
            self.assertTrue(old.exists())

    def test_days_after_last_backup_due_gate(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "days_after_last_backup"
        data["schedule"]["days_interval"] = 3
        data["schedule"]["start_time"] = "02:00"
        history = [{"status": "Completed", "date": "2026-06-01T02:00:00"}]
        manager = ConfigManager(data=data)
        with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=history):
            self.assertFalse(scheduled_run_is_due(manager, datetime(2026, 6, 3, 2, 0, 0)))
            self.assertTrue(scheduled_run_is_due(manager, datetime(2026, 6, 4, 2, 0, 0)))

    def test_weekly_due_gate_is_checked_by_tool(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "weekly"
        data["schedule"]["weekday"] = "Sunday"
        data["schedule"]["start_time"] = "02:00"
        manager = ConfigManager(data=data)

        with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=[]):
            self.assertFalse(scheduled_run_is_due(manager, datetime(2026, 6, 7, 1, 59, 0)))
            self.assertTrue(scheduled_run_is_due(manager, datetime(2026, 6, 7, 2, 0, 0)))
            self.assertTrue(scheduled_run_is_due(manager, datetime(2026, 6, 8, 2, 0, 0)))

        history = [{"status": "Completed", "date": "2026-06-07T02:05:00"}]
        with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=history):
            self.assertFalse(scheduled_run_is_due(manager, datetime(2026, 6, 8, 2, 0, 0)))
            self.assertTrue(scheduled_run_is_due(manager, datetime(2026, 6, 14, 2, 0, 0)))

    def test_daily_due_gate_is_checked_by_tool(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "daily"
        data["schedule"]["start_time"] = "02:00"
        manager = ConfigManager(data=data)

        with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=[]):
            self.assertFalse(scheduled_run_is_due(manager, datetime(2026, 6, 3, 1, 59, 0)))
            self.assertTrue(scheduled_run_is_due(manager, datetime(2026, 6, 3, 2, 0, 0)))

        history = [{"status": "Completed", "date": "2026-06-03T02:05:00"}]
        with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=history):
            self.assertFalse(scheduled_run_is_due(manager, datetime(2026, 6, 3, 3, 0, 0)))
            self.assertTrue(scheduled_run_is_due(manager, datetime(2026, 6, 4, 2, 0, 0)))

    def test_actual_backup_folder_prevents_duplicate_when_history_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Automatedbackup_20260927_020001").mkdir()
            data = deepcopy(DEFAULT_CONFIG)
            data["general"]["backup_root_folder"] = str(root)
            data["schedule"].update({
                "enabled": True, "mode": "weekly", "weekday": "Sunday", "start_time": "02:00",
            })
            manager = ConfigManager(data=data)
            stale_history = [{"status": "Completed", "date": "2026-06-01T02:00:00"}]

            with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=stale_history):
                self.assertFalse(scheduled_run_is_due(manager, datetime(2026, 9, 28, 10, 0)))

    def test_after_restart_due_gate_runs_when_woken(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["mode"] = "after_restart"
        manager = ConfigManager(data=data)

        with patch("modules.scheduler_manager.HistoryManager.load_history", return_value=[]):
            self.assertTrue(scheduled_run_is_due(manager, datetime(2026, 6, 3, 1, 0, 0)))

    def test_restart_test_run_bypasses_due_gate_once(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["enabled"] = True
        data["schedule"]["test_run_after_restart"] = True
        manager = ConfigManager(data=data)
        fake_result = {"execution_date": "2026-06-03T02:00:00", "duration": "1s", "status": "Completed"}

        with (
            patch("modules.scheduler_manager.ChecklistRunner") as runner_class,
            patch("modules.scheduler_manager.HistoryManager"),
        ):
            runner_class.return_value.run.return_value = fake_result
            manager.save_config = Mock()

            result = run_scheduled_checklist(manager, force_test_run=True)

        self.assertEqual(result, fake_result)
        self.assertFalse(manager.get("schedule", "test_run_after_restart"))
        manager.save_config.assert_called_once()

    def test_old_backups_are_deleted_only_after_success_and_current_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_backup = root / "Automatedbackup_old"
            current_backup = root / "Automatedbackup_current"
            old_backup.mkdir()
            current_backup.mkdir()
            data = deepcopy(DEFAULT_CONFIG)
            data["general"]["backup_root_folder"] = str(root)
            data["schedule"]["enabled"] = True
            data["schedule"]["delete_previous_automated_backups"] = True
            manager = ConfigManager(data=data)
            result_data = {
                "execution_date": "2026-06-03T02:00:00",
                "duration": "1s",
                "status": "Completed",
                "backup_path": str(current_backup),
            }

            with (
                patch("modules.scheduler_manager.scheduled_run_is_due", return_value=True),
                patch("modules.scheduler_manager.ChecklistRunner") as runner_class,
                patch("modules.scheduler_manager.HistoryManager"),
            ):
                runner_class.return_value.run.return_value = result_data
                run_scheduled_checklist(manager)

            self.assertFalse(old_backup.exists())
            self.assertTrue(current_backup.exists())

    def test_scheduled_run_skips_when_schedule_is_not_due(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)
        data["schedule"]["enabled"] = True
        data["schedule"]["mode"] = "weekly"
        data["schedule"]["weekday"] = "Sunday"
        data["schedule"]["start_time"] = "02:00"
        manager = ConfigManager(data=data)

        with (
            patch("modules.scheduler_manager.ChecklistRunner") as runner_class,
            patch("modules.scheduler_manager.scheduled_run_is_due", return_value=False),
        ):
            result = run_scheduled_checklist(manager)

        self.assertIsNone(result)
        runner_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
