from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHeaderView, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QTimeEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWizard,
    QWizardPage, QWidget,
)

from core.windows_service_manager import WindowsServiceManager
from core.scheduled_jobs import ScheduledJobStore
from modules.checklist_runner import ChecklistRunner
from modules.cleanup_manager import detect_iis_cleanup_tasks, normalize_extensions
from modules.iis_manager import IISManager
from modules.iis_selection import site_child_selection
from modules.service_backup import ServiceBackup
from modules.sql_backup import SQLBackup
from utils.config_manager import ConfigManager
from utils.email_validation import invalid_millensys_addresses, parse_addresses
from utils.external_drive import capture_external_destination
from utils.paths import get_app_dir


def _addresses(text: str) -> list[str]:
    return parse_addresses(text)


def _invalid_millensys_addresses(text: str) -> list[str]:
    return invalid_millensys_addresses(text)


def _check_item(label: str, data: object, checked: bool = False) -> QListWidgetItem:
    item = QListWidgetItem(label)
    item.setData(Qt.UserRole, data)
    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    return item


class StartupGuide(QWizard):
    """Complete one-time setup ending with service installation and activation."""

    def __init__(self, config_manager: ConfigManager, parent=None) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self._iis_app_records: list[dict[str, str]] = []
        self._cleanup_app_keys = set(config_manager.get("iis", "selected_cleanup_apps", []) or [])
        self._applying_iis_site_rules = False
        self.setWindowTitle("MiChecklist First-Time Setup")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage)
        self.resize(820, 620)
        self._build_client_info()
        self._build_destination()
        self._build_sql()
        self._build_iis()
        self._build_services()
        self._build_folders_cleanup()
        self._build_retention()
        self._build_notifications()
        self._build_schedule()
        self._build_finish()

    def _scheduled_job_store(self) -> ScheduledJobStore:
        default_config = (get_app_dir() / "config" / "config.json").resolve()
        if self.config_manager.config_path.resolve() == default_config:
            return ScheduledJobStore()
        return ScheduledJobStore(self.config_manager.config_path.parent / "saved_jobs.json")

    def _build_client_info(self) -> None:
        _, layout = self._page(
            "Client Information and Recipients",
            "Identify the client and choose the Millensys recipients before configuring backups.",
        )
        email = self.config_manager.get_section("email")
        self.client_name = QLineEdit(str(email.get("client_name", "")))
        self.client_branch = QLineEdit(str(email.get("client_branch", "")))
        self.client_server = QLineEdit(str(email.get("client_server", "")))
        self.client_comment = QLineEdit(str(email.get("client_comment", "")))
        self.client_comment.setPlaceholderText("Optional note shown in notifications")
        self.to_input = QLineEdit(", ".join(email.get("to", []) or []))
        self.to_input.setPlaceholderText("support@millensys.com")
        self.cc_input = QLineEdit(", ".join(email.get("cc", []) or []))
        self.cc_input.setPlaceholderText("Optional; must use @millensys.com")
        form = QFormLayout()
        form.addRow("Client name:", self.client_name)
        form.addRow("Branch / location:", self.client_branch)
        form.addRow("Server name:", self.client_server)
        form.addRow("Comment:", self.client_comment)
        form.addRow("Reception email:", self.to_input)
        form.addRow("Cc (optional):", self.cc_input)
        layout.addLayout(form)
        rule = QLabel("Reception and Cc addresses must end with @millensys.com. Separate multiple addresses with commas.")
        rule.setWordWrap(True)
        layout.addWidget(rule)
        layout.addStretch()

    def _page(self, title: str, subtitle: str = "") -> tuple[QWizardPage, QVBoxLayout]:
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        layout = QVBoxLayout(page)
        self.addPage(page)
        return page, layout

    def _build_welcome(self) -> None:
        _, layout = self._page(
            "Welcome to MiChecklist",
            "This one-time guide configures the complete checklist and installs its service.",
        )
        text = QLabel(
            "Use Next to configure each checklist area. Optional areas can be disabled and "
            "skipped. Finish saves and validates everything, removes the former service if "
            "present, installs MiChecklistService, and applies the schedule."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch()

    def _build_destination(self) -> None:
        _, layout = self._page("Backup Destination", "Choose a dedicated location outside the application folder.")
        row = QHBoxLayout()
        self.destination_input = QLineEdit(str(self.config_manager.get("general", "backup_root_folder", "")))
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_destination)
        row.addWidget(self.destination_input)
        row.addWidget(browse)
        layout.addWidget(QLabel("Backup root folder:"))
        layout.addLayout(row)
        layout.addWidget(QLabel("Recommended example: D:\\MiChecklistBackups"))
        external = self.config_manager.get_section("external_copy")
        self.external_destination_enabled = QCheckBox(
            "Copy completed backups to a removable external drive"
        )
        self.external_destination_enabled.setChecked(bool(
            external.get("enabled", False) or external.get("migration_requires_local_destination", False)
        ))
        self.external_destination_input = QLineEdit(str(external.get("configured_path", "")))
        self.external_destination_input.setPlaceholderText("Select a folder on the external drive")
        external_browse = QPushButton("Browse External Drive")
        external_browse.clicked.connect(self._browse_external_destination)
        external_path_row = QHBoxLayout()
        external_path_row.addWidget(self.external_destination_input)
        external_path_row.addWidget(external_browse)
        self.external_copy_latest = QCheckBox("Copy only the latest verified backup")
        self.external_copy_latest.setChecked(bool(external.get("copy_latest_only", True)))
        self.external_check_minutes = QSpinBox()
        self.external_check_minutes.setRange(1, 1440)
        self.external_check_minutes.setValue(int(external.get("check_interval_minutes", 5)))
        self.external_check_minutes.setSuffix(" minutes")
        self.external_reminder_minutes = QSpinBox()
        self.external_reminder_minutes.setRange(5, 10080)
        self.external_reminder_minutes.setValue(int(external.get("reminder_interval_minutes", 120)))
        self.external_reminder_minutes.setSuffix(" minutes")
        external_form = QFormLayout()
        external_form.addRow(self.external_destination_enabled)
        external_form.addRow("External copy folder:", external_path_row)
        external_form.addRow(self.external_copy_latest)
        external_form.addRow("Check for drive every:", self.external_check_minutes)
        external_form.addRow("Missing-drive reminder every:", self.external_reminder_minutes)
        layout.addLayout(external_form)
        external_note = QLabel(
            "The checklist always completes and verifies in the local backup root first. "
            "MiChecklist then copies the verified backup when this drive is available."
        )
        if external.get("migration_requires_local_destination", False):
            external_note.setText(
                "Previous direct-to-external settings were preserved. Select a permanent local "
                "backup root above, then confirm or disable the external copy option."
            )
        external_note.setWordWrap(True)
        layout.addWidget(external_note)
        general = self.config_manager.get_section("general")
        self.compression_enabled = QCheckBox("Use SQL Server native backup compression")
        self.compression_enabled.setChecked(bool(general.get("compression_enabled", True)))
        self.hashes_enabled = QCheckBox("Generate SHA-256 hashes")
        self.hashes_enabled.setChecked(bool(general.get("generate_sha256_hashes", True)))
        self.incremental_enabled = QCheckBox("Use incremental backups when supported")
        self.incremental_enabled.setChecked(bool(general.get("incremental_backups", True)))
        layout.addWidget(self.compression_enabled)
        layout.addWidget(self.hashes_enabled)
        layout.addWidget(self.incremental_enabled)
        layout.addStretch()

    def _build_sql(self) -> None:
        _, layout = self._page(
            "SQL Server and Databases",
            "Connect and select databases, or skip this page when SQL Server is not installed here.",
        )
        sql = self.config_manager.get_section("sql_server")
        self.sql_enabled = QCheckBox("Enable SQL Server backup")
        self.sql_enabled.setChecked(bool(sql.get("backup_enabled", True)))
        self.sql_server = QLineEdit(str(sql.get("server_name", "")))
        self.sql_instance = QLineEdit(str(sql.get("instance_name", "")))
        self.sql_windows_auth = QCheckBox("Use Windows Authentication")
        self.sql_windows_auth.setChecked(bool(sql.get("use_windows_auth", True)))
        self.sql_archive_compression = QCheckBox("Compress verified SQL backups to ZIP")
        self.sql_archive_compression.setChecked(bool(sql.get("archive_compression_enabled", True)))
        self.sql_username = QLineEdit(str(sql.get("username", "")))
        self.sql_password = QLineEdit(str(sql.get("password", "")))
        self.sql_password.setEchoMode(QLineEdit.Password)
        self.sql_username.textEdited.connect(lambda _text: self.sql_windows_auth.setChecked(False))
        self.sql_password.textEdited.connect(lambda _text: self.sql_windows_auth.setChecked(False))
        form = QFormLayout()
        form.addRow(self.sql_enabled)
        form.addRow(self.sql_archive_compression)
        form.addRow("Server:", self.sql_server)
        form.addRow("Instance (optional):", self.sql_instance)
        form.addRow(self.sql_windows_auth)
        form.addRow("SQL username:", self.sql_username)
        form.addRow("SQL password:", self.sql_password)
        layout.addLayout(form)
        load = QPushButton("Test Connection and Load Databases")
        load.clicked.connect(self._load_databases)
        layout.addWidget(load)
        self.database_list = QListWidget()
        for name in sql.get("databases", []) or []:
            self.database_list.addItem(_check_item(str(name), str(name), True))
        layout.addWidget(self.database_list)
        self.skip_sql_button = QPushButton("Skip SQL Server on this server")
        self.skip_sql_button.setObjectName("skipSqlButton")
        self.skip_sql_button.clicked.connect(self._skip_sql)
        layout.addWidget(self.skip_sql_button)
        self._sql_controls = [
            self.sql_archive_compression, self.sql_server, self.sql_instance,
            self.sql_windows_auth, self.sql_username, self.sql_password, load,
            self.database_list,
        ]
        self.sql_enabled.toggled.connect(
            lambda enabled: self._set_widgets_enabled(self._sql_controls, enabled)
        )
        self._set_widgets_enabled(self._sql_controls, self.sql_enabled.isChecked())

    def _build_iis(self) -> None:
        _, layout = self._page(
            "IIS Websites and Child Applications",
            "Select what to back up and which child applications should provide cleanup folders.",
        )
        iis = self.config_manager.get_section("iis")
        self.iis_enabled = QCheckBox("Enable IIS backup")
        self.iis_enabled.setChecked(bool(iis.get("backup_enabled", True)))
        self.iis_autodetect = QCheckBox("Auto-detect IIS sites")
        self.iis_autodetect.setChecked(bool(iis.get("auto_detect_sites", True)))
        self.iis_include_config = QCheckBox("Include IIS configuration backup")
        self.iis_include_config.setChecked(bool(iis.get("include_iis_configuration", True)))
        layout.addWidget(self.iis_enabled)
        layout.addWidget(self.iis_autodetect)
        layout.addWidget(self.iis_include_config)
        detect = QPushButton("Detect IIS Sites and Child Applications")
        detect.clicked.connect(self._load_iis)
        layout.addWidget(detect)
        sites_header = QHBoxLayout()
        sites_header.addWidget(QLabel("Websites to back up"))
        self.select_all_sites = QCheckBox("Select all")
        self.select_all_sites.toggled.connect(lambda checked: self._set_list_all(self.iis_list, checked))
        sites_header.addStretch()
        sites_header.addWidget(self.select_all_sites)
        layout.addLayout(sites_header)
        self.iis_list = QListWidget()
        self.iis_list.setMaximumHeight(95)
        self.iis_list.itemChanged.connect(self._apply_iis_site_child_rules)
        for name in iis.get("selected_sites", []) or []:
            self.iis_list.addItem(_check_item(str(name), {"name": str(name), "path": ""}, True))
        layout.addWidget(self.iis_list)
        apps_header = QHBoxLayout()
        apps_header.addWidget(QLabel("Child applications"))
        self.select_all_app_backups = QCheckBox("Select all backups")
        self.select_all_app_cleanup = QCheckBox("Select all cleanup")
        self.select_all_app_backups.toggled.connect(lambda checked: self._set_tree_all(self.iis_app_list, 0, checked))
        self.select_all_app_cleanup.toggled.connect(lambda checked: self._set_tree_all(self.iis_app_list, 1, checked))
        apps_header.addStretch()
        apps_header.addWidget(self.select_all_app_backups)
        apps_header.addWidget(self.select_all_app_cleanup)
        layout.addLayout(apps_header)
        self.iis_app_list = QTreeWidget()
        self.iis_app_list.setHeaderLabels(["Backup", "Cleanup", "Application", "App pool"])
        self.iis_app_list.setRootIsDecorated(False)
        self.iis_app_list.setAlternatingRowColors(True)
        self.iis_app_list.setMinimumHeight(170)
        app_header = self.iis_app_list.header()
        app_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        app_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        app_header.setSectionResizeMode(2, QHeaderView.Stretch)
        app_header.setSectionResizeMode(3, QHeaderView.Interactive)
        self.iis_app_list.setColumnWidth(3, 120)
        self.iis_app_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        selected_cleanup = set(iis.get("selected_cleanup_apps", []) or [])
        for key in iis.get("selected_apps", []) or []:
            key = str(key)
            site, path = (key.split("|", 1) + [""])[:2]
            item = QTreeWidgetItem(["", "", f"{site} > {path}", ""])
            item.setData(0, Qt.UserRole, {"key": key, "site": site, "path": path, "physical_path": ""})
            item.setCheckState(0, Qt.Checked)
            item.setCheckState(1, Qt.Checked if key in selected_cleanup else Qt.Unchecked)
            self.iis_app_list.addTopLevelItem(item)
        layout.addWidget(self.iis_app_list)
        cleanup_help = QLabel(
            "Backup saves the application. Cleanup scans that application's Logs, Exception, "
            "and Exceptions folders for the extensions selected on the next page."
        )
        cleanup_help.setWordWrap(True)
        layout.addWidget(cleanup_help)
        detect_cleanup = QPushButton("Detect Cleanup Folders for Checked Applications")
        detect_cleanup.clicked.connect(self._detect_iis_cleanup)
        layout.addWidget(detect_cleanup)
        pools_header = QHBoxLayout()
        pools_header.addWidget(QLabel("Application pools to back up"))
        self.select_all_pools = QCheckBox("Select all")
        self.select_all_pools.toggled.connect(lambda checked: self._set_list_all(self.iis_pool_list, checked))
        pools_header.addStretch()
        pools_header.addWidget(self.select_all_pools)
        layout.addLayout(pools_header)
        self.iis_pool_list = QListWidget()
        self.iis_pool_list.setMaximumHeight(80)
        for name in iis.get("selected_pools", []) or []:
            self.iis_pool_list.addItem(_check_item(str(name), str(name), True))
        layout.addWidget(self.iis_pool_list)
        self.skip_iis_button = QPushButton("Skip IIS on this server")
        self.skip_iis_button.setObjectName("skipIisButton")
        self.skip_iis_button.clicked.connect(self._skip_iis)
        layout.addWidget(self.skip_iis_button)
        self._iis_controls = [
            self.iis_autodetect, self.iis_include_config, detect,
            self.select_all_sites, self.iis_list, self.select_all_app_backups,
            self.select_all_app_cleanup, self.iis_app_list, detect_cleanup,
            self.select_all_pools, self.iis_pool_list,
        ]
        self.iis_enabled.toggled.connect(
            lambda enabled: self._set_widgets_enabled(self._iis_controls, enabled)
        )
        self._set_widgets_enabled(self._iis_controls, self.iis_enabled.isChecked())

    def _build_services(self) -> None:
        _, layout = self._page(
            "Windows Services",
            "Detect and select service folders, or skip this page when no application services are hosted here.",
        )
        section = self.config_manager.get_section("services")
        self.services_enabled = QCheckBox("Enable Windows service backup")
        self.services_enabled.setChecked(bool(section.get("backup_enabled", True)))
        layout.addWidget(self.services_enabled)
        row = QHBoxLayout()
        self.service_search = QLineEdit(str(section.get("service_search_filter", "Mi")))
        self.service_search.setPlaceholderText("Optional service-name filter")
        detect = QPushButton("Detect Services")
        detect.clicked.connect(self._load_services)
        row.addWidget(self.service_search)
        row.addWidget(detect)
        layout.addLayout(row)
        self.service_list = QListWidget()
        for name in section.get("selected_services", []) or []:
            self.service_list.addItem(_check_item(str(name), str(name), True))
        layout.addWidget(self.service_list)
        self.skip_services_button = QPushButton("Skip Windows service backup on this server")
        self.skip_services_button.setObjectName("skipServicesButton")
        self.skip_services_button.clicked.connect(self._skip_services)
        layout.addWidget(self.skip_services_button)
        self._service_controls = [self.service_search, detect, self.service_list]
        self.services_enabled.toggled.connect(
            lambda enabled: self._set_widgets_enabled(self._service_controls, enabled)
        )
        self._set_widgets_enabled(self._service_controls, self.services_enabled.isChecked())

    def _build_folders_cleanup(self) -> None:
        _, layout = self._page("Folders and Cleanup", "Add optional folders and cleanup locations, or continue without them.")
        layout.addWidget(QLabel("Additional folders to back up:"))
        folder_row = QHBoxLayout()
        self.folder_input = QLineEdit()
        add_folder = QPushButton("Browse and Add")
        add_folder.clicked.connect(self._add_folder)
        folder_row.addWidget(self.folder_input)
        folder_row.addWidget(add_folder)
        layout.addLayout(folder_row)
        self.folder_list = QListWidget()
        for folder in self.config_manager.get("folder_backup", "folders", []) or []:
            if folder.get("enabled", True):
                self.folder_list.addItem(str(folder.get("path", "")))
        layout.addWidget(self.folder_list)
        self.cleanup_enabled = QCheckBox("Enable cleanup tasks")
        self.cleanup_enabled.setChecked(bool(self.config_manager.get("cleanup", "enabled", False)))
        layout.addWidget(self.cleanup_enabled)
        self.cleanup_extensions = QLineEdit(".log, .txt, .json")
        layout.addWidget(QLabel("Cleanup file extensions:"))
        layout.addWidget(self.cleanup_extensions)
        cleanup_note = QLabel("Startup cleanup removes only .log, .txt, and .json files from selected folders.")
        cleanup_note.setWordWrap(True)
        layout.addWidget(cleanup_note)
        detect_cleanup = QPushButton("Detect Cleanup Folders from Selected IIS Child Apps")
        detect_cleanup.clicked.connect(self._detect_iis_cleanup)
        layout.addWidget(detect_cleanup)
        cleanup_row = QHBoxLayout()
        self.cleanup_input = QLineEdit()
        add_cleanup = QPushButton("Browse and Add Cleanup Folder")
        add_cleanup.clicked.connect(self._add_cleanup)
        cleanup_row.addWidget(self.cleanup_input)
        cleanup_row.addWidget(add_cleanup)
        layout.addLayout(cleanup_row)
        self.cleanup_list = QListWidget()
        for task in self.config_manager.get("cleanup", "tasks", []) or []:
            if task.get("enabled", True):
                self.cleanup_list.addItem(str(task.get("path", "")))
        layout.addWidget(self.cleanup_list)
        self.skip_folders_cleanup_button = QPushButton("Skip additional folders and cleanup")
        self.skip_folders_cleanup_button.setObjectName("skipFoldersCleanupButton")
        self.skip_folders_cleanup_button.clicked.connect(self._skip_folders_cleanup)
        layout.addWidget(self.skip_folders_cleanup_button)

    def _build_schedule(self) -> None:
        _, layout = self._page("Checklist Schedule", "Choose when the complete checklist runs.")
        schedule = self.config_manager.get_section("schedule")
        self.schedule_enabled = QCheckBox("Enable automated checklist schedule")
        self.schedule_enabled.setChecked(bool(schedule.get("enabled", False)))
        self.schedule_mode = QComboBox()
        for text, value in (("Daily", "daily"), ("Weekly", "weekly"), ("Every N Days", "days_after_last_backup"), ("At Windows Startup", "after_restart")):
            self.schedule_mode.addItem(text, value)
        self.schedule_mode.setCurrentIndex(max(0, self.schedule_mode.findData(schedule.get("mode", "weekly"))))
        self.schedule_weekday = QComboBox()
        self.schedule_weekday.addItems(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
        self.schedule_weekday.setCurrentText(str(schedule.get("weekday", "Sunday")))
        self.schedule_days = QSpinBox()
        self.schedule_days.setRange(1, 365)
        self.schedule_days.setValue(int(schedule.get("days_interval", 7)))
        self.schedule_time = QTimeEdit()
        self.schedule_time.setDisplayFormat("HH:mm")
        parsed_time = QTime.fromString(str(schedule.get("start_time", "02:00")), "HH:mm")
        self.schedule_time.setTime(parsed_time if parsed_time.isValid() else QTime(2, 0))
        self.schedule_run_missed = QCheckBox("Run a missed schedule when the machine becomes available")
        self.schedule_run_missed.setChecked(bool(schedule.get("run_missed_schedule", True)))
        self.schedule_test_restart = QCheckBox("Create a one-time restart test trigger")
        self.schedule_test_restart.setChecked(bool(schedule.get("test_run_after_restart", False)))
        self.schedule_hide_to_tray = QCheckBox("Hide the application to the tray when closed")
        self.schedule_hide_to_tray.setChecked(bool(schedule.get("hide_to_tray", True)))
        form = QFormLayout()
        form.addRow(self.schedule_enabled)
        form.addRow("Frequency:", self.schedule_mode)
        form.addRow("Weekday:", self.schedule_weekday)
        form.addRow("Every:", self.schedule_days)
        form.addRow("Start time:", self.schedule_time)
        form.addRow(self.schedule_run_missed)
        form.addRow(self.schedule_test_restart)
        form.addRow(self.schedule_hide_to_tray)
        layout.addLayout(form)
        self.advanced_module_schedules = QCheckBox(
            "Advanced: enable separate schedules for individual tabs"
        )
        module_note = QLabel(
            "Most installations should use only the primary schedule above. Enable advanced "
            "schedules only when a module must run more frequently. A collision within 10 "
            "minutes is automatically satisfied by the primary checklist."
        )
        module_note.setWordWrap(True)
        layout.addWidget(self.advanced_module_schedules)
        layout.addWidget(module_note)
        existing_modules = {
            item.get("payload", {}).get("module"): item
            for item in self._scheduled_job_store().load()
            if item.get("job_type") == "module_schedule"
        }
        self.module_schedule_controls: dict[str, tuple[QCheckBox, QTimeEdit]] = {}
        self.module_schedule_container = QWidget()
        module_form = QFormLayout(self.module_schedule_container)
        defaults = {
            "cleanup": "23:00", "database": "01:00", "iis": "03:00",
            "services": "04:00", "folders": "05:00",
        }
        for module, label in (
            ("cleanup", "Cleanup"), ("database", "Database"), ("iis", "IIS"),
            ("services", "Services"), ("folders", "Folders"),
        ):
            saved = existing_modules.get(module, {})
            enabled = QCheckBox(f"Enable daily {label} schedule")
            enabled.setChecked(bool(saved.get("enabled", False)))
            run_time = QTimeEdit()
            run_time.setDisplayFormat("HH:mm")
            parsed = QTime.fromString(str(saved.get("time", defaults[module])), "HH:mm")
            run_time.setTime(parsed if parsed.isValid() else QTime.fromString(defaults[module], "HH:mm"))
            row = QHBoxLayout()
            row.addWidget(enabled)
            row.addWidget(run_time)
            module_form.addRow(f"{label} tab:", row)
            self.module_schedule_controls[module] = (enabled, run_time)
        advanced_enabled = any(control[0].isChecked() for control in self.module_schedule_controls.values())
        self.advanced_module_schedules.setChecked(advanced_enabled)
        self.module_schedule_container.setVisible(advanced_enabled)
        self.advanced_module_schedules.toggled.connect(self.module_schedule_container.setVisible)
        layout.addWidget(self.module_schedule_container)
        layout.addWidget(QLabel("Safe retention keeps the latest valid scheduled backup by default."))
        layout.addStretch()

    def _build_notifications(self) -> None:
        _, layout = self._page("Notification Delivery", "Configure SMTP delivery and which events send notifications.")
        email = self.config_manager.get_section("email")
        self.notifications_enabled = QCheckBox("Enable email notifications")
        self.notifications_enabled.setChecked(bool(email.get("enabled", True)))
        self.smtp_server = QLineEdit(str(email.get("smtp_server", "smtp.gmail.com")))
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(int(email.get("smtp_port", 587)))
        self.smtp_tls = QCheckBox("Use STARTTLS")
        self.smtp_tls.setChecked(bool(email.get("use_tls", True)))
        self.smtp_username = QLineEdit(str(email.get("username", "")))
        self.smtp_password = QLineEdit(str(email.get("password", "")))
        self.smtp_password.setEchoMode(QLineEdit.Password)
        self.sender_name = QLineEdit(str(email.get("sender_name", "MiChecklist")))
        self.subject_template = QLineEdit(str(email.get(
            "subject_template", "[{company} {product}] {status} - {job_type}"
        )))
        self.subject_template.setToolTip(
            "Available fields: {company}, {product}, {status}, {job_type}, {client}, "
            "{branch}, {server}, {job_id}, {date}"
        )
        form = QFormLayout()
        form.addRow(self.notifications_enabled)
        form.addRow("SMTP server:", self.smtp_server)
        form.addRow("SMTP port:", self.smtp_port)
        form.addRow(self.smtp_tls)
        form.addRow("SMTP username:", self.smtp_username)
        form.addRow("SMTP password:", self.smtp_password)
        form.addRow("Sender name:", self.sender_name)
        form.addRow("Subject template:", self.subject_template)
        self.notify_success = QCheckBox("Notify after successful jobs")
        self.notify_success.setChecked(bool(email.get("notify_success", True)))
        self.notify_warning = QCheckBox("Notify after jobs with warnings")
        self.notify_warning.setChecked(bool(email.get("notify_warning", True)))
        self.notify_failure = QCheckBox("Notify after failed jobs")
        self.notify_failure.setChecked(bool(email.get("notify_failure", True)))
        self.notify_recovery = QCheckBox("Notify after interrupted-job recovery")
        self.notify_recovery.setChecked(bool(email.get("notify_recovery", True)))
        self.notify_module_success = QCheckBox("Notify after successful advanced module schedules")
        self.notify_module_success.setChecked(bool(email.get("notify_module_success", False)))
        form.addRow(self.notify_success)
        form.addRow(self.notify_warning)
        form.addRow(self.notify_failure)
        form.addRow(self.notify_recovery)
        form.addRow(self.notify_module_success)
        layout.addLayout(form)
        note = QLabel("Recipients and client information were configured on the first page and can be changed later in Notifications.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def _build_retention(self) -> None:
        _, layout = self._page("Backup Retention", "Protect the latest valid backup and control older generations.")
        retention = self.config_manager.get_section("retention")
        schedule = self.config_manager.get_section("schedule")
        self.retention_enabled = QCheckBox("Remove old backup folders automatically")
        self.retention_enabled.setChecked(bool(retention.get("enabled", True)))
        self.keep_latest_backup = QCheckBox("Keep only the latest valid scheduled backup")
        self.keep_latest_backup.setChecked(bool(schedule.get("delete_previous_automated_backups", True)))
        self.retention_minimum = QSpinBox()
        self.retention_minimum.setRange(1, 100)
        self.retention_minimum.setValue(int(retention.get("minimum_successful_backups", 1)))
        self.retention_max_age = QSpinBox()
        self.retention_max_age.setRange(1, 3650)
        self.retention_max_age.setValue(int(retention.get("max_age_days", 90)))
        self.retention_max_age.setSuffix(" days")
        form = QFormLayout()
        form.addRow(self.retention_enabled)
        form.addRow(self.keep_latest_backup)
        form.addRow("Always preserve at least:", self.retention_minimum)
        form.addRow("Maximum backup age:", self.retention_max_age)
        layout.addLayout(form)
        note = QLabel("The previous valid backup is removed only after its replacement completes successfully.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def _build_finish(self) -> None:
        _, layout = self._page("Review and Install", "Finish validates the configuration before installing the service.")
        review = QLabel(
            "Click Finish to save all selections, validate the checklist, remove the former "
            "SupportAssistantService if present, install and start MiChecklistService, and apply the schedule."
        )
        review.setWordWrap(True)
        layout.addWidget(review)
        layout.addStretch()

    def _browse_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select Backup Destination", self.destination_input.text() or str(Path.home()))
        if selected:
            self.destination_input.setText(selected)

    def _browse_external_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Select External Copy Folder",
            self.external_destination_input.text() or str(Path.home()),
        )
        if selected:
            self.external_destination_input.setText(selected)

    @staticmethod
    def _set_widgets_enabled(widgets: list, enabled: bool) -> None:
        for widget in widgets:
            widget.setEnabled(enabled)

    def _skip_sql(self) -> None:
        self.sql_enabled.setChecked(False)
        self.next()

    def _skip_iis(self) -> None:
        self.iis_enabled.setChecked(False)
        self.next()

    def _skip_services(self) -> None:
        self.services_enabled.setChecked(False)
        self.next()

    def _skip_folders_cleanup(self) -> None:
        self.folder_list.clear()
        self.cleanup_enabled.setChecked(False)
        self.cleanup_list.clear()
        self.next()

    def _load_databases(self) -> None:
        if not self.sql_enabled.isChecked():
            return
        try:
            connection = SQLBackup(self.sql_server.text().strip(), self.sql_instance.text().strip(), "", self.sql_username.text().strip(), self.sql_password.text(), self.sql_windows_auth.isChecked())
            databases = connection.list_databases()
        except Exception as exc:
            QMessageBox.warning(self, "SQL Connection", str(exc))
            return
        selected = set(self._checked_data(self.database_list))
        self.database_list.clear()
        for database in databases:
            name = str(database.get("name", ""))
            self.database_list.addItem(_check_item(name, name, name in selected))

    def _load_iis(self) -> None:
        try:
            manager = IISManager()
            sites = manager.detect_sites()
            applications = manager.list_applications()
            pools = manager.list_app_pools() if hasattr(manager, "list_app_pools") else []
        except Exception as exc:
            QMessageBox.warning(self, "IIS Detection", str(exc))
            return
        selected = {value["name"] for value in self._checked_data(self.iis_list)}
        self.iis_list.clear()
        for site in sites:
            item = _check_item(site.name, {"name": site.name, "path": site.physical_path}, site.name in selected)
            item.setToolTip(f"Physical path: {site.physical_path}")
            self.iis_list.addItem(item)
        selected_apps = {
            value.get("key", "") for value in self._checked_tree_data(self.iis_app_list, 0)
            if isinstance(value, dict)
        }
        selected_cleanup = {
            value.get("key", "") for value in self._checked_tree_data(self.iis_app_list, 1)
            if isinstance(value, dict)
        } | self._cleanup_app_keys
        self.iis_app_list.clear()
        self._iis_app_records = [dict(app) for app in applications]
        for app in applications:
            site = str(app.get("site", ""))
            path = str(app.get("path", ""))
            key = f"{site}|{path}"
            data = dict(app)
            data["key"] = key
            item = QTreeWidgetItem(["", "", f"{site} > {path}", str(app.get("application_pool", ""))])
            item.setData(0, Qt.UserRole, data)
            item.setCheckState(0, Qt.Checked if key in selected_apps else Qt.Unchecked)
            item.setCheckState(1, Qt.Checked if key in selected_cleanup else Qt.Unchecked)
            item.setToolTip(2, f"Physical path: {app.get('physical_path', '')}")
            self.iis_app_list.addTopLevelItem(item)
        selected_pools = set(self._checked_data(self.iis_pool_list))
        self.iis_pool_list.clear()
        for pool in pools:
            name = str(pool.get("name", ""))
            item = _check_item(name, name, name in selected_pools)
            item.setToolTip(f"State: {pool.get('state', '')}")
            self.iis_pool_list.addItem(item)
        self._apply_iis_site_child_rules()

    def _apply_iis_site_child_rules(self, *_args) -> None:
        """Select and discover cleanup for children stored outside a selected site root."""
        if self._applying_iis_site_rules or not self._iis_app_records:
            return
        selected_sites = {
            str(value.get("name", "")): str(value.get("path", ""))
            for value in self._checked_data(self.iis_list)
            if isinstance(value, dict) and value.get("name")
        }
        selection = site_child_selection(selected_sites, self._iis_app_records)
        if not selected_sites:
            for index in range(self.iis_app_list.topLevelItemCount()):
                self.iis_app_list.topLevelItem(index).setHidden(False)
            return
        self._applying_iis_site_rules = True
        try:
            for index in range(self.iis_app_list.topLevelItemCount()):
                item = self.iis_app_list.topLevelItem(index)
                data = item.data(0, Qt.UserRole)
                key = str(data.get("key", "")) if isinstance(data, dict) else ""
                item.setHidden(key not in selection.visible_keys)
                if key in selection.visible_keys:
                    item.setCheckState(
                        0, Qt.Checked if key in selection.backup_keys else Qt.Unchecked
                    )
                    item.setCheckState(1, Qt.Checked)
            self._cleanup_app_keys.update(selection.cleanup_keys)
            tasks = detect_iis_cleanup_tasks(
                self._iis_app_records, set(selection.cleanup_keys)
            )
            existing = {self.cleanup_list.item(i).text() for i in range(self.cleanup_list.count())}
            for task in tasks:
                path = str(task.get("path", ""))
                if path and path not in existing:
                    self.cleanup_list.addItem(path)
                    existing.add(path)
            if tasks:
                self.cleanup_enabled.setChecked(True)
        finally:
            self._applying_iis_site_rules = False

    def _detect_iis_cleanup(self) -> None:
        selected_records = [
            value for value in self._checked_tree_data(self.iis_app_list, 1)
            if isinstance(value, dict) and value.get("key")
        ]
        if not selected_records:
            QMessageBox.warning(self, "Cleanup Detection", "Select IIS child applications first.")
            return
        if not self._iis_app_records:
            QMessageBox.warning(self, "Cleanup Detection", "Run IIS detection first so physical paths are available.")
            return
        selected_keys = {str(record["key"]) for record in selected_records}
        tasks = detect_iis_cleanup_tasks(self._iis_app_records, selected_keys)
        existing = {self.cleanup_list.item(i).text() for i in range(self.cleanup_list.count())}
        for task in tasks:
            path = str(task.get("path", ""))
            if path and path not in existing:
                self.cleanup_list.addItem(path)
                existing.add(path)
        if tasks:
            self._cleanup_app_keys.update(selected_keys)
            self.cleanup_enabled.setChecked(True)
        else:
            QMessageBox.information(self, "Cleanup Detection", "No Logs, Exception, or Exceptions folders were found under the selected child applications.")

    def _load_services(self) -> None:
        try:
            services = ServiceBackup().list_services(search=self.service_search.text().strip())
        except Exception as exc:
            QMessageBox.warning(self, "Service Detection", str(exc))
            return
        selected = set(self._checked_data(self.service_list))
        self.service_list.clear()
        for service in services:
            self.service_list.addItem(_check_item(f"{service.name} — {service.display_name}", service.name, service.name in selected))

    def _add_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select Additional Backup Folder", self.folder_input.text() or str(Path.home()))
        if selected and not self.folder_list.findItems(selected, Qt.MatchExactly):
            self.folder_list.addItem(selected)

    def _add_cleanup(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select Cleanup Folder", self.cleanup_input.text() or str(Path.home()))
        if selected and not self.cleanup_list.findItems(selected, Qt.MatchExactly):
            self.cleanup_list.addItem(selected)

    @staticmethod
    def _checked_data(widget: QListWidget) -> list[object]:
        return [widget.item(i).data(Qt.UserRole) for i in range(widget.count()) if widget.item(i).checkState() == Qt.Checked]

    @staticmethod
    def _checked_tree_data(widget: QTreeWidget, column: int) -> list[object]:
        return [
            widget.topLevelItem(i).data(0, Qt.UserRole)
            for i in range(widget.topLevelItemCount())
            if widget.topLevelItem(i).checkState(column) == Qt.Checked
        ]

    @staticmethod
    def _set_list_all(widget: QListWidget, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for index in range(widget.count()):
            widget.item(index).setCheckState(state)

    @staticmethod
    def _set_tree_all(widget: QTreeWidget, column: int, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for index in range(widget.topLevelItemCount()):
            widget.topLevelItem(index).setCheckState(column, state)

    def validateCurrentPage(self) -> bool:
        page = self.currentId()
        if page == 0:
            if not _addresses(self.to_input.text()):
                QMessageBox.warning(self, "Reception Email", "Enter at least one reception email address.")
                return False
            invalid = _invalid_millensys_addresses(self.to_input.text()) + _invalid_millensys_addresses(self.cc_input.text())
            if invalid:
                QMessageBox.warning(
                    self, "Reception Email",
                    "Every Reception and Cc address must end with @millensys.com:\n\n" + "\n".join(invalid),
                )
                return False
        if page == 1 and not self.destination_input.text().strip():
            QMessageBox.warning(self, "Backup Destination", "Select a backup destination before continuing.")
            return False
        if page == 1 and self.external_destination_enabled.isChecked():
            try:
                capture_external_destination(self.external_destination_input.text().strip())
            except Exception as exc:
                QMessageBox.warning(
                    self, "External Backup Drive",
                    "Connect the external drive and select a folder on it before continuing.\n\n"
                    + str(exc),
                )
                return False
        if page == 2 and self.sql_enabled.isChecked():
            if not self.sql_server.text().strip() or not self._checked_data(self.database_list):
                QMessageBox.warning(self, "SQL Configuration", "Test SQL and select at least one database, or disable SQL backup.")
                return False
            if not self.sql_windows_auth.isChecked() and (not self.sql_username.text().strip() or not self.sql_password.text()):
                QMessageBox.warning(self, "SQL Configuration", "Enter both SQL username and password.")
                return False
        if page == 3 and self.iis_enabled.isChecked() and not (
            self._checked_data(self.iis_list) or self._checked_tree_data(self.iis_app_list, 0)
        ):
            answer = QMessageBox.question(self, "IIS Selection", "No IIS sites or child applications are selected. Disable IIS backup and continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                self.iis_enabled.setChecked(False)
            else:
                return False
        if page == 4 and self.services_enabled.isChecked() and not self._checked_data(self.service_list):
            answer = QMessageBox.question(self, "Service Selection", "No services are selected. Disable service backup and continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                self.services_enabled.setChecked(False)
            else:
                return False
        return True

    def _save_all(self) -> None:
        invalid_recipients = _invalid_millensys_addresses(self.to_input.text()) + _invalid_millensys_addresses(self.cc_input.text())
        if not _addresses(self.to_input.text()):
            raise ValueError("At least one reception email address is required.")
        if invalid_recipients:
            raise ValueError("Reception and Cc addresses must end with @millensys.com: " + ", ".join(invalid_recipients))
        self.config_manager.set("general", "backup_root_folder", self.destination_input.text().strip())
        external_settings = self.config_manager.get_section("external_copy")
        external_settings["enabled"] = self.external_destination_enabled.isChecked()
        external_settings["copy_latest_only"] = self.external_copy_latest.isChecked()
        external_settings["check_interval_minutes"] = self.external_check_minutes.value()
        external_settings["reminder_interval_minutes"] = self.external_reminder_minutes.value()
        external_settings["migration_requires_local_destination"] = False
        if self.external_destination_enabled.isChecked():
            identity = capture_external_destination(self.external_destination_input.text().strip())
            external_settings.update(identity)
        self.config_manager.data["external_copy"] = external_settings
        self.config_manager.set("general", "compression_enabled", self.compression_enabled.isChecked())
        self.config_manager.set("general", "generate_sha256_hashes", self.hashes_enabled.isChecked())
        self.config_manager.set("general", "incremental_backups", self.incremental_enabled.isChecked())
        self.config_manager.set("sql_server", "backup_enabled", self.sql_enabled.isChecked())
        self.config_manager.set("sql_server", "archive_compression_enabled", self.sql_archive_compression.isChecked())
        self.config_manager.set("sql_server", "server_name", self.sql_server.text().strip())
        self.config_manager.set("sql_server", "instance_name", self.sql_instance.text().strip())
        self.config_manager.set("sql_server", "username", self.sql_username.text().strip())
        self.config_manager.set("sql_server", "password", self.sql_password.text())
        self.config_manager.set("sql_server", "use_windows_auth", self.sql_windows_auth.isChecked())
        self.config_manager.set("sql_server", "databases", [str(value) for value in self._checked_data(self.database_list)])
        self.config_manager.set("iis", "backup_enabled", self.iis_enabled.isChecked())
        self.config_manager.set("iis", "auto_detect_sites", self.iis_autodetect.isChecked())
        self.config_manager.set("iis", "include_iis_configuration", self.iis_include_config.isChecked())
        self.config_manager.set("iis", "selected_sites", [str(value["name"]) for value in self._checked_data(self.iis_list)])
        self.config_manager.set("iis", "selected_apps", [
            str(value.get("key", "")) for value in self._checked_tree_data(self.iis_app_list, 0)
            if isinstance(value, dict) and value.get("key")
        ])
        selected_cleanup_apps = {
            str(value.get("key", "")) for value in self._checked_tree_data(self.iis_app_list, 1)
            if isinstance(value, dict) and value.get("key")
        }
        self.config_manager.set(
            "iis", "selected_cleanup_apps",
            sorted(selected_cleanup_apps),
        )
        self.config_manager.set("iis", "selected_pools", [str(value) for value in self._checked_data(self.iis_pool_list)])
        self.config_manager.set("services", "backup_enabled", self.services_enabled.isChecked())
        self.config_manager.set("services", "service_search_filter", self.service_search.text().strip())
        self.config_manager.set("services", "selected_services", [str(value) for value in self._checked_data(self.service_list)])
        self.config_manager.set("folder_backup", "folders", [{"name": Path(self.folder_list.item(i).text()).name, "path": self.folder_list.item(i).text(), "enabled": True} for i in range(self.folder_list.count())])
        self.config_manager.set("cleanup", "enabled", self.cleanup_enabled.isChecked())
        cleanup_extensions = normalize_extensions(self.cleanup_extensions.text())
        self.config_manager.set("cleanup", "tasks", [{"name": Path(self.cleanup_list.item(i).text()).name, "path": self.cleanup_list.item(i).text(), "extensions": cleanup_extensions, "enabled": True, "source": "Startup Guide"} for i in range(self.cleanup_list.count())])
        self.config_manager.set("schedule", "enabled", self.schedule_enabled.isChecked())
        self.config_manager.set("schedule", "mode", self.schedule_mode.currentData())
        self.config_manager.set("schedule", "run_at_startup", self.schedule_mode.currentData() == "after_restart")
        self.config_manager.set("schedule", "weekday", self.schedule_weekday.currentText())
        self.config_manager.set("schedule", "days_interval", self.schedule_days.value())
        self.config_manager.set("schedule", "start_time", self.schedule_time.time().toString("HH:mm"))
        self.config_manager.set("schedule", "delete_previous_automated_backups", self.keep_latest_backup.isChecked())
        self.config_manager.set("schedule", "run_missed_schedule", self.schedule_run_missed.isChecked())
        self.config_manager.set("schedule", "test_run_after_restart", self.schedule_test_restart.isChecked())
        self.config_manager.set("schedule", "hide_to_tray", self.schedule_hide_to_tray.isChecked())
        module_labels = {
            "cleanup": "Cleanup", "database": "Database", "iis": "IIS",
            "services": "Services", "folders": "Folders",
        }
        schedule_store = self._scheduled_job_store()
        for module, (enabled, run_time) in self.module_schedule_controls.items():
            schedule_store.upsert_module(
                module,
                f"{module_labels[module]} tab schedule",
                {
                    "enabled": self.advanced_module_schedules.isChecked() and enabled.isChecked(),
                    "frequency": "daily",
                    "time": run_time.time().toString("HH:mm"),
                    "run_missed": True,
                },
            )
        self.config_manager.set("retention", "enabled", self.retention_enabled.isChecked())
        self.config_manager.set("retention", "minimum_successful_backups", self.retention_minimum.value())
        self.config_manager.set("retention", "max_age_days", self.retention_max_age.value())
        self.config_manager.set("email", "enabled", self.notifications_enabled.isChecked())
        self.config_manager.set("email", "client_name", self.client_name.text().strip())
        self.config_manager.set("email", "client_branch", self.client_branch.text().strip())
        self.config_manager.set("email", "client_server", self.client_server.text().strip())
        self.config_manager.set("email", "client_comment", self.client_comment.text().strip())
        self.config_manager.set("email", "smtp_server", self.smtp_server.text().strip())
        self.config_manager.set("email", "smtp_port", self.smtp_port.value())
        self.config_manager.set("email", "use_tls", self.smtp_tls.isChecked())
        self.config_manager.set("email", "username", self.smtp_username.text().strip())
        self.config_manager.set("email", "password", self.smtp_password.text())
        self.config_manager.set("email", "sender_name", self.sender_name.text().strip() or "MiChecklist")
        self.config_manager.set(
            "email", "subject_template",
            self.subject_template.text().strip() or "[{company} {product}] {status} - {job_type}",
        )
        self.config_manager.set("email", "to", _addresses(self.to_input.text()))
        self.config_manager.set("email", "cc", _addresses(self.cc_input.text()))
        self.config_manager.set("email", "notify_success", self.notify_success.isChecked())
        self.config_manager.set("email", "notify_warning", self.notify_warning.isChecked())
        self.config_manager.set("email", "notify_failure", self.notify_failure.isChecked())
        self.config_manager.set("email", "notify_recovery", self.notify_recovery.isChecked())
        self.config_manager.set("email", "notify_module_success", self.notify_module_success.isChecked())
        self.config_manager.save_config()

    def accept(self) -> None:
        original_data = deepcopy(self.config_manager.data)
        try:
            self._save_all()
            errors, warnings = ChecklistRunner(self.config_manager).get_preflight_messages()
            if errors:
                self.config_manager.data = original_data
                self.config_manager.save_config()
                QMessageBox.warning(self, "Setup Needs Attention", "\n".join(f"- {item}" for item in errors))
                return
            if warnings:
                answer = QMessageBox.question(self, "Setup Warnings", "\n".join(f"- {item}" for item in warnings) + "\n\nInstall the service anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    self.config_manager.data = original_data
                    self.config_manager.save_config()
                    return
            service = WindowsServiceManager()
            status = service.status()
            if status.get("installed") != "true":
                service.install()
                status = service.status()
            if "RUNNING" not in status.get("state", "").upper():
                service.start()
            self.config_manager.set("general", "setup_completed", True)
            self.config_manager.save_config()
            ready_message = (
                "Configuration is valid, MiChecklistService is running, and setup is complete.\n\n"
                "Configured schedules are managed by MiChecklistService."
            )
            QMessageBox.information(self, "MiChecklist Ready", ready_message)
        except Exception as exc:
            self.config_manager.data = original_data
            try:
                self.config_manager.save_config()
            except Exception:
                pass
            QMessageBox.critical(self, "Setup Failed", f"Setup could not finish:\n\n{exc}\n\nRun MiChecklist as Administrator and retry.")
            return
        super().accept()
