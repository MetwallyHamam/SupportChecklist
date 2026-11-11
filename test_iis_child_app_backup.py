from copy import deepcopy
from pathlib import Path

from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


class FakeIISManager:
    def __init__(self, apps):
        self.apps = apps

    def detect_sites(self):
        return []

    def list_applications(self):
        return self.apps

    def list_app_pools(self):
        return []


class FailFirstFolderBackup:
    def __init__(self):
        self.calls = []

    def backup_folders(self, folders, destination):
        source = folders[0]
        self.calls.append(source["path"])
        if len(self.calls) == 1:
            raise PermissionError("first archive is locked")
        archive = Path(destination) / f"{source['name']}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"valid zip placeholder")
        return [archive]


def test_iis_child_backup_continues_after_one_application_fails(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    apps = [
        {"site": "Default Web Site", "path": "/first", "physical_path": str(first), "application_pool": "Pool1"},
        {"site": "Default Web Site", "path": "/second", "physical_path": str(second), "application_pool": "Pool2"},
    ]
    config = deepcopy(DEFAULT_CONFIG)
    config["general"]["backup_root_folder"] = str(tmp_path / "backups")
    config["iis"].update({
        "backup_enabled": True,
        "auto_detect_sites": True,
        "include_iis_configuration": False,
        "selected_sites": [],
        "selected_apps": ["Default Web Site|/first", "Default Web Site|/second"],
    })
    runner = ChecklistRunner(ConfigManager(data=config))
    runner.iis_manager = FakeIISManager(apps)
    folder_backup = FailFirstFolderBackup()
    runner.folder_backup = folder_backup
    runner._automated_root = tmp_path / "backups" / "Automatedbackup_test"
    runner._backup_timestamp = "20261111_120000"
    report = {"tasks": []}

    runner.backup_iis(report, None, None)

    assert folder_backup.calls == [str(first), str(second)]
    assert [item["status"] for item in report["iis_apps_detail"]] == ["Failed", "Completed"]
    child_summary = next(item for item in report["tasks"] if item["task"] == "IIS Child Applications")
    assert child_summary["status"] == "Partial"
    assert "first archive is locked" in child_summary["detail"]
    assert "1 archived or covered" in child_summary["detail"]
