from copy import deepcopy

from core.service_runtime import ServiceRuntime, config_for_module
from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


def test_module_config_keeps_only_requested_backup_module(tmp_path):
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path)
    data["iis"]["selected_apps"] = ["Site|/App"]
    data["services"]["selected_services"] = ["ServiceOne"]
    data["sql_server"]["databases"] = ["SupportDB"]
    data["folder_backup"]["folders"] = [{"name": "Files", "path": str(tmp_path), "enabled": True}]
    manager = ConfigManager(data=data)

    iis_only = config_for_module(manager, "iis")

    assert iis_only.get("iis", "selected_apps") == ["Site|/App"]
    assert iis_only.get("services", "selected_services") == []
    assert iis_only.get("sql_server", "databases") == []
    assert iis_only.get("folder_backup", "folders") == []
    assert iis_only.get("cleanup", "enabled") is False


def test_service_executes_cleanup_module_schedule(tmp_path, monkeypatch):
    runtime = ServiceRuntime()
    runtime.config = ConfigManager(data=deepcopy(DEFAULT_CONFIG))
    observed = []

    def fake_cleanup(self):
        observed.append(self.config_manager)
        return {"status": "Completed", "tasks": [{"task": "Cleanup", "status": "Completed"}]}

    monkeypatch.setattr(ChecklistRunner, "run_cleanup_only", fake_cleanup)

    result = runtime.execute({"type": "module_schedule", "payload": {"module": "cleanup"}})

    assert result["status"] == "Completed"
    assert result["schedule_scope"] == "cleanup"
    assert observed == [runtime.config]


def test_service_executes_backup_module_with_isolated_config(tmp_path, monkeypatch):
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path)
    data["iis"]["selected_apps"] = ["Site|/App"]
    data["services"]["selected_services"] = ["ServiceOne"]
    runtime = ServiceRuntime()
    runtime.config = ConfigManager(data=data)
    observed = []

    def fake_run(self, **kwargs):
        observed.append(self.config_manager)
        return {"status": "Completed", "backup_path": str(tmp_path / "backup")}

    monkeypatch.setattr(ChecklistRunner, "run", fake_run)

    result = runtime.execute({"type": "module_schedule", "payload": {"module": "services"}})

    assert result["schedule_scope"] == "services"
    assert observed[0].get("services", "selected_services") == ["ServiceOne"]
    assert observed[0].get("iis", "selected_apps") == []


def test_successful_module_email_is_opt_in_but_warnings_always_notify():
    runtime = ServiceRuntime()
    runtime.config = ConfigManager(data=deepcopy(DEFAULT_CONFIG))
    job = {"type": "module_schedule"}

    assert runtime._should_notify_success(job, {
        "tasks": [{"task": "Cleanup", "status": "Completed"}],
    }) is False
    assert runtime._should_notify_success(job, {
        "tasks": [{"task": "Cleanup", "status": "Partial"}],
    }) is True
    runtime.config.set("email", "notify_module_success", True)
    assert runtime._should_notify_success(job, {"tasks": []}) is True
