from copy import deepcopy

import pytest

from modules.checklist_runner import ChecklistRunner
from core.service_runtime import ServiceRuntime
from core.job_queue import PersistentJobQueue
from core.backup_lifecycle import (
    INCOMPLETE_MARKER,
    is_legacy_interrupted_scheduled_checklist,
    mark_backup_started,
    remove_incomplete_backups,
    remove_legacy_interrupted_backup,
)
from utils.config_manager import ConfigManager, DEFAULT_CONFIG


def config_without_backup_targets(tmp_path):
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path)
    data["iis"]["selected_sites"] = []
    data["iis"]["selected_apps"] = []
    data["services"]["selected_services"] = []
    data["sql_server"]["databases"] = []
    data["sql_server"]["database_name"] = ""
    data["folder_backup"]["folders"] = []
    return ConfigManager(data=data)


def test_full_checklist_rejects_run_without_backup_targets(tmp_path):
    runner = ChecklistRunner(config_without_backup_targets(tmp_path))

    errors, _ = runner.get_preflight_messages()

    assert any("No backup items are selected" in error for error in errors)
    with pytest.raises(ValueError, match="No backup items are selected"):
        runner.run()
    assert list(tmp_path.glob("Automatedbackup_*")) == []


def test_cleanup_only_remains_available_without_backup_targets(tmp_path):
    manager = config_without_backup_targets(tmp_path)
    manager.set("cleanup", "enabled", True)
    manager.set("cleanup", "tasks", [{"path": str(tmp_path), "enabled": True}])
    runner = ChecklistRunner(manager)
    runner.cleanup_logs = lambda report, progress_callback, stop_check: report["tasks"].append(
        {"task": "Cleanup", "status": "Completed"}
    )

    result = runner.run_cleanup_only()

    assert result["status"] == "Completed"
    assert "backup_path" not in result


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [(512, "512 bytes"), (1536, "1.5 KB"), (2 * 1024**2, "2.0 MB"), (2 * 1024**3, "2.0 GB")],
)
def test_backup_size_does_not_round_small_backups_to_zero(size_bytes, expected):
    assert ChecklistRunner._format_backup_size(size_bytes) == expected


def test_service_falls_back_to_full_config_when_schedule_filter_removes_all_targets(tmp_path, monkeypatch):
    manager = config_without_backup_targets(tmp_path)
    manager.set("services", "selected_services", ["BackupService"])
    runtime = ServiceRuntime()
    runtime.config = manager
    filtered = config_without_backup_targets(tmp_path)
    observed = []

    monkeypatch.setattr("core.service_runtime.config_for_default_schedule", lambda *args: filtered)

    def fake_run(runner):
        observed.append(runner.config_manager)
        return {"status": "Completed"}

    monkeypatch.setattr(ChecklistRunner, "run", fake_run)

    result = runtime.execute({"type": "checklist", "payload": {"include_all": False}})

    assert result["status"] == "Completed"
    assert observed == [manager]


def test_restart_cleanup_removes_only_marked_incomplete_backups(tmp_path):
    incomplete = tmp_path / "Automatedbackup_20261026_020000"
    completed = tmp_path / "Automatedbackup_20261025_020000"
    mark_backup_started(incomplete)
    completed.mkdir()
    (completed / "valid.bak.zip").write_bytes(b"valid")

    removed = remove_incomplete_backups(tmp_path)

    assert removed == [incomplete.resolve()]
    assert not incomplete.exists()
    assert completed.exists()
    assert not (completed / INCOMPLETE_MARKER).exists()


def test_service_deletes_previous_backup_only_after_scheduled_success(tmp_path, monkeypatch):
    old_backup = tmp_path / "Automatedbackup_20261025_020000"
    new_backup = tmp_path / "Automatedbackup_20261026_020000"
    old_backup.mkdir()
    new_backup.mkdir()
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path)
    data["schedule"]["enabled"] = False
    data["schedule"]["delete_previous_automated_backups"] = True
    runtime = ServiceRuntime()
    runtime.config = ConfigManager(data=data)
    runtime.config.load_config = lambda create_if_missing=False: None
    runtime.queue = PersistentJobQueue(tmp_path / "queue" / "jobs.json")
    runtime.queue.enqueue("checklist", {"schedule_kind": "default"})
    monkeypatch.setattr(
        runtime,
        "execute",
        lambda job: {"status": "Completed", "backup_path": str(new_backup)},
    )
    monkeypatch.setattr("core.service_runtime.EmailNotifier.notify_result", lambda *args, **kwargs: None)

    assert runtime.process_once() is True

    assert not old_backup.exists()
    assert new_backup.exists()
    completed = runtime.queue.load()[0]
    assert completed["status"] == "completed"
    assert completed["result"]["previous_backups_deleted"] == 1


def test_legacy_interrupted_job_matches_and_removes_only_its_exact_folder(tmp_path):
    interrupted = tmp_path / "Automatedbackup_20261025_020002"
    valid = tmp_path / "Automatedbackup_20261018_175952"
    interrupted.mkdir()
    valid.mkdir()
    job = {
        "type": "checklist",
        "status": "failed",
        "created_at": "2026-10-25T02:00:02",
        "last_error": (
            "Checklist was interrupted by a service stop and was not automatically "
            "restarted. Retry it manually if required."
        ),
        "payload": {"schedule_kind": "default"},
    }

    assert is_legacy_interrupted_scheduled_checklist(job)
    assert remove_legacy_interrupted_backup(tmp_path, job) == interrupted.resolve()
    assert not interrupted.exists()
    assert valid.exists()


def test_service_startup_migrates_legacy_interrupted_job(tmp_path, monkeypatch):
    interrupted = tmp_path / "Automatedbackup_20261025_020002"
    interrupted.mkdir()
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path)
    runtime = ServiceRuntime(stop_requested=lambda: True)
    runtime.config = ConfigManager(data=data)
    runtime.config.load_config = lambda create_if_missing=False: None
    runtime.queue = PersistentJobQueue(tmp_path / "queue" / "jobs.json")
    job_id = runtime.queue.enqueue("checklist", {"schedule_kind": "default"})
    runtime.queue.claim_next()
    runtime.queue.finish(
        job_id,
        "failed",
        error=(
            "Checklist was interrupted by a service stop and was not automatically "
            "restarted. Retry it manually if required."
        ),
    )
    jobs = runtime.queue.load()
    jobs[0]["created_at"] = "2026-10-25T02:00:02"
    runtime.queue.save(jobs)
    notifications = []
    monkeypatch.setattr(
        "core.service_runtime.EmailNotifier.notify_result",
        lambda self, job, status, detail="", result=None: notifications.append(
            (job["id"], status, detail)
        ),
    )

    runtime.run()

    assert not interrupted.exists()
    recovered = runtime.queue.load()[0]
    assert recovered["status"] == "queued"
    assert runtime.recovery_summary == (
        "requeued_or_closed_jobs=1, removed_incomplete_backups=1"
    )
    assert notifications
    assert notifications[0][0] == job_id
    assert notifications[0][1] == "interrupted and recovered"
    assert "queued to restart" in notifications[0][2]
