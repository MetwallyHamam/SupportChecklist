from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from modules.cleanup_manager import CleanupManager
from modules.folder_backup import FolderBackup
from modules.iis_manager import IISManager
from modules.service_backup import ServiceBackup
from modules.sql_backup import SQLBackup, is_backup_supported
from core.preflight import validate_paths
from core.backup_lifecycle import mark_backup_completed, mark_backup_started
from utils.config_manager import ConfigManager
from utils.duration import format_duration
from utils.hashing import hash_file
from utils.logger import get_logger
from utils.paths import get_app_dir
from utils.compression import compress_file_to_zip


TaskCallback = Callable[[int, str], None]
StopCheck = Callable[[], bool]
CheckpointCallback = Callable[[dict[str, Any]], None]


class ChecklistRunner:
    """Execute the checklist workflow using modular backup providers."""

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager
        self.logger = get_logger(self.__class__.__name__)
        self.iis_manager = IISManager()
        self.service_backup = ServiceBackup()
        self.folder_backup = FolderBackup(
            incremental=bool(config_manager.get("general", "incremental_backups", True))
        )
        self.cleanup_manager = CleanupManager()
        self.sql_backup: SQLBackup | None = None

    def get_preflight_messages(self) -> tuple[list[str], list[str]]:
        """Return blocking configuration errors and non-blocking skip warnings."""
        errors: list[str] = []
        warnings: list[str] = []

        backup_root = str(self.config_manager.get("general", "backup_root_folder", "")).strip()
        if not backup_root:
            errors.append("Backup destination path is empty. Configure a backup root folder before starting.")
        else:
            for issue in validate_paths(None, backup_root):
                (errors if issue.severity == "error" else warnings).append(issue.message)

        for folder in self.config_manager.get("folder_backup", "folders", []):
            if not folder.get("enabled", False):
                continue
            source = str(folder.get("path", "")).strip()
            if not source or not Path(source).is_dir():
                errors.append(f"Configured folder source is unavailable: {source or '(empty)'}")

        if (
            self.config_manager.get("services", "backup_enabled", True)
            and not self.config_manager.get("services", "selected_services", [])
        ):
            warnings.append("No Windows services are selected. Service backup will be skipped.")

        if self.config_manager.get("iis", "backup_enabled", True):
            selected_sites = self.config_manager.get("iis", "selected_sites", [])
            selected_apps = self.config_manager.get("iis", "selected_apps", [])
            if not selected_sites and not selected_apps:
                warnings.append("No IIS sites or child applications are selected. IIS backup will be skipped.")

        selected_databases = self.config_manager.get("sql_server", "databases", [])
        if not selected_databases:
            legacy_database = str(
                self.config_manager.get("sql_server", "database_name", "") or ""
            ).strip()
            selected_databases = [legacy_database] if legacy_database else []
        if (
            self.config_manager.get("sql_server", "backup_enabled", True)
            and not any(is_backup_supported(str(database)) for database in selected_databases)
        ):
            warnings.append("No databases are selected. Database backup will be skipped.")

        if (
            self.config_manager.get("cleanup", "enabled", False)
            and not any(task.get("enabled", True) for task in self.config_manager.get("cleanup", "tasks", []))
        ):
            warnings.append("Cleanup is enabled but no cleanup tasks are selected. Cleanup will be skipped.")

        if not self._has_selected_backup_targets():
            errors.append(
                "No backup items are selected. Select at least one IIS site or application, "
                "Windows service, database, or folder before running the full checklist."
            )

        return errors, warnings

    def run(
        self,
        progress_callback: TaskCallback | None = None,
        stop_check: StopCheck | None = None,
        resume_state: dict[str, Any] | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
    ) -> dict[str, Any]:
        start_time = time.perf_counter()
        errors, warnings = self.get_preflight_messages()
        if errors:
            raise ValueError("\n".join(errors))
        for warning in warnings:
            self.logger.warning(warning)

        resume_state = dict(resume_state or {})
        completed_steps = list(resume_state.get("completed_steps", []) or [])
        # Reuse the same backup folder after an external-drive interruption so
        # verified phases are preserved and only the interrupted phase is retried.
        timestamp = str(resume_state.get("backup_timestamp", "")) or datetime.now().strftime("%Y%m%d_%H%M%S")
        base_backup_root = Path(str(self.config_manager.get("general", "backup_root_folder", "")).strip()).expanduser()
        saved_backup_path = str(resume_state.get("backup_path", "")).strip()
        # A removable volume may return under a different drive letter. Keep
        # the run folder name but rebase it onto the currently resolved root.
        automated_root = (
            base_backup_root / Path(saved_backup_path).name
            if saved_backup_path else base_backup_root / f"Automatedbackup_{timestamp}"
        )
        try:
            automated_root.resolve().relative_to(base_backup_root.resolve())
        except ValueError as exc:
            raise ValueError("Saved external-drive recovery path is outside the configured destination.") from exc
        automated_root.mkdir(parents=True, exist_ok=True)
        mark_backup_started(automated_root)
        # store for later use
        self._automated_root = automated_root
        self._backup_timestamp = timestamp
        report: dict[str, Any] = {
            "server_name": self.config_manager.get("sql_server", "server_name", ""),
            "backup_path": str(automated_root),
            "execution_date": datetime.now().isoformat(),
            "status": "Pending",
            "duration": "0",
            "tasks": [],
        }

        state: dict[str, Any] = {
            "backup_path": str(automated_root),
            "backup_timestamp": timestamp,
            "completed_steps": completed_steps,
            "current_step": "",
            "status": "running",
        }

        def checkpoint(status: str, step: str = "") -> None:
            state["status"] = status
            state["current_step"] = step
            state["completed_steps"] = list(completed_steps)
            report["resume_state"] = dict(state)
            if checkpoint_callback:
                checkpoint_callback(dict(state))

        try:
            self.logger.info("Checklist execution started.")
            self.prepare_sql_backup()
            self._update_progress(progress_callback, 5, "Preparing backup")
            phases = [
                ("cleanup", self.cleanup_logs),
                ("iis", self.backup_iis),
                ("services", self.backup_services),
                ("database", self.backup_sql),
                ("folders", self.backup_folders),
            ]
            for step, operation in phases:
                if step in completed_steps:
                    report["tasks"].append({
                        "task": step.title(), "status": "Completed",
                        "detail": "Preserved from the previous verified attempt",
                    })
                    continue
                if (
                    resume_state.get("status") == "interrupted"
                    and resume_state.get("current_step") == step
                ):
                    self._remove_interrupted_step_output(step)
                checkpoint("running", step)
                operation(report, progress_callback, stop_check)
                completed_steps.append(step)
                checkpoint("running", "")
            full_size, file_count = self._compute_directory_stats(self._get_backup_root())
            if file_count == 0 or full_size == 0:
                raise RuntimeError(
                    "Backup verification failed: no backup files were produced. "
                    "Check the selected items and the task details."
                )
            report["backup_size_bytes"] = full_size
            report["backup_file_count"] = file_count
            report["backup_size"] = self._format_backup_size(full_size)
            warning_statuses = {"partial", "warning", "completed with warnings", "failed"}
            has_warnings = any(
                str(task.get("status", "")).casefold() in warning_statuses
                for task in report.get("tasks", [])
                if isinstance(task, dict)
            )
            report["status"] = "Completed with Warnings" if has_warnings else "Completed"
            mark_backup_completed(automated_root)
            checkpoint("completed", "")
        except Exception as error:
            self.logger.exception("Checklist execution failed.")
            report["status"] = "Failed"
            report["error"] = str(error)
            checkpoint("interrupted", str(state.get("current_step", "")))
        finally:
            duration = time.perf_counter() - start_time
            report["duration"] = format_duration(duration)
            self.logger.info("Checklist execution finished in %.1f seconds.", duration)

        try:
            self.generate_report(report)
        except Exception:
            self.logger.exception("Unable to generate the final execution report.")

        return report

    def _remove_interrupted_step_output(self, step: str) -> None:
        folder_name = {
            "iis": "IIS",
            "services": "Services",
            "database": "Database",
            "folders": "Folders",
        }.get(step)
        if not folder_name:
            return
        automated_root = self._get_backup_root().resolve()
        target = (automated_root / folder_name).resolve()
        if target.parent != automated_root:
            raise ValueError("Interrupted backup cleanup escaped the active backup folder.")
        if target.is_dir():
            shutil.rmtree(target)
            self.logger.info("Removed incomplete %s phase output before retry: %s", step, target)

    def run_cleanup_only(self, progress_callback: TaskCallback | None = None, stop_check: StopCheck | None = None) -> dict[str, Any]:
        """Execute configured cleanup tasks without running backup phases."""
        if not self.config_manager.get("cleanup", "enabled", False):
            raise ValueError("Cleanup tasks are disabled. Enable cleanup before running cleanup only.")
        if not any(task.get("enabled", True) for task in self.config_manager.get("cleanup", "tasks", [])):
            raise ValueError("No enabled cleanup tasks are configured.")

        start_time = time.perf_counter()
        report: dict[str, Any] = {
            "execution_date": datetime.now().isoformat(),
            "status": "Pending",
            "duration": "0",
            "tasks": [],
        }
        try:
            self.logger.info("Cleanup-only execution started.")
            self._update_progress(progress_callback, 5, "Starting cleanup only")
            self.cleanup_logs(report, progress_callback, stop_check)
            report["status"] = (
                "Completed with Warnings"
                if any(
                    str(task.get("status", "")).casefold() in {"partial", "warning", "failed"}
                    for task in report.get("tasks", [])
                )
                else "Completed"
            )
            self._update_progress(progress_callback, 100, "Cleanup only completed")
        except Exception as error:
            self.logger.exception("Cleanup-only execution failed.")
            report["status"] = "Failed"
            report["error"] = str(error)
        finally:
            duration = time.perf_counter() - start_time
            report["duration"] = format_duration(duration)
            self.logger.info("Cleanup-only execution finished in %.1f seconds.", duration)
        return report

    def _has_selected_backup_targets(self) -> bool:
        """Return whether the full checklist has at least one enabled backup target."""
        iis_selected = (
            self.config_manager.get("iis", "backup_enabled", True)
            and self.config_manager.get("iis", "auto_detect_sites", True)
            and bool(
                self.config_manager.get("iis", "selected_sites", [])
                or self.config_manager.get("iis", "selected_apps", [])
            )
        )
        services_selected = (
            self.config_manager.get("services", "backup_enabled", True)
            and bool(self.config_manager.get("services", "selected_services", []))
        )
        databases = list(self.config_manager.get("sql_server", "databases", []) or [])
        legacy_database = str(
            self.config_manager.get("sql_server", "database_name", "") or ""
        ).strip()
        database_candidates = databases or ([legacy_database] if legacy_database else [])
        database_selected = (
            self.config_manager.get("sql_server", "backup_enabled", True)
            and any(is_backup_supported(str(database)) for database in database_candidates)
        )
        folders_selected = any(
            folder.get("enabled", True)
            for folder in self.config_manager.get("folder_backup", "folders", [])
        )
        return bool(iis_selected or services_selected or database_selected or folders_selected)

    @staticmethod
    def _format_backup_size(size_bytes: int) -> str:
        """Format small backups without rounding a valid backup down to 0.0 GB."""
        if size_bytes >= 1024**3:
            return f"{size_bytes / 1024**3:.1f} GB"
        if size_bytes >= 1024**2:
            return f"{size_bytes / 1024**2:.1f} MB"
        if size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} bytes"

    def run_database_only(self) -> dict[str, Any]:
        """Back up configured SQL databases without running the full checklist."""
        if not self.config_manager.get("sql_server", "backup_enabled", True):
            raise ValueError("Database backup is disabled in SQL Server settings.")
        databases = list(self.config_manager.get("sql_server", "databases", []) or [])
        if not databases:
            fallback = str(self.config_manager.get("sql_server", "database_name", "") or "").strip()
            databases = [fallback] if fallback else []
        if not any(is_backup_supported(str(database)) for database in databases):
            raise ValueError("No supported databases are selected for backup.")

        backup_root = str(self.config_manager.get("general", "backup_root_folder", "")).strip()
        if not backup_root:
            raise ValueError("Backup destination path is empty.")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._automated_root = Path(backup_root).expanduser() / f"DatabaseBackup_{timestamp}"
        self._automated_root.mkdir(parents=True, exist_ok=True)
        self._backup_timestamp = timestamp
        report: dict[str, Any] = {
            "backup_path": str(self._automated_root),
            "execution_date": datetime.now().isoformat(),
            "status": "Pending",
            "tasks": [],
        }
        try:
            self.prepare_sql_backup()
            self.backup_sql(report, None, None)
            report["status"] = "Completed"
        except Exception as error:
            self.logger.exception("Database-only backup failed.")
            report["status"] = "Failed"
            report["error"] = str(error)
        return report

    def _update_progress(self, callback: TaskCallback | None, value: int, task: str) -> None:
        if callback:
            callback(value, task)

    def _should_stop(self, stop_check: StopCheck | None) -> bool:
        return stop_check() if stop_check else False

    def prepare_sql_backup(self) -> None:
        sql_config = self.config_manager.get_section("sql_server")
        use_windows_auth = bool(sql_config.get("use_windows_auth", True))
        username = str(sql_config.get("username", ""))
        password = str(sql_config.get("password", ""))
        if not use_windows_auth and (not username or not password):
            raise ValueError(
                "SQL authentication is selected, but its username or password is missing "
                "from the shared configuration. Save SQL Server settings in the GUI first."
            )
        self.logger.info(
            "Preparing SQL connection to %s using %s%s",
            str(sql_config.get("server_name", "")),
            "Windows authentication" if use_windows_auth else "SQL authentication",
            "" if use_windows_auth else f" (user={username})",
        )
        self.sql_backup = SQLBackup(
            server=str(sql_config.get("server_name", "")),
            instance=str(sql_config.get("instance_name", "")),
            database=str(sql_config.get("database_name", "")),
            username=username,
            password=password,
            use_windows_auth=use_windows_auth,
            compression_enabled=bool(self.config_manager.get("general", "compression_enabled", True)),
        )

    def backup_iis(self, report: dict[str, Any], progress_callback: TaskCallback | None, stop_check: StopCheck | None) -> None:
        self.logger.info("Starting IIS backup phase.")
        if self._should_stop(stop_check):
            raise RuntimeError("Checklist stopped by user.")

        if not self.config_manager.get("iis", "backup_enabled", True):
            self.logger.info("IIS backup skipped by configuration.")
            report["tasks"].append({"task": "IIS", "status": "Skipped", "detail": "Disabled in configuration"})
            return

        if not self.config_manager.get("iis", "auto_detect_sites", True):
            self.logger.info("IIS site auto-detection is disabled.")
            report["tasks"].append({"task": "IIS", "status": "Skipped", "detail": "Auto-detection is disabled"})
            return

        selected_site_names = set(self.config_manager.get("iis", "selected_sites", []))
        selected_apps = set(self.config_manager.get("iis", "selected_apps", []))
        selected_pools = set(self.config_manager.get("iis", "selected_pools", []))
        if not selected_site_names and not selected_apps:
            self.logger.warning("Skipping IIS backup because no IIS sites or child applications are selected.")
            report["tasks"].append({"task": "IIS", "status": "Skipped", "detail": "No IIS sites or child apps selected"})
            return

        report["tasks"].append({"task": "IIS", "status": "Running"})
        self._update_progress(progress_callback, 10, "Detecting IIS sites")

        sites = self.iis_manager.detect_sites()
        self.logger.info("Detected %d IIS sites.", len(sites))
        report["tasks"].append({"task": "IIS Detection", "status": "Completed", "detail": f"{len(sites)} sites"})

        # Collect selected child applications and app pool info for reporting.
        try:
            apps = self.iis_manager.list_applications()
            pools = self.iis_manager.list_app_pools()
        except Exception:
            apps = []
            pools = []
        selected_app_records = [
            app for app in apps
            if f"{app.get('site', '')}|{app.get('path', '')}" in selected_apps
        ]
        detected_app_keys = {
            f"{app.get('site', '')}|{app.get('path', '')}" for app in selected_app_records
        }
        missing_app_keys = sorted(selected_apps - detected_app_keys)
        report["iis_apps"] = selected_app_records
        report["iis_pools"] = [
            pool for pool in pools
            if pool.get("name", "") in selected_pools
        ]
        if self.config_manager.get("iis", "include_iis_configuration", True):
            self._update_progress(progress_callback, 15, "Backing up IIS configuration")
            backup_root = self._get_backup_root() / "IIS" / "Config"
            self.iis_manager.backup_configuration(backup_root)
            self.logger.info("IIS configuration backup stored at %s", backup_root)
            report["tasks"].append({"task": "IIS Configuration", "status": "Completed", "detail": str(backup_root)})

        sites_to_backup = [site for site in sites if site.name in selected_site_names]
        if selected_site_names:
            self.logger.info("Backing up %d selected IIS sites (out of %d detected).", len(sites_to_backup), len(sites))
        else:
            self.logger.warning("Skipping IIS site folder backup because no IIS sites are selected.")
            report["tasks"].append({"task": "IIS Sites", "status": "Skipped", "detail": "No sites selected"})

        site_backup_root = self._get_backup_root() / "IIS" / "Sites"
        successful_site_paths: list[Path] = []
        for index, site in enumerate(sites_to_backup, start=1):
            if self._should_stop(stop_check):
                raise RuntimeError("Checklist stopped by user.")
            self._update_progress(progress_callback, 15 + int(25 * index / max(1, len(sites_to_backup))), f"Backing up IIS site {site.name}")
            site_folder = Path(site.physical_path)

            # Skip if site path is inside backup root (would cause recursive backup)
            try:
                site_folder.relative_to(self._get_backup_root())
                self.logger.warning("Skipping IIS site %s: path is inside backup root (would cause recursive backup)", site.name)
                continue
            except ValueError:
                # Good - path is not inside backup root
                pass
            
            if site_folder.exists():
                try:
                    destination = site_backup_root / f"{site.name}_{self._backup_timestamp}.zip"
                    self.folder_backup.backup_folders([{"name": f"{site.name}_{self._backup_timestamp}", "path": str(site_folder), "enabled": True}], site_backup_root)
                    self.logger.info("Backed up IIS site folder %s to %s", site_folder, destination)
                    successful_site_paths.append(site_folder)
                except Exception as backup_error:
                    self.logger.warning("Failed to backup IIS site folder %s: %s", site_folder, backup_error)
            else:
                self.logger.warning("IIS site folder missing: %s", site_folder)

        app_backup_root = self._get_backup_root() / "IIS" / "ChildApps"
        archived_or_covered_apps = 0
        app_results: list[dict[str, str]] = []
        for app in selected_app_records:
            app_folder = Path(str(app.get("physical_path", "")))
            app_label = f"{app.get('site', '')} > {app.get('path', '')}"
            if not app_folder.exists():
                self.logger.warning("IIS child application folder missing for %s: %s", app_label, app_folder)
                app_results.append({
                    **app,
                    "name": app_label,
                    "status": "Failed",
                    "detail": f"Physical folder not found: {app_folder}",
                })
                continue
            if any(self._is_path_within(app_folder, site_path) for site_path in successful_site_paths):
                self.logger.info("IIS child application %s is covered by its selected site folder.", app_label)
                archived_or_covered_apps += 1
                app_results.append({
                    **app,
                    "name": app_label,
                    "status": "Covered",
                    "detail": "Included in a successfully created IIS site archive",
                })
                continue
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", app_label).strip("_") or "iis_app"
            try:
                archives = self.folder_backup.backup_folders(
                    [{"name": f"{safe_name}_{self._backup_timestamp}", "path": str(app_folder), "enabled": True}],
                    app_backup_root,
                )
                archive = archives[0] if archives else None
                if archive is None or not archive.is_file() or archive.stat().st_size <= 0:
                    raise RuntimeError("No valid archive was produced")
                self.logger.info("Backed up IIS child application %s from %s to %s", app_label, app_folder, archive)
                archived_or_covered_apps += 1
                app_results.append({
                    **app,
                    "name": app_label,
                    "status": "Completed",
                    "archive": str(archive),
                    "detail": f"Archived from {app_folder}",
                })
            except Exception as backup_error:
                self.logger.exception("Failed to back up IIS child application %s from %s", app_label, app_folder)
                app_results.append({
                    **app,
                    "name": app_label,
                    "status": "Failed",
                    "detail": str(backup_error),
                })

        for missing_key in missing_app_keys:
            app_results.append({
                "name": missing_key.replace("|", " > ", 1),
                "status": "Failed",
                "detail": "Selected application was not returned by IIS detection",
            })
        report["iis_apps_detail"] = app_results
        failed_apps = [item for item in app_results if item.get("status") == "Failed"]

        app_detail = (
            f"{len(selected_apps)} selected, {len(selected_app_records)} detected, "
            f"{archived_or_covered_apps} archived or covered by a selected site"
        )
        if missing_app_keys:
            app_detail += f" | Missing: {', '.join(missing_app_keys)}"
        if failed_apps:
            app_detail += " | Failed: " + "; ".join(
                f"{item.get('name', 'IIS application')}: {item.get('detail', 'Unknown error')}"
                for item in failed_apps
            )
        report["tasks"].append({
            "task": "IIS Child Applications",
            "status": "Partial" if failed_apps else "Completed",
            "detail": app_detail,
        })

        report["tasks"].append({
            "task": "IIS",
            "status": "Partial" if failed_apps else "Completed",
            "detail": f"{len(failed_apps)} child application backup(s) failed" if failed_apps else "",
        })

    def backup_services(self, report: dict[str, Any], progress_callback: TaskCallback | None, stop_check: StopCheck | None) -> None:
        self.logger.info("Starting service backup phase.")
        if self._should_stop(stop_check):
            raise RuntimeError("Checklist stopped by user.")

        if not self.config_manager.get("services", "backup_enabled", True):
            self.logger.info("Service backup skipped by configuration.")
            report["tasks"].append({"task": "Services", "status": "Skipped", "detail": "Disabled in configuration"})
            report["services_detail"] = []
            return

        selected_service_names = set(self.config_manager.get("services", "selected_services", []))
        if not selected_service_names:
            self.logger.warning("Skipping service backup because no Windows services are selected.")
            report["tasks"].append({"task": "Services", "status": "Skipped", "detail": "No services selected"})
            report["services_detail"] = []
            return

        detected_services = self.service_backup.list_services()
        services = [service for service in detected_services if service.name in selected_service_names]
        detected_service_names = {service.name for service in services}
        missing_service_names = sorted(selected_service_names - detected_service_names)

        report["tasks"].append({"task": "Services", "status": "Running", "detail": f"{len(services)} services detected"})
        self._update_progress(progress_callback, 45, "Detecting Windows services")

        service_backup_root = self._get_backup_root() / "Services"
        services_detail: list[dict[str, str]] = []
        for index, service in enumerate(services, start=1):
            if self._should_stop(stop_check):
                raise RuntimeError("Checklist stopped by user.")
            if not service.executable_path:
                services_detail.append({
                    "name": service.name,
                    "display_name": service.display_name,
                    "runtime_status": service.status,
                    "status": "Failed",
                    "detail": "The service executable path is empty",
                })
            else:
                try:
                    archive_path = self.service_backup.backup_service_folder(
                        service.executable_path, service_backup_root
                    )
                    if not archive_path.is_file() or archive_path.stat().st_size <= 0:
                        raise RuntimeError("No valid service archive was produced")
                    self.logger.info("Backed up service %s to %s", service.name, archive_path)
                    services_detail.append({
                        "name": service.name,
                        "display_name": service.display_name,
                        "path": service.executable_path,
                        # The report status describes the requested archive operation.
                        # Keep the Windows service state as separate supporting detail.
                        "status": "Completed",
                        "runtime_status": service.status,
                        "archive": str(archive_path),
                        "detail": "Service folder archived successfully",
                    })
                except Exception as backup_error:
                    self.logger.exception(
                        "Failed to back up service %s from %s",
                        service.name,
                        service.executable_path,
                    )
                    services_detail.append({
                        "name": service.name,
                        "display_name": service.display_name,
                        "path": service.executable_path,
                        "runtime_status": service.status,
                        "status": "Failed",
                        "detail": str(backup_error),
                    })
            self._update_progress(progress_callback, 45 + int(15 * index / max(1, len(services))), f"Backing up service {service.name}")

        for service_name in missing_service_names:
            services_detail.append({
                "name": service_name,
                "status": "Failed",
                "detail": "Selected service was not returned by Windows service detection",
            })

        archived_count = sum(item.get("status") == "Completed" for item in services_detail)
        failed_services = [item for item in services_detail if item.get("status") == "Failed"]
        summary = (
            f"{len(selected_service_names)} selected, {len(services)} detected, "
            f"{archived_count} archived"
        )
        if failed_services:
            summary += " | Failed: " + "; ".join(
                f"{item.get('name', 'Service')}: {item.get('detail', 'Unknown error')}"
                for item in failed_services
            )
        report["tasks"].append({
            "task": "Services",
            "status": "Partial" if failed_services else "Completed",
            "detail": summary,
        })
        report["services_detail"] = services_detail

    def backup_sql(self, report: dict[str, Any], progress_callback: TaskCallback | None, stop_check: StopCheck | None) -> None:
        self.logger.info("Starting SQL backup phase.")
        if self._should_stop(stop_check):
            raise RuntimeError("Checklist stopped by user.")

        if not self.config_manager.get("sql_server", "backup_enabled", True):
            self.logger.info("Database backup skipped by configuration.")
            report["tasks"].append({"task": "Database", "status": "Skipped", "detail": "Disabled in configuration"})
            return

        configured_dbs = self.config_manager.get("sql_server", "databases", [])
        if not configured_dbs:
            legacy_database = str(
                self.config_manager.get("sql_server", "database_name", "") or ""
            ).strip()
            if legacy_database:
                configured_dbs = [legacy_database]
                self.logger.info(
                    "Using the configured database name because no databases are selected: %s",
                    legacy_database,
                )
        unsupported_dbs = [str(database) for database in configured_dbs if not is_backup_supported(str(database))]
        if unsupported_dbs:
            self.logger.warning("Skipping unsupported SQL databases: %s", ", ".join(unsupported_dbs))
            report["tasks"].append({
                "task": "Database Selection",
                "status": "Skipped",
                "detail": f"Unsupported databases: {', '.join(unsupported_dbs)}",
            })
        configured_dbs = [str(database) for database in configured_dbs if is_backup_supported(str(database))]
        if not configured_dbs:
            self.logger.warning("Database backup skipped because no databases are selected.")
            report["tasks"].append({"task": "Database", "status": "Skipped", "detail": "No databases selected"})
            return

        sql_backup_root = self._get_backup_root() / "Database"
        sql_backup_root.mkdir(parents=True, exist_ok=True)
        dbs_to_backup = configured_dbs
        db_reports: list[dict[str, str]] = []
        for index, db_name in enumerate(dbs_to_backup, start=1):
            if self._should_stop(stop_check):
                raise RuntimeError("Checklist stopped by user.")
            backup_file = sql_backup_root / f"{db_name}_{self._backup_timestamp}.bak"
            self._update_progress(progress_callback, 60 + int(5 * index), f"Backing up database {db_name}")
            # set the target database on the SQLBackup instance for the command
            old_db = self.sql_backup.database
            self.sql_backup.database = db_name
            sql_backup_file: Path | None = None
            try:
                sql_backup_file = self.sql_backup.backup_database(backup_file)
                try:
                    self.sql_backup.verify_backup(sql_backup_file)
                except Exception as verify_error:
                    self.logger.warning("SQL backup verification skipped: %s", verify_error)
                backup_file = self.sql_backup.move_backup_to_destination(sql_backup_file, backup_file)

                stored_file = backup_file
                compression_description = (
                    "SQL Server native" if self.sql_backup.compression_enabled else "Disabled"
                )
                if self.config_manager.get("sql_server", "archive_compression_enabled", True):
                    self._update_progress(
                        progress_callback,
                        60 + int(5 * index),
                        f"Compressing database {db_name}",
                    )
                    stored_file = compress_file_to_zip(backup_file)
                    compression_description = (
                        "ZIP (SQL Server native + ZIP)"
                        if self.sql_backup.compression_enabled
                        else "ZIP"
                    )

                self.logger.info("SQL backup stored at %s", stored_file)
                db_entry = {
                    "database": db_name,
                    "file": str(stored_file),
                    "compression": compression_description,
                }
                if self.config_manager.get("general", "generate_sha256_hashes", True):
                    try:
                        checksum = hash_file(stored_file)
                        db_entry["sha256"] = checksum
                    except Exception as hash_error:
                        self.logger.warning("SQL backup hashing skipped: %s", hash_error)
                db_reports.append(db_entry)
            except Exception:
                # Remove only artifacts created for this database and this run.
                # A staged .bak left after an external-drive disconnect would
                # otherwise be appended to or mistaken for a fresh retry.
                for candidate in (sql_backup_file, backup_file):
                    if candidate is None:
                        continue
                    try:
                        if candidate.is_file():
                            candidate.unlink()
                            self.logger.info("Removed incomplete SQL backup item: %s", candidate)
                    except OSError:
                        self.logger.warning("Could not remove incomplete SQL backup item: %s", candidate)
                raise
            finally:
                self.sql_backup.database = old_db

        report["tasks"].append({"task": "Database", "status": "Completed", "detail": f"{len(db_reports)} databases"})
        report["databases"] = db_reports
        self._update_progress(progress_callback, 70, "Database backup completed")

    def backup_folders(self, report: dict[str, Any], progress_callback: TaskCallback | None, stop_check: StopCheck | None) -> None:
        self.logger.info("Starting folder backup phase.")
        if self._should_stop(stop_check):
            raise RuntimeError("Checklist stopped by user.")

        folders = self.config_manager.get("folder_backup", "folders", [])
        if not folders:
            self.logger.info("No folder backup configuration found.")
            report["tasks"].append({"task": "Folders", "status": "Skipped"})
            return

        folder_backup_root = self._get_backup_root() / "Folders"
        backup_paths = self.folder_backup.backup_folders(folders, folder_backup_root)
        self.logger.info("Backed up %d configured folders.", len(backup_paths))
        report["tasks"].append({"task": "Folders", "status": "Completed", "detail": f"{len(backup_paths)} folders"})
        self._update_progress(progress_callback, 90, "Folder backups completed")

        if self.config_manager.get("general", "generate_sha256_hashes", True):
            for backup_path in backup_paths:
                checksum = hash_file(backup_path)
                report["tasks"].append({"task": "Folder Hash", "status": "Completed", "detail": f"{backup_path.name}: {checksum}"})

    def cleanup_logs(self, report: dict[str, Any], progress_callback: TaskCallback | None, stop_check: StopCheck | None) -> None:
        self.logger.info("Starting cleanup phase.")
        if self._should_stop(stop_check):
            raise RuntimeError("Checklist stopped by user.")
        if not self.config_manager.get("cleanup", "enabled", False):
            self.logger.info("Cleanup skipped by configuration.")
            report["tasks"].append({"task": "Cleanup", "status": "Skipped", "detail": "Disabled in configuration"})
            return

        tasks = self.config_manager.get("cleanup", "tasks", [])
        results = self.cleanup_manager.cleanup_tasks(tasks)
        if not results:
            self.logger.info("No enabled cleanup tasks found.")
            report["tasks"].append({"task": "Cleanup", "status": "Skipped", "detail": "No enabled cleanup tasks"})
            return

        deleted_files = sum(int(result.get("deleted_files", 0)) for result in results)
        partial_tasks = sum(result.get("status") == "Partial" for result in results)
        self.logger.info("Cleanup completed: %d files deleted across %d tasks.", deleted_files, len(results))
        report["cleanup_detail"] = results
        report["tasks"].append({
            "task": "Cleanup",
            "status": "Partial" if partial_tasks else "Completed",
            "detail": f"{deleted_files} files deleted across {len(results)} tasks",
        })
        self._update_progress(progress_callback, 8, "Log cleanup completed")

    def generate_report(self, report: dict[str, Any]) -> None:
        report_path = get_app_dir() / "reports"
        report_path.mkdir(parents=True, exist_ok=True)
        report_file = report_path / f"checklist_report_{datetime.now():Y%m%d_%H%M%S}.json"
        with report_file.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=4)
        self.logger.info("Execution report generated: %s", report_file)
        # Also generate an Excel summary if openpyxl is available
        excel_file = report_path / f"checklist_report_{datetime.now():Y%m%d_%H%M%S}.xlsx"
        try:
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.title = "Summary"
            ws.append(["Server", "Execution Date", "Status", "Duration", "Backup Size"])
            ws.append([
                report.get("server_name", ""),
                report.get("execution_date", ""),
                report.get("status", ""),
                report.get("duration", ""),
                report.get("backup_size", ""),
            ])

            if "iis_apps" in report:
                ws2 = wb.create_sheet("IIS Child Apps")
                ws2.append(["Site", "Path", "Physical Path", "Application Pool", "Backup Status", "Archive", "Details"])
                app_results = {
                    (item.get("site", ""), item.get("path", "")): item
                    for item in report.get("iis_apps_detail", [])
                }
                for app in report["iis_apps"]:
                    result = app_results.get((app.get("site", ""), app.get("path", "")), {})
                    ws2.append([
                        app.get("site", ""),
                        app.get("path", ""),
                        app.get("physical_path", ""),
                        app.get("application_pool", ""),
                        result.get("status", ""),
                        result.get("archive", ""),
                        result.get("detail", ""),
                    ])

            if "iis_pools" in report:
                ws3 = wb.create_sheet("App Pools")
                ws3.append(["Pool Name", "State"])
                for pool in report["iis_pools"]:
                    ws3.append([pool.get("name", ""), pool.get("state", "")])

            if "services_detail" in report:
                ws4 = wb.create_sheet("Services")
                ws4.append(["Name", "Display Name", "Path", "Archive Status", "Runtime State", "Archive", "Details"])
                for svc in report["services_detail"]:
                    ws4.append([
                        svc.get("name", ""), svc.get("display_name", ""), svc.get("path", ""),
                        svc.get("status", ""), svc.get("runtime_status", ""), svc.get("archive", ""),
                        svc.get("detail", ""),
                    ])

            if "databases" in report:
                ws5 = wb.create_sheet("Databases")
                ws5.append(["Database", "File", "Compression", "SHA256"])
                for db in report["databases"]:
                    ws5.append([db.get("database", ""), db.get("file", ""), db.get("compression", ""), db.get("sha256", "")])

            if "cleanup_detail" in report:
                ws6 = wb.create_sheet("Cleanup")
                ws6.append(["Task", "Path", "Status", "Deleted Files", "Failed Files", "Detail"])
                for task in report["cleanup_detail"]:
                    ws6.append([
                        task.get("name", ""),
                        task.get("path", ""),
                        task.get("status", ""),
                        task.get("deleted_files", 0),
                        task.get("failed_files", 0),
                        task.get("detail", ""),
                    ])

            wb.save(excel_file)
            self.logger.info("Excel report generated: %s", excel_file)
        except Exception as e:
            # Fallback: write CSVs for key parts
            self.logger.warning("Could not generate Excel report: %s; falling back to CSVs.", e)
            try:
                import csv

                if "services_detail" in report:
                    csvf = report_path / f"services_{datetime.now():Y%m%d_%H%M%S}.csv"
                    with csvf.open("w", newline="", encoding="utf-8") as fh:
                        writer = csv.writer(fh)
                        writer.writerow(["Name", "Display Name", "Path", "Archive Status", "Runtime State", "Archive"])
                        for svc in report["services_detail"]:
                            writer.writerow([
                                svc.get("name", ""), svc.get("display_name", ""), svc.get("path", ""),
                                svc.get("status", ""), svc.get("runtime_status", ""), svc.get("archive", ""),
                            ])
                if "databases" in report:
                    csvf = report_path / f"databases_{datetime.now():Y%m%d_%H%M%S}.csv"
                    with csvf.open("w", newline="", encoding="utf-8") as fh:
                        writer = csv.writer(fh)
                        writer.writerow(["Database", "File", "Compression", "SHA256"])
                        for db in report["databases"]:
                            writer.writerow([db.get("database", ""), db.get("file", ""), db.get("compression", ""), db.get("sha256", "")])
            except Exception:
                self.logger.exception("Failed to write fallback CSV reports.")

    def _compute_directory_stats(self, root_path: Path) -> tuple[int, int]:
        total_bytes = 0
        total_files = 0
        for path in root_path.rglob("*"):
            if path.is_file() and path.name != ".supportassistant_incomplete.json":
                try:
                    total_bytes += path.stat().st_size
                    total_files += 1
                except OSError:
                    continue
        return total_bytes, total_files

    def _is_path_within(self, path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except (OSError, ValueError):
            return False

    def _get_backup_root(self) -> Path:
        # If an automated root was created for this run, prefer it
        if hasattr(self, "_automated_root") and self._automated_root:
            return Path(self._automated_root)
        root_value = str(self.config_manager.get("general", "backup_root_folder", ""))
        if not root_value:
            raise ValueError("Backup root folder is not configured.")
        backup_root = Path(root_value).expanduser()
        backup_root.mkdir(parents=True, exist_ok=True)
        return backup_root
