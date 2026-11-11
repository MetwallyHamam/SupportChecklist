from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QFrame,
)

from utils.config_manager import ConfigManager


class DashboardTab(QWidget):
    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager

        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.current_task_label = QLabel("Current Task: Idle")
        self.progress_bar = QProgressBar()
        self.last_execution_label = QLabel("N/A")
        self.duration_label = QLabel("N/A")

        self.iis_status_label = QLabel("Pending")
        self.services_status_label = QLabel("Pending")
        self.database_status_label = QLabel("Pending")
        self.folder_status_label = QLabel("Pending")
        self.cleanup_status_label = QLabel("Pending")

        self.start_button = QPushButton("Start Checklist")
        self.cleanup_only_button = QPushButton("Run Cleanup Only")
        self.stop_button = QPushButton("Stop Checklist")

        self.build_ui()
        self.stop_button.setEnabled(False)

    def build_ui(self) -> None:
        from version import __version__
        title = QLabel(f"MiChecklist v{__version__}")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Run and monitor infrastructure backups from one workspace.")
        subtitle.setObjectName("pageSubtitle")

        status_layout = QGridLayout()
        status_layout.setSpacing(12)
        status_layout.addWidget(self.create_status_card("Cleanup", self.cleanup_status_label, "#d9822b"), 0, 0)
        status_layout.addWidget(self.create_status_card("IIS Backup", self.iis_status_label, "#4c9aff"), 0, 1)
        status_layout.addWidget(self.create_status_card("Windows Services", self.services_status_label, "#36b37e"), 1, 0)
        status_layout.addWidget(self.create_status_card("Databases", self.database_status_label, "#8777d9"), 1, 1)
        status_layout.addWidget(self.create_status_card("Folders", self.folder_status_label, "#de6f9b"), 2, 0)
        status_layout.addWidget(self.create_status_card("Last Successful Backup", self.last_execution_label, "#00b8d9"), 2, 1)
        status_layout.addWidget(self.create_status_card("Last Backup Duration", self.duration_label, "#ffab00"), 3, 0, 1, 2)

        control_layout = QHBoxLayout()
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.cleanup_only_button)
        control_layout.addWidget(self.stop_button)

        self.layout.setContentsMargins(18, 18, 18, 18)
        self.layout.setSpacing(14)
        self.layout.addWidget(title)
        self.layout.addWidget(subtitle)
        self.layout.addLayout(status_layout)
        self.layout.addWidget(self.current_task_label)
        self.layout.addWidget(self.progress_bar)
        self.layout.addLayout(control_layout)
        self.layout.addStretch()

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.start_button.setObjectName("startChecklistButton")
        self.stop_button.setObjectName("stopChecklistButton")

    def create_status_card(self, title: str, value_label: QLabel, accent_color: str) -> QFrame:
        card = QFrame()
        card.setObjectName("statusCard")
        card.setStyleSheet(f"QFrame#statusCard {{ border-left: 4px solid {accent_color}; }}")
        layout = QVBoxLayout(card)
        caption = QLabel(title)
        caption.setObjectName("cardCaption")
        value_label.setObjectName("cardValue")
        layout.addWidget(caption)
        layout.addWidget(value_label)
        return card

    def update_progress(self, value: int, task: str) -> None:
        self.progress_bar.setValue(value)
        self.current_task_label.setText(f"Current Task: {task}")

    def update_status(self, cleanup: str, iis: str, services: str, database: str, folders: str) -> None:
        self.cleanup_status_label.setText(cleanup)
        self.iis_status_label.setText(iis)
        self.services_status_label.setText(services)
        self.database_status_label.setText(database)
        self.folder_status_label.setText(folders)
