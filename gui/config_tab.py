from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, QTime, Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTabWidget,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QFileDialog,
    QMessageBox,
    QLabel,
    QSpinBox,
    QTimeEdit,
)

from modules.sql_backup import SQLBackup
from core.scheduled_jobs import ScheduledJobStore
from utils.config_manager import ConfigError, ConfigManager
from utils.email_validation import invalid_millensys_addresses, parse_addresses
from utils.external_drive import capture_external_destination


class ConfigTab(QWidget):
    cleanup_only_requested = Signal()

    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager
        self._iis_tables_loaded = False
        self._loading_iis_tables = False
        self._services_loaded = False
        self._cleanup_detection_thread: QThread | None = None
        self._cleanup_detection_worker = None
        self._cleanup_detection_source = ""

        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.backup_root_input = QLineEdit()
        self.backup_browse_button = QPushButton("Browse")
        self.external_destination_checkbox = QCheckBox(
            "Destination is a removable external drive"
        )
        self.external_check_minutes = QSpinBox()
        self.external_check_minutes.setRange(1, 1440)
        self.external_check_minutes.setSuffix(" minutes")
        self.external_reminder_minutes = QSpinBox()
        self.external_reminder_minutes.setRange(5, 10080)
        self.external_reminder_minutes.setSuffix(" minutes")
        self.external_identity_label = QLabel("External drive identity: Not registered")
        self.external_identity_label.setWordWrap(True)
        self.compression_checkbox = QCheckBox(
            "Use SQL Server native backup compression (WITH COMPRESSION)"
        )
        self.compression_checkbox.setToolTip(
            "When enabled, SQL Server compresses the full backup while creating the .bak file. "
            "This is independent of the ZIP archive option below."
        )
        self.hash_checkbox = QCheckBox("Generate SHA256 Hashes")
        self.incremental_checkbox = QCheckBox("Use incremental backups when supported")
        self.retention_enabled = QCheckBox("Remove old backup folders automatically")
        self.retention_max_age = QSpinBox()
        self.retention_max_age.setRange(1, 3650)
        self.retention_max_age.setSuffix(" days")
        self.retention_max_age.setToolTip("Backup folders older than this number of days will be deleted.")
        self.retention_minimum = QSpinBox()
        self.retention_minimum.setRange(1, 100)
        self.retention_minimum.setSuffix(" backups")
        self.retention_minimum.setToolTip(
            "Retention never deletes the newest successful backup generations."
        )

        self.email_enabled = QCheckBox("Enable SMTP notifications")
        self.email_server = QLineEdit()
        self.email_port = QSpinBox()
        self.email_port.setRange(1, 65535)
        self.email_tls = QCheckBox("Use STARTTLS")
        self.email_username = QLineEdit()
        self.email_sender_name = QLineEdit()
        self.email_client_name = QLineEdit()
        self.email_client_branch = QLineEdit()
        self.email_client_server = QLineEdit()
        self.email_client_comment = QLineEdit()
        self.email_client_comment.setPlaceholderText("Optional note shown in the email header")
        self.email_password = QLineEdit()
        self.email_password.setEchoMode(QLineEdit.Password)
        self.email_to = QLineEdit()
        self.email_cc = QLineEdit()
        self.email_success = QCheckBox("Notify after successful jobs")
        self.email_warning = QCheckBox("Notify when a job completes with warnings")
        self.email_failure = QCheckBox("Notify after failed jobs")
        self.email_recovery = QCheckBox("Notify when an interrupted job is recovered and restarted")

        self.server_input = QLineEdit()
        self.sql_backup_enabled_checkbox = QCheckBox("Enable Database Backup")
        self.sql_archive_compression_checkbox = QCheckBox(
            "Compress each verified full backup to ZIP and remove the original .bak"
        )
        self.sql_archive_compression_checkbox.setToolTip(
            "Creates a WinRAR-compatible ZIP only after SQL verification succeeds. "
            "The Windows service uses this saved setting too."
        )
        self.instance_input = QLineEdit()
        self.database_input = QLineEdit()
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.windows_auth_checkbox = QCheckBox(
            "Use Windows Authentication (SQL username and password are ignored)"
        )

        self.iis_backup_enabled_checkbox = QCheckBox("Enable IIS Backup")
        self.iis_autodetect_checkbox = QCheckBox("Auto Detect IIS Sites")
        self.iis_config_checkbox = QCheckBox("Include IIS Configuration Backup")
        self.iis_refresh_sites_button = QPushButton("Refresh IIS Sites")
        self.iis_refresh_apps_button = QPushButton("Refresh Child Apps")
        self.iis_refresh_pools_button = QPushButton("Refresh App Pools")
        self.schedule_iis_site_button = QPushButton("Schedule Selected Site")
        self.schedule_iis_app_button = QPushButton("Schedule Selected Application")
        self.iis_sites_table = QTableWidget(0, 2)
        self.iis_apps_table = QTableWidget(0, 5)
        self.iis_pools_table = QTableWidget(0, 3)

        self.db_refresh_button = QPushButton("Refresh Databases")
        self.db_table = QTableWidget(0, 3)

        self.service_backup_enabled_checkbox = QCheckBox("Enable Windows Service Backup")
        self.service_search_input = QLineEdit()
        self.service_search_input.setPlaceholderText("Search services (default: Mi)")
        self.service_refresh_button = QPushButton("Refresh Services")
        self.service_select_all_button = QPushButton("Select All")
        self.service_deselect_all_button = QPushButton("Deselect All")
        self.schedule_service_button = QPushButton("Schedule Selected Service")
        self.service_table = QTableWidget(0, 5)
        self.save_service_settings_button = QPushButton("Save Service Settings")

        self.folder_table = QTableWidget(0, 3)
        self.add_folder_button = QPushButton("Add Folder")
        self.edit_folder_button = QPushButton("Edit Folder")
        self.toggle_folder_button = QPushButton("Enable / Disable")
        self.schedule_folder_button = QPushButton("Schedule Selected Folder")
        self.remove_folder_button = QPushButton("Remove Folder")
        self.cleanup_enabled_checkbox = QCheckBox("Enable Cleanup Tasks")
        self.cleanup_detect_button = QPushButton("Auto Detect from Selected IIS Apps")
        self.cleanup_detect_services_button = QPushButton("Auto Detect from Selected Services")
        self.cleanup_add_button = QPushButton("Add Cleanup Task")
        self.cleanup_edit_button = QPushButton("Edit Cleanup Task")
        self.cleanup_remove_button = QPushButton("Remove Cleanup Task")
        self.cleanup_table = QTableWidget(0, 5)
        self.save_general_button = QPushButton("Save General Settings")
        self.save_sql_button = QPushButton("Save SQL Settings")
        self.save_iis_button = QPushButton("Save IIS Settings")
        self.save_folders_button = QPushButton("Save Folder Settings")
        self.save_cleanup_button = QPushButton("Save Cleanup Settings")
        self.save_schedule_button = QPushButton("Save Schedule Settings")
        self.save_retention_button = QPushButton("Save Retention Settings")
        self.save_notifications_button = QPushButton("Save Notification Settings")
        self.run_cleanup_button = QPushButton("Run Cleanup Only")
        self.schedule_enabled_checkbox = QCheckBox("Enable Automated Checklist Schedule")
        self.schedule_mode_combo = QComboBox()
        self.schedule_mode_combo.addItem("Weekly", "weekly")
        self.schedule_mode_combo.addItem("Daily", "daily")
        self.schedule_mode_combo.addItem("Every N Days", "days_after_last_backup")
        self.schedule_mode_combo.addItem("At Windows Startup", "after_restart")
        self.schedule_weekday_label = QLabel("Weekly Day:")
        self.schedule_days_label = QLabel("Run Every:")
        self.schedule_time_label = QLabel("Start Time:")
        self.schedule_weekday_combo = QComboBox()
        self.schedule_weekday_combo.addItems(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
        self.schedule_days_input = QSpinBox()
        self.schedule_days_input.setRange(1, 365)
        self.schedule_days_input.setSuffix(" days")
        self.schedule_time_input = QTimeEdit(QTime(2, 0))
        self.schedule_time_input.setDisplayFormat("HH:mm")
        self.schedule_test_restart_checkbox = QCheckBox("Test: Run Checklist Once At Next Log On")
        self.schedule_run_missed_checkbox = QCheckBox("Run a missed schedule as soon as the server is available")
        self.schedule_delete_backups_checkbox = QCheckBox("Keep only the latest valid scheduled backup")
        self.schedule_delete_backups_checkbox.setToolTip(
            "The previous backup is removed only after the replacement completes successfully."
        )
        self.schedule_tray_checkbox = QCheckBox("Hide GUI to Tray When Closed")
        self.schedule_install_button = QPushButton("Apply Elevated Checklist Schedule")
        self.schedule_remove_button = QPushButton("Remove Scheduled Tasks")
        self.schedule_refresh_status_button = QPushButton("Refresh Schedule Status")
        self.schedule_run_now_button = QPushButton("Run Full Checklist Now")
        self.schedule_summary_label = QLabel("")
        self.schedule_summary_label.setWordWrap(True)
        self.schedule_scope_label = QLabel(
            "This schedule runs the full checklist using the saved settings from Cleanup, IIS, Services, Databases, and Folders. Disabled or unselected sections are skipped."
        )
        self.schedule_scope_label.setWordWrap(True)
        self.schedule_status_label = QLabel("Schedule status: Not checked")
        self.schedule_status_label.setWordWrap(True)
        self.save_button = QPushButton("Save All Configuration")
        self.activate_button = QPushButton("Save, Validate and Install Service")
        self.activate_button.setObjectName("activateChecklistButton")
        self.activate_button.setToolTip(
            "Save the complete checklist configuration, validate it, install and start "
            "the Windows service, and apply the enabled schedule."
        )
        self.test_connection_button = QPushButton("Test Connection")
        self.save_status_label = QLabel("")

        self.build_ui()
        self.load_settings()
        self.load_folder_table()
        self.attach_signals()

    def build_ui(self) -> None:
        general_group = QGroupBox("General Settings")
        general_layout = QFormLayout()
        backup_layout = QHBoxLayout()
        backup_layout.addWidget(self.backup_root_input)
        backup_layout.addWidget(self.backup_browse_button)
        general_layout.addRow("Backup Root Folder:", backup_layout)
        general_layout.addRow(self.external_destination_checkbox)
        general_layout.addRow("Drive check interval:", self.external_check_minutes)
        general_layout.addRow("Reminder interval:", self.external_reminder_minutes)
        general_layout.addRow(self.external_identity_label)
        general_layout.addRow(self.hash_checkbox)
        general_layout.addRow(self.save_general_button)
        general_group.setLayout(general_layout)

        sql_group = QGroupBox("SQL Server Settings")
        sql_layout = QFormLayout()
        sql_layout.addRow(self.sql_backup_enabled_checkbox)
        sql_layout.addRow(self.compression_checkbox)
        sql_layout.addRow(self.sql_archive_compression_checkbox)
        sql_layout.addRow("Server Name:", self.server_input)
        sql_layout.addRow("Instance Name:", self.instance_input)
        sql_layout.addRow("Database Name:", self.database_input)
        sql_layout.addRow("Username:", self.username_input)
        sql_layout.addRow("Password:", self.password_input)
        sql_layout.addRow(self.windows_auth_checkbox)
        sql_layout.addRow(self.test_connection_button)
        sql_layout.addRow(self.db_refresh_button)
        self.db_table.setHorizontalHeaderLabels(["Select", "Database", "State"])
        self.db_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        sql_layout.addRow(self.db_table)
        sql_layout.addRow(self.save_sql_button)
        sql_group.setLayout(sql_layout)

        iis_group = QGroupBox("IIS Settings")
        iis_layout = QVBoxLayout()
        iis_layout.addWidget(self.iis_backup_enabled_checkbox)
        iis_layout.addWidget(self.iis_autodetect_checkbox)
        iis_layout.addWidget(self.iis_config_checkbox)

        sites_tab = QWidget()
        sites_layout = QVBoxLayout(sites_tab)
        sites_label = QLabel("IIS Sites to Backup:")
        sites_layout.addWidget(sites_label)
        sites_layout.addWidget(self.iis_refresh_sites_button)
        self.iis_sites_table.setHorizontalHeaderLabels(["Select", "Site Name"])
        self.iis_sites_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        sites_layout.addWidget(self.iis_sites_table)
        sites_layout.addWidget(self.schedule_iis_site_button)

        apps_tab = QWidget()
        apps_layout = QVBoxLayout(apps_tab)
        apps_label = QLabel("Child Applications Under Sites:")
        apps_layout.addWidget(apps_label)
        apps_layout.addWidget(self.iis_refresh_apps_button)
        self.iis_apps_table.setHorizontalHeaderLabels(["Backup", "Cleanup", "Site > Application", "Application Pool", "Physical Path"])
        self.iis_apps_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        apps_layout.addWidget(self.iis_apps_table)
        apps_layout.addWidget(self.schedule_iis_app_button)

        pools_tab = QWidget()
        pools_layout = QVBoxLayout(pools_tab)
        pools_label = QLabel("Application Pools:")
        pools_layout.addWidget(pools_label)
        pools_layout.addWidget(self.iis_refresh_pools_button)
        self.iis_pools_table.setHorizontalHeaderLabels(["Select", "Pool Name", "State"])
        self.iis_pools_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        pools_layout.addWidget(self.iis_pools_table)

        self.iis_tabs = QTabWidget()
        self.iis_tabs.addTab(sites_tab, "Sites")
        self.iis_tabs.addTab(apps_tab, "Child Applications")
        self.iis_tabs.addTab(pools_tab, "Application Pools")
        iis_layout.addWidget(self.iis_tabs)
        iis_layout.addWidget(self.save_iis_button)
        iis_group.setLayout(iis_layout)

        service_group = QGroupBox("Windows Service Backup Settings")
        service_layout = QVBoxLayout()
        service_layout.addWidget(self.service_backup_enabled_checkbox)
        service_search_layout = QHBoxLayout()
        service_search_layout.addWidget(self.service_search_input)
        service_search_layout.addWidget(self.service_refresh_button)
        service_layout.addLayout(service_search_layout)

        self.service_table.setHorizontalHeaderLabels(["Select", "Service Name", "Display Name", "Path", "State"])
        self.service_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        service_layout.addWidget(self.service_table)

        service_button_layout = QHBoxLayout()
        service_button_layout.addWidget(self.service_select_all_button)
        service_button_layout.addWidget(self.service_deselect_all_button)
        service_button_layout.addWidget(self.schedule_service_button)
        service_button_layout.addWidget(self.save_service_settings_button)
        service_layout.addLayout(service_button_layout)
        service_group.setLayout(service_layout)

        folder_group = QGroupBox("Folder Backup Settings")
        folder_layout = QVBoxLayout()
        self.folder_table.setHorizontalHeaderLabels(["Folder Name", "Folder Path", "Enabled"])
        self.folder_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        folder_layout.addWidget(self.folder_table)

        folder_button_layout = QHBoxLayout()
        folder_button_layout.addWidget(self.add_folder_button)
        folder_button_layout.addWidget(self.edit_folder_button)
        folder_button_layout.addWidget(self.toggle_folder_button)
        folder_button_layout.addWidget(self.schedule_folder_button)
        folder_button_layout.addWidget(self.remove_folder_button)
        folder_layout.addLayout(folder_button_layout)
        folder_layout.addWidget(self.save_folders_button)
        folder_group.setLayout(folder_layout)

        cleanup_group = QGroupBox("Log Cleanup Settings")
        cleanup_layout = QVBoxLayout()
        cleanup_layout.addWidget(self.cleanup_enabled_checkbox)
        cleanup_layout.addWidget(QLabel("Cleanup deletes matching files only. Leave extensions empty to delete all files. Folders are preserved."))
        self.cleanup_table.setHorizontalHeaderLabels(["Enabled", "Task Name", "Folder Path", "Extensions", "Source"])
        self.cleanup_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        cleanup_layout.addWidget(self.cleanup_table)
        cleanup_button_layout = QHBoxLayout()
        cleanup_button_layout.addWidget(self.cleanup_detect_button)
        cleanup_button_layout.addWidget(self.cleanup_detect_services_button)
        cleanup_button_layout.addWidget(self.cleanup_add_button)
        cleanup_button_layout.addWidget(self.cleanup_edit_button)
        cleanup_button_layout.addWidget(self.cleanup_remove_button)
        cleanup_layout.addLayout(cleanup_button_layout)
        cleanup_layout.addWidget(self.save_cleanup_button)
        cleanup_layout.addWidget(self.run_cleanup_button)
        cleanup_group.setLayout(cleanup_layout)

        schedule_group = QGroupBox("Automated Full Checklist")
        schedule_layout = QVBoxLayout(schedule_group)
        intro = QLabel("Set one reliable Windows schedule for the complete support checklist.")
        intro.setWordWrap(True)
        schedule_layout.addWidget(intro)
        schedule_layout.addWidget(self.schedule_enabled_checkbox)

        timing_group = QGroupBox("1. Choose When It Runs")
        timing_layout = QFormLayout(timing_group)
        timing_layout.addRow("Frequency:", self.schedule_mode_combo)
        timing_layout.addRow(self.schedule_weekday_label, self.schedule_weekday_combo)
        timing_layout.addRow(self.schedule_days_label, self.schedule_days_input)
        timing_layout.addRow(self.schedule_time_label, self.schedule_time_input)
        timing_layout.addRow("Summary:", self.schedule_summary_label)
        schedule_layout.addWidget(timing_group)

        scope_group = QGroupBox("2. What Will Run")
        scope_layout = QVBoxLayout(scope_group)
        scope_layout.addWidget(self.schedule_scope_label)
        schedule_layout.addWidget(scope_group)

        safety_group = QGroupBox("3. Reliability and Safety")
        safety_layout = QVBoxLayout(safety_group)
        safety_layout.addWidget(self.schedule_run_missed_checkbox)
        safety_layout.addWidget(self.schedule_delete_backups_checkbox)
        safety_layout.addWidget(self.schedule_test_restart_checkbox)
        safety_layout.addWidget(self.schedule_tray_checkbox)
        overlap_note = QLabel("Overlapping runs are blocked automatically. Failed runs keep the previous successful backup. The task runs as Windows SYSTEM with highest privileges.")
        overlap_note.setWordWrap(True)
        safety_layout.addWidget(overlap_note)
        schedule_layout.addWidget(safety_group)

        status_group = QGroupBox("4. Apply and Verify")
        status_layout = QVBoxLayout(status_group)
        status_layout.addWidget(self.schedule_status_label)
        status_buttons = QHBoxLayout()
        status_buttons.addWidget(self.schedule_install_button)
        status_buttons.addWidget(self.schedule_refresh_status_button)
        status_buttons.addWidget(self.schedule_run_now_button)
        status_buttons.addWidget(self.schedule_remove_button)
        status_layout.addLayout(status_buttons)
        access_note = QLabel("Before testing, confirm that SYSTEM can access the backup destination and SQL Server. Windows authentication is recommended for unattended database backups.")
        access_note.setWordWrap(True)
        status_layout.addWidget(access_note)
        schedule_layout.addWidget(status_group)
        schedule_layout.addWidget(self.save_schedule_button)
        schedule_layout.addStretch()

        notification_group = QGroupBox("SMTP Email Notifications")
        notification_layout = QFormLayout(notification_group)
        notification_layout.addRow(self.email_enabled)
        notification_layout.addRow("SMTP server:", self.email_server)
        notification_layout.addRow("SMTP port:", self.email_port)
        notification_layout.addRow(self.email_tls)
        notification_layout.addRow("Username:", self.email_username)
        notification_layout.addRow("Sender name:", self.email_sender_name)
        notification_layout.addRow("App password:", self.email_password)
        notification_layout.addRow("To (comma separated):", self.email_to)
        notification_layout.addRow("Cc (comma separated):", self.email_cc)
        notification_layout.addRow(self.email_success)
        notification_layout.addRow(self.email_warning)
        notification_layout.addRow(self.email_failure)
        notification_layout.addRow(self.email_recovery)
        client_info_group = QGroupBox("Client Information")
        client_info_layout = QFormLayout(client_info_group)
        client_info_layout.addRow("Client name:", self.email_client_name)
        client_info_layout.addRow("Branch:", self.email_client_branch)
        client_info_layout.addRow("Server:", self.email_client_server)
        client_info_layout.addRow("Comment:", self.email_client_comment)
        notification_layout.addRow(client_info_group)
        notification_layout.addRow(self.save_notifications_button)

        retention_group = QGroupBox("Backup Retention Policy")
        retention_layout = QFormLayout(retention_group)
        retention_layout.addRow(self.incremental_checkbox)
        retention_layout.addRow(self.retention_enabled)
        retention_layout.addRow("Remove backups older than:", self.retention_max_age)
        retention_layout.addRow("Always preserve at least:", self.retention_minimum)
        retention_layout.addRow(self.save_retention_button)

        self.config_tabs = QTabWidget()
        self.config_tabs.addTab(general_group, "General")
        self.config_tabs.addTab(sql_group, "SQL Server")
        self.config_tabs.addTab(iis_group, "IIS")
        self.config_tabs.addTab(service_group, "Services")
        self.config_tabs.addTab(folder_group, "Folders")
        self.config_tabs.addTab(cleanup_group, "Cleanup")
        self.config_tabs.addTab(schedule_group, "Schedule")
        self.config_tabs.addTab(retention_group, "Retention")
        self.config_tabs.addTab(notification_group, "Notifications")

        self.layout.addWidget(self.config_tabs)
        self.layout.addWidget(self.save_button)
        self.layout.addWidget(self.activate_button)
        self.layout.addWidget(self.save_status_label)
        self.layout.addStretch()

    def on_refresh_databases(self) -> None:
        self.load_database_table()

    def on_refresh_iis_pools(self) -> None:
        self._refresh_iis_table(self.load_iis_pools_table)

    def on_refresh_iis_apps(self) -> None:
        self._refresh_iis_table(self.load_iis_apps_table)

    def on_refresh_iis_sites(self) -> None:
        self._refresh_iis_table(self.load_iis_sites_table)

    def _refresh_iis_table(self, loader) -> None:
        self._loading_iis_tables = True
        try:
            loader()
        finally:
            self._loading_iis_tables = False

    def load_database_table(self) -> None:
        if not self.sql_backup_enabled_checkbox.isChecked():
            return
        selected = set(self.config_manager.get("sql_server", "databases", []))
        self.db_table.setRowCount(0)
        try:
            sql = SQLBackup(
                server=self.server_input.text().strip(),
                instance=self.instance_input.text().strip(),
                database=self.database_input.text().strip(),
                username=self.username_input.text().strip(),
                password=self.password_input.text(),
                use_windows_auth=self.windows_auth_checkbox.isChecked(),
            )
            dbs = sql.list_databases()
        except Exception as exc:
            QMessageBox.warning(self, "Database Load Error", f"Unable to load databases: {exc}")
            return

        for db in dbs:
            row = self.db_table.rowCount()
            self.db_table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if db.get("name") in selected else Qt.Unchecked)
            self.db_table.setItem(row, 0, chk)
            self.db_table.setItem(row, 1, QTableWidgetItem(db.get("name", "")))
            self.db_table.setItem(row, 2, QTableWidgetItem(db.get("state", "")))

    def load_iis_sites_table(self) -> None:
        if not self.iis_backup_enabled_checkbox.isChecked():
            return
        selected = set(self.config_manager.get("iis", "selected_sites", []))
        self.iis_sites_table.setRowCount(0)
        try:
            from modules.iis_manager import IISManager
            sites = IISManager().detect_sites()
        except Exception as exc:
            QMessageBox.warning(self, "IIS Sites Load Error", f"Unable to load IIS sites: {exc}")
            return

        for site in sites:
            row = self.iis_sites_table.rowCount()
            self.iis_sites_table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if site.name in selected else Qt.Unchecked)
            self.iis_sites_table.setItem(row, 0, chk)
            site_item = QTableWidgetItem(site.name)
            site_item.setData(Qt.UserRole, site.physical_path)
            self.iis_sites_table.setItem(row, 1, site_item)

    def load_iis_pools_table(self) -> None:
        if not self.iis_backup_enabled_checkbox.isChecked():
            return
        selected = set(self.config_manager.get("iis", "selected_pools", []))
        self.iis_pools_table.setRowCount(0)
        try:
            from modules.iis_manager import IISManager
            pools = IISManager().list_app_pools()
        except Exception as exc:
            QMessageBox.warning(self, "IIS App Pools Load Error", f"Unable to load IIS app pools: {exc}")
            return

        for pool in pools:
            row = self.iis_pools_table.rowCount()
            self.iis_pools_table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if pool.get("name") in selected else Qt.Unchecked)
            self.iis_pools_table.setItem(row, 0, chk)
            self.iis_pools_table.setItem(row, 1, QTableWidgetItem(pool.get("name", "")))
            self.iis_pools_table.setItem(row, 2, QTableWidgetItem(pool.get("state", "")))

    def load_iis_apps_table(self) -> None:
        if not self.iis_backup_enabled_checkbox.isChecked():
            return
        selected = set(self.config_manager.get("iis", "selected_apps", []))
        selected_cleanup = set(self.config_manager.get("iis", "selected_cleanup_apps", []))
        self.iis_apps_table.setRowCount(0)
        try:
            from modules.iis_manager import IISManager
            apps = IISManager().list_applications()
        except Exception as exc:
            QMessageBox.warning(self, "IIS Child Apps Load Error", f"Unable to load IIS child applications: {exc}")
            return

        for app in apps:
            row = self.iis_apps_table.rowCount()
            self.iis_apps_table.insertRow(row)
            site_name = app.get("site", "")
            app_path = app.get("path", "")
            app_key = f"{site_name}|{app_path}"
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if app_key in selected else Qt.Unchecked)
            self.iis_apps_table.setItem(row, 0, chk)
            cleanup_chk = QTableWidgetItem()
            cleanup_chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            cleanup_chk.setCheckState(Qt.Checked if app_key in selected_cleanup else Qt.Unchecked)
            self.iis_apps_table.setItem(row, 1, cleanup_chk)
            self.iis_apps_table.setItem(row, 2, QTableWidgetItem(f"{site_name} > {app_path}"))
            self.iis_apps_table.setItem(row, 3, QTableWidgetItem(app.get("application_pool", "")))
            path_item = QTableWidgetItem(app.get("physical_path", ""))
            path_item.setData(Qt.UserRole, app_key)
            self.iis_apps_table.setItem(row, 4, path_item)

    def attach_signals(self) -> None:
        self.backup_browse_button.clicked.connect(self.on_browse_backup_root)
        self.add_folder_button.clicked.connect(self.on_add_folder)
        self.edit_folder_button.clicked.connect(self.on_edit_folder)
        self.toggle_folder_button.clicked.connect(self.on_toggle_folder)
        self.schedule_folder_button.clicked.connect(self.on_schedule_folder)
        self.schedule_iis_site_button.clicked.connect(self.on_schedule_iis_site)
        self.schedule_iis_app_button.clicked.connect(self.on_schedule_iis_application)
        self.schedule_service_button.clicked.connect(self.on_schedule_service)
        self.remove_folder_button.clicked.connect(self.on_remove_folder)
        self.save_general_button.clicked.connect(self.on_save_general_clicked)
        self.save_sql_button.clicked.connect(self.on_save_sql_clicked)
        self.save_iis_button.clicked.connect(self.on_save_iis_clicked)
        self.save_service_settings_button.clicked.connect(self.on_save_service_settings_clicked)
        self.save_folders_button.clicked.connect(self.on_save_folders_clicked)
        self.save_cleanup_button.clicked.connect(self.on_save_cleanup_clicked)
        self.save_schedule_button.clicked.connect(self.on_save_schedule_clicked)
        self.save_retention_button.clicked.connect(self.on_save_retention_clicked)
        self.save_notifications_button.clicked.connect(self.on_save_notifications_clicked)
        self.run_cleanup_button.clicked.connect(self.on_run_cleanup_only_clicked)
        self.schedule_install_button.clicked.connect(self.on_install_schedule_clicked)
        self.schedule_remove_button.clicked.connect(self.on_remove_schedule_clicked)
        self.schedule_refresh_status_button.clicked.connect(self.on_refresh_schedule_status)
        self.schedule_run_now_button.clicked.connect(self.on_run_schedule_now)
        self.schedule_enabled_checkbox.toggled.connect(self.update_schedule_states)
        self.schedule_mode_combo.currentIndexChanged.connect(self.update_schedule_states)
        self.schedule_weekday_combo.currentTextChanged.connect(self.update_schedule_summary)
        self.schedule_days_input.valueChanged.connect(self.update_schedule_summary)
        self.schedule_time_input.timeChanged.connect(self.update_schedule_summary)
        self.retention_enabled.toggled.connect(self.update_retention_summary)
        self.save_button.clicked.connect(self.on_save_clicked)
        self.activate_button.clicked.connect(self.on_save_validate_and_install)
        self.test_connection_button.clicked.connect(self.on_test_connection)
        self.db_refresh_button.clicked.connect(self.on_refresh_databases)
        self.iis_refresh_sites_button.clicked.connect(self.on_refresh_iis_sites)
        self.iis_refresh_apps_button.clicked.connect(self.on_refresh_iis_apps)
        self.iis_refresh_pools_button.clicked.connect(self.on_refresh_iis_pools)
        self.service_refresh_button.clicked.connect(self.on_refresh_services)
        self.service_select_all_button.clicked.connect(self.on_select_all_services)
        self.service_deselect_all_button.clicked.connect(self.on_deselect_all_services)
        self.cleanup_detect_button.clicked.connect(self.on_detect_cleanup_tasks)
        self.cleanup_detect_services_button.clicked.connect(self.on_detect_service_cleanup_tasks)
        self.cleanup_add_button.clicked.connect(self.on_add_cleanup_task)
        self.cleanup_edit_button.clicked.connect(self.on_edit_cleanup_task)
        self.cleanup_remove_button.clicked.connect(self.on_remove_cleanup_task)
        self.service_search_input.textChanged.connect(self.on_service_search_changed)
        self.sql_backup_enabled_checkbox.toggled.connect(self.update_section_states)
        self.windows_auth_checkbox.toggled.connect(self.update_section_states)
        self.username_input.textEdited.connect(self._select_sql_auth_for_credentials)
        self.password_input.textEdited.connect(self._select_sql_auth_for_credentials)
        self.iis_backup_enabled_checkbox.toggled.connect(self.on_iis_backup_enabled_changed)
        self.iis_sites_table.itemChanged.connect(self.on_iis_selection_changed)
        self.iis_apps_table.itemChanged.connect(self.on_iis_selection_changed)
        self.iis_pools_table.itemChanged.connect(self.on_iis_selection_changed)
        self.service_backup_enabled_checkbox.toggled.connect(self.on_service_backup_enabled_changed)
        self.cleanup_enabled_checkbox.toggled.connect(self.update_section_states)
        self.config_tabs.currentChanged.connect(self.on_config_tab_changed)

    def load_settings(self) -> None:
        general = self.config_manager.get_section("general")
        self.backup_root_input.setText(str(general.get("backup_root_folder", "")))
        external = self.config_manager.get_section("external_destination")
        self.external_destination_checkbox.setChecked(bool(external.get("enabled", False)))
        self.external_check_minutes.setValue(int(external.get("check_interval_minutes", 5)))
        self.external_reminder_minutes.setValue(int(external.get("reminder_interval_minutes", 120)))
        serial = str(external.get("volume_serial", "")).strip()
        label = str(external.get("volume_label", "")).strip()
        self.external_identity_label.setText(
            f"External drive identity: {label or 'Unnamed'} ({serial})"
            if serial else "External drive identity: Not registered"
        )
        self.compression_checkbox.setChecked(bool(general.get("compression_enabled", True)))
        self.hash_checkbox.setChecked(bool(general.get("generate_sha256_hashes", True)))
        self.incremental_checkbox.setChecked(bool(general.get("incremental_backups", True)))
        retention = self.config_manager.get_section("retention")
        self.retention_enabled.setChecked(bool(retention.get("enabled", True)))
        self.retention_max_age.setValue(int(retention.get("max_age_days", 90)))
        self.retention_minimum.setValue(int(retention.get("minimum_successful_backups", 1)))
        self.update_retention_summary()

        email = self.config_manager.get_section("email")
        self.email_enabled.setChecked(bool(email.get("enabled", False)))
        self.email_server.setText(str(email.get("smtp_server", "smtp.gmail.com")))
        self.email_port.setValue(int(email.get("smtp_port", 587)))
        self.email_tls.setChecked(bool(email.get("use_tls", True)))
        self.email_username.setText(str(email.get("username", "")))
        self.email_sender_name.setText(str(email.get("sender_name", "Support Checklist")))
        self.email_client_name.setText(str(email.get("client_name", "")))
        self.email_client_branch.setText(str(email.get("client_branch", "")))
        self.email_client_server.setText(str(email.get("client_server", "")))
        self.email_client_comment.setText(str(email.get("client_comment", "")))
        self.email_password.setText(str(email.get("password", "")))
        self.email_to.setText(", ".join(email.get("to", [])))
        self.email_cc.setText(", ".join(email.get("cc", [])))
        self.email_success.setChecked(bool(email.get("notify_success", True)))
        self.email_warning.setChecked(bool(email.get("notify_warning", True)))
        self.email_failure.setChecked(bool(email.get("notify_failure", True)))
        self.email_recovery.setChecked(bool(email.get("notify_recovery", True)))

        sql = self.config_manager.get_section("sql_server")
        self.sql_backup_enabled_checkbox.setChecked(bool(sql.get("backup_enabled", True)))
        self.sql_archive_compression_checkbox.setChecked(
            bool(sql.get("archive_compression_enabled", True))
        )
        self.server_input.setText(str(sql.get("server_name", "")))
        self.instance_input.setText(str(sql.get("instance_name", "")))
        self.database_input.setText(str(sql.get("database_name", "")))
        self.username_input.setText(str(sql.get("username", "")))
        self.password_input.setText(str(sql.get("password", "")))
        self.windows_auth_checkbox.setChecked(bool(sql.get("use_windows_auth", True)))

        iis = self.config_manager.get_section("iis")
        self.iis_backup_enabled_checkbox.setChecked(bool(iis.get("backup_enabled", True)))
        self.iis_autodetect_checkbox.setChecked(bool(iis.get("auto_detect_sites", True)))
        self.iis_config_checkbox.setChecked(bool(iis.get("include_iis_configuration", True)))

        services = self.config_manager.get_section("services")
        self.service_backup_enabled_checkbox.setChecked(bool(services.get("backup_enabled", True)))
        self.service_search_input.setText(str(services.get("service_search_filter", "Mi")))
        self.cleanup_enabled_checkbox.setChecked(bool(self.config_manager.get("cleanup", "enabled", False)))
        self.load_cleanup_table()
        schedule = self.config_manager.get_section("schedule")
        self.schedule_enabled_checkbox.setChecked(bool(schedule.get("enabled", False)))
        self.schedule_mode_combo.setCurrentIndex(max(0, self.schedule_mode_combo.findData(schedule.get("mode", "weekly"))))
        self.schedule_weekday_combo.setCurrentText(str(schedule.get("weekday", "Sunday")))
        self.schedule_days_input.setValue(int(schedule.get("days_interval", 7)))
        self.schedule_time_input.setTime(QTime.fromString(str(schedule.get("start_time", "02:00")), "HH:mm"))
        self.schedule_test_restart_checkbox.setChecked(bool(schedule.get("test_run_after_restart", False)))
        self.schedule_run_missed_checkbox.setChecked(bool(schedule.get("run_missed_schedule", True)))
        self.schedule_delete_backups_checkbox.setChecked(bool(schedule.get("delete_previous_automated_backups", True)))
        self.schedule_tray_checkbox.setChecked(bool(schedule.get("hide_to_tray", True)))
        self.load_folder_table()
        self.update_schedule_states()
        self.update_section_states()

    def update_schedule_states(self) -> None:
        mode = self.schedule_mode_combo.currentData()
        weekly = mode == "weekly"
        interval = mode == "days_after_last_backup"
        timed = mode in {"weekly", "daily", "days_after_last_backup"}
        self.schedule_weekday_label.setVisible(weekly)
        self.schedule_weekday_combo.setVisible(weekly)
        self.schedule_days_label.setVisible(interval)
        self.schedule_days_input.setVisible(interval)
        self.schedule_time_label.setVisible(timed)
        self.schedule_time_input.setVisible(timed)
        self.update_schedule_summary()
        enabled = self.schedule_enabled_checkbox.isChecked()
        for widget in (
            self.schedule_mode_combo,
            self.schedule_weekday_combo,
            self.schedule_days_input,
            self.schedule_time_input,
            self.schedule_test_restart_checkbox,
            self.schedule_delete_backups_checkbox,
            self.schedule_run_missed_checkbox,
        ):
            widget.setEnabled(enabled)
        self.schedule_run_now_button.setEnabled(enabled)

    def update_schedule_summary(self) -> None:
        mode = self.schedule_mode_combo.currentData()
        start_time = self.schedule_time_input.time().toString("HH:mm")
        if mode == "weekly":
            summary = f"Every {self.schedule_weekday_combo.currentText()} at {start_time}."
        elif mode == "daily":
            summary = f"Every day at {start_time}."
        elif mode == "days_after_last_backup":
            summary = f"Every {self.schedule_days_input.value()} days at {start_time}."
        else:
            summary = "At Windows startup, after core services become available."
        self.schedule_summary_label.setText(summary)

    def update_retention_summary(self) -> None:
        enabled = self.retention_enabled.isChecked()
        self.retention_max_age.setEnabled(enabled)
        self.retention_minimum.setEnabled(enabled)

    def update_section_states(self) -> None:
        sql_enabled = self.sql_backup_enabled_checkbox.isChecked()
        for widget in (
            self.compression_checkbox,
            self.sql_archive_compression_checkbox,
            self.server_input,
            self.instance_input,
            self.database_input,
            self.username_input,
            self.password_input,
            self.windows_auth_checkbox,
            self.test_connection_button,
            self.db_refresh_button,
            self.db_table,
        ):
            widget.setEnabled(sql_enabled)

        sql_credentials_active = sql_enabled and not self.windows_auth_checkbox.isChecked()
        self.username_input.setStyleSheet("" if sql_credentials_active else "color: #7f8794;")
        self.password_input.setStyleSheet("" if sql_credentials_active else "color: #7f8794;")

        iis_enabled = self.iis_backup_enabled_checkbox.isChecked()
        for widget in (
            self.iis_autodetect_checkbox,
            self.iis_config_checkbox,
            self.iis_refresh_sites_button,
            self.iis_refresh_apps_button,
            self.iis_refresh_pools_button,
            self.schedule_iis_site_button,
            self.schedule_iis_app_button,
            self.iis_tabs,
            self.iis_sites_table,
            self.iis_apps_table,
            self.iis_pools_table,
        ):
            widget.setEnabled(iis_enabled)

        services_enabled = self.service_backup_enabled_checkbox.isChecked()
        for widget in (
            self.service_search_input,
            self.service_refresh_button,
            self.service_select_all_button,
            self.service_deselect_all_button,
            self.schedule_service_button,
            self.service_table,
        ):
            widget.setEnabled(services_enabled)

        cleanup_enabled = self.cleanup_enabled_checkbox.isChecked()
        for widget in (
            self.cleanup_detect_button,
            self.cleanup_detect_services_button,
            self.cleanup_add_button,
            self.cleanup_edit_button,
            self.cleanup_remove_button,
            self.cleanup_table,
            self.run_cleanup_button,
        ):
            widget.setEnabled(cleanup_enabled)

    def _select_sql_auth_for_credentials(self, _text: str = "") -> None:
        """Entering SQL credentials must make them the active authentication method."""
        if self.username_input.text().strip() or self.password_input.text():
            self.windows_auth_checkbox.setChecked(False)
            self.update_section_states()

    def on_config_tab_changed(self, index: int) -> None:
        if index == 2 and self.iis_backup_enabled_checkbox.isChecked() and not self._iis_tables_loaded:
            self.load_iis_tables()
        elif index == 3 and self.service_backup_enabled_checkbox.isChecked() and not self._services_loaded:
            self.load_service_table()
        elif index == 6:
            self.on_refresh_schedule_status()

    def on_iis_backup_enabled_changed(self, enabled: bool) -> None:
        self.update_section_states()
        if enabled and self.config_tabs.currentIndex() == 2:
            self.load_iis_tables()

    def on_service_backup_enabled_changed(self, enabled: bool) -> None:
        self.update_section_states()
        if enabled and self.config_tabs.currentIndex() == 3:
            self.load_service_table()

    def load_iis_tables(self) -> None:
        self._loading_iis_tables = True
        try:
            self.load_iis_sites_table()
            self.load_iis_apps_table()
            self.load_iis_pools_table()
            self._iis_tables_loaded = True
        finally:
            self._loading_iis_tables = False

    def on_iis_selection_changed(self, item: QTableWidgetItem) -> None:
        """Persist IIS checkbox selections immediately so service runs match the UI."""
        if self._loading_iis_tables or not self._iis_tables_loaded:
            return
        table = self.sender()
        checkbox_column = item.column() in {0, 1} if table is self.iis_apps_table else item.column() == 0
        if not checkbox_column:
            return
        try:
            self.save_iis_settings()
            self.config_manager.save_config()
            self.save_status_label.setText("IIS selection saved automatically.")
        except (ConfigError, ValueError) as exc:
            self.save_status_label.setText(f"Unable to save IIS selection: {exc}")

    def load_folder_table(self) -> None:
        folders = self.config_manager.get("folder_backup", "folders", [])
        self.folder_table.setRowCount(0)
        for folder in folders:
            row = self.folder_table.rowCount()
            self.folder_table.insertRow(row)
            self.folder_table.setItem(row, 0, QTableWidgetItem(folder.get("name", "")))
            self.folder_table.setItem(row, 1, QTableWidgetItem(folder.get("path", "")))
            self.folder_table.setItem(row, 2, QTableWidgetItem("Yes" if folder.get("enabled", True) else "No"))

    def load_cleanup_table(self) -> None:
        from modules.cleanup_manager import format_extensions

        tasks = self.config_manager.get("cleanup", "tasks", [])
        self.cleanup_table.setRowCount(0)
        for task in tasks:
            row = self.cleanup_table.rowCount()
            self.cleanup_table.insertRow(row)
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            enabled_item.setCheckState(Qt.Checked if task.get("enabled", True) else Qt.Unchecked)
            self.cleanup_table.setItem(row, 0, enabled_item)
            self.cleanup_table.setItem(row, 1, QTableWidgetItem(str(task.get("name", ""))))
            self.cleanup_table.setItem(row, 2, QTableWidgetItem(str(task.get("path", ""))))
            self.cleanup_table.setItem(row, 3, QTableWidgetItem(format_extensions(task.get("extensions", []))))
            self.cleanup_table.setItem(row, 4, QTableWidgetItem(str(task.get("source", "Manual"))))

    def on_detect_cleanup_tasks(self) -> None:
        selected_apps = self._get_selected_iis_cleanup_app_keys()
        if not selected_apps:
            QMessageBox.warning(self, "Cleanup Auto Detect", "Select IIS child applications for cleanup before auto-detecting cleanup folders.")
            return
        self._start_cleanup_detection("iis", selected_apps)

    def on_detect_service_cleanup_tasks(self) -> None:
        selected_services = self._get_selected_service_names()
        if not selected_services:
            QMessageBox.warning(self, "Cleanup Auto Detect", "Select Windows services before auto-detecting cleanup folders.")
            return
        self._start_cleanup_detection("services", selected_services)

    def _start_cleanup_detection(self, source: str, selected_items: set[str]) -> None:
        from modules.cleanup_manager import normalize_folder_names
        from modules.cleanup_detection_worker import CleanupDetectionWorker

        folder_names, ok = QInputDialog.getText(
            self,
            "Cleanup Auto Detect",
            "Folder names to detect, separated by commas:",
            text="Exception, logs",
        )
        if not ok:
            return
        if not normalize_folder_names(folder_names):
            QMessageBox.warning(self, "Cleanup Auto Detect", "Configure at least one folder name to detect.")
            return
        if self._cleanup_detection_thread and self._cleanup_detection_thread.isRunning():
            return
        self._cleanup_detection_source = source
        self.cleanup_detect_button.setEnabled(False)
        self.cleanup_detect_services_button.setEnabled(False)
        self.cleanup_detect_button.setText("Detecting Cleanup Folders...")
        self._cleanup_detection_thread = QThread(self)
        self._cleanup_detection_worker = CleanupDetectionWorker(source, selected_items, folder_names)
        self._cleanup_detection_worker.moveToThread(self._cleanup_detection_thread)
        self._cleanup_detection_thread.started.connect(self._cleanup_detection_worker.run)
        self._cleanup_detection_worker.finished.connect(self.on_cleanup_detection_finished)
        self._cleanup_detection_worker.failed.connect(self.on_cleanup_detection_failed)
        self._cleanup_detection_worker.finished.connect(self._cleanup_detection_thread.quit)
        self._cleanup_detection_worker.failed.connect(self._cleanup_detection_thread.quit)
        self._cleanup_detection_thread.finished.connect(self.on_cleanup_detection_thread_finished)
        self._cleanup_detection_thread.start()

    def on_cleanup_detection_finished(self, detected_tasks: list[dict]) -> None:
        replaced_source = "IIS Auto Detect" if self._cleanup_detection_source == "iis" else "Service Auto Detect"
        manual_tasks = [
            task for task in self.config_manager.get("cleanup", "tasks", [])
            if task.get("source", "Manual") != replaced_source
        ]
        self.config_manager.set("cleanup", "tasks", manual_tasks + detected_tasks)
        self.load_cleanup_table()
        self.show_unsaved_status("Cleanup changes")
        QMessageBox.information(self, "Cleanup Auto Detect", f"Detected {len(detected_tasks)} cleanup folders.")

    def on_cleanup_detection_failed(self, error: str) -> None:
        QMessageBox.warning(self, "Cleanup Auto Detect", f"Unable to inspect IIS application paths: {error}")

    def on_cleanup_detection_thread_finished(self) -> None:
        self.cleanup_detect_button.setText("Auto Detect from Selected IIS Apps")
        self.cleanup_detect_button.setEnabled(self.cleanup_enabled_checkbox.isChecked())
        self.cleanup_detect_services_button.setEnabled(self.cleanup_enabled_checkbox.isChecked())
        self._cleanup_detection_worker = None
        self._cleanup_detection_thread = None
        self._cleanup_detection_source = ""

    def on_add_cleanup_task(self) -> None:
        from modules.cleanup_manager import DEFAULT_LOG_EXTENSIONS, normalize_extensions

        path = QFileDialog.getExistingDirectory(self, "Select Cleanup Folder")
        if not path:
            return
        extensions, ok = QInputDialog.getText(
            self,
            "Cleanup File Extensions",
            "Extensions separated by commas. Leave empty to delete all files:",
            text=", ".join(DEFAULT_LOG_EXTENSIONS),
        )
        if not ok:
            return
        try:
            normalized_extensions = normalize_extensions(extensions)
        except ValueError as exc:
            QMessageBox.warning(self, "Cleanup Task", str(exc))
            return
        tasks = self.config_manager.get("cleanup", "tasks", [])
        tasks.append({
            "name": Path(path).name or path,
            "path": path,
            "extensions": normalized_extensions,
            "enabled": True,
            "source": "Manual",
        })
        self.config_manager.set("cleanup", "tasks", tasks)
        self.load_cleanup_table()
        self.show_unsaved_status("Cleanup changes")

    def on_edit_cleanup_task(self) -> None:
        from modules.cleanup_manager import normalize_extensions

        row = self._get_selected_cleanup_row("edit")
        if row is None:
            return
        tasks = self.config_manager.get("cleanup", "tasks", [])
        if row >= len(tasks):
            return
        task = tasks[row]
        path = QFileDialog.getExistingDirectory(self, "Select Cleanup Folder", str(task.get("path", "")))
        if not path:
            return
        extensions, ok = QInputDialog.getText(
            self,
            "Cleanup File Extensions",
            "Extensions separated by commas. Leave empty to delete all files:",
            text=", ".join(task.get("extensions", [])),
        )
        if not ok:
            return
        try:
            normalized_extensions = normalize_extensions(extensions)
        except ValueError as exc:
            QMessageBox.warning(self, "Cleanup Task", str(exc))
            return
        task["name"] = Path(path).name or path
        task["path"] = path
        task["extensions"] = normalized_extensions
        self.config_manager.set("cleanup", "tasks", tasks)
        self.load_cleanup_table()
        self.cleanup_table.selectRow(row)
        self.show_unsaved_status("Cleanup changes")

    def on_remove_cleanup_task(self) -> None:
        row = self._get_selected_cleanup_row("remove")
        if row is None:
            return
        tasks = self.config_manager.get("cleanup", "tasks", [])
        if row >= len(tasks):
            return
        tasks.pop(row)
        self.config_manager.set("cleanup", "tasks", tasks)
        self.load_cleanup_table()
        self.show_unsaved_status("Cleanup changes")

    def _get_selected_cleanup_row(self, action: str) -> int | None:
        selected_rows = self.cleanup_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Cleanup Task", f"Select a cleanup task to {action}.")
            return None
        return selected_rows[0].row()

    def _get_selected_iis_cleanup_app_keys(self) -> set[str]:
        selected_apps: set[str] = set()
        for row in range(self.iis_apps_table.rowCount()):
            item = self.iis_apps_table.item(row, 1)
            path_item = self.iis_apps_table.item(row, 4)
            if item and item.checkState() == Qt.Checked and path_item:
                selected_apps.add(str(path_item.data(Qt.UserRole)))
        return selected_apps or set(self.config_manager.get("iis", "selected_cleanup_apps", []))

    def _get_selected_service_names(self) -> set[str]:
        selected_services: set[str] = set()
        for row in range(self.service_table.rowCount()):
            item = self.service_table.item(row, 0)
            name_item = self.service_table.item(row, 1)
            if item and item.checkState() == Qt.Checked and name_item:
                selected_services.add(name_item.text())
        return selected_services or set(self.config_manager.get("services", "selected_services", []))

    def on_browse_backup_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select Backup Root Folder")
        if selected:
            self.backup_root_input.setText(selected)

    def on_add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Back Up",
            str(Path.home()),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not path:
            return
        selected_path = Path(path)
        name = selected_path.name or selected_path.drive or "Folder Backup"
        folders = self.config_manager.get("folder_backup", "folders", [])
        folders.append({"name": name, "path": path, "enabled": True})
        self.config_manager.set("folder_backup", "folders", folders)
        self.load_folder_table()
        self.show_unsaved_status()

    def on_edit_folder(self) -> None:
        selected_rows = self.folder_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Edit Folder", "Select a folder to edit.")
            return
        row = selected_rows[0].row()
        folders = self.config_manager.get("folder_backup", "folders", [])
        if row >= len(folders):
            return
        folder_item = folders[row]
        current_path = str(folder_item.get("path", ""))
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Replacement Folder",
            current_path or str(Path.home()),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not path:
            return
        selected_path = Path(path)
        folder_item["name"] = selected_path.name or selected_path.drive or "Folder Backup"
        folder_item["path"] = path
        self.config_manager.set("folder_backup", "folders", folders)
        self.load_folder_table()
        self.show_unsaved_status()

    def on_toggle_folder(self) -> None:
        selected_rows = self.folder_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Enable / Disable Folder", "Select a folder to update.")
            return
        row = selected_rows[0].row()
        folders = self.config_manager.get("folder_backup", "folders", [])
        if row >= len(folders):
            return
        folders[row]["enabled"] = not folders[row].get("enabled", True)
        self.config_manager.set("folder_backup", "folders", folders)
        self.load_folder_table()
        self.folder_table.selectRow(row)
        self.show_unsaved_status()

    def on_schedule_folder(self) -> None:
        selected_rows = self.folder_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Schedule Folder", "Select a folder to schedule first.")
            return
        folders = self.config_manager.get("folder_backup", "folders", [])
        row = selected_rows[0].row()
        if row >= len(folders):
            return
        folder = folders[row]
        if not folder.get("enabled", True):
            QMessageBox.warning(self, "Schedule Folder", "Enable the selected folder before scheduling it.")
            return

        frequency, accepted = QInputDialog.getItem(
            self, "Schedule Folder", "Frequency",
            ["daily", "weekly", "monthly", "interval"], 0, False,
        )
        if not accepted:
            return
        run_time, accepted = QInputDialog.getText(
            self, "Schedule Folder", "Start time (HH:mm)", text="02:00",
        )
        if not accepted:
            return
        parsed_time = QTime.fromString(run_time, "HH:mm")
        if not parsed_time.isValid():
            QMessageBox.warning(self, "Schedule Folder", "Enter time as HH:mm, for example 02:00.")
            return

        weekdays: list[str] = []
        interval_days = 1
        day_of_month = 1
        if frequency == "weekly":
            weekday, accepted = QInputDialog.getItem(
                self, "Schedule Folder", "Weekday",
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                0, False,
            )
            if not accepted:
                return
            weekdays = [weekday]
        elif frequency == "monthly":
            day_of_month, accepted = QInputDialog.getInt(
                self, "Schedule Folder", "Day of month", 1, 1, 31,
            )
            if not accepted:
                return
        elif frequency == "interval":
            interval_days, accepted = QInputDialog.getInt(
                self, "Schedule Folder", "Run every N days", 7, 1, 365,
            )
            if not accepted:
                return

        schedule_id = ScheduledJobStore().add(
            f"Folder: {folder.get('name', 'Backup')}",
            "folder_backup",
            {"name": folder.get("name", "Backup"), "path": folder.get("path", "")},
            {
                "frequency": frequency, "time": run_time,
                "weekdays": weekdays, "interval_days": interval_days,
                "day_of_month": day_of_month, "run_missed": True,
            },
        )
        QMessageBox.information(
            self, "Folder Scheduled",
            f"The selected folder now has an independent {frequency} backup schedule.\n"
            f"Schedule ID: {schedule_id[:12]}\n\n"
            "You can review or remove it from the Operations tab.",
        )

    def _schedule_backup_item(self, name: str, job_type: str, payload: dict) -> None:
        frequency, accepted = QInputDialog.getItem(
            self, "Schedule Backup Job", "Frequency",
            ["daily", "weekly", "monthly", "interval"], 0, False,
        )
        if not accepted:
            return
        run_time, accepted = QInputDialog.getText(
            self, "Schedule Backup Job", "Start time (HH:mm)", text="02:00",
        )
        if not accepted:
            return
        if not QTime.fromString(run_time, "HH:mm").isValid():
            QMessageBox.warning(self, "Schedule Backup Job", "Enter time as HH:mm, for example 02:00.")
            return
        weekdays: list[str] = []
        interval_days = 1
        day_of_month = 1
        if frequency == "weekly":
            weekday, accepted = QInputDialog.getItem(
                self, "Schedule Backup Job", "Weekday",
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                0, False,
            )
            if not accepted:
                return
            weekdays = [weekday]
        elif frequency == "monthly":
            day_of_month, accepted = QInputDialog.getInt(
                self, "Schedule Backup Job", "Day of month", 1, 1, 31,
            )
            if not accepted:
                return
        elif frequency == "interval":
            interval_days, accepted = QInputDialog.getInt(
                self, "Schedule Backup Job", "Run every N days", 7, 1, 365,
            )
            if not accepted:
                return
        schedule_id = ScheduledJobStore().add(name, job_type, payload, {
            "frequency": frequency, "time": run_time, "weekdays": weekdays,
            "interval_days": interval_days, "day_of_month": day_of_month,
            "run_missed": True,
        })
        QMessageBox.information(
            self, "Backup Job Scheduled",
            f"{name} now has an independent {frequency} schedule.\n"
            f"Schedule ID: {schedule_id[:12]}\n\nReview or remove it from Operations.",
        )

    def on_schedule_iis_site(self) -> None:
        rows = self.iis_sites_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Schedule IIS Site", "Select an IIS site row first.")
            return
        item = self.iis_sites_table.item(rows[0].row(), 1)
        if not item or not str(item.data(Qt.UserRole) or "").strip():
            QMessageBox.warning(self, "Schedule IIS Site", "The selected site's physical path is unavailable.")
            return
        self._schedule_backup_item(
            f"IIS site: {item.text()}", "iis_backup",
            {"name": item.text(), "path": str(item.data(Qt.UserRole)), "kind": "site"},
        )

    def on_schedule_iis_application(self) -> None:
        rows = self.iis_apps_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Schedule IIS Application", "Select an IIS application row first.")
            return
        row = rows[0].row()
        name_item = self.iis_apps_table.item(row, 2)
        path_item = self.iis_apps_table.item(row, 4)
        if not name_item or not path_item or not path_item.text().strip():
            QMessageBox.warning(self, "Schedule IIS Application", "The selected application's physical path is unavailable.")
            return
        self._schedule_backup_item(
            f"IIS app: {name_item.text()}", "iis_backup",
            {
                "name": name_item.text(), "path": path_item.text(), "kind": "application",
                "key": str(path_item.data(Qt.UserRole) or ""),
            },
        )

    def on_schedule_service(self) -> None:
        rows = self.service_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Schedule Service", "Select a Windows service row first.")
            return
        row = rows[0].row()
        name_item = self.service_table.item(row, 1)
        path_item = self.service_table.item(row, 3)
        if not name_item or not path_item or not path_item.text().strip():
            QMessageBox.warning(self, "Schedule Service", "The selected service executable path is unavailable.")
            return
        self._schedule_backup_item(
            f"Service: {name_item.text()}", "service_backup",
            {"name": name_item.text(), "executable_path": path_item.text()},
        )

    def on_remove_folder(self) -> None:
        selected_rows = self.folder_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Remove Folder", "Select a folder to remove.")
            return
        row = selected_rows[0].row()
        folders = self.config_manager.get("folder_backup", "folders", [])
        if row >= len(folders):
            return
        folders.pop(row)
        self.config_manager.set("folder_backup", "folders", folders)
        self.load_folder_table()
        self.show_unsaved_status()

    def on_save_clicked(self) -> None:
        try:
            self.save_settings()
            self.show_saved_status("All configuration")
        except (ConfigError, ValueError) as exc:
            QMessageBox.warning(self, "Validation Error", str(exc))

    def on_save_validate_and_install(self) -> None:
        """Finish setup only after the saved checklist passes preflight validation."""
        from core.windows_service_manager import WindowsServiceManager
        from modules.checklist_runner import ChecklistRunner

        try:
            self.save_settings()
            errors, warnings = ChecklistRunner(self.config_manager).get_preflight_messages()
            if errors:
                QMessageBox.warning(
                    self,
                    "Configuration Required",
                    "The service was not installed because configuration needs attention:\n\n"
                    + "\n".join(f"- {error}" for error in errors),
                )
                return
            if warnings:
                response = QMessageBox.question(
                    self,
                    "Configuration Warnings",
                    "The configuration is valid but has these warnings:\n\n"
                    + "\n".join(f"- {warning}" for warning in warnings)
                    + "\n\nContinue with service installation?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if response != QMessageBox.Yes:
                    return

            service = WindowsServiceManager()
            status = service.status()
            if status.get("installed") != "true":
                service.install()
                status = service.status()
            if "RUNNING" not in status.get("state", "").upper():
                service.start()

            schedule_message = (
                "The checklist schedule was applied through MiChecklistService."
                if self.schedule_enabled_checkbox.isChecked()
                else "Scheduling is disabled."
            )

            final_status = service.status()
            self.config_manager.set("general", "setup_completed", True)
            self.config_manager.save_config()
            self.save_status_label.setText("Checklist configuration activated successfully.")
            QMessageBox.information(
                self,
                "Checklist Ready",
                "Configuration was saved and validated.\n"
                f"Windows service: {final_status.get('state', 'Unknown')}\n"
                f"{schedule_message}",
            )
        except (ConfigError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Activation Failed",
                f"The configuration was saved, but setup could not be completed:\n\n{exc}\n\n"
                "Run MiChecklist as Administrator and try again.",
            )

    def on_save_general_clicked(self) -> None:
        self._save_section(self.save_general_settings, "General settings")

    def on_save_sql_clicked(self) -> None:
        self._save_section(self.save_sql_settings, "SQL settings")

    def on_save_iis_clicked(self) -> None:
        self._save_section(self.save_iis_settings, "IIS settings")

    def on_save_folders_clicked(self) -> None:
        self._save_section(self.save_folder_settings, "Folder settings")

    def on_save_cleanup_clicked(self) -> None:
        self._save_section(self.save_cleanup_settings, "Cleanup settings")

    def on_save_schedule_clicked(self) -> None:
        self._save_section(self.save_schedule_settings, "Schedule settings")

    def on_save_retention_clicked(self) -> None:
        self._save_section(self.save_retention_settings, "Retention settings")

    def on_save_notifications_clicked(self) -> None:
        self._save_section(self.save_notification_settings, "Notification settings")

    def on_run_cleanup_only_clicked(self) -> None:
        try:
            self.save_cleanup_settings()
            self.config_manager.save_config()
            self.cleanup_only_requested.emit()
        except (ConfigError, ValueError) as exc:
            QMessageBox.warning(self, "Validation Error", str(exc))

    def on_install_schedule_clicked(self) -> None:
        from modules.scheduler_manager import SchedulerManager

        try:
            self.save_schedule_settings()
            self.config_manager.save_config()
            scheduler = SchedulerManager(self.config_manager)
            if self.schedule_enabled_checkbox.isChecked():
                scheduler.register_tasks()
                QMessageBox.information(self, "Schedule Installed", "Elevated checklist scheduled task was created successfully.")
            else:
                scheduler.remove_tasks()
                QMessageBox.information(self, "Schedule Disabled", "Scheduling is disabled and existing checklist tasks were removed.")
            self.on_refresh_schedule_status()
        except (ConfigError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Schedule Error", str(exc))

    def on_remove_schedule_clicked(self) -> None:
        from modules.scheduler_manager import SchedulerManager

        try:
            SchedulerManager(self.config_manager).remove_tasks()
            self.schedule_status_label.setText("Schedule status: Not installed")
            QMessageBox.information(self, "Schedule Removed", "Scheduled tasks were removed successfully.")
        except RuntimeError as exc:
            QMessageBox.warning(self, "Schedule Error", str(exc))

    def on_refresh_schedule_status(self) -> None:
        from modules.scheduler_manager import SchedulerManager

        try:
            status = SchedulerManager(self.config_manager).get_task_status()
            if status.get("installed", "False").lower() != "true":
                self.schedule_status_label.setText("Schedule status: Not installed")
                return
            result_code = status.get("last_result", "N/A")
            result_text = {
                "0": "Success (0)",
                "267009": "Running",
                "267011": "Not yet run",
            }.get(result_code, result_code)
            self.schedule_status_label.setText(
                "Schedule status: "
                f"{status.get('state', 'Unknown')} | "
                f"Last: {status.get('last_run', 'N/A')} | "
                f"Next: {status.get('next_run', 'N/A')} | "
                f"Result: {result_text}"
            )
        except RuntimeError as exc:
            self.schedule_status_label.setText(f"Schedule status unavailable: {exc}")

    def on_run_schedule_now(self) -> None:
        from modules.scheduler_manager import SchedulerManager

        if not self.schedule_enabled_checkbox.isChecked():
            QMessageBox.warning(self, "Schedule Disabled", "Enable and apply the schedule before running it.")
            return
        response = QMessageBox.question(
            self,
            "Run Full Checklist Now",
            "This will start the complete unattended checklist using all currently saved module settings. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            return
        try:
            self.save_schedule_settings()
            self.config_manager.save_config()
            scheduler = SchedulerManager(self.config_manager)
            scheduler.register_tasks()
            scheduler.run_task_now()
            self.schedule_status_label.setText("Schedule status: Start requested; refresh shortly to see the running state.")
            QMessageBox.information(self, "Checklist Started", "The full checklist was submitted to Windows Task Scheduler.")
        except (ConfigError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Schedule Test Error", str(exc))

    def on_save_service_settings_clicked(self) -> None:
        self._save_section(self.save_service_settings, "Service settings")

    def _save_section(self, update_section, label: str) -> None:
        try:
            update_section()
            self.config_manager.save_config()
            self.show_saved_status(label)
        except (ConfigError, ValueError) as exc:
            QMessageBox.warning(self, "Validation Error", str(exc))

    def show_saved_status(self, label: str) -> None:
        self.save_status_label.setText(f"{label} saved successfully.")
        QMessageBox.information(self, "Configuration Saved", f"{label} saved successfully.")

    def show_unsaved_status(self, label: str = "Folder changes") -> None:
        self.save_status_label.setText(f"{label} are pending. Save the section or save all configuration.")

    def on_refresh_services(self) -> None:
        self.load_service_table()

    def on_service_search_changed(self, text: str) -> None:
        if self.service_backup_enabled_checkbox.isChecked():
            self.load_service_table()

    def on_select_all_services(self) -> None:
        for row in range(self.service_table.rowCount()):
            item = self.service_table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked)

    def on_deselect_all_services(self) -> None:
        for row in range(self.service_table.rowCount()):
            item = self.service_table.item(row, 0)
            if item:
                item.setCheckState(Qt.Unchecked)

    def load_service_table(self) -> None:
        if not self.service_backup_enabled_checkbox.isChecked():
            return
        search_filter = self.service_search_input.text().strip()
        selected_services = set(self.config_manager.get("services", "selected_services", []))
        try:
            from modules.service_backup import ServiceBackup
            service_list = ServiceBackup().list_services(search=search_filter)
        except Exception as exc:
            QMessageBox.warning(self, "Service Load Error", f"Unable to load services: {exc}")
            return

        self.service_table.setRowCount(0)
        for service in service_list:
            row = self.service_table.rowCount()
            self.service_table.insertRow(row)
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            checkbox_item.setCheckState(Qt.Checked if service.name in selected_services else Qt.Unchecked)
            self.service_table.setItem(row, 0, checkbox_item)
            self.service_table.setItem(row, 1, QTableWidgetItem(service.name))
            self.service_table.setItem(row, 2, QTableWidgetItem(service.display_name))
            self.service_table.setItem(row, 3, QTableWidgetItem(service.executable_path))
            self.service_table.setItem(row, 4, QTableWidgetItem(service.status))
        self._services_loaded = True

    def on_test_connection(self) -> None:
        if not self.sql_backup_enabled_checkbox.isChecked():
            return
        sql = SQLBackup(
            server=self.server_input.text().strip(),
            instance=self.instance_input.text().strip(),
            database=self.database_input.text().strip(),
            username=self.username_input.text().strip(),
            password=self.password_input.text(),
            use_windows_auth=self.windows_auth_checkbox.isChecked(),
        )
        try:
            success = sql.test_connection()
            if success:
                QMessageBox.information(self, "SQL Connection", "SQL Server connection succeeded.")
            else:
                QMessageBox.warning(self, "SQL Connection", "SQL Server connection failed.")
        except Exception as exc:
            QMessageBox.critical(self, "SQL Connection", f"Connection failed: {exc}")

    def save_settings(self) -> None:
        self.save_general_settings()
        self.save_sql_settings()
        self.save_iis_settings()
        self.save_service_settings()
        self.save_folder_settings()
        self.save_cleanup_settings()
        self.save_schedule_settings()
        self.save_retention_settings()
        self.save_notification_settings()
        self.config_manager.save_config()

    def save_general_settings(self) -> None:
        backup_root = self.backup_root_input.text().strip()
        if not backup_root:
            raise ValueError("Backup root folder must be configured.")
        self.config_manager.set("general", "backup_root_folder", backup_root)
        external = self.config_manager.get_section("external_destination")
        external["enabled"] = self.external_destination_checkbox.isChecked()
        external["check_interval_minutes"] = self.external_check_minutes.value()
        external["reminder_interval_minutes"] = self.external_reminder_minutes.value()
        if self.external_destination_checkbox.isChecked():
            try:
                external.update(capture_external_destination(backup_root))
            except Exception as exc:
                raise ValueError(
                    "Connect the external drive and select its backup folder before saving: "
                    f"{exc}"
                ) from exc
        self.config_manager.data["external_destination"] = external
        self.config_manager.set("general", "compression_enabled", self.compression_checkbox.isChecked())
        self.config_manager.set("general", "generate_sha256_hashes", self.hash_checkbox.isChecked())

    def save_retention_settings(self) -> None:
        self.config_manager.set("general", "incremental_backups", self.incremental_checkbox.isChecked())
        self.config_manager.set("retention", "enabled", self.retention_enabled.isChecked())
        self.config_manager.set("retention", "max_age_days", self.retention_max_age.value())
        self.config_manager.set(
            "retention", "minimum_successful_backups", self.retention_minimum.value()
        )

    def save_notification_settings(self) -> None:
        to_addresses = parse_addresses(self.email_to.text())
        cc_addresses = parse_addresses(self.email_cc.text())
        invalid_recipients = invalid_millensys_addresses(self.email_to.text()) + invalid_millensys_addresses(self.email_cc.text())
        if self.email_enabled.isChecked() and not to_addresses:
            raise ValueError("At least one reception email address is required.")
        if invalid_recipients:
            raise ValueError("Reception and Cc addresses must end with @millensys.com: " + ", ".join(invalid_recipients))
        self.config_manager.set("email", "enabled", self.email_enabled.isChecked())
        self.config_manager.set("email", "smtp_server", self.email_server.text().strip())
        self.config_manager.set("email", "smtp_port", self.email_port.value())
        self.config_manager.set("email", "use_tls", self.email_tls.isChecked())
        self.config_manager.set("email", "username", self.email_username.text().strip())
        self.config_manager.set("email", "sender_name", self.email_sender_name.text().strip() or "Support Checklist")
        self.config_manager.set("email", "client_name", self.email_client_name.text().strip())
        self.config_manager.set("email", "client_branch", self.email_client_branch.text().strip())
        self.config_manager.set("email", "client_server", self.email_client_server.text().strip())
        self.config_manager.set("email", "client_comment", self.email_client_comment.text().strip())
        self.config_manager.set("email", "password", self.email_password.text())
        self.config_manager.set("email", "to", to_addresses)
        self.config_manager.set("email", "cc", cc_addresses)
        self.config_manager.set("email", "notify_success", self.email_success.isChecked())
        self.config_manager.set("email", "notify_warning", self.email_warning.isChecked())
        self.config_manager.set("email", "notify_failure", self.email_failure.isChecked())
        self.config_manager.set("email", "notify_recovery", self.email_recovery.isChecked())

    def save_sql_settings(self) -> None:
        self.config_manager.set("sql_server", "backup_enabled", self.sql_backup_enabled_checkbox.isChecked())
        self.config_manager.set(
            "general", "compression_enabled", self.compression_checkbox.isChecked()
        )
        self.config_manager.set(
            "sql_server",
            "archive_compression_enabled",
            self.sql_archive_compression_checkbox.isChecked(),
        )
        if not self.sql_backup_enabled_checkbox.isChecked():
            return
        self.config_manager.set("sql_server", "server_name", self.server_input.text().strip())
        self.config_manager.set("sql_server", "instance_name", self.instance_input.text().strip())
        self.config_manager.set("sql_server", "database_name", self.database_input.text().strip())
        # Save selected databases
        selected_dbs = []
        for row in range(self.db_table.rowCount()):
            item = self.db_table.item(row, 0)
            name_item = self.db_table.item(row, 1)
            if item and item.checkState() == Qt.Checked and name_item:
                selected_dbs.append(name_item.text())
        self.config_manager.set("sql_server", "databases", selected_dbs)
        self.config_manager.set("sql_server", "username", self.username_input.text().strip())
        self.config_manager.set("sql_server", "password", self.password_input.text())
        self.config_manager.set("sql_server", "use_windows_auth", self.windows_auth_checkbox.isChecked())

    def save_iis_settings(self) -> None:
        self.config_manager.set("iis", "backup_enabled", self.iis_backup_enabled_checkbox.isChecked())
        if not self.iis_backup_enabled_checkbox.isChecked():
            return
        self.config_manager.set("iis", "auto_detect_sites", self.iis_autodetect_checkbox.isChecked())
        self.config_manager.set("iis", "include_iis_configuration", self.iis_config_checkbox.isChecked())
        # Save selected IIS sites
        sites = []
        for row in range(self.iis_sites_table.rowCount()):
            item = self.iis_sites_table.item(row, 0)
            name_item = self.iis_sites_table.item(row, 1)
            if item and item.checkState() == Qt.Checked and name_item:
                sites.append(name_item.text())
        self.config_manager.set("iis", "selected_sites", sites)
        apps = []
        cleanup_apps = []
        for row in range(self.iis_apps_table.rowCount()):
            item = self.iis_apps_table.item(row, 0)
            cleanup_item = self.iis_apps_table.item(row, 1)
            path_item = self.iis_apps_table.item(row, 4)
            if item and item.checkState() == Qt.Checked and path_item:
                apps.append(str(path_item.data(Qt.UserRole)))
            if cleanup_item and cleanup_item.checkState() == Qt.Checked and path_item:
                cleanup_apps.append(str(path_item.data(Qt.UserRole)))
        self.config_manager.set("iis", "selected_apps", apps)
        self.config_manager.set("iis", "selected_cleanup_apps", cleanup_apps)

        pools = []
        for row in range(self.iis_pools_table.rowCount()):
            item = self.iis_pools_table.item(row, 0)
            name_item = self.iis_pools_table.item(row, 1)
            if item and item.checkState() == Qt.Checked and name_item:
                pools.append(name_item.text())
        self.config_manager.set("iis", "selected_pools", pools)

    def save_service_settings(self) -> None:
        self.config_manager.set("services", "backup_enabled", self.service_backup_enabled_checkbox.isChecked())
        if not self.service_backup_enabled_checkbox.isChecked():
            return
        self.config_manager.set("services", "service_search_filter", self.service_search_input.text().strip())
        selected_services = []
        for row in range(self.service_table.rowCount()):
            item = self.service_table.item(row, 0)
            name_item = self.service_table.item(row, 1)
            if item and item.checkState() == Qt.Checked and name_item:
                selected_services.append(name_item.text())
        self.config_manager.set("services", "selected_services", selected_services)

    def save_cleanup_settings(self) -> None:
        from modules.cleanup_manager import normalize_extensions

        self.config_manager.set("cleanup", "enabled", self.cleanup_enabled_checkbox.isChecked())
        tasks = []
        for row in range(self.cleanup_table.rowCount()):
            enabled_item = self.cleanup_table.item(row, 0)
            name_item = self.cleanup_table.item(row, 1)
            path_item = self.cleanup_table.item(row, 2)
            extensions_item = self.cleanup_table.item(row, 3)
            source_item = self.cleanup_table.item(row, 4)
            if not path_item:
                continue
            extensions = normalize_extensions(extensions_item.text() if extensions_item else "")
            tasks.append({
                "name": name_item.text() if name_item else Path(path_item.text()).name,
                "path": path_item.text(),
                "extensions": extensions,
                "enabled": bool(enabled_item and enabled_item.checkState() == Qt.Checked),
                "source": source_item.text() if source_item else "Manual",
            })
        self.config_manager.set("cleanup", "tasks", tasks)

    def save_schedule_settings(self) -> None:
        self.config_manager.set("schedule", "enabled", self.schedule_enabled_checkbox.isChecked())
        self.config_manager.set("schedule", "mode", self.schedule_mode_combo.currentData())
        self.config_manager.set("schedule", "weekday", self.schedule_weekday_combo.currentText())
        self.config_manager.set("schedule", "days_interval", self.schedule_days_input.value())
        self.config_manager.set("schedule", "start_time", self.schedule_time_input.time().toString("HH:mm"))
        self.config_manager.set("schedule", "run_missed_schedule", self.schedule_run_missed_checkbox.isChecked())
        self.config_manager.set("schedule", "run_at_startup", self.schedule_mode_combo.currentData() == "after_restart")
        self.config_manager.set("schedule", "test_run_after_restart", self.schedule_test_restart_checkbox.isChecked())
        self.config_manager.set("schedule", "delete_previous_automated_backups", self.schedule_delete_backups_checkbox.isChecked())
        self.config_manager.set("schedule", "hide_to_tray", self.schedule_tray_checkbox.isChecked())

    def save_folder_settings(self) -> None:
        folders = self.config_manager.get("folder_backup", "folders", [])
        for folder in folders:
            if not folder.get("name", "").strip() or not folder.get("path", "").strip():
                raise ValueError("Every folder backup entry must have a name and path.")
