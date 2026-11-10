import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from gui.config_tab import ConfigTab
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


def make_tab(tmp_path):
    QApplication.instance() or QApplication([])
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path)
    manager = ConfigManager(config_path=tmp_path / "config.json", data=data)
    return ConfigTab(manager), manager


def test_every_configuration_tab_has_a_save_action(tmp_path):
    tab, _ = make_tab(tmp_path)

    buttons = [
        tab.save_general_button,
        tab.save_sql_button,
        tab.save_iis_button,
        tab.save_service_settings_button,
        tab.save_folders_button,
        tab.save_cleanup_button,
        tab.save_schedule_button,
        tab.save_retention_button,
        tab.save_notifications_button,
    ]

    assert all(button.text().startswith("Save ") for button in buttons)


def test_save_all_invokes_every_settings_section(tmp_path, monkeypatch):
    tab, manager = make_tab(tmp_path)
    calls = []
    method_names = [
        "save_general_settings",
        "save_sql_settings",
        "save_iis_settings",
        "save_service_settings",
        "save_folder_settings",
        "save_cleanup_settings",
        "save_schedule_settings",
        "save_retention_settings",
        "save_notification_settings",
    ]
    for name in method_names:
        monkeypatch.setattr(tab, name, lambda name=name: calls.append(name))
    monkeypatch.setattr(manager, "save_config", lambda: calls.append("save_config"))

    tab.save_settings()

    assert calls == method_names + ["save_config"]


def test_retention_and_notification_saves_persist_their_visible_fields(tmp_path):
    tab, manager = make_tab(tmp_path)
    tab.incremental_checkbox.setChecked(False)
    tab.retention_enabled.setChecked(True)
    tab.retention_max_age.setValue(45)
    tab.email_enabled.setChecked(True)
    tab.email_to.setText("support@millensys.com")
    tab.email_client_name.setText("Test Client")

    tab.save_retention_settings()
    tab.save_notification_settings()

    assert manager.get("general", "incremental_backups") is False
    assert manager.get("retention", "enabled") is True
    assert manager.get("retention", "max_age_days") == 45
    assert manager.get("email", "enabled") is True
    assert manager.get("email", "client_name") == "Test Client"


def test_sql_save_persists_archive_compression_for_service_runs(tmp_path):
    tab, manager = make_tab(tmp_path)
    tab.compression_checkbox.setChecked(False)
    tab.sql_archive_compression_checkbox.setChecked(False)

    tab.save_sql_settings()

    assert manager.get("general", "compression_enabled") is False
    assert manager.get("sql_server", "archive_compression_enabled") is False


def test_iis_app_checkbox_changes_are_saved_immediately(tmp_path):
    tab, manager = make_tab(tmp_path)
    tab._loading_iis_tables = True
    tab.iis_apps_table.setRowCount(1)
    backup_item = QTableWidgetItem()
    backup_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
    backup_item.setCheckState(Qt.Unchecked)
    cleanup_item = QTableWidgetItem()
    cleanup_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
    cleanup_item.setCheckState(Qt.Unchecked)
    path_item = QTableWidgetItem(r"D:\Apps\Portal")
    path_item.setData(Qt.UserRole, "SupportDashboard|/Portal")
    tab.iis_apps_table.setItem(0, 0, backup_item)
    tab.iis_apps_table.setItem(0, 1, cleanup_item)
    tab.iis_apps_table.setItem(0, 4, path_item)
    tab._loading_iis_tables = False
    tab._iis_tables_loaded = True

    backup_item.setCheckState(Qt.Checked)
    cleanup_item.setCheckState(Qt.Checked)

    assert manager.get("iis", "selected_apps") == ["SupportDashboard|/Portal"]
    assert manager.get("iis", "selected_cleanup_apps") == ["SupportDashboard|/Portal"]
    assert tab.save_status_label.text() == "IIS selection saved automatically."

def test_entering_sql_credentials_selects_sql_authentication(tmp_path):
    tab, manager = make_tab(tmp_path)
    tab.windows_auth_checkbox.setChecked(True)
    tab.username_input.setText("backup_login")
    tab.password_input.setText("secret")

    tab._select_sql_auth_for_credentials()
    tab.save_sql_settings()

    assert tab.windows_auth_checkbox.isChecked() is False
    assert manager.get("sql_server", "use_windows_auth") is False
