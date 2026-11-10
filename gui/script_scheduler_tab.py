from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from core.job_queue import PersistentJobQueue
from core.scheduled_jobs import ScheduledJobStore


class ScriptSchedulerTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.schedules = ScheduledJobStore()
        self.queue = PersistentJobQueue()
        self.name_input = QLineEdit()
        self.path_input = QLineEdit()
        self.path_input.setReadOnly(True)
        self.browse_button = QPushButton("Browse Script")
        self.frequency = QComboBox()
        self.frequency.addItem("Every N Minutes", "minutes")
        self.frequency.addItem("Daily", "daily")
        self.frequency.addItem("Weekly", "weekly")
        self.frequency.addItem("Monthly", "monthly")
        self.frequency.addItem("At Windows / Service Startup", "startup")
        self.time_input = QTimeEdit(QTime(2, 0))
        self.time_input.setDisplayFormat("HH:mm")
        self.weekday = QComboBox()
        self.weekday.addItems(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
        self.interval_minutes = QSpinBox()
        self.interval_minutes.setRange(1, 10080)
        self.interval_minutes.setValue(15)
        self.interval_minutes.setSuffix(" minutes")
        self.day_of_month = QSpinBox()
        self.day_of_month.setRange(1, 31)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 1440)
        self.timeout.setValue(60)
        self.timeout.setSuffix(" minutes")
        self.run_missed = QCheckBox("Run a missed occurrence when the server becomes available")
        self.run_missed.setChecked(True)
        self.add_button = QPushButton("Add Script Schedule")
        self.run_button = QPushButton("Run Selected Now")
        self.toggle_button = QPushButton("Enable / Disable Selected")
        self.delete_button = QPushButton("Delete Selected")
        self.refresh_button = QPushButton("Refresh")
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "ID", "Enabled", "Name", "Script", "Frequency", "Timing", "Last queued", "Last run",
        ])
        self._build_ui()
        self.browse_button.clicked.connect(self.browse_script)
        self.frequency.currentIndexChanged.connect(self.update_fields)
        self.add_button.clicked.connect(self.add_schedule)
        self.run_button.clicked.connect(self.run_selected)
        self.toggle_button.clicked.connect(self.toggle_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.refresh_button.clicked.connect(self.refresh)
        self.update_fields()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Run PowerShell, CMD, or BAT cleanup scripts through the Windows service. "
            "The service account must have permission to read the script and access every target it modifies."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form_group = QGroupBox("New Script Schedule")
        form = QFormLayout(form_group)
        form.addRow("Schedule name:", self.name_input)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_input)
        path_row.addWidget(self.browse_button)
        form.addRow("Script file:", path_row)
        form.addRow("Frequency:", self.frequency)
        form.addRow("Run time:", self.time_input)
        form.addRow("Weekday:", self.weekday)
        form.addRow("Day of month:", self.day_of_month)
        form.addRow("Interval:", self.interval_minutes)
        form.addRow("Execution timeout:", self.timeout)
        form.addRow(self.run_missed)
        form.addRow(self.add_button)
        layout.addWidget(form_group)
        schedules_group = QGroupBox("Saved Script Schedules")
        schedules_layout = QVBoxLayout(schedules_group)
        schedules_layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for button in (self.run_button, self.toggle_button, self.delete_button, self.refresh_button):
            buttons.addWidget(button)
        schedules_layout.addLayout(buttons)
        layout.addWidget(schedules_group)

    def browse_script(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Select Cleanup Script", str(Path.home()),
            "Supported scripts (*.ps1 *.cmd *.bat);;PowerShell (*.ps1);;Command scripts (*.cmd *.bat)",
        )
        if selected:
            self.path_input.setText(str(Path(selected).resolve()))
            if not self.name_input.text().strip():
                self.name_input.setText(Path(selected).stem)

    def update_fields(self) -> None:
        mode = self.frequency.currentData()
        self.interval_minutes.setVisible(mode == "minutes")
        self.time_input.setVisible(mode not in {"minutes", "startup"})
        self.weekday.setVisible(mode == "weekly")
        self.day_of_month.setVisible(mode == "monthly")

    def add_schedule(self) -> None:
        name = self.name_input.text().strip()
        path = Path(self.path_input.text().strip())
        if not name:
            QMessageBox.warning(self, "Script schedule", "Enter a schedule name.")
            return
        if not path.is_absolute() or not path.is_file() or path.suffix.casefold() not in {".ps1", ".cmd", ".bat"}:
            QMessageBox.warning(self, "Script schedule", "Select an existing .ps1, .cmd, or .bat file.")
            return
        schedule_id = self.schedules.add(name, "script", {
            "path": str(path), "timeout_minutes": self.timeout.value(),
        }, {
            "frequency": self.frequency.currentData(),
            "time": self.time_input.time().toString("HH:mm"),
            "weekdays": [self.weekday.currentText()],
            "day_of_month": self.day_of_month.value(),
            "interval_minutes": self.interval_minutes.value(),
            "run_missed": self.run_missed.isChecked(),
        })
        self.refresh()
        QMessageBox.information(self, "Script scheduled", f"Schedule created: {schedule_id[:12]}")

    def _selected(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        short_id = self.table.item(row, 0).text()
        return next((item for item in self.schedules.load() if str(item.get("id", "")).startswith(short_id)), None)

    def run_selected(self) -> None:
        item = self._selected()
        if not item:
            QMessageBox.warning(self, "Run script", "Select a script schedule first.")
            return
        payload = dict(item.get("payload", {}))
        payload.update({"schedule_id": item["id"], "manual": True})
        job_id = self.queue.enqueue("script", payload)
        QMessageBox.information(self, "Script queued", f"Script job queued: {job_id[:12]}")

    def toggle_selected(self) -> None:
        selected = self._selected()
        if not selected:
            QMessageBox.warning(self, "Script schedule", "Select a schedule first.")
            return
        schedules = self.schedules.load()
        for item in schedules:
            if item.get("id") == selected.get("id"):
                item["enabled"] = not bool(item.get("enabled", True))
                break
        self.schedules.save(schedules)
        self.refresh()

    def delete_selected(self) -> None:
        selected = self._selected()
        if not selected:
            QMessageBox.warning(self, "Script schedule", "Select a schedule first.")
            return
        if QMessageBox.question(self, "Delete schedule", f"Delete {selected.get('name', 'this schedule')}?") == QMessageBox.Yes:
            self.schedules.delete(str(selected["id"]))
            self.refresh()

    def refresh(self) -> None:
        items = [item for item in self.schedules.load() if item.get("job_type") == "script"]
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            frequency = str(item.get("frequency", "daily"))
            if frequency == "minutes":
                timing = f"Every {item.get('interval_minutes', 15)} min"
            elif frequency == "startup":
                timing = "At service startup"
            elif frequency == "weekly":
                timing = f"{', '.join(item.get('weekdays', []))} {item.get('time', '')}"
            elif frequency == "monthly":
                timing = f"Day {item.get('day_of_month', 1)} at {item.get('time', '')}"
            else:
                timing = str(item.get("time", ""))
            values = [
                str(item.get("id", ""))[:12], "Yes" if item.get("enabled", True) else "No",
                str(item.get("name", "")), str(item.get("payload", {}).get("path", "")),
                frequency.title(), timing, str(item.get("last_enqueued_for", "")),
                str(item.get("last_run", "")),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
