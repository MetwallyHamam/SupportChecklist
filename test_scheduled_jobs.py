from datetime import datetime

from core.job_queue import PersistentJobQueue
from core.scheduled_jobs import ScheduledJobStore
from core.storage_paths import classify_storage_path, storage_warnings


def test_legacy_file_manager_schedules_are_removed(tmp_path):
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedules.save([
        {"id": "old", "name": "Old copy", "job_type": "file_profile", "enabled": True},
        {"id": "keep", "name": "Database", "job_type": "database_backup", "enabled": True},
    ])

    assert [item["id"] for item in schedules.load()] == ["keep"]
    assert "file_profile" not in schedules.path.read_text(encoding="utf-8")


def test_daily_schedule_is_enqueued_only_once(tmp_path):
    queue = PersistentJobQueue(tmp_path / "queue.json")
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedules.add("Daily", "database_backup", {}, {
        "frequency": "daily", "time": "02:00", "run_missed": True,
    })
    items = schedules.load()
    items[0]["created_at"] = "2026-09-27T01:00:00"
    schedules.save(items)
    now = datetime(2026, 9, 28, 3, 0)
    assert schedules.enqueue_due(queue, now) == 1
    assert schedules.enqueue_due(queue, now) == 0
    assert len(queue.load()) == 1


def test_weekly_schedules_are_independent(tmp_path):
    queue = PersistentJobQueue(tmp_path / "queue.json")
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedules.add("Weekly", "cleanup", {"path": "C:/logs"}, {
        "frequency": "weekly", "time": "02:00", "weekdays": ["Monday"],
    })
    items = schedules.load()
    items[0]["created_at"] = "2026-09-20T01:00:00"
    schedules.save(items)
    assert schedules.enqueue_due(queue, datetime(2026, 9, 28, 3, 0)) == 1
    assert queue.load()[0]["type"] == "cleanup"


def test_minute_schedule_enqueues_once_per_interval(tmp_path):
    queue = PersistentJobQueue(tmp_path / "queue.json")
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedule_id = schedules.add("Log cleanup", "script", {"path": "C:/Scripts/clean.ps1"}, {
        "frequency": "minutes", "interval_minutes": 15, "run_missed": True,
    })
    items = schedules.load()
    items[0]["created_at"] = "2026-10-26T10:00:00"
    schedules.save(items)

    assert schedules.enqueue_due(queue, datetime(2026, 10, 26, 10, 14)) == 1
    assert schedules.enqueue_due(queue, datetime(2026, 10, 26, 10, 14, 59)) == 0
    assert schedules.enqueue_due(queue, datetime(2026, 10, 26, 10, 15)) == 1
    jobs = queue.load()
    assert [job["type"] for job in jobs] == ["script", "script"]
    assert all(job["payload"]["schedule_id"] == schedule_id for job in jobs)


def test_monthly_schedule_catches_up_and_clamps_to_month_end(tmp_path):
    queue = PersistentJobQueue(tmp_path / "queue.json")
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedules.add("Monthly database", "database_backup", {}, {
        "frequency": "monthly", "time": "02:00", "day_of_month": 31,
        "run_missed": True,
    })
    items = schedules.load()
    items[0]["created_at"] = "2026-01-01T01:00:00"
    schedules.save(items)

    assert schedules.enqueue_due(queue, datetime(2026, 2, 28, 3, 0)) == 1
    job = queue.load()[0]
    assert job["payload"]["scheduled_for"] == "2026-02-28T02:00"
    assert schedules.enqueue_due(queue, datetime(2026, 3, 1, 12, 0)) == 0


def test_startup_schedule_runs_once_per_service_start(tmp_path):
    queue = PersistentJobQueue(tmp_path / "queue.json")
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedules.add("Startup script", "script", {"path": "C:/Scripts/start.cmd"}, {
        "frequency": "startup",
    })
    now = datetime(2026, 11, 10, 8, 0)

    assert schedules.enqueue_due(queue, now, startup_marker="boot-a") == 1
    assert schedules.enqueue_due(queue, now, startup_marker="boot-a") == 0
    assert schedules.enqueue_due(queue, now, startup_marker="boot-b") == 1
    assert [job["payload"]["scheduled_for"] for job in queue.load()] == [
        "startup:boot-a", "startup:boot-b",
    ]


def test_module_schedule_is_updated_instead_of_duplicated(tmp_path):
    schedules = ScheduledJobStore(tmp_path / "schedules.json")

    first_id = schedules.upsert_module("cleanup", "Cleanup tab schedule", {
        "frequency": "daily", "time": "23:00",
    })
    second_id = schedules.upsert_module("cleanup", "Cleanup tab schedule", {
        "frequency": "daily", "time": "22:30",
    })

    assert first_id == second_id
    items = schedules.load()
    assert len(items) == 1
    assert items[0]["payload"] == {"module": "cleanup", "module_scope": "cleanup"}
    assert items[0]["time"] == "22:30"


def test_primary_schedule_satisfies_colliding_module_schedule(tmp_path):
    queue = PersistentJobQueue(tmp_path / "queue.json")
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedules.upsert_module("cleanup", "Cleanup tab schedule", {
        "frequency": "daily", "time": "02:00", "run_missed": True,
    })
    queue.enqueue("checklist", {
        "schedule_kind": "default", "scheduled_for": "2026-11-11T02:00",
    })

    assert schedules.enqueue_due(queue, datetime(2026, 11, 11, 2, 5)) == 0
    assert [job["type"] for job in queue.load()] == ["checklist"]


def test_new_daily_schedule_does_not_catch_up_before_it_was_created(tmp_path):
    queue = PersistentJobQueue(tmp_path / "queue.json")
    schedules = ScheduledJobStore(tmp_path / "schedules.json")
    schedules.upsert_module("cleanup", "Cleanup tab schedule", {
        "frequency": "daily", "time": "03:00", "run_missed": True,
    })
    items = schedules.load()
    items[0]["created_at"] = "2026-11-11T02:55:00"
    schedules.save(items)

    assert schedules.enqueue_due(queue, datetime(2026, 11, 11, 2, 59)) == 0
    assert queue.load() == []


def test_storage_path_classification():
    assert classify_storage_path(r"\\server\share\backup") == "unc"
    assert classify_storage_path(r"C:\Users\User\OneDrive - Company\Backup") == "onedrive_sync"
    assert classify_storage_path("https://company.sharepoint.com/site") == "cloud_url"
    assert storage_warnings("https://company.sharepoint.com/site", service_mode=True)
