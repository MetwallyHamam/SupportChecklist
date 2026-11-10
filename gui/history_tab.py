from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from utils.config_manager import ConfigManager
from utils.duration import format_duration_value


class HistoryTab(QWidget):
    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager

        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Date", "Duration", "Status", "Backup Size"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

        self.layout.addWidget(self.history_table)

    def load_history(self, history_entries: list[dict[str, str]]) -> None:
        self.history_table.setRowCount(0)
        for entry in history_entries:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, QTableWidgetItem(entry.get("date", "")))
            self.history_table.setItem(row, 1, QTableWidgetItem(format_duration_value(entry.get("duration", ""))))
            self.history_table.setItem(row, 2, QTableWidgetItem(entry.get("status", "")))
            self.history_table.setItem(row, 3, QTableWidgetItem(entry.get("backup_size", "")))
