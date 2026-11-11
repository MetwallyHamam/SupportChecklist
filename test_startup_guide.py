import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.startup_guide import StartupGuide, _invalid_millensys_addresses
from core.scheduled_jobs import ScheduledJobStore
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


def test_startup_guide_saves_notification_to_and_optional_cc(tmp_path):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")
    guide.destination_input.setText(str(tmp_path / "backups"))
    guide.notifications_enabled.setChecked(True)
    guide.to_input.setText("primary@millensys.com")
    guide.cc_input.setText("manager@millensys.com; audit@millensys.com")

    guide._save_all()

    assert manager.get("general", "backup_root_folder") == str(tmp_path / "backups")
    assert manager.get("email", "enabled") is True
    assert manager.get("email", "to") == ["primary@millensys.com"]
    assert manager.get("email", "cc") == ["manager@millensys.com", "audit@millensys.com"]


def test_startup_guide_contains_complete_setup_steps(tmp_path):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")

    titles = [guide.page(page_id).title() for page_id in guide.pageIds()]

    assert titles == [
        "Client Information and Recipients",
        "Backup Destination",
        "SQL Server and Databases",
        "IIS Websites and Child Applications",
        "Windows Services",
        "Folders and Cleanup",
        "Backup Retention",
        "Notification Delivery",
        "Checklist Schedule",
        "Review and Install",
    ]


