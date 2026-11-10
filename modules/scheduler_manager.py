from __future__ import annotations

import shutil
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from modules.checklist_runner import ChecklistRunner
from core.scheduled_jobs import ScheduledJobStore
from core.job_queue import PersistentJobQueue
from utils.config_manager import ConfigManager
from utils.history_manager import HistoryManager
from utils.logger import get_logger
from utils.paths import get_app_dir
from utils.subprocess_utils import hidden_window_kwargs


AUTOMATION_TASK = "MiChecklist - Automated Run"
BACKGROUND_TASK = "MiChecklist - Background"
STARTUP_TASK = "MiChecklist - Logon Schedule Check"
TEST_RESTART_TASK = "MiChecklist - Restart Test"
TRAY_TASK = "MiChecklist - Tray"


def get_launch_command(mode: str) -> tuple[str, str]:
    """Return executable and arguments for source and packaged runs."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), mode
    return sys.executable, f'"{Path(__file__).resolve().parent.parent / "app.py"}" {mode}'


def get_launch_working_directory() -> str:
    """Return the folder Task Scheduler should run the checklist from."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parent.parent)


def quote_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class SchedulerManager:
    """Register the elevated unattended checklist task."""

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager

    def build_registration_script(self) -> str:
        schedule = self.config_manager.get_section("schedule")
        mode = str(schedule.get("mode", "weekly"))
        executable, scheduled_args = get_launch_command("--scheduled-run")
        _, test_args = get_launch_command("--scheduled-test-run")
        working_directory = get_launch_working_directory()
        start_time = str(schedule.get("start_time", "02:00"))
        weekday = str(schedule.get("weekday", "Sunday"))
        settings_options = [
            "-DontStopIfGoingOnBatteries",
            "-AllowStartIfOnBatteries",
            "-MultipleInstances IgnoreNew",
            "-ExecutionTimeLimit (New-TimeSpan -Hours 12)",
            "-RestartCount 2",
            "-RestartInterval (New-TimeSpan -Minutes 5)",
        ]
        if schedule.get("run_missed_schedule", True):
            settings_options.insert(0, "-StartWhenAvailable")
        lines = [
            "$ErrorActionPreference = 'Stop'",
            f"$action = New-ScheduledTaskAction -Execute {quote_ps(executable)} -Argument {quote_ps(scheduled_args)} -WorkingDirectory {quote_ps(working_directory)}",
            f"Unregister-ScheduledTask -TaskName {quote_ps(AUTOMATION_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"Unregister-ScheduledTask -TaskName {quote_ps(BACKGROUND_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"Unregister-ScheduledTask -TaskName {quote_ps(STARTUP_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"Unregister-ScheduledTask -TaskName {quote_ps(TRAY_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"$settings = New-ScheduledTaskSettingsSet {' '.join(settings_options)}",
            "$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest",
        ]
        if mode in {"daily", "days_after_last_backup"}:
            lines.append(f"$trigger = New-ScheduledTaskTrigger -Daily -At {quote_ps(start_time)}")
        elif mode == "after_restart":
            lines.append("$trigger = New-ScheduledTaskTrigger -AtStartup; $trigger.Delay = 'PT2M'")
        else:
            lines.append(f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {quote_ps(weekday)} -At {quote_ps(start_time)}")
        lines.extend([
            f"Register-ScheduledTask -TaskName {quote_ps(AUTOMATION_TASK)} -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null",
            f"$registeredAutomation = Get-ScheduledTask -TaskName {quote_ps(AUTOMATION_TASK)} -ErrorAction Stop",
            "if (-not $registeredAutomation) { throw 'Automated scheduled task was not created.' }",
        ])
        if schedule.get("test_run_after_restart", False):
            lines.extend([
                f"$testAction = New-ScheduledTaskAction -Execute {quote_ps(executable)} -Argument {quote_ps(test_args)} -WorkingDirectory {quote_ps(working_directory)}",
                "$testTrigger = New-ScheduledTaskTrigger -AtLogOn",
                f"Register-ScheduledTask -TaskName {quote_ps(TEST_RESTART_TASK)} -Action $testAction -Trigger $testTrigger -Settings $settings -Principal $principal -Force | Out-Null",
                f"$registeredTest = Get-ScheduledTask -TaskName {quote_ps(TEST_RESTART_TASK)} -ErrorAction Stop",
                "if (-not $registeredTest) { throw 'Restart test scheduled task was not created.' }",
            ])
        else:
            lines.append(f"Unregister-ScheduledTask -TaskName {quote_ps(TEST_RESTART_TASK)} -Confirm:$false -ErrorAction SilentlyContinue")
        return "; ".join(lines)

    def register_tasks(self) -> None:
        self._run_powershell(self.build_registration_script())

    def remove_tasks(self) -> None:
        script = "; ".join([
            "$ErrorActionPreference = 'Stop'",
            f"Unregister-ScheduledTask -TaskName {quote_ps(AUTOMATION_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"Unregister-ScheduledTask -TaskName {quote_ps(BACKGROUND_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"Unregister-ScheduledTask -TaskName {quote_ps(STARTUP_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"Unregister-ScheduledTask -TaskName {quote_ps(TEST_RESTART_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
            f"Unregister-ScheduledTask -TaskName {quote_ps(TRAY_TASK)} -Confirm:$false -ErrorAction SilentlyContinue",
        ])
        self._run_powershell(script)

    def get_task_status(self) -> dict[str, str]:
        """Return the installed automation task state and timing information."""
        script = "; ".join([
            "$ErrorActionPreference = 'Stop'",
            f"$task = Get-ScheduledTask -TaskName {quote_ps(AUTOMATION_TASK)} -ErrorAction SilentlyContinue",
            "if (-not $task) { '{\"installed\":false}' } else {",
            f"$info = Get-ScheduledTaskInfo -TaskName {quote_ps(AUTOMATION_TASK)}",
            "[pscustomobject]@{ installed=$true; state=[string]$task.State; last_run=[string]$info.LastRunTime; next_run=[string]$info.NextRunTime; last_result=[string]$info.LastTaskResult } | ConvertTo-Json -Compress",
            "}",
        ])
        result = self._run_powershell(script)
        try:
            data = json.loads((result.stdout or "").strip())
        except (json.JSONDecodeError, AttributeError) as exc:
            raise RuntimeError("Unable to parse the scheduled task status.") from exc
        return {key: str(value) for key, value in data.items()}

    def run_task_now(self) -> None:
        """Start the installed full-checklist task on demand."""
        script = "; ".join([
            "$ErrorActionPreference = 'Stop'",
            f"$task = Get-ScheduledTask -TaskName {quote_ps(AUTOMATION_TASK)} -ErrorAction SilentlyContinue",
            "if (-not $task) { throw 'The automated checklist task is not installed.' }",
            f"Start-ScheduledTask -TaskName {quote_ps(AUTOMATION_TASK)}",
        ])
        self._run_powershell(script)

    def _run_powershell(self, script: str) -> subprocess.CompletedProcess[str]:
        wrapped_script = (
            "try { " + script + " } catch { "
            "[Console]::Error.WriteLine($_.Exception.Message); "
            "[Console]::Error.WriteLine($_.InvocationInfo.PositionMessage); exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", wrapped_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_window_kwargs(),
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            message = f"Task Scheduler update failed with exit code {result.returncode}."
            if details:
                message = f"{message}\n{details}"
            raise RuntimeError(message)
        return result


def apply_task_scheduler_best_effort(config_manager: ConfigManager) -> str:
    """Apply the optional Windows task and return a non-fatal warning on failure.

    The MiChecklist service independently evaluates due schedules on every poll,
    so a locked-down Task Scheduler must not prevent service activation.
    """
    scheduler = SchedulerManager(config_manager)
    try:
        if config_manager.get("schedule", "enabled", False):
            scheduler.register_tasks()
        else:
            scheduler.remove_tasks()
    except RuntimeError as exc:
        return (
            "The optional Windows scheduled task could not be updated. "
            "MiChecklistService will still run the configured schedule.\n\n"
            f"Task Scheduler details:\n{exc}"
        )
    return ""


def _parse_start_time(value: Any) -> time:
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except ValueError:
        return time(2, 0)


def _load_last_successful_run(backup_root: Path | None = None) -> datetime | None:
    history = HistoryManager(get_app_dir()).load_history()
    candidates: list[datetime] = []
    for item in history:
        if item.get("status") != "Completed":
            continue
        try:
            candidates.append(
                datetime.fromisoformat(str(item.get("date", ""))).replace(microsecond=0)
            )
        except ValueError:
            continue
    if backup_root and backup_root.is_dir():
        for folder in backup_root.glob("Automatedbackup_*"):
            if not folder.is_dir():
                continue
            try:
                candidates.append(datetime.strptime(folder.name, "Automatedbackup_%Y%m%d_%H%M%S"))
            except ValueError:
                try:
                    candidates.append(datetime.fromtimestamp(folder.stat().st_mtime).replace(microsecond=0))
                except OSError:
                    continue
    return max(candidates) if candidates else None


def _last_weekly_occurrence(now: datetime, weekday: str, start_at: time) -> datetime:
    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    target_weekday = weekday_map.get(str(weekday).lower(), 6)
    days_since = (now.weekday() - target_weekday) % 7
    occurrence = datetime.combine(now.date() - timedelta(days=days_since), start_at)
    if occurrence > now:
        occurrence -= timedelta(days=7)
    return occurrence


def scheduled_run_is_due(config_manager: ConfigManager, now: datetime | None = None) -> bool:
    """Let the tool decide whether a scheduler wake should execute the checklist."""
    current_time = (now or datetime.now()).replace(microsecond=0)
    schedule = config_manager.get_section("schedule")
    start_at = _parse_start_time(schedule.get("start_time", "02:00"))
    backup_root_value = str(config_manager.get("general", "backup_root_folder", "")).strip()
    backup_root = Path(backup_root_value).expanduser() if backup_root_value else None
    last_successful = _load_last_successful_run(backup_root)
    mode = str(schedule.get("mode", "weekly"))

    if mode == "weekly":
        last_due_time = _last_weekly_occurrence(current_time, str(schedule.get("weekday", "Sunday")), start_at)
        if last_successful is None:
            if current_time.weekday() == last_due_time.weekday() and current_time.time() < start_at:
                return False
            return current_time >= last_due_time
        return last_successful < last_due_time

    if mode == "daily":
        due_time = datetime.combine(current_time.date(), start_at)
        if last_successful is None:
            return current_time >= due_time
        if current_time < due_time:
            due_time -= timedelta(days=1)
        return last_successful < due_time

    if mode == "after_restart":
        return True

    if not last_successful:
        first_due_time = datetime.combine(current_time.date(), start_at)
        return current_time >= first_due_time

    next_due_time = datetime.combine(last_successful.date(), start_at) + timedelta(days=int(schedule.get("days_interval", 7)))
    if next_due_time <= last_successful:
        next_due_time += timedelta(days=1)
    return current_time >= next_due_time


def delete_previous_automated_backups(config_manager: ConfigManager, keep_path: Path | None = None) -> int:
    """Remove old generations only after success, preserving the configured minimum."""
    if not config_manager.get("schedule", "delete_previous_automated_backups", False):
        return 0
    if keep_path is None or not keep_path.is_dir():
        return 0
    backup_root = Path(str(config_manager.get("general", "backup_root_folder", ""))).expanduser()
    if not backup_root.is_dir():
        return 0
    folders = sorted(
        (folder for folder in backup_root.glob("Automatedbackup_*") if folder.is_dir()),
        key=lambda folder: folder.stat().st_mtime,
        reverse=True,
    )
    minimum = max(1, int(config_manager.get("retention", "minimum_successful_backups", 1)))
    protected: set[Path] = {keep_path.resolve()}
    for folder in folders:
        if len(protected) >= minimum:
            break
        if not (folder / ".supportassistant_incomplete.json").exists():
            protected.add(folder.resolve())
    deleted = 0
    for folder in folders:
        if folder.resolve() not in protected and not (folder / ".supportassistant_incomplete.json").exists():
            shutil.rmtree(folder)
            deleted += 1
    return deleted


def config_for_default_schedule(
    config_manager: ConfigManager,
    schedule_store: ScheduledJobStore | None = None,
) -> ConfigManager:
    """Exclude items with enabled custom schedules from the default scheduled run."""
    data = deepcopy(config_manager.data)
    schedules = (schedule_store or ScheduledJobStore()).load()
    enabled = [item for item in schedules if item.get("enabled", True)]

    def normalized_path(value: object) -> str:
        return os.path.normcase(os.path.normpath(str(value or "")))

    custom_folders = {
        normalized_path(item.get("payload", {}).get("path"))
        for item in enabled if item.get("job_type") == "folder_backup"
    }
    folder_section = data.setdefault("folder_backup", {})
    folder_section["folders"] = [
        item for item in folder_section.get("folders", [])
        if normalized_path(item.get("path")) not in custom_folders
    ]

    custom_services = {
        str(item.get("payload", {}).get("name", "")).casefold()
        for item in enabled if item.get("job_type") == "service_backup"
    }
    service_section = data.setdefault("services", {})
    service_section["selected_services"] = [
        name for name in service_section.get("selected_services", [])
        if str(name).casefold() not in custom_services
    ]

    custom_sites = {
        str(item.get("payload", {}).get("name", "")).casefold()
        for item in enabled
        if item.get("job_type") == "iis_backup" and item.get("payload", {}).get("kind") == "site"
    }
    custom_apps = {
        str(item.get("payload", {}).get("key", "")).casefold()
        for item in enabled
        if item.get("job_type") == "iis_backup" and item.get("payload", {}).get("kind") == "application"
    }
    iis_section = data.setdefault("iis", {})
    iis_section["selected_sites"] = [
        name for name in iis_section.get("selected_sites", [])
        if str(name).casefold() not in custom_sites
    ]
    iis_section["selected_apps"] = [
        key for key in iis_section.get("selected_apps", [])
        if str(key).casefold() not in custom_apps
    ]

    custom_cleanup = {
        normalized_path(item.get("payload", {}).get("path"))
        for item in enabled if item.get("job_type") == "cleanup"
    }
    cleanup_section = data.setdefault("cleanup", {})
    cleanup_section["tasks"] = [
        item for item in cleanup_section.get("tasks", [])
        if normalized_path(item.get("path")) not in custom_cleanup
    ]

    if any(item.get("job_type") == "database_backup" for item in enabled):
        sql_section = data.setdefault("sql_server", {})
        sql_section["databases"] = []
        sql_section["database_name"] = ""

    return ConfigManager(config_path=config_manager.config_path, data=data)


def enqueue_default_checklist_if_due(
    config_manager: ConfigManager,
    queue: PersistentJobQueue,
    now: datetime | None = None,
    force_full: bool = False,
) -> str | None:
    """Queue one default checklist occurrence for the service, without duplicates."""
    if not config_manager.get("schedule", "enabled", False):
        return None
    current = (now or datetime.now()).replace(microsecond=0)
    history = HistoryManager(get_app_dir()).load_history()
    successful = [item for item in history if item.get("status") == "Completed"]
    backup_root_value = str(config_manager.get("general", "backup_root_folder", "")).strip()
    backup_root = Path(backup_root_value).expanduser() if backup_root_value else None
    recorded_backup_exists = any(
        item.get("backup_path") and Path(str(item["backup_path"])).exists()
        for item in successful
    )
    legacy_backup_exists = bool(
        backup_root and backup_root.is_dir()
        and next(backup_root.glob("Automatedbackup_*"), None) is not None
    )
    include_all = force_full or not successful or not (
        recorded_backup_exists or legacy_backup_exists
    )
    if include_all:
        occurrence_key = (
            f"forced:{current.isoformat(timespec='minutes')}"
            if force_full else "bootstrap:no-successful-backup"
        )
    else:
        if not scheduled_run_is_due(config_manager, current):
            return None
        schedule = config_manager.get_section("schedule")
        mode = str(schedule.get("mode", "weekly"))
        start_at = _parse_start_time(schedule.get("start_time", "02:00"))
        if mode == "weekly":
            occurrence = _last_weekly_occurrence(
                current, str(schedule.get("weekday", "Sunday")), start_at,
            )
        elif mode == "daily":
            occurrence = datetime.combine(current.date(), start_at)
            if occurrence > current:
                occurrence -= timedelta(days=1)
        elif mode == "days_after_last_backup":
            last_success = _load_last_successful_run(backup_root)
            occurrence = last_success or current
            if last_success:
                occurrence = last_success + timedelta(
                    days=max(1, int(schedule.get("days_interval", 7)))
                )
        else:
            occurrence = current
        occurrence_key = f"default:{mode}:{occurrence.isoformat(timespec='minutes')}"

    for job in queue.load():
        if (
            job.get("type") == "checklist"
            and job.get("payload", {}).get("default_schedule_key") == occurrence_key
        ):
            return None
    return queue.enqueue("checklist", {
        "schedule_kind": "default",
        "default_schedule_key": occurrence_key,
        "include_all": include_all,
        "scheduled_for": current.isoformat(timespec="minutes"),
    })


def run_scheduled_checklist(
    config_manager: ConfigManager,
    force_test_run: bool = False,
) -> dict[str, Any] | None:
    """Run the unattended checklist when its configured interval is due."""
    logger = get_logger("ScheduledChecklist")
    if not config_manager.get("schedule", "enabled", False):
        logger.info("Scheduled checklist skipped because scheduling is disabled.")
        return None
    if force_test_run:
        if not config_manager.get("schedule", "test_run_after_restart", False):
            logger.info("Scheduled restart test skipped because the test checkbox is disabled.")
            return None
        config_manager.set("schedule", "test_run_after_restart", False)
        try:
            config_manager.save_config()
        except Exception as exc:
            logger.warning("Unable to disable restart test flag before running: %s", exc)
        logger.info("Scheduled restart test is enabled; running checklist outside the normal due gate.")
    elif not scheduled_run_is_due(config_manager):
        logger.info("Scheduled checklist skipped because the configured schedule is not due.")
        return None
    scheduled_config = config_for_default_schedule(config_manager)
    result = ChecklistRunner(scheduled_config).run()
    deleted = 0
    if result.get("status") == "Completed":
        backup_path = Path(str(result.get("backup_path", ""))) if result.get("backup_path") else None
        deleted = delete_previous_automated_backups(config_manager, keep_path=backup_path)
        logger.info("Deleted %d previous automated backup folders after the successful scheduled run.", deleted)
    HistoryManager(get_app_dir()).add_record({
        "date": result.get("execution_date", ""),
        "duration": result.get("duration", ""),
        "status": result.get("status", ""),
        "backup_size": result.get("backup_size", "N/A"),
        "backup_path": result.get("backup_path", ""),
        "run_type": "scheduled-test" if force_test_run else "scheduled",
    })
    return result
