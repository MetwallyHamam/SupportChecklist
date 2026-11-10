from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog,
)

from core.job_queue import PersistentJobQueue
from core.restore_manager import RestoreManager
from core.scheduled_jobs import ScheduledJobStore
from core.windows_service_manager import WindowsServiceManager
from utils.config_manager import ConfigManager
from utils.paths import get_app_dir


class OperationsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.queue = PersistentJobQueue()
        self.restore_manager = RestoreManager()
        self.schedules = ScheduledJobStore()
        self.service_manager = WindowsServiceManager()
        self.health_label = QLabel("Service health has not been checked")
        self.health_label.setWordWrap(True)
        self.service_status_label = QLabel("Windows service status has not been checked")
        self.service_status_label.setWordWrap(True)
        self.install_service_button = QPushButton("Install Service")
        self.start_service_button = QPushButton("Start")
        self.stop_service_button = QPushButton("Stop")
        self.remove_service_button = QPushButton("Remove Service")
        self.queue_table = QTableWidget(0, 6)
        self.queue_table.setHorizontalHeaderLabels([
            "ID", "Type", "Status", "Attempts", "Updated", "Last error",
        ])
        self.refresh_button = QPushButton("Refresh")
        self.enqueue_button = QPushButton("Queue Full Checklist")
        self.restore_button = QPushButton("Restore Backup")
        self.retry_button = QPushButton("Retry Selected Failed Job")
        self.schedule_table = QTableWidget(0, 6)
        self.schedule_table.setHorizontalHeaderLabels([
            "ID", "Name", "Job type", "Frequency", "Time", "Last queued occurrence",
        ])
        self.add_schedule_button = QPushButton("Add Scheduled Job")
        self.delete_schedule_button = QPushButton("Delete Selected Schedule")
        self.build_ui()
        self.refresh_button.clicked.connect(self.refresh)
        self.enqueue_button.clicked.connect(self.enqueue_checklist)
        self.restore_button.clicked.connect(self.restore_backup)
        self.retry_button.clicked.connect(self.retry_selected)
        self.add_schedule_button.clicked.connect(self.add_schedule)
        self.delete_schedule_button.clicked.connect(self.delete_schedule)
        self.install_service_button.clicked.connect(self.install_service)
        self.start_service_button.clicked.connect(self.start_service)
        self.stop_service_button.clicked.connect(self.stop_service)
        self.remove_service_button.clicked.connect(self.remove_service)
        self.refresh()

    def build_ui(self):
        layout = QVBoxLayout(self)
        health = QGroupBox("Windows Service Health")
        health_layout = QVBoxLayout(health)
        health_layout.addWidget(self.health_label)
        health_layout.addWidget(self.service_status_label)
        service_buttons = QHBoxLayout()
        service_buttons.addWidget(self.install_service_button)
        service_buttons.addWidget(self.start_service_button)
        service_buttons.addWidget(self.stop_service_button)
        service_buttons.addWidget(self.remove_service_button)
        health_layout.addLayout(service_buttons)
        health_layout.addWidget(self.refresh_button)
        queue = QGroupBox("Persistent Job Queue")
        queue_layout = QVBoxLayout(queue)
        queue_layout.addWidget(self.queue_table)
        queue_layout.addWidget(self.enqueue_button)
        queue_layout.addWidget(self.retry_button)
        restore = QGroupBox("Restore")
        restore_layout = QVBoxLayout(restore)
        restore_layout.addWidget(QLabel(
            "Select a backup directory, file, or ZIP. The backup is validated and restored to a separate destination."
        ))
        restore_layout.addWidget(self.restore_button)
        schedules = QGroupBox("Independent Scheduled Jobs")
        schedules_layout = QVBoxLayout(schedules)
        schedules_layout.addWidget(QLabel(
            "Schedule checklist database backups or configured cleanup jobs independently."
        ))
        schedules_layout.addWidget(self.schedule_table)
        schedule_buttons = QHBoxLayout()
        schedule_buttons.addWidget(self.add_schedule_button)
        schedule_buttons.addWidget(self.delete_schedule_button)
        schedules_layout.addLayout(schedule_buttons)
        layout.addWidget(health)
        layout.addWidget(schedules)
        layout.addWidget(queue)
        layout.addWidget(restore)

    def refresh(self):
        try:
            service_status = self.service_manager.status()
            self.service_status_label.setText(
                f"Installed: {service_status['installed']} | Windows state: {service_status['state']}"
            )
        except Exception as exc:
            self.service_status_label.setText(f"Unable to query Windows service: {exc}")
        health_path = get_app_dir() / "service" / "health.json"
        if health_path.exists():
            try:
                health = json.loads(health_path.read_text(encoding="utf-8"))
                self.health_label.setText(
                    f"State: {health.get('state', 'unknown')} | "
                    f"Heartbeat: {health.get('heartbeat', '-')} | "
                    f"Job: {health.get('job_id') or '-'} | "
                    f"Running: {int(health.get('job_elapsed_seconds', 0)) // 60} min | "
                    f"Error: {health.get('last_error') or '-'} | "
                    f"Startup recovery: {health.get('last_startup_recovery') or '-'}"
                )
            except (OSError, json.JSONDecodeError) as exc:
                self.health_label.setText(f"Health file is invalid: {exc}")
        else:
            self.health_label.setText("No heartbeat found. The Windows service may not be installed or running.")
        try:
            jobs = list(reversed(self.queue.load()))
        except RuntimeError as exc:
            self.health_label.setText(str(exc))
            jobs = []
        self.queue_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = [
                str(job.get("id", ""))[:12], str(job.get("type", "")),
                str(job.get("status", "")), str(job.get("attempts", 0)),
                str(job.get("updated_at", "")), str(job.get("last_error", "")),
            ]
            for column, value in enumerate(values):
                self.queue_table.setItem(row, column, QTableWidgetItem(value))
        scheduled_jobs = self.schedules.load()
        self.schedule_table.setRowCount(len(scheduled_jobs))
        for row, item in enumerate(scheduled_jobs):
            frequency = str(item.get("frequency", ""))
            timing = "At service startup" if frequency == "startup" else str(item.get("time", ""))
            values = [
                str(item.get("id", ""))[:12], str(item.get("name", "")),
                str(item.get("job_type", "")), frequency,
                timing, str(item.get("last_enqueued_for", "")),
            ]
            for column, value in enumerate(values):
                self.schedule_table.setItem(row, column, QTableWidgetItem(value))

    def enqueue_checklist(self):
        job_id = self.queue.enqueue("checklist", {"include_all": True, "manual": True})
        self.refresh()
        QMessageBox.information(self, "Job queued", f"Checklist job queued: {job_id[:12]}")

    def _service_action(self, action, success_message):
        try:
            output = action()
            QMessageBox.information(self, "Windows service", success_message + (f"\n\n{output}" if output else ""))
        except Exception as exc:
            QMessageBox.critical(
                self, "Windows service",
                f"{exc}\n\nRun MiChecklist as Administrator and confirm the service executable is beside the application.",
            )
        self.refresh()

    def install_service(self):
        self._service_action(self.service_manager.install, "MiChecklist service installed with automatic startup.")

    def start_service(self):
        self._service_action(self.service_manager.start, "MiChecklist service started.")

    def stop_service(self):
        self._service_action(self.service_manager.stop, "MiChecklist service stopped.")

    def remove_service(self):
        answer = QMessageBox.question(
            self, "Remove Windows service",
            "Remove the MiChecklist Windows service? Saved schedules and queued jobs will be kept.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._service_action(self.service_manager.remove, "MiChecklist service removed.")

    def retry_selected(self):
        row = self.queue_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Retry job", "Select a failed or interrupted job first.")
            return
        short_id = self.queue_table.item(row, 0).text()
        job = next((item for item in self.queue.load() if str(item.get("id", "")).startswith(short_id)), None)
        if not job or not self.queue.retry(job["id"]):
            QMessageBox.warning(self, "Retry job", "Only failed or interrupted jobs can be retried.")
            return
        self.refresh()

    def add_schedule(self):
        kind, accepted = QInputDialog.getItem(
            self, "Scheduled job", "Job source",
            ["Configured database backup", "Existing cleanup job"], 0, False,
        )
        if not accepted:
            return
        if kind.startswith("Configured database"):
            config = ConfigManager()
            config.load_config()
            selected = list(config.get("sql_server", "databases", []) or [])
            fallback = str(config.get("sql_server", "database_name", "") or "").strip()
            if not selected and fallback:
                selected = [fallback]
            if not config.get("sql_server", "backup_enabled", True) or not selected:
                QMessageBox.warning(
                    self, "Scheduled job",
                    "Enable database backup and select at least one database in SQL Server settings first.",
                )
                return
            name = "Database backup"
            job_type = "database_backup"
            payload = {}
        else:
            config = ConfigManager()
            config.load_config()
            tasks = [task for task in config.get("cleanup", "tasks", []) if task.get("enabled", True)]
            if not tasks:
                QMessageBox.warning(self, "Scheduled job", "No enabled cleanup jobs are configured.")
                return
            labels = [str(task.get("name", task.get("path", "Cleanup"))) for task in tasks]
            name, accepted = QInputDialog.getItem(self, "Scheduled job", "Cleanup job", labels, 0, False)
            if not accepted:
                return
            task = tasks[labels.index(name)]
            age_days, accepted = QInputDialog.getInt(
                self, "Cleanup age", "Delete files older than this many days (0 = any age)", 30, 0, 3650,
            )
            if not accepted:
                return
            job_type = "cleanup"
            payload = {
                "path": task.get("path", ""), "extensions": task.get("extensions", []),
                "minimum_age_days": age_days, "keep_folders": True, "max_retries": 3,
            }
        frequency, accepted = QInputDialog.getItem(
            self, "Schedule", "Frequency", ["daily", "weekly", "monthly", "interval"], 0, False,
        )
        if not accepted:
            return
        run_time, accepted = QInputDialog.getText(self, "Schedule", "Start time (HH:mm)", text="02:00")
        if not accepted:
            return
        try:
            from datetime import datetime as _datetime
            _datetime.strptime(run_time, "%H:%M")
        except ValueError:
            QMessageBox.warning(self, "Schedule", "Enter time as HH:mm, for example 02:00.")
            return
        weekdays = []
        interval_days = 1
        day_of_month = 1
        if frequency == "weekly":
            day, accepted = QInputDialog.getItem(
                self, "Schedule", "Weekday",
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], 0, False,
            )
            if not accepted:
                return
            weekdays = [day]
        elif frequency == "interval":
            interval_days, accepted = QInputDialog.getInt(self, "Schedule", "Run every N days", 7, 1, 365)
            if not accepted:
                return
        elif frequency == "monthly":
            day_of_month, accepted = QInputDialog.getInt(
                self, "Schedule", "Day of month (29-31 uses the month's last day when needed)", 1, 1, 31,
            )
            if not accepted:
                return
        self.schedules.add(name, job_type, payload, {
            "frequency": frequency, "time": run_time,
            "weekdays": weekdays, "interval_days": interval_days,
            "day_of_month": day_of_month, "run_missed": True,
        })
        self.refresh()

    def delete_schedule(self):
        row = self.schedule_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Scheduled job", "Select a schedule first.")
            return
        short_id = self.schedule_table.item(row, 0).text()
        item = next((entry for entry in self.schedules.load() if entry.get("id", "").startswith(short_id)), None)
        if item:
            self.schedules.delete(item["id"])
        self.refresh()

    def restore_backup(self):
        backup = QFileDialog.getExistingDirectory(self, "Select backup directory")
        if not backup:
            backup, _ = QFileDialog.getOpenFileName(self, "Select backup file", filter="Backups (*.zip);;All files (*)")
        if not backup:
            return
        destination = QFileDialog.getExistingDirectory(self, "Select restore destination")
        if not destination:
            return
        preview = self.restore_manager.preview(Path(backup), Path(destination))
        if not preview["valid"]:
            QMessageBox.critical(self, "Restore validation failed", "\n".join(preview["errors"]))
            return
        answer = QMessageBox.question(
            self, "Confirm restore",
            f"Restore {preview['files']} files to:\n{destination}\n\nExisting files will be skipped.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = self.restore_manager.restore(Path(backup), Path(destination))
            QMessageBox.information(self, "Restore complete", f"Restored {result['restored_files']} files.")
        except Exception as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))