def test_startup_guide_has_explicit_server_module_skip_actions(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")
    monkeypatch.setattr(guide, "next", lambda: None)

    guide._skip_sql()
    guide._skip_iis()
    guide._skip_services()
    guide.folder_list.addItem(str(tmp_path / "extra"))
    guide.cleanup_enabled.setChecked(True)
    guide.cleanup_list.addItem(str(tmp_path / "logs"))
    guide._skip_folders_cleanup()
    guide._save_all()

    assert guide.skip_sql_button.text() == "Skip SQL Server on this server"
    assert guide.skip_iis_button.text() == "Skip IIS on this server"
    assert guide.skip_services_button.text() == "Skip Windows service backup on this server"
    assert manager.get("sql_server", "backup_enabled") is False
    assert manager.get("iis", "backup_enabled") is False
    assert manager.get("services", "backup_enabled") is False
    assert manager.get("folder_backup", "folders") == []
    assert manager.get("cleanup", "enabled") is False
    assert manager.get("cleanup", "tasks") == []


def test_startup_recipients_require_millensys_domain():
    assert _invalid_millensys_addresses("ops@millensys.com; Manager@MILLENSYS.COM") == []
    assert _invalid_millensys_addresses("external@example.com, bad@other@millensys.com") == [
        "external@example.com", "bad@other@millensys.com",
    ]


def test_startup_cannot_leave_first_page_with_external_recipient(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("external@example.com")
    guide.cc_input.setText("manager@millensys.com")
    monkeypatch.setattr("gui.startup_guide.QMessageBox.warning", lambda *args: None)
    guide.restart()

    assert guide.currentId() == 0
    assert guide.validateCurrentPage() is False


def test_startup_guide_detects_and_saves_iis_child_apps(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")

    class FakeSite:
        name = "Default Web Site"
        physical_path = r"C:\inetpub\wwwroot"

    app_root = tmp_path / "Portal"
    logs = app_root / "Logs"
    logs.mkdir(parents=True)

    class FakeIIS:
        def detect_sites(self):
            return [FakeSite()]

        def list_applications(self):
            return [{
                "site": "Default Web Site",
                "path": "/Portal",
                "physical_path": str(app_root),
                "application_pool": "PortalPool",
            }]

    monkeypatch.setattr("gui.startup_guide.IISManager", FakeIIS)
    guide._load_iis()
    guide.iis_app_list.topLevelItem(0).setCheckState(0, Qt.Checked)
    guide.iis_app_list.topLevelItem(0).setCheckState(1, Qt.Checked)
    guide._detect_iis_cleanup()
    guide._save_all()

    assert manager.get("iis", "selected_apps") == ["Default Web Site|/Portal"]
    assert manager.get("iis", "selected_cleanup_apps") == ["Default Web Site|/Portal"]
    assert manager.get("cleanup", "enabled") is True
    assert manager.get("cleanup", "tasks")[0]["path"] == str(logs)


def test_startup_iis_select_all_controls_backup_and_cleanup_independently(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")

    class FakeIIS:
        def detect_sites(self):
            return []

        def list_applications(self):
            return [
                {"site": "Site", "path": "/One", "physical_path": "C:/One", "application_pool": "Pool"},
                {"site": "Site", "path": "/Two", "physical_path": "C:/Two", "application_pool": "Pool"},
            ]

        def list_app_pools(self):
            return []

    monkeypatch.setattr("gui.startup_guide.IISManager", FakeIIS)
    guide._load_iis()
    guide.select_all_app_backups.setChecked(True)
    guide.select_all_app_cleanup.setChecked(True)
    guide._save_all()

    assert manager.get("iis", "selected_apps") == ["Site|/One", "Site|/Two"]
    assert manager.get("iis", "selected_cleanup_apps") == ["Site|/One", "Site|/Two"]


def test_selecting_site_auto_selects_external_child_and_detects_cleanup(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")

    site_root = tmp_path / "site"
    inside_root = site_root / "inside"
    external_root = tmp_path / "external"
    (external_root / "Logs").mkdir(parents=True)
    inside_root.mkdir(parents=True)

    class FakeSite:
        name = "Portal"
        physical_path = str(site_root)

    class FakeIIS:
        def detect_sites(self):
            return [FakeSite()]

        def list_applications(self):
            return [
                {"site": "Portal", "path": "/Inside", "physical_path": str(inside_root), "application_pool": "Pool"},
                {"site": "Portal", "path": "/External", "physical_path": str(external_root), "application_pool": "Pool"},
            ]

        def list_app_pools(self):
            return []

    monkeypatch.setattr("gui.startup_guide.IISManager", FakeIIS)
    guide._load_iis()
    guide.iis_list.item(0).setCheckState(Qt.Checked)
    guide._save_all()

    assert manager.get("iis", "selected_apps") == ["Portal|/External"]
    assert manager.get("iis", "selected_cleanup_apps") == [
        "Portal|/External", "Portal|/Inside"
    ]
    assert manager.get("cleanup", "enabled") is True
    assert [task["path"] for task in manager.get("cleanup", "tasks")] == [
        str(external_root / "Logs")
    ]


def test_selecting_site_filters_child_application_list(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)

    class Site:
        def __init__(self, name, physical_path):
            self.name = name
            self.physical_path = physical_path

    class FakeIIS:
        def detect_sites(self):
            return [Site("Site A", r"D:\Sites\A"), Site("Site B", r"D:\Sites\B")]

        def list_applications(self):
            return [
                {"site": "Site A", "path": "/AppA", "physical_path": r"D:\Sites\A\AppA", "application_pool": "A"},
                {"site": "Site B", "path": "/AppB", "physical_path": r"D:\Sites\B\AppB", "application_pool": "B"},
            ]

        def list_app_pools(self):
            return []

    monkeypatch.setattr("gui.startup_guide.IISManager", FakeIIS)
    guide._load_iis()
    guide.iis_list.item(0).setCheckState(Qt.Checked)

    assert guide.iis_app_list.topLevelItem(0).isHidden() is False
    assert guide.iis_app_list.topLevelItem(1).isHidden() is True

    guide.iis_list.item(0).setCheckState(Qt.Unchecked)
    assert guide.iis_app_list.topLevelItem(0).isHidden() is False
    assert guide.iis_app_list.topLevelItem(1).isHidden() is False


def test_startup_guide_saves_configuration_tab_options(tmp_path):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")

    guide.compression_enabled.setChecked(False)
    guide.hashes_enabled.setChecked(False)
    guide.incremental_enabled.setChecked(False)
    guide.sql_archive_compression.setChecked(False)
    guide.iis_autodetect.setChecked(False)
    guide.iis_include_config.setChecked(False)
    guide.cleanup_enabled.setChecked(True)
    guide.cleanup_extensions.setText("log, .bak")
    guide.cleanup_list.addItem(str(tmp_path / "cleanup"))
    guide.schedule_mode.setCurrentIndex(guide.schedule_mode.findData("after_restart"))
    guide.schedule_run_missed.setChecked(False)
    guide.schedule_test_restart.setChecked(True)
    guide.schedule_hide_to_tray.setChecked(False)
    guide.notify_success.setChecked(False)
    guide.notify_warning.setChecked(False)
    guide.notify_failure.setChecked(True)
    guide.notify_recovery.setChecked(False)

    guide._save_all()

    assert manager.get("general", "compression_enabled") is False
    assert manager.get("general", "generate_sha256_hashes") is False
    assert manager.get("general", "incremental_backups") is False
    assert manager.get("sql_server", "archive_compression_enabled") is False
    assert manager.get("iis", "auto_detect_sites") is False
    assert manager.get("iis", "include_iis_configuration") is False
    assert manager.get("cleanup", "tasks")[0]["extensions"] == [".log", ".bak"]
    assert manager.get("schedule", "run_at_startup") is True
    assert manager.get("schedule", "run_missed_schedule") is False
    assert manager.get("schedule", "test_run_after_restart") is True
    assert manager.get("schedule", "hide_to_tray") is False
    assert manager.get("email", "notify_success") is False
    assert manager.get("email", "notify_warning") is False
    assert manager.get("email", "notify_failure") is True
    assert manager.get("email", "notify_recovery") is False


def test_startup_guide_saves_daily_module_schedules(tmp_path):
    QApplication.instance() or QApplication([])
    manager = ConfigManager(config_path=tmp_path / "config.json", data=deepcopy(DEFAULT_CONFIG))
    guide = StartupGuide(manager)
    guide.to_input.setText("support@millensys.com")
    cleanup_enabled, cleanup_time = guide.module_schedule_controls["cleanup"]
    database_enabled, database_time = guide.module_schedule_controls["database"]
    guide.advanced_module_schedules.setChecked(True)
    cleanup_enabled.setChecked(True)
    cleanup_time.setTime(cleanup_time.time().fromString("23:15", "HH:mm"))
    database_enabled.setChecked(True)
    database_time.setTime(database_time.time().fromString("01:30", "HH:mm"))

    guide._save_all()

    schedules = ScheduledJobStore(tmp_path / "saved_jobs.json").load()
    by_module = {item["payload"]["module"]: item for item in schedules}
    assert by_module["cleanup"]["enabled"] is True
    assert by_module["cleanup"]["time"] == "23:15"
    assert by_module["database"]["enabled"] is True
    assert by_module["database"]["time"] == "01:30"
    assert by_module["iis"]["enabled"] is False


def test_michecklist_starts_at_version_one():
    from version import VERSION, __version__

    assert VERSION[0] == 1
    assert __version__ == ".".join(str(part) for part in VERSION)
