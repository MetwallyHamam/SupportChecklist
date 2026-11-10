from copy import deepcopy
from pathlib import Path

import pytest

from core.job_queue import PersistentJobQueue
from core.external_copy import copy_verified_backup
from core.retention import apply_retention
from core.service_runtime import ServiceRuntime
from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager, DEFAULT_CONFIG, migrate_external_destination
from utils.external_drive import ExternalDriveUnavailable, resolve_external_destination


def test_external_destination_resolves_by_serial_when_drive_letter_changes(tmp_path, monkeypatch):
    destination = tmp_path / "MiChecklist"
    destination.mkdir()
    monkeypatch.setattr(
        "utils.external_drive.iter_windows_volumes",
        lambda: [(str(tmp_path) + "\\", "A1B2C3D4", "BACKUP")],
    )
    monkeypatch.setattr("utils.external_drive.os.access", lambda *_args: True)

    resolved = resolve_external_destination({
        "volume_serial": "a1b2c3d4",
        "volume_label": "BACKUP",
        "relative_path": "MiChecklist",
    })

    assert resolved == destination


def test_external_destination_reports_missing_configured_volume(monkeypatch):
    monkeypatch.setattr("utils.external_drive.iter_windows_volumes", lambda: [])
    with pytest.raises(ExternalDriveUnavailable, match="BACKUP"):
        resolve_external_destination({
            "volume_serial": "A1B2C3D4", "volume_label": "BACKUP", "relative_path": "Backups",
        })


def test_queue_waits_for_external_drive_and_resumes_without_failing(tmp_path):
    queue = PersistentJobQueue(tmp_path / "jobs.json")
    source = tmp_path / "Automatedbackup_1"
    source.mkdir()
    job_id = queue.enqueue_external_copy(str(source), "checklist-1")
    claimed = queue.claim_next()
    assert claimed and claimed["id"] == job_id

    assert queue.defer_for_external_drive(job_id, "Drive missing") is True
    waiting = queue.load()[0]
    assert waiting["status"] == "waiting_external"
    assert queue.claim_next() is None

    assert queue.resume_external_jobs() == 1
    resumed = queue.claim_next()
    assert resumed and resumed["payload"]["external_reconnected"] is True


def test_latest_external_copy_supersedes_only_older_pending_copy(tmp_path):
    queue = PersistentJobQueue(tmp_path / "jobs.json")
    first = tmp_path / "Automatedbackup_1"
    second = tmp_path / "Automatedbackup_2"
    first.mkdir()
    second.mkdir()
    first_id = queue.enqueue_external_copy(str(first), "checklist-1", latest_only=True)
    second_id = queue.enqueue_external_copy(str(second), "checklist-2", latest_only=True)

    jobs = {job["id"]: job for job in queue.load()}
    assert jobs[first_id]["status"] == "superseded"
    assert jobs[second_id]["status"] == "queued"
    assert queue.pending_external_source_paths() == {second.resolve()}


def test_external_copy_is_verified_and_uses_no_partial_final_name(tmp_path):
    source = tmp_path / "Automatedbackup_20261111_020000"
    (source / "Database").mkdir(parents=True)
    (source / "Database" / "db.zip").write_bytes(b"verified backup")
    destination = tmp_path / "external"

    result = copy_verified_backup(source, destination, "copy-job")

    final = destination / source.name
    assert result["status"] == "Completed"
    assert (final / "Database" / "db.zip").read_bytes() == b"verified backup"
    assert not any("partial-copy-job" in item.name for item in destination.iterdir())


def test_retention_preserves_backup_awaiting_external_copy(tmp_path):
    import os
    import time

    pending = tmp_path / "Automatedbackup_pending"
    removable = tmp_path / "Automatedbackup_old"
    pending.mkdir()
    removable.mkdir()
    old = time.time() - 200 * 86400
    os.utime(pending, (old, old))
    os.utime(removable, (old, old))

    removed = apply_retention(
        tmp_path, max_age_days=30, protected_paths={pending.resolve()},
    )

    assert pending.is_dir()
    assert str(removable) in removed


def test_legacy_external_destination_requires_local_destination_selection():
    data = {
        "general": {"backup_root_folder": r"E:\MiChecklist"},
        "external_destination": {
            "enabled": True, "volume_serial": "A1B2C3D4", "volume_label": "BACKUP",
            "relative_path": "MiChecklist", "check_interval_minutes": 5,
            "reminder_interval_minutes": 120,
        },
    }

    assert migrate_external_destination(data) is True
    assert data["external_copy"]["enabled"] is False
    assert data["external_copy"]["volume_serial"] == "A1B2C3D4"
    assert data["external_copy"]["migration_requires_local_destination"] is True


