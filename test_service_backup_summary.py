from copy import deepcopy
from pathlib import Path

from modules.checklist_runner import ChecklistRunner
from modules.service_backup import ServiceInfo
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


class FakeServiceBackup:
    def __init__(self, services, failing_names=()):
        self.services = services
        self.failing_names = set(failing_names)

    def list_services(self):
        return self.services

    def backup_service_folder(self, executable_path, backup_root):
        name = Path(executable_path).stem
        if name in self.failing_names:
            raise PermissionError("service folder is locked")
        archive = Path(backup_root) / f"{name}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"service archive")
        return archive


def make_runner(tmp_path, selected_services):
    config = deepcopy(DEFAULT_CONFIG)
    config["general"]["backup_root_folder"] = str(tmp_path / "backups")
    config["services"]["backup_enabled"] = True
    config["services"]["selected_services"] = selected_services
    runner = ChecklistRunner(ConfigManager(data=config))
    runner._automated_root = tmp_path / "backups" / "Automatedbackup_test"
    runner._backup_timestamp = "20261111_120000"
    return runner


def test_services_summary_reports_selected_detected_and_archived_counts(tmp_path):
    executable = tmp_path / "ServiceOne.exe"
    executable.write_bytes(b"exe")
    runner = make_runner(tmp_path, ["ServiceOne"])
    runner.service_backup = FakeServiceBackup([
        ServiceInfo("ServiceOne", "Service One", str(executable), "Running")
    ])
    report = {"tasks": []}

    runner.backup_services(report, None, None)

    summary = report["tasks"][-1]
    assert summary == {
        "task": "Services",
        "status": "Completed",
        "detail": "1 selected, 1 detected, 1 archived",
    }
    assert report["services_detail"][0]["status"] == "Completed"


def test_services_summary_warns_and_continues_for_failed_and_missing_services(tmp_path):
    failing_executable = tmp_path / "ServiceOne.exe"
    good_executable = tmp_path / "ServiceTwo.exe"
    failing_executable.write_bytes(b"exe")
    good_executable.write_bytes(b"exe")
    runner = make_runner(tmp_path, ["ServiceOne", "ServiceTwo", "MissingService"])
    runner.service_backup = FakeServiceBackup(
        [
            ServiceInfo("ServiceOne", "Service One", str(failing_executable), "Running"),
            ServiceInfo("ServiceTwo", "Service Two", str(good_executable), "Stopped"),
        ],
        failing_names={"ServiceOne"},
    )
    report = {"tasks": []}

    runner.backup_services(report, None, None)

    summary = report["tasks"][-1]
    assert summary["status"] == "Partial"
    assert summary["detail"].startswith("3 selected, 2 detected, 1 archived")
    assert "ServiceOne: service folder is locked" in summary["detail"]
    assert "MissingService: Selected service was not returned" in summary["detail"]
    assert [item["status"] for item in report["services_detail"]] == [
        "Failed", "Completed", "Failed"
    ]
