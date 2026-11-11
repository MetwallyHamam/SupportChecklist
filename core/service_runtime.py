from __future__ import annotations

import json
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from core.job_queue import PersistentJobQueue
from core.notifications import EmailNotifier
from core.retention import apply_retention
from core.scheduled_jobs import ScheduledJobStore
from core.script_runner import run_script
from core.external_copy import copy_verified_backup
from core.execution_lock import ChecklistExecutionBusy, checklist_execution_lock
from core.backup_lifecycle import (
    is_legacy_interrupted_scheduled_checklist,
    remove_incomplete_backups,
    remove_legacy_interrupted_backup,
)
from modules.checklist_runner import ChecklistRunner
from modules.folder_backup import FolderBackup
from modules.scheduler_manager import (
    delete_previous_automated_backups,
    enqueue_default_checklist_if_due,
)
from modules.cleanup_manager import CleanupManager
from utils.config_manager import ConfigManager
from utils.logger import get_logger
from utils.paths import get_app_dir
from utils.external_drive import ExternalDriveUnavailable, resolve_external_destination


def config_for_module(config: ConfigManager, module: str) -> ConfigManager:
    """Return a checklist configuration containing one backup module only."""
    data = deepcopy(config.data)
    data.setdefault("cleanup", {})["enabled"] = False
    if module != "iis":
        data.setdefault("iis", {})["backup_enabled"] = False
        data["iis"]["selected_sites"] = []
        data["iis"]["selected_apps"] = []
    if module != "services":
        data.setdefault("services", {})["backup_enabled"] = False
        data["services"]["selected_services"] = []
    if module != "database":
        data.setdefault("sql_server", {})["backup_enabled"] = False
        data["sql_server"]["databases"] = []
        data["sql_server"]["database_name"] = ""
    if module != "folders":
        data.setdefault("folder_backup", {})["folders"] = []
    return ConfigManager(config_path=config.config_path, data=data)