def test_missing_external_drive_does_not_block_local_checklist(tmp_path, monkeypatch):
    local_backup = tmp_path / "local" / "Automatedbackup_20261111_030000"
    local_backup.mkdir(parents=True)
    (local_backup / "verified.zip").write_bytes(b"local backup")
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path / "local")
    data["schedule"]["enabled"] = False
    data["email"]["enabled"] = False
    data["external_copy"].update({
        "enabled": True,
        "volume_serial": "A1B2C3D4",
        "volume_label": "BACKUP",
        "relative_path": "MiChecklist",
    })
    manager = ConfigManager(config_path=tmp_path / "config.json", data=data)
    manager.save_config()
    runtime = ServiceRuntime()
    runtime.config = manager
    runtime.queue = PersistentJobQueue(tmp_path / "jobs.json")
    checklist_id = runtime.queue.enqueue("checklist", {"include_all": True})
    monkeypatch.setattr(
        "core.service_runtime.resolve_external_destination",
        lambda _settings: (_ for _ in ()).throw(ExternalDriveUnavailable("Drive missing")),
    )
    monkeypatch.setattr(
        ChecklistRunner, "run",
        lambda self, **_kwargs: {
            "status": "Completed", "backup_path": str(local_backup), "tasks": [],
        },
    )

    assert runtime.process_once() is True

    jobs = runtime.queue.load()
    checklist = next(job for job in jobs if job["id"] == checklist_id)
    copy_job = next(job for job in jobs if job["type"] == "external_backup_copy")
    assert checklist["status"] == "completed"
    assert copy_job["status"] == "queued"
    assert copy_job["payload"]["source_path"] == str(local_backup)


def test_service_copies_and_verifies_pending_backup_when_drive_returns(tmp_path, monkeypatch):
    source = tmp_path / "local" / "Automatedbackup_20261111_040000"
    source.mkdir(parents=True)
    (source / "backup.zip").write_bytes(b"verified local backup")
    external = tmp_path / "external"
    external.mkdir()
    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(tmp_path / "local")
    data["schedule"]["enabled"] = False
    data["email"]["enabled"] = False
    data["external_copy"].update({
        "enabled": True,
        "volume_serial": "A1B2C3D4",
        "volume_label": "BACKUP",
        "relative_path": "MiChecklist",
    })
    manager = ConfigManager(config_path=tmp_path / "config.json", data=data)
    manager.save_config()
    runtime = ServiceRuntime()
    runtime.config = manager
    runtime.queue = PersistentJobQueue(tmp_path / "jobs.json")
    copy_id = runtime.queue.enqueue_external_copy(str(source), "checklist-1")
    monkeypatch.setattr(
        "core.service_runtime.resolve_external_destination", lambda _settings: external,
    )

    assert runtime.process_once() is True

    job = next(item for item in runtime.queue.load() if item["id"] == copy_id)
    assert job["status"] == "completed"
    assert (external / source.name / "backup.zip").read_bytes() == b"verified local backup"
    assert job["result"]["external_copy"]["files"] == 1


def test_checklist_retry_preserves_completed_phases_and_restarts_interrupted_phase(tmp_path, monkeypatch):
    backup_root = tmp_path / "external"
    run_root = backup_root / "Automatedbackup_20261111_020000"
    (run_root / "IIS").mkdir(parents=True)
    (run_root / "IIS" / "verified.zip").write_bytes(b"verified")
    (run_root / "Services").mkdir()
    (run_root / "Services" / "partial.zip").write_bytes(b"partial")

    data = deepcopy(DEFAULT_CONFIG)
    data["general"]["backup_root_folder"] = str(backup_root)
    manager = ConfigManager(config_path=tmp_path / "config.json", data=data)
    runner = ChecklistRunner(manager)
    monkeypatch.setattr(runner, "get_preflight_messages", lambda: ([], []))
    monkeypatch.setattr(runner, "prepare_sql_backup", lambda: None)
    called = []

    def operation(name):
        def run(_report, _progress, _stop):
            called.append(name)
            folder = {"services": "Services", "database": "Database", "folders": "Folders"}.get(name)
            if folder:
                target = run_root / folder
                target.mkdir(parents=True, exist_ok=True)
                (target / f"{name}.zip").write_bytes(name.encode())
        return run

    monkeypatch.setattr(runner, "cleanup_logs", operation("cleanup"))
    monkeypatch.setattr(runner, "backup_iis", operation("iis"))
    monkeypatch.setattr(runner, "backup_services", operation("services"))
    monkeypatch.setattr(runner, "backup_sql", operation("database"))
    monkeypatch.setattr(runner, "backup_folders", operation("folders"))

    result = runner.run(resume_state={
        "backup_path": str(run_root),
        "backup_timestamp": "20261111_020000",
        "completed_steps": ["cleanup", "iis"],
        "current_step": "services",
        "status": "interrupted",
    })

    assert result["status"] == "Completed"
    assert called == ["services", "database", "folders"]
    assert not (run_root / "Services" / "partial.zip").exists()
    assert (run_root / "Services" / "services.zip").is_file()
    assert (run_root / "IIS" / "verified.zip").is_file()
