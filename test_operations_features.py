import json
from pathlib import Path

from core.job_queue import PersistentJobQueue
from core.preflight import validate_paths
from core.restore_manager import RestoreManager
from core.retention import apply_retention
from modules.folder_backup import FolderBackup
from core.service_runtime import ServiceRuntime


def test_persistent_queue_does_not_restart_interrupted_checklist(tmp_path):
    queue = PersistentJobQueue(tmp_path / "jobs.json")
    job_id = queue.enqueue("checklist")
    assert queue.claim_next()["id"] == job_id
    assert queue.recover_running() == 1
    recovered = queue.load()[0]
    assert recovered["status"] == "failed"
    assert "not automatically restarted" in recovered["last_error"]
    assert queue.claim_next() is None


def test_persistent_queue_resumes_interrupted_backup_job(tmp_path):
    queue = PersistentJobQueue(tmp_path / "jobs.json")
    job_id = queue.enqueue("database_backup")
    assert queue.claim_next()["id"] == job_id
    assert queue.recover_running() == 1
    assert queue.load()[0]["status"] == "interrupted"
    assert queue.claim_next()["id"] == job_id


def test_preflight_rejects_destination_inside_source(tmp_path):
    source = tmp_path / "source"
    destination = source / "backup"
    source.mkdir()
    issues = validate_paths(str(source), str(destination))
    assert "destination_inside_source" in {issue.code for issue in issues}


def test_retention_removes_every_backup_older_than_limit(tmp_path):
    import os
    import time
    folders = []
    for index in range(3):
        folder = tmp_path / str(index)
        folder.mkdir()
        old = time.time() - (200 - index) * 86400
        os.utime(folder, (old, old))
        folders.append(folder)
    removed = apply_retention(tmp_path, max_age_days=30)
    assert len(removed) == 3
    assert not any(folder.exists() for folder in folders)


def test_restore_rejects_zip_slip(tmp_path):
    import zipfile
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("../escape.txt", "unsafe")
    manager = RestoreManager()
    try:
        manager.restore(archive, tmp_path / "restore")
    except ValueError as exc:
        assert "Unsafe archive member" in str(exc)
    else:
        raise AssertionError("unsafe archive member was accepted")


def test_incremental_folder_backup_reuses_cached_archive(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("same", encoding="utf-8")
    backup = FolderBackup(incremental=True)
    backup.cache_root = tmp_path / "cache"
    first = backup.backup_folders([{"name": "data", "path": str(source), "enabled": True}], tmp_path / "one")[0]
    second = backup.backup_folders([{"name": "data", "path": str(source), "enabled": True}], tmp_path / "two")[0]
    assert first.read_bytes() == second.read_bytes()


def test_service_can_back_up_one_scheduled_folder(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "item.txt").write_text("backup me", encoding="utf-8")
    runtime = ServiceRuntime()
    runtime.config.data = {
        "general": {"backup_root_folder": str(tmp_path / "backups"), "incremental_backups": False}
    }

    result = runtime.execute({
        "type": "folder_backup",
        "payload": {"name": "Monthly Data", "path": str(source)},
    })

    archive = Path(result["backup_path"])
    assert result["status"] == "Completed"
    assert archive.is_file()
    assert "ScheduledFolderBackups" in archive.parts


def test_service_can_back_up_one_scheduled_windows_service_folder(tmp_path):
    service_folder = tmp_path / "service"
    service_folder.mkdir()
    executable = service_folder / "ExampleService.exe"
    executable.write_bytes(b"executable")
    runtime = ServiceRuntime()
    runtime.config.data = {
        "general": {"backup_root_folder": str(tmp_path / "backups"), "incremental_backups": False}
    }

    result = runtime.execute({
        "type": "service_backup",
        "payload": {"name": "ExampleService", "executable_path": str(executable)},
    })

    assert Path(result["backup_path"]).is_file()
    assert "ScheduledServiceBackups" in Path(result["backup_path"]).parts