class ServiceRuntime:
    def __init__(self, stop_requested=lambda: False):
        self.stop_requested = stop_requested
        self.queue = PersistentJobQueue()
        self.logger = get_logger("MiChecklistService")
        self.config = ConfigManager()
        self.schedules = ScheduledJobStore()
        self.startup_marker = uuid4().hex
        self.heartbeat_path = get_app_dir() / "service" / "health.json"
        self.recovery_summary = "None"
        self._external_last_check = 0.0
        self._external_cached_destination: Path | None = None
        self._external_cached_error: ExternalDriveUnavailable | None = None

    @staticmethod
    def _uses_backup_destination(job: dict) -> bool:
        return job.get("type") == "external_backup_copy"

    def _should_notify_success(self, job: dict, result: dict) -> bool:
        if job.get("type") != "module_schedule":
            return True
        if self.config.get("email", "notify_module_success", False):
            return True
        return any(
            str(item.get("status", "")).casefold() in {
                "partial", "warning", "completed with warnings", "failed",
            }
            for item in result.get("tasks", []) or []
            if isinstance(item, dict)
        )

    def _external_settings(self) -> dict:
        return self.config.get_section("external_copy")

    def _activate_external_destination(self) -> Path | None:
        settings = self._external_settings()
        if not settings.get("enabled", False):
            return None
        return resolve_external_destination(settings)

    def _external_notice_due(self, job: dict) -> bool:
        last_value = str(job.get("last_external_notice_at", "")).strip()
        if not last_value:
            return True
        try:
            last_notice = datetime.fromisoformat(last_value)
        except ValueError:
            return True
        minutes = max(5, int(self._external_settings().get("reminder_interval_minutes", 120)))
        return datetime.now() - last_notice >= timedelta(minutes=minutes)

    def _notify_external_missing(self, job: dict, notifier: EmailNotifier, reason: str) -> None:
        if not self._external_notice_due(job):
            return
        label = str(self._external_settings().get("volume_label", "")).strip() or "configured external drive"
        notifier.send(
            "[Support Checklist] External backup copy pending",
            f"The checklist completed locally. MiChecklist is waiting to copy the verified "
            f"backup for job {job.get('id', '')}.\n\n"
            f"Please connect the external backup drive '{label}'.\n"
            f"Reason: {reason}\n\n"
            "The local backup is safe. MiChecklist will retry only the external copy automatically.",
        )
        self.queue.mark_external_notice(str(job.get("id", "")))

    def _notify_external_ready(self, job: dict, notifier: EmailNotifier, destination: Path) -> None:
        notifier.send(
            "[Support Checklist] External drive detected - copy starting",
            f"The configured external drive is available at {destination}.\n\n"
            f"MiChecklist is starting or resuming external copy job {job.get('id', '')}. Please do not "
            "disconnect the drive until the completion email confirms it is safe to remove.",
        )

    def write_heartbeat(self, state="idle", job_id="", error="", started_at="", elapsed_seconds=0):
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.heartbeat_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "service": "MiChecklistService",
            "state": state,
            "job_id": job_id,
            "last_error": error,
            "heartbeat": datetime.now().isoformat(timespec="seconds"),
            "last_startup_recovery": self.recovery_summary,
            "job_started_at": started_at,
            "job_elapsed_seconds": int(elapsed_seconds),
        }, indent=2), encoding="utf-8")
        temporary.replace(self.heartbeat_path)

    def execute(self, job: dict) -> dict:
        job_type = job.get("type")
        payload = job.get("payload", {})
        if job_type == "checklist":
            # The primary schedule always runs all configured modules. Specific
            # module schedules are additional occurrences, not overrides.
            runner = ChecklistRunner(self.config)
            if job.get("id"):
                result = runner.run(
                    resume_state=payload.get("resume", {}),
                    checkpoint_callback=lambda state: self.queue.update_payload(
                        str(job.get("id", "")), {"resume": state},
                    ),
                )
            else:
                result = runner.run()
            if result.get("status") not in {"Completed", "Completed with Warnings"}:
                raise RuntimeError(str(result.get("error", "Checklist failed")))
            return result
        if job_type == "module_schedule":
            module = str(payload.get("module", "")).strip().casefold()
            if module == "cleanup":
                result = ChecklistRunner(self.config).run_cleanup_only()
            elif module == "database":
                result = ChecklistRunner(self.config).run_database_only()
            elif module in {"iis", "services", "folders"}:
                result = ChecklistRunner(config_for_module(self.config, module)).run()
            else:
                raise ValueError(f"Unsupported scheduled module: {module or 'empty'}")
            if result.get("status") not in {"Completed", "Completed with Warnings"}:
                raise RuntimeError(str(result.get("error", f"{module.title()} module failed")))
            result["schedule_scope"] = module
            return result
        if job_type == "database_backup":
            result = ChecklistRunner(self.config).run_database_only()
            if result.get("status") != "Completed":
                raise RuntimeError(str(result.get("error", "Database backup failed")))
            return result
        if job_type in {"folder_backup", "iis_backup", "service_backup"}:
            raw_source = (
                Path(str(payload.get("executable_path", ""))).expanduser().parent
                if job_type == "service_backup"
                else Path(str(payload.get("path", ""))).expanduser()
            )
            source = raw_source
            if not source.is_dir():
                raise ValueError(f"Scheduled {job_type.replace('_', ' ')} source is unavailable: {source}")
            configured_root = str(self.config.get("general", "backup_root_folder", "")).strip()
            if not configured_root:
                raise ValueError("Backup destination path is empty.")
            name = str(payload.get("name", source.name or "Folder Backup"))
            safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._") or "Folder_Backup"
            category = {
                "folder_backup": "ScheduledFolderBackups",
                "iis_backup": "ScheduledIISBackups",
                "service_backup": "ScheduledServiceBackups",
            }[job_type]
            destination = (
                Path(configured_root).expanduser() / category /
                safe_name / datetime.now().strftime("%Y%m%d_%H%M%S")
            )
            paths = FolderBackup(
                incremental=bool(self.config.get("general", "incremental_backups", True))
            ).backup_folders(
                [{"name": safe_name, "path": str(source), "enabled": True}], destination,
            )
            if not paths:
                raise RuntimeError("Scheduled folder backup produced no archive.")
            return {"status": "Completed", "backup_path": str(paths[0])}
        if job_type == "script":
            return run_script(
                str(payload.get("path", "")),
                timeout_minutes=max(1, int(payload.get("timeout_minutes", 60))),
            )
        if job_type == "cleanup":
            result = CleanupManager().cleanup_tasks([{
                "name": payload.get("name", "Scheduled cleanup"),
                "path": payload.get("path", ""),
                "extensions": payload.get("extensions", []),
                "minimum_age_days": payload.get("minimum_age_days", 0),
                "enabled": True,
            }])
            if not result or result[0].get("status") not in {"Completed", "Skipped"}:
                raise RuntimeError(str(result[0].get("detail", "Cleanup failed")))
            return {"status": "Completed", "cleanup": result[0]}
        if job_type == "external_backup_copy":
            destination = self._activate_external_destination()
            if destination is None:
                raise ExternalDriveUnavailable("External copy is disabled.")
            copied = copy_verified_backup(
                Path(str(payload.get("source_path", ""))), destination,
                str(job.get("id", "external-copy")),
            )
            return {
                "status": "Completed",
                "backup_path": copied["destination"],
                "tasks": [{
                    "task": "External Backup Copy",
                    "status": "Completed",
                    "detail": (
                        f"{copied['files']} files verified at {copied['destination']}. "
                        "The external drive can now be safely removed."
                    ),
                }],
                "external_copy": copied,
            }
        raise ValueError(f"Unsupported service job type: {job_type}")

    def process_once(self) -> bool:
        self.config.load_config(create_if_missing=False)
        self.queue.migrate_legacy_external_waits()
        if self._external_settings().get("migration_requires_local_destination", False):
            message = (
                "External-drive settings were migrated. Select a permanent local backup root "
                "and save External Copy settings before schedules can continue."
            )
            self.write_heartbeat("configuration_required", error=message)
            self.logger.warning(message)
            return False
        default_job_id = enqueue_default_checklist_if_due(self.config, self.queue)
        if default_job_id:
            self.logger.info("Queued due default checklist job %s", default_job_id)
        scheduled_count = self.schedules.enqueue_due(
            self.queue, startup_marker=self.startup_marker,
        )
        if scheduled_count:
            self.logger.info("Queued %d due scheduled job(s)", scheduled_count)
        external_destination: Path | None = None
        external_error: ExternalDriveUnavailable | None = None
        if self._external_settings().get("enabled", False):
            check_seconds = max(
                60, int(self._external_settings().get("check_interval_minutes", 5)) * 60,
            )
            if not self._external_last_check or time.monotonic() - self._external_last_check >= check_seconds:
                self._external_last_check = time.monotonic()
                try:
                    self._external_cached_destination = self._activate_external_destination()
                    self._external_cached_error = None
                except ExternalDriveUnavailable as exc:
                    self._external_cached_destination = None
                    self._external_cached_error = exc
            external_destination = self._external_cached_destination
            external_error = self._external_cached_error
            if external_destination:
                resumed = self.queue.resume_external_jobs()
                if resumed:
                    self.logger.info("Requeued %d job(s) after the external drive returned.", resumed)

        notifier = EmailNotifier(self.config.get_section("email"))
        waiting_external_copy = False
        if external_error:
            for waiting_job in self.queue.load():
                if waiting_job.get("status") == "waiting_external":
                    waiting_external_copy = True
                    try:
                        self._notify_external_missing(waiting_job, notifier, str(external_error))
                    except Exception:
                        self.logger.exception("External-drive reminder email failed")

        job = self.queue.claim_next()
        if not job:
            self.write_heartbeat(
                "external_copy_pending" if external_error and waiting_external_copy else "idle",
                error=str(external_error or "") if waiting_external_copy else "",
            )
            return False
        if external_error and self._uses_backup_destination(job):
            self.queue.defer_for_external_drive(job["id"], str(external_error))
            waiting_job = next(
                (item for item in self.queue.load() if item.get("id") == job.get("id")), job,
            )
            try:
                self._notify_external_missing(waiting_job, notifier, str(external_error))
            except Exception:
                self.logger.exception("External-drive required email failed")
            self.write_heartbeat("waiting_external", job["id"], str(external_error))
            return True
        self.write_heartbeat("running", job["id"])
        self.logger.info(
            "Starting job %s (type=%s, attempt=%s)",
            job["id"], job.get("type", ""), job.get("attempts", 0),
        )
        job_payload = job.get("payload", {})
        if job.get("type") == "external_backup_copy" and external_destination:
            try:
                self._notify_external_ready(job, notifier, external_destination)
                if job_payload.get("external_reconnected"):
                    self.queue.update_payload(job["id"], {"external_reconnected": False})
            except Exception:
                self.logger.exception("External-drive detected email failed")
        monitor_stop = threading.Event()
        monitor_started = datetime.now()
        monitor_clock = time.monotonic()
        heartbeat_seconds = max(5, int(self.config.get("service", "heartbeat_seconds", 30)))

        def monitor_running_job() -> None:
            while not monitor_stop.wait(heartbeat_seconds):
                self.write_heartbeat(
                    "running", job["id"], started_at=monitor_started.isoformat(timespec="seconds"),
                    elapsed_seconds=time.monotonic() - monitor_clock,
                )

        monitor_thread = threading.Thread(
            target=monitor_running_job, name="backup-heartbeat", daemon=True,
        )
        monitor_thread.start()
        try:
            try:
                with checklist_execution_lock():
                    result = self.execute(job)
            except ChecklistExecutionBusy as exc:
                self.queue.requeue(str(job.get("id", "")), str(exc))
                self.logger.info("Deferred job %s because another checklist is running.", job.get("id", ""))
                self.write_heartbeat("waiting_for_active_checklist", error=str(exc))
                return False
            external_copy_job_id = ""
            if (
                self._external_settings().get("enabled", False)
                and job.get("type") in {"checklist", "module_schedule", "database_backup", "folder_backup", "iis_backup", "service_backup"}
                and result.get("backup_path")
            ):
                external_copy_job_id = self.queue.enqueue_external_copy(
                    str(result["backup_path"]), str(job.get("id", "")),
                    latest_only=bool(self._external_settings().get("copy_latest_only", True)),
                )
                result.setdefault("tasks", []).append({
                    "task": "External Backup Copy",
                    "status": "Pending",
                    "detail": "Local backup is complete and verified; external copy is queued.",
                })
                result["external_copy_job_id"] = external_copy_job_id
            if (
                job.get("type") == "checklist"
                and self.queue._is_scheduled_execution(job)
                and result.get("status") == "Completed"
            ):
                backup_path = Path(str(result["backup_path"])) if result.get("backup_path") else None
                deleted = delete_previous_automated_backups(
                    self.config, keep_path=backup_path,
                    protected_paths=self.queue.pending_external_source_paths(),
                )
                result["previous_backups_deleted"] = deleted
                self.logger.info(
                    "Deleted %d previous automated backup folder(s) after scheduled success.",
                    deleted,
                )
            self.queue.finish(job["id"], "completed", result=result)
            if job.get("type") == "checklist":
                from utils.history_manager import HistoryManager
                HistoryManager(get_app_dir()).add_record({
                    "date": result.get("execution_date", datetime.now().isoformat()),
                    "duration": result.get("duration", ""),
                    "status": result.get("status", "Completed"),
                    "backup_size": result.get("backup_size", "N/A"),
                    "backup_path": result.get("backup_path", ""),
                    "run_type": "service-default" if job_payload.get("schedule_kind") == "default" else "service-manual",
                })
            self.logger.info("Completed job %s (type=%s)", job["id"], job.get("type", ""))
            retention = self.config.get_section("retention")
            retention_safe = (
                job.get("type") != "checklist" or result.get("status") == "Completed"
            )
            if retention.get("enabled", True) and retention_safe:
                root = self.config.get("general", "backup_root_folder", "")
                if root:
                    apply_retention(
                        Path(root).expanduser(),
                        max_age_days=int(retention.get("max_age_days", 90)),
                        min_keep=int(retention.get("minimum_successful_backups", 1)),
                        protected_paths=self.queue.pending_external_source_paths(),
                    )
            try:
                if self._should_notify_success(job, result):
                    notifier.notify_result(job, "completed", result=result)
                else:
                    self.logger.info(
                        "Successful module-schedule email suppressed by notification settings."
                    )
            except Exception:
                self.logger.exception("Completion email failed")
            self.write_heartbeat("idle")
            self.queue.prune_terminal()
        except Exception as exc:
            self.logger.exception("Service job failed")
            if self._uses_backup_destination(job) and self._external_settings().get("enabled", False):
                try:
                    self._activate_external_destination()
                except ExternalDriveUnavailable as drive_error:
                    self._external_cached_destination = None
                    self._external_cached_error = drive_error
                    self._external_last_check = time.monotonic()
                    self.queue.defer_for_external_drive(job["id"], str(drive_error))
                    waiting_job = next(
                        (item for item in self.queue.load() if item.get("id") == job.get("id")), job,
                    )
                    try:
                        self._notify_external_missing(waiting_job, notifier, str(drive_error))
                    except Exception:
                        self.logger.exception("External-drive interruption email failed")
                    self.write_heartbeat("waiting_external", job["id"], str(drive_error))
                    return True
            self.queue.finish(job["id"], "failed", error=str(exc))
            if job.get("type") == "checklist":
                from utils.history_manager import HistoryManager
                HistoryManager(get_app_dir()).add_record({
                    "date": datetime.now().isoformat(timespec="seconds"),
                    "duration": "",
                    "status": "Failed",
                    "backup_size": "N/A",
                    "backup_path": "",
                    "error": str(exc),
                    "run_type": "service-failed",
                })
            try:
                notifier.notify_result(job, "failed", str(exc))
            except Exception:
                self.logger.exception("Failure email failed")
            self.write_heartbeat("error", job["id"], str(exc))
        finally:
            monitor_stop.set()
            monitor_thread.join(timeout=2)
        return True

    def run(self):
        # A service must never replace a missing production configuration with
        # defaults. The GUI is responsible for creating and validating it.
        self.config.load_config(create_if_missing=False)
        backup_root_value = str(self.config.get("general", "backup_root_folder", "")).strip()
        backup_root = Path(backup_root_value).expanduser() if backup_root_value else None
        interrupted_jobs = [
            dict(job) for job in self.queue.load()
            if job.get("status") == "running"
            and job.get("type") == "checklist"
            and self.queue._is_scheduled_execution(job)
        ]
        removed = (
            remove_incomplete_backups(backup_root)
            if backup_root_value else []
        )
        legacy_requeued = 0
        legacy_removed: list[Path] = []
        if backup_root:
            for job in self.queue.load():
                if not is_legacy_interrupted_scheduled_checklist(job):
                    continue
                interrupted_jobs.append(dict(job))
                try:
                    removed_path = remove_legacy_interrupted_backup(backup_root, job)
                except Exception:
                    self.logger.exception(
                        "Unable to remove legacy incomplete backup for job %s; "
                        "the job will not restart until cleanup succeeds.",
                        job.get("id", ""),
                    )
                    continue
                if removed_path:
                    legacy_removed.append(removed_path)
                if self.queue.retry(str(job.get("id", ""))):
                    legacy_requeued += 1
                    self.logger.warning(
                        "Migrated interrupted scheduled job %s from an older release; "
                        "it will restart from the beginning.",
                        job.get("id", ""),
                    )
        recovered = self.queue.recover_running()
        self.recovery_summary = (
            f"requeued_or_closed_jobs={recovered + legacy_requeued}, "
            f"removed_incomplete_backups={len(removed) + len(legacy_removed)}"
        )
        self.write_heartbeat(
            "recovering" if recovered or legacy_requeued or removed or legacy_removed else "idle"
        )
        for path in [*removed, *legacy_removed]:
            self.logger.warning("Removed incomplete backup after restart: %s", path)
        if interrupted_jobs:
            recovery_notifier = EmailNotifier(self.config.get_section("email"))
            removed_text = ", ".join(str(path) for path in [*removed, *legacy_removed]) or "none"
            for interrupted_job in interrupted_jobs:
                try:
                    recovery_notifier.notify_result(
                        interrupted_job,
                        "interrupted and recovered",
                        "The server or backup service stopped before the scheduled backup completed. "
                        f"Incomplete backup folders removed: {removed_text}. The job was queued to restart from the beginning.",
                        {"execution_date": datetime.now().isoformat(timespec="seconds")},
                    )
                    self.logger.warning(
                        "Sent interrupted-backup recovery notification for job %s.",
                        interrupted_job.get("id", ""),
                    )
                except Exception:
                    self.logger.exception(
                        "Interrupted-backup recovery notification failed for job %s.",
                        interrupted_job.get("id", ""),
                    )
        self.logger.info(
            "Service runtime initialized (queue=%s, schedules=%s, recovered=%d, "
            "incomplete_backups_removed=%d)",
            self.queue.path, self.schedules.path, recovered + legacy_requeued,
            len(removed) + len(legacy_removed),
        )
        try:
            while not self.stop_requested():
                worked = self.process_once()
                if not worked:
                    for _ in range(max(1, int(self.config.get("service", "poll_seconds", 5))) * 10):
                        if self.stop_requested():
                            return
                        time.sleep(0.1)
        finally:
            self.logger.info("Service runtime stopped")
