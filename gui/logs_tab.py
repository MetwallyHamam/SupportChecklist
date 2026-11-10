from __future__ import annotations

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QDateEdit,
)

from utils.config_manager import ConfigManager


class LogsTab(QWidget):
    refresh_requested = Signal()

    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager

        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(["Timestamp", "Module", "Status", "Message"])
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter message text...")
        self.date_filter = QDateEdit(QDate.currentDate())
        self.date_filter.setCalendarPopup(True)
        self.date_filter.setDisplayFormat("yyyy-MM-dd")
        self.level_filter = QComboBox()
        self.level_filter.addItems(["All Levels", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.module_filter = QComboBox()
        self.module_filter.addItem("All Modules")
        self.auto_refresh_checkbox = QCheckBox("Auto Refresh")
        self.auto_refresh_checkbox.setChecked(True)
        self.refresh_interval_input = QSpinBox()
        self.refresh_interval_input.setRange(1, 300)
        self.refresh_interval_input.setValue(5)
        self.refresh_interval_input.setSuffix(" sec")
        self.refresh_button = QPushButton("Refresh")
        self.export_button = QPushButton("Export to CSV")
        self._log_entries: list[dict[str, str]] = []

        self.build_ui()
        self.attach_signals()

    def build_ui(self) -> None:
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Date:"))
        filter_layout.addWidget(self.date_filter)
        filter_layout.addWidget(QLabel("Search:"))
        filter_layout.addWidget(self.search_input)
        filter_layout.addWidget(QLabel("Level:"))
        filter_layout.addWidget(self.level_filter)
        filter_layout.addWidget(QLabel("Module:"))
        filter_layout.addWidget(self.module_filter)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.auto_refresh_checkbox)
        button_layout.addWidget(QLabel("Interval:"))
        button_layout.addWidget(self.refresh_interval_input)
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch()
        button_layout.addWidget(self.export_button)

        self.layout.addLayout(filter_layout)
        self.layout.addWidget(self.log_table)
        self.layout.addLayout(button_layout)

    def attach_signals(self) -> None:
        self.search_input.textChanged.connect(self.apply_filters)
        self.level_filter.currentTextChanged.connect(self.apply_filters)
        self.module_filter.currentTextChanged.connect(self.apply_filters)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.date_filter.dateChanged.connect(self.refresh_requested)

    def selected_date(self):
        return self.date_filter.date().toPython()

    def load_logs(self, log_entries: list[dict[str, str]]) -> None:
        self._log_entries = log_entries
        current_module = self.module_filter.currentText()
        modules = sorted({entry.get("module", "") for entry in log_entries if entry.get("module")})
        self.module_filter.blockSignals(True)
        self.module_filter.clear()
        self.module_filter.addItem("All Modules")
        self.module_filter.addItems(modules)
        if current_module in modules:
            self.module_filter.setCurrentText(current_module)
        self.module_filter.blockSignals(False)
        self.apply_filters()

    def apply_filters(self) -> None:
        search_text = self.search_input.text().strip().lower()
        selected_level = self.level_filter.currentText()
        selected_module = self.module_filter.currentText()

        self.log_table.setUpdatesEnabled(False)
        try:
            self.log_table.setRowCount(0)
            for entry in self._log_entries:
                if selected_level != "All Levels" and entry.get("status") != selected_level:
                    continue
                if selected_module != "All Modules" and entry.get("module") != selected_module:
                    continue
                if search_text and search_text not in entry.get("message", "").lower():
                    continue
                row = self.log_table.rowCount()
                self.log_table.insertRow(row)
                self.log_table.setItem(row, 0, QTableWidgetItem(entry.get("timestamp", "")))
                self.log_table.setItem(row, 1, QTableWidgetItem(entry.get("module", "")))
                self.log_table.setItem(row, 2, QTableWidgetItem(entry.get("status", "")))
                self.log_table.setItem(row, 3, QTableWidgetItem(entry.get("message", "")))
        finally:
            self.log_table.setUpdatesEnabled(True)
