from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QColor, QCloseEvent, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QHeaderView, QLabel, QMainWindow, QMessageBox, QTableWidget,
    QTabWidget, QVBoxLayout, QWidget,
)

from gui.dashboard_tab import DashboardTab
from gui.config_tab import ConfigTab
from gui.history_tab import HistoryTab
from gui.logs_tab import LogsTab
from gui.operations_tab import OperationsTab
from gui.script_scheduler_tab import ScriptSchedulerTab
from gui.about_tab import AboutTab
from gui.startup_guide import StartupGuide
from modules.checklist_worker import ChecklistWorker
from modules.checklist_runner import ChecklistRunner
from modules.cleanup_worker import CleanupOnlyWorker
from utils.config_manager import ConfigManager
from utils.duration import format_duration_value
from utils.history_manager import HistoryManager
from utils.logger import export_logs_to_csv, get_recent_logs
from utils.paths import get_app_dir, get_resource_path
from version import COPYRIGHT, __version__


class MainWindow(QMainWindow):
    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager
        self.history_manager = HistoryManager(get_app_dir())
        self.checklist_thread: QThread | None = None
        self.checklist_worker: ChecklistWorker | None = None
        self.cleanup_only_thread: QThread | None = None
        self.cleanup_only_worker: CleanupOnlyWorker | None = None
        self.logs_refresh_timer = QTimer(self)
        self._refreshing_logs = False
        self.tray_icon = None
        self._exit_requested = False
        self._shutdown_complete = False
        self.setWindowTitle(f"MiChecklist v{__version__}")
        self.setWindowIcon(QIcon(str(get_resource_path("michecklist.ico"))))
        self.resize(1200, 820)

        self.apply_dark_theme()

        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 8)
        self.product_header = QLabel(f"MiChecklist  •  Version {__version__}")
        self.product_header.setObjectName("productHeader")
        central_layout.addWidget(self.product_header)
        self.tabs = QTabWidget(central)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.dashboard_tab = DashboardTab(self.config_manager)
        self.config_tab = ConfigTab(self.config_manager)
        self.logs_tab = LogsTab(self.config_manager)
        self.history_tab = HistoryTab(self.config_manager)
        self.operations_tab = OperationsTab()
        self.script_scheduler_tab = ScriptSchedulerTab()
        self.about_tab = AboutTab()

        self.tabs.addTab(self.dashboard_tab, "Checklist Dashboard")
        self.tabs.addTab(self.config_tab, "Checklist Configuration")
        self.tabs.addTab(self.operations_tab, "Operations")
        self.tabs.addTab(self.script_scheduler_tab, "Script Scheduler")
        self.tabs.addTab(self.logs_tab, "Checklist Logs")
        self.tabs.addTab(self.history_tab, "Backup History")
        self.tabs.addTab(self.about_tab, "About")

        self.statusBar().showMessage(f"{COPYRIGHT}  |  MiChecklist v{__version__}")

        self.configure_tables()
        self.attach_signals()
        self.update_logs_refresh_timer()
        self.refresh_logs()
        self.refresh_history()
        if not bool(self.config_manager.get("general", "setup_completed", False)):
            QTimer.singleShot(0, self.show_startup_guide)

    def show_startup_guide(self) -> None:
        guide = StartupGuide(self.config_manager, self)
        if guide.exec():
            self.config_tab.load_settings()
            self.tabs.setCurrentWidget(self.config_tab)

    def configure_tables(self) -> None:
        """Apply consistent table behavior and fill available width initially."""
        for table in self.findChildren(QTableWidget):
            table.setAlternatingRowColors(True)
            header = table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.Interactive)
            header.setMinimumSectionSize(60)
            header.setHighlightSections(False)
        QTimer.singleShot(0, self.stretch_table_columns)

    def stretch_table_columns(self) -> None:
        """Distribute initial column widths evenly without disabling resizing."""
        for table in self.findChildren(QTableWidget):
            column_count = table.columnCount()
            if not column_count:
                continue
            column_width = max(60, table.viewport().width() // column_count)
            for column in range(column_count):
                table.setColumnWidth(column, column_width)

    def attach_signals(self) -> None:
        self.dashboard_tab.start_button.clicked.connect(self.start_checklist)
        self.dashboard_tab.cleanup_only_button.clicked.connect(self.start_cleanup_only)
        self.dashboard_tab.stop_button.clicked.connect(self.stop_checklist)
        self.config_tab.cleanup_only_requested.connect(self.start_cleanup_only)
        self.logs_tab.export_button.clicked.connect(self.on_export_logs)
        self.logs_tab.refresh_requested.connect(self.refresh_logs)
        self.logs_tab.auto_refresh_checkbox.toggled.connect(self.update_logs_refresh_timer)
        self.logs_tab.refresh_interval_input.valueChanged.connect(self.update_logs_refresh_timer)
        self.logs_refresh_timer.timeout.connect(self.refresh_logs)

    def apply_dark_theme(self) -> None:
        palette = QPalette()
        base_color = QColor(24, 26, 32)
        text_color = QColor(232, 234, 240)
        highlight_color = QColor(113, 75, 103)

        palette.setColor(QPalette.Window, base_color)
        palette.setColor(QPalette.WindowText, text_color)
        palette.setColor(QPalette.Base, QColor(31, 34, 42))
        palette.setColor(QPalette.AlternateBase, QColor(37, 40, 49))
        palette.setColor(QPalette.ToolTipBase, text_color)
        palette.setColor(QPalette.ToolTipText, text_color)
        palette.setColor(QPalette.Text, text_color)
        palette.setColor(QPalette.Button, QColor(45, 48, 58))
        palette.setColor(QPalette.ButtonText, text_color)
        palette.setColor(QPalette.Highlight, highlight_color)
        palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))

        self.setPalette(palette)
        self.setStyleSheet(
            """
            QWidget {
                color: #d7dce5;
                font-family: "Segoe UI";
                font-size: 9pt;
                background: transparent;
            }
            QMainWindow {
                background-color: #191c23;
            }
            QLabel {
                background: transparent;
                color: #d7dce5;
            }
            QTabWidget::pane {
                border: 1px solid #353b47;
                border-radius: 5px;
                background: #20242c;
            }
            QTabBar::tab {
                background: #242932;
                color: #aeb7c6;
                border: none;
                border-right: 1px solid #353b47;
                border-top: 1px solid transparent;
                padding: 7px 13px;
                min-width: 72px;
            }
            QTabBar::tab:hover {
                color: #ffffff;
                background: #303744;
            }
            QTabBar::tab:selected {
                color: #ffffff;
                background: #20242c;
                border-top: 1px solid #b783a7;
            }
            QGroupBox, QFrame#statusCard {
                background: #252a34;
                border: 1px solid #39414f;
                border-radius: 6px;
                margin-top: 9px;
                padding: 9px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: #dcbdd5;
                font-weight: 600;
            }
            QPushButton {
                background: #343b47;
                border: 1px solid #475264;
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 18px;
            }
            QPushButton:hover {
                background: #414b5b;
            }
            QPushButton:pressed {
                background: #2b313c;
            }
            QPushButton:disabled {
                color: #7f8794;
                background: #292e37;
            }
            QPushButton#startChecklistButton {
                background: #714b67;
                border-color: #8e6382;
                font-weight: 600;
            }
            QPushButton#startChecklistButton:hover {
                background: #855c7a;
            }
            QPushButton#activateChecklistButton {
                background: #356b52;
                border-color: #4f8a6d;
                font-weight: 600;
                min-height: 26px;
            }
            QPushButton#activateChecklistButton:hover {
                background: #417e62;
            }
            QPushButton#stopChecklistButton {
                background: #8b3e4a;
                border-color: #a65360;
            }
            QLineEdit, QComboBox, QSpinBox, QTableWidget {
                background: #1f242d;
                border: 1px solid #3d4655;
                border-radius: 4px;
                padding: 4px;
                selection-background-color: #714b67;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTableWidget:focus {
                border-color: #8e6382;
            }
            QTableWidget {
                alternate-background-color: #242a34;
                gridline-color: #303846;
            }
            QHeaderView::section {
                background: #2b313c;
                color: #d7dce5;
                border: 0;
                border-right: 1px solid #414b5b;
                border-bottom: 1px solid #414b5b;
                padding: 5px 7px;
                font-weight: 600;
            }
            QProgressBar {
                background: #252a34;
                border: 1px solid #3d4655;
                border-radius: 4px;
                text-align: center;
                min-height: 15px;
            }
            QProgressBar::chunk {
                background: #714b67;
                border-radius: 1px;
            }
            QLabel#pageTitle {
                color: #ffffff;
                font-size: 16pt;
                font-weight: 600;
            }
            QLabel#productHeader {
                color: #ffffff;
                font-size: 14pt;
                font-weight: 700;
                padding: 7px 10px;
                background: #202733;
                border: 1px solid #39485b;
                border-radius: 6px;
            }
            QLabel#pageSubtitle, QLabel#cardCaption {
                color: #a9aeba;
            }
            QLabel#cardValue {
                color: #ffffff;
                font-size: 11pt;
                font-weight: 600;
            }
            QCheckBox {
                spacing: 5px;
            }
            """
        )

    def start_checklist(self) -> None:
        if self._operation_running():
            return

        errors, warnings = ChecklistRunner(self.config_manager).get_preflight_messages()
        if errors:
            QMessageBox.warning(
                self,
                "Configuration Required",
                "\n".join(errors),
            )
            self.tabs.setCurrentWidget(self.config_tab)
            return

        if warnings:
            warning_text = "\n".join(f"- {warning}" for warning in warnings)
            response = QMessageBox.question(
                self,
                "Checklist Preflight Warning",
                f"{warning_text}\n\nContinue with the remaining backup tasks?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if response != QMessageBox.Yes:
                return

        self.checklist_thread = QThread(self)
        self.checklist_worker = ChecklistWorker(self.config_manager)
        self.checklist_worker.moveToThread(self.checklist_thread)

        self.checklist_worker.progress_changed.connect(self.on_progress_changed)
        self.checklist_worker.finished.connect(self.on_checklist_finished)
        self.checklist_worker.failed.connect(self.on_checklist_failed)
        self.checklist_thread.started.connect(self.checklist_worker.run)
        self.checklist_worker.finished.connect(self.checklist_thread.quit)
        self.checklist_worker.failed.connect(self.checklist_thread.quit)
        self.checklist_thread.finished.connect(self.cleanup_thread)

        self.dashboard_tab.start_button.setEnabled(False)
        self.dashboard_tab.cleanup_only_button.setEnabled(False)
        self.config_tab.run_cleanup_button.setEnabled(False)
        self.dashboard_tab.stop_button.setEnabled(True)
        self.dashboard_tab.update_status("Pending", "Pending", "Pending", "Pending", "Pending")
        self.dashboard_tab.update_progress(0, "Starting checklist...")

        self.checklist_thread.start()

    def stop_checklist(self) -> None:
        if self.checklist_worker:
            self.checklist_worker.request_stop()
        if self.cleanup_only_worker:
            self.cleanup_only_worker.request_stop()
        self.dashboard_tab.update_progress(self.dashboard_tab.progress_bar.value(), "Stopping...")

    def start_cleanup_only(self) -> None:
        if self._operation_running():
            return
        if not self.config_manager.get("cleanup", "enabled", False):
            QMessageBox.warning(self, "Cleanup Required", "Enable cleanup tasks before running cleanup only.")
            self.tabs.setCurrentWidget(self.config_tab)
            return
        if not any(task.get("enabled", True) for task in self.config_manager.get("cleanup", "tasks", [])):
            QMessageBox.warning(self, "Cleanup Required", "Configure at least one enabled cleanup task.")
            self.tabs.setCurrentWidget(self.config_tab)
            return

        self.cleanup_only_thread = QThread(self)
        self.cleanup_only_worker = CleanupOnlyWorker(self.config_manager)
        self.cleanup_only_worker.moveToThread(self.cleanup_only_thread)
        self.cleanup_only_worker.progress_changed.connect(self.on_progress_changed)
        self.cleanup_only_worker.finished.connect(self.on_cleanup_only_finished)
        self.cleanup_only_worker.failed.connect(self.on_cleanup_only_failed)
        self.cleanup_only_thread.started.connect(self.cleanup_only_worker.run)
        self.cleanup_only_worker.finished.connect(self.cleanup_only_thread.quit)
        self.cleanup_only_worker.failed.connect(self.cleanup_only_thread.quit)
        self.cleanup_only_thread.finished.connect(self.cleanup_cleanup_only_thread)

        self.dashboard_tab.start_button.setEnabled(False)
        self.dashboard_tab.cleanup_only_button.setEnabled(False)
        self.config_tab.run_cleanup_button.setEnabled(False)
        self.dashboard_tab.stop_button.setEnabled(True)
        self.dashboard_tab.cleanup_status_label.setText("Running")
        self.dashboard_tab.update_progress(0, "Starting cleanup only...")
        self.cleanup_only_thread.start()

    def on_cleanup_only_finished(self, result: dict[str, Any]) -> None:
        self.dashboard_tab.update_progress(100, "Cleanup only completed")
        cleanup_task = next(
            (task for task in result.get("tasks", []) if task.get("task") == "Cleanup"),
            {},
        )
        self.dashboard_tab.cleanup_status_label.setText(cleanup_task.get("status", "Completed"))
        self.refresh_logs()
        QMessageBox.information(self, "Cleanup Completed", "Cleanup-only execution has completed.")

    def on_cleanup_only_failed(self, error: str) -> None:
        self.dashboard_tab.update_progress(0, "Cleanup only failed")
        self.dashboard_tab.cleanup_status_label.setText("Failed")
        self.refresh_logs()
        QMessageBox.critical(self, "Cleanup Failed", f"Cleanup-only execution failed: {error}")

    def on_progress_changed(self, value: int, task: str) -> None:
        self.dashboard_tab.update_progress(value, task)

    def on_checklist_finished(self, result: dict[str, Any]) -> None:
        self.dashboard_tab.update_progress(100, "Checklist completed")
        self.update_dashboard_statuses(result)
        self.history_manager.add_record(
            {
                "date": result.get("execution_date", ""),
                "duration": result.get("duration", ""),
                "status": result.get("status", ""),
                "backup_size": result.get("backup_size", "N/A"),
            }
        )
        self.refresh_history()
        self.refresh_logs()
        QMessageBox.information(self, "Checklist Completed", "Support checklist execution has completed.")

    def on_checklist_failed(self, error: str) -> None:
        self.dashboard_tab.update_progress(0, "Checklist failed")
        self.dashboard_tab.update_status("Failed", "Failed", "Failed", "Failed", "Failed")
        self.refresh_logs()
        QMessageBox.critical(self, "Checklist Failed", f"Execution failed: {error}")

    def update_dashboard_statuses(self, result: dict[str, Any]) -> None:
        statuses = {"Cleanup": "Skipped", "IIS": "Skipped", "Services": "Skipped", "Database": "Skipped", "Folders": "Skipped"}
        for task in result.get("tasks", []):
            name = task.get("task")
            if name in statuses:
                statuses[name] = task.get("status", statuses[name])
        self.dashboard_tab.update_status(
            statuses["Cleanup"],
            statuses["IIS"],
            statuses["Services"],
            statuses["Database"],
            statuses["Folders"],
        )

    def cleanup_thread(self) -> None:
        self.dashboard_tab.start_button.setEnabled(True)
        self.dashboard_tab.cleanup_only_button.setEnabled(True)
        self.config_tab.run_cleanup_button.setEnabled(self.config_tab.cleanup_enabled_checkbox.isChecked())
        self.dashboard_tab.stop_button.setEnabled(False)
        self.checklist_worker = None
        self.checklist_thread = None

    def cleanup_cleanup_only_thread(self) -> None:
        self.dashboard_tab.start_button.setEnabled(True)
        self.dashboard_tab.cleanup_only_button.setEnabled(True)
        self.config_tab.run_cleanup_button.setEnabled(self.config_tab.cleanup_enabled_checkbox.isChecked())
        self.dashboard_tab.stop_button.setEnabled(False)
        self.cleanup_only_worker = None
        self.cleanup_only_thread = None

    def _operation_running(self) -> bool:
        return bool(
            (self.checklist_thread and self.checklist_thread.isRunning())
            or (self.cleanup_only_thread and self.cleanup_only_thread.isRunning())
        )

    def refresh_logs(self) -> None:
        if self._refreshing_logs:
            return
        self._refreshing_logs = True
        try:
            self.logs_tab.load_logs(get_recent_logs(log_date=self.logs_tab.selected_date()))
        finally:
            self._refreshing_logs = False

    def update_logs_refresh_timer(self) -> None:
        if self.logs_tab.auto_refresh_checkbox.isChecked():
            self.logs_refresh_timer.start(self.logs_tab.refresh_interval_input.value() * 1000)
        else:
            self.logs_refresh_timer.stop()

    def refresh_history(self) -> None:
        self.history_tab.load_history(self.history_manager.load_history())
        self.refresh_last_backup()

    def refresh_last_backup(self) -> None:
        history = self.history_manager.load_history()
        last_successful = next((record for record in history if record.get("status") == "Completed"), None)
        if not last_successful:
            self.dashboard_tab.last_execution_label.setText("N/A")
            self.dashboard_tab.duration_label.setText("N/A")
            return

        raw_date = str(last_successful.get("date", "")).strip()
        try:
            display_date = datetime.fromisoformat(raw_date).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            display_date = raw_date or "N/A"
        self.dashboard_tab.last_execution_label.setText(display_date)
        self.dashboard_tab.duration_label.setText(format_duration_value(last_successful.get("duration", "N/A")))

    def show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._exit_requested:
            event.accept()
            return

        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Question)
        prompt.setWindowTitle("Close MiChecklist")
        message = "What would you like to do?"
        prompt.setText(message)
        tray_button = None
        hide_to_tray = bool(self.config_manager.get("schedule", "hide_to_tray", True))
        if self.tray_icon and hide_to_tray:
            tray_button = prompt.addButton("Minimize to Tray", QMessageBox.AcceptRole)
        exit_button = prompt.addButton("Exit MiChecklist", QMessageBox.DestructiveRole)
        prompt.setDefaultButton(exit_button)
        cancel_button = prompt.addButton(QMessageBox.Cancel)
        prompt.exec()

        if tray_button is not None and prompt.clickedButton() is tray_button:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "MiChecklist",
                "MiChecklist is still running in the notification area.",
            )
            return

        if prompt.clickedButton() is exit_button:
            self._exit_requested = True
            event.accept()
            QTimer.singleShot(0, QApplication.instance().quit)
            return

        event.ignore()

    def request_exit(self) -> None:
        """Confirm a full exit requested from the notification-area menu."""
        message = "Exit MiChecklist completely?"
        answer = QMessageBox.question(
            self,
            "Exit MiChecklist",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._exit_requested = True
            QApplication.instance().quit()

    def shutdown(self) -> None:
        """Release application resources during exit."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True

    def on_export_logs(self) -> None:
        selected_date = self.logs_tab.selected_date()
        path = get_app_dir() / "logs" / f"exported_logs_{selected_date:%Y-%m-%d}.csv"
        export_logs_to_csv(path, log_date=selected_date)
        QMessageBox.information(self, "Export Logs", f"Logs exported to: {path}")
