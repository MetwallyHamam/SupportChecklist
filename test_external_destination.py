from copy import deepcopy
from pathlib import Path

import pytest

from core.job_queue import PersistentJobQueue
from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager, DEFAULT_CONFIG
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
    job_id = queue.enqueue("checklist", {"schedule_kind": "default"})
    claimed = queue.claim_next()
    assert claimed and claimed["id"] == job_id

    assert queue.defer_for_external_drive(job_id, "Drive missing") is True
    waiting = queue.load()[0]
    assert waiting["status"] == "waiting_external"
    assert queue.claim_next() is None

    assert queue.resume_external_jobs() == 1
    resumed = queue.claim_next()
    assert resumed and resumed["payload"]["external_reconnected"] is True


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
