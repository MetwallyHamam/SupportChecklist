from __future__ import annotations

import json
import os
import tempfile
from functools import wraps
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from core.job_queue import PersistentJobQueue
from utils.paths import get_app_dir
from utils.process_lock import process_lock


def _locked_store(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with process_lock(self.path):
            return method(self, *args, **kwargs)
    return wrapped


class ScheduledJobStore:
    """Persistent saved job definitions, separate from their executions."""

    def __init__(self, path: Path | None = None):
        queue_dir = get_app_dir() / "queue"
        self.path = path or queue_dir / "saved_jobs.json"
        self.legacy_path = queue_dir / "schedules.json" if path is None else None

    @_locked_store
    def load(self) -> list[dict]:
        self._migrate_legacy_file()
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read scheduled jobs: {exc}") from exc
        if not isinstance(data, list):
            raise RuntimeError("Scheduled jobs file must contain a JSON list")
        supported = [item for item in data if item.get("job_type") != "file_profile"]
        if len(supported) != len(data):
            self.save(supported)
        return supported

    def _migrate_legacy_file(self) -> None:
        """Move existing schedule definitions to the clearly named saved-jobs file."""
        if self.path.exists() or self.legacy_path is None or not self.legacy_path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self.legacy_path, self.path)
        except FileNotFoundError:
            pass

    @_locked_store
    def save(self, schedules: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix="saved-jobs-", suffix=".tmp", delete=False,
            ) as stream:
                json.dump(schedules, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    @_locked_store
    def add(self, name: str, job_type: str, payload: dict, schedule: dict) -> str:
        schedules = self.load()
        schedule_id = uuid4().hex
        schedules.append({
            "id": schedule_id,
            "name": name,
            "job_type": job_type,
            "payload": payload,
            "enabled": True,
            "frequency": schedule.get("frequency", "daily"),
            "time": schedule.get("time", "02:00"),
            "weekdays": schedule.get("weekdays", []),
            "interval_days": max(1, int(schedule.get("interval_days", 1))),
            "interval_minutes": max(1, int(schedule.get("interval_minutes", 15))),
            "day_of_month": min(31, max(1, int(schedule.get("day_of_month", 1)))),
            "run_missed": bool(schedule.get("run_missed", True)),
            "last_enqueued_for": "",
            "last_run": "",
            "next_run": "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        self.save(schedules)
        return schedule_id

    @_locked_store
    def upsert_module(self, module: str, name: str, schedule: dict) -> str:
        """Create or replace the single tab-level schedule for a module."""
        schedules = self.load()
        existing = next(
            (
                item for item in schedules
                if item.get("job_type") == "module_schedule"
                and item.get("payload", {}).get("module") == module
            ),
            None,
        )
        schedule_id = str(existing.get("id")) if existing else uuid4().hex
        record = {
            "id": schedule_id,
            "name": name,
            "job_type": "module_schedule",
            "payload": {"module": module, "module_scope": module},
            "enabled": bool(schedule.get("enabled", True)),
            "frequency": schedule.get("frequency", "daily"),
            "time": schedule.get("time", "02:00"),
            "weekdays": schedule.get("weekdays", []),
            "interval_days": max(1, int(schedule.get("interval_days", 1))),
            "interval_minutes": max(1, int(schedule.get("interval_minutes", 15))),
            "day_of_month": min(31, max(1, int(schedule.get("day_of_month", 1)))),
            "run_missed": bool(schedule.get("run_missed", True)),
            "last_enqueued_for": existing.get("last_enqueued_for", "") if existing else "",
            "last_run": existing.get("last_run", "") if existing else "",
            "next_run": "",
            "created_at": existing.get("created_at", datetime.now().isoformat(timespec="seconds")) if existing else datetime.now().isoformat(timespec="seconds"),
        }
        schedules = [item for item in schedules if item.get("id") != schedule_id]
        schedules.append(record)
        self.save(schedules)
        return schedule_id

    @_locked_store
    def delete(self, schedule_id: str) -> bool:
        schedules = self.load()
        remaining = [item for item in schedules if item.get("id") != schedule_id]
        if len(remaining) == len(schedules):
            return False
        self.save(remaining)
        return True

    @staticmethod
    def _scheduled_occurrence(item: dict, now: datetime) -> datetime | None:
        try:
            created = datetime.fromisoformat(str(item.get("created_at", now.isoformat())))
        except ValueError:
            created = now
        try:
            hour, minute = (int(value) for value in str(item.get("time", "02:00")).split(":"))
        except (TypeError, ValueError):
            hour, minute = 2, 0
        today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        frequency = item.get("frequency", "daily")
        if frequency == "minutes":
            created = created.replace(microsecond=0)
            interval = max(1, int(item.get("interval_minutes", 15)))
            if created > now:
                return None
            elapsed_minutes = int((now - created).total_seconds() // 60)
            return created + timedelta(minutes=(elapsed_minutes // interval) * interval)
        if frequency == "monthly":
            requested_day = min(31, max(1, int(item.get("day_of_month", 1))))
            day = min(requested_day, monthrange(now.year, now.month)[1])
            candidate = today.replace(day=day)
            if candidate <= now:
                return candidate if candidate >= created else None
            previous_month = now.month - 1 or 12
            previous_year = now.year if now.month > 1 else now.year - 1
            previous_day = min(requested_day, monthrange(previous_year, previous_month)[1])
            candidate = candidate.replace(year=previous_year, month=previous_month, day=previous_day)
            return candidate if candidate >= created else None
        if frequency == "weekly":
            weekdays = {str(day).lower() for day in item.get("weekdays", [])}
            for days_back in range(8):
                candidate = today - timedelta(days=days_back)
                if candidate.strftime("%A").lower() in weekdays and candidate <= now:
                    return candidate if candidate >= created else None
            return None
        if frequency == "interval":
            interval = max(1, int(item.get("interval_days", 1)))
            first = created.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if first < created:
                first += timedelta(days=interval)
            if first > now:
                return None
            elapsed_days = max(0, (now.date() - first.date()).days)
            return first + timedelta(days=(elapsed_days // interval) * interval)
        candidate = today if today <= now else today - timedelta(days=1)
        return candidate if candidate >= created else None

    @_locked_store
    def enqueue_due(
        self,
        queue: PersistentJobQueue,
        now: datetime | None = None,
        startup_marker: str | None = None,
    ) -> int:
        current = now or datetime.now()
        schedules = self.load()
        queued = 0
        changed = False
        for item in schedules:
            if not item.get("enabled", True):
                continue
            if item.get("frequency") == "startup":
                if not startup_marker:
                    continue
                occurrence = current
                occurrence_key = f"startup:{startup_marker}"
            else:
                occurrence = self._scheduled_occurrence(item, current)
                if occurrence is None:
                    continue
                occurrence_key = occurrence.isoformat(timespec="minutes")
            if item.get("last_enqueued_for") == occurrence_key:
                continue
            if (
                item.get("frequency") != "startup"
                and not item.get("run_missed", True)
                and current - occurrence > timedelta(minutes=5)
            ):
                item["last_enqueued_for"] = occurrence_key
                changed = True
                continue
            # The primary checklist includes every configured module. When a
            # module schedule collides with it, the primary occurrence satisfies
            # the module schedule and avoids duplicate backup/cleanup work.
            if item.get("job_type") == "module_schedule":
                collision = any(
                    job.get("type") == "checklist"
                    and job.get("status") in {"queued", "running", "completed"}
                    and self._within_collision_window(
                        job.get("payload", {}).get("scheduled_for"), occurrence, minutes=10,
                    )
                    for job in queue.load()
                )
                if collision:
                    item["last_enqueued_for"] = occurrence_key
                    item["last_run"] = current.isoformat(timespec="seconds")
                    changed = True
                    continue
            payload = dict(item.get("payload", {}))
            payload.update({"schedule_id": item["id"], "scheduled_for": occurrence_key})
            queue.enqueue(str(item.get("job_type")), payload)
            item["last_enqueued_for"] = occurrence_key
            item["last_run"] = current.isoformat(timespec="seconds")
            queued += 1
            changed = True
        if changed:
            self.save(schedules)
        return queued

    @staticmethod
    def _within_collision_window(value: object, occurrence: datetime, minutes: int) -> bool:
        try:
            scheduled = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return False
        return abs((scheduled - occurrence).total_seconds()) <= minutes * 60
