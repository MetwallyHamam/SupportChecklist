import json

from core.job_queue import PersistentJobQueue
from core.scheduled_jobs import ScheduledJobStore


def test_default_storage_uses_separate_descriptive_files(monkeypatch, tmp_path):
    monkeypatch.setattr("core.job_queue.get_app_dir", lambda: tmp_path)
    monkeypatch.setattr("core.scheduled_jobs.get_app_dir", lambda: tmp_path)

    queue = PersistentJobQueue()
    schedules = ScheduledJobStore()

    assert queue.path == tmp_path / "queue" / "job_executions.json"
    assert schedules.path == tmp_path / "queue" / "saved_jobs.json"


def test_legacy_files_are_migrated_without_losing_records(monkeypatch, tmp_path):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    executions = [{"id": "execution-1", "status": "completed"}]
    saved_jobs = [{"id": "saved-1", "name": "Nightly backup"}]
    (queue_dir / "jobs.json").write_text(json.dumps(executions), encoding="utf-8")
    (queue_dir / "schedules.json").write_text(json.dumps(saved_jobs), encoding="utf-8")
    monkeypatch.setattr("core.job_queue.get_app_dir", lambda: tmp_path)
    monkeypatch.setattr("core.scheduled_jobs.get_app_dir", lambda: tmp_path)

    queue = PersistentJobQueue()
    schedules = ScheduledJobStore()

    assert queue.load() == executions
    assert schedules.load() == saved_jobs
    assert (queue_dir / "job_executions.json").exists()
    assert (queue_dir / "saved_jobs.json").exists()
    assert not (queue_dir / "jobs.json").exists()
    assert not (queue_dir / "schedules.json").exists()


def test_scheduled_execution_gets_its_own_dated_log(tmp_path):
    queue = PersistentJobQueue(tmp_path / "job_executions.json")
    job_id = queue.enqueue(
        "folder_backup",
        {"schedule_id": "saved-1", "scheduled_for": "2026-10-18T02:00"},
    )
    claimed = queue.claim_next()
    assert claimed["id"] == job_id

    queue.finish(job_id, "completed", result={"backup_path": "E:/Backup/test.zip"})

    logs = list((tmp_path / "execution_logs").glob("*/*.json"))
    assert len(logs) == 1
    execution = json.loads(logs[0].read_text(encoding="utf-8"))
    assert execution["id"] == job_id
    assert execution["status"] == "completed"
    assert execution["result"]["backup_path"] == "E:/Backup/test.zip"


def test_manual_execution_does_not_create_a_scheduled_log(tmp_path):
    queue = PersistentJobQueue(tmp_path / "job_executions.json")
    job_id = queue.enqueue("checklist", {"manual": True})
    queue.claim_next()

    queue.finish(job_id, "completed", result={"status": "Completed"})

    assert not (tmp_path / "execution_logs").exists()


def test_interrupted_scheduled_checklist_is_requeued_after_restart(tmp_path):
    queue = PersistentJobQueue(tmp_path / "job_executions.json")
    job_id = queue.enqueue("checklist", {"schedule_kind": "default"})
    queue.claim_next()

    assert queue.recover_running() == 1

    recovered = next(job for job in queue.load() if job["id"] == job_id)
    assert recovered["status"] == "queued"
    assert "restart" in recovered["last_error"]
    claimed_again = queue.claim_next()
    assert claimed_again["id"] == job_id
    assert claimed_again["attempts"] == 2


def test_interrupted_manual_checklist_requires_manual_retry(tmp_path):
    queue = PersistentJobQueue(tmp_path / "job_executions.json")
    job_id = queue.enqueue("checklist", {"manual": True})
    queue.claim_next()

    queue.recover_running()

    recovered = next(job for job in queue.load() if job["id"] == job_id)
    assert recovered["status"] == "failed"


def test_queue_prunes_old_terminal_jobs_but_keeps_active_work(tmp_path):
    queue = PersistentJobQueue(tmp_path / "job_executions.json")
    for index in range(5):
        job_id = queue.enqueue("cleanup", {"index": index})
        queue.claim_next()
        queue.finish(job_id, "completed")
    active_id = queue.enqueue("checklist", {})

    assert queue.prune_terminal(keep=2) == 3
    jobs = queue.load()
    assert len([job for job in jobs if job["status"] == "completed"]) == 2
    assert any(job["id"] == active_id and job["status"] == "queued" for job in jobs)
