from __future__ import annotations

import json
import os
import tempfile
import threading
import ctypes
import hashlib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from utils.paths import get_app_dir


class PersistentJobQueue:
    """Crash-safe execution queue and history shared by the GUI and service."""

    def __init__(self, path: Path | None = None):
        queue_dir = get_app_dir() / "queue"
        self.path = path or queue_dir / "job_executions.json"
        self.legacy_path = queue_dir / "jobs.json" if path is None else None
        self.execution_log_dir = self.path.parent / "execution_logs"
        self._lock = threading.RLock()
        mutex_id = hashlib.sha256(str(self.path.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
        self._mutex_name = f"Local\\MiChecklistQueue_{mutex_id}"

    @contextmanager
    def _process_lock(self):
        """Serialize queue transactions across the GUI and service processes."""
        if os.name != "nt":
            with self._lock:
                yield
            return
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, self._mutex_name)
        if not handle:
            raise OSError("Unable to create the job queue mutex")
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, 30000)
            if result not in (0, 0x80):
                raise TimeoutError("Timed out waiting for the job queue lock")
            with self._lock:
                yield
        finally:
            ctypes.windll.kernel32.ReleaseMutex(handle)
            ctypes.windll.kernel32.CloseHandle(handle)

    def load(self) -> list[dict]:
        with self._process_lock():
            self._migrate_legacy_file()
            if not self.path.exists():
                return []
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Unable to read persistent job queue: {exc}") from exc
            if not isinstance(data, list):
                raise RuntimeError("Persistent job queue must contain a JSON list")
            return data

    def _migrate_legacy_file(self) -> None:
        """Move the former mixed-name queue file to the execution-history file."""
        if self.path.exists() or self.legacy_path is None or not self.legacy_path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self.legacy_path, self.path)
        except FileNotFoundError:
            # Another process may have completed the migration first.
            pass

    def save(self, jobs: list[dict]) -> None:
        with self._process_lock():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.path.parent,
                    prefix="job-executions-", suffix=".tmp", delete=False,
                ) as stream:
                    json.dump(jobs, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                    temporary = Path(stream.name)
                os.replace(temporary, self.path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()

    def enqueue(self, job_type: str, payload: dict | None = None) -> str:
        with self._process_lock():
            jobs = self.load()
            job_id = uuid4().hex
            jobs.append({
            "id": job_id,
            "type": job_type,
            "payload": payload or {},
            "status": "queued",
            "attempts": 0,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "last_error": "",
            })
            self.save(jobs)
            return job_id

    def enqueue_external_copy(
        self, source_path: str, source_job_id: str, latest_only: bool = True,
    ) -> str:
        """Queue a verified local backup for removable-drive replication."""
        with self._process_lock():
            jobs = self.load()
            if latest_only:
                for job in jobs:
                    if (
                        job.get("type") == "external_backup_copy"
                        and job.get("status") in {"queued", "waiting_external", "interrupted", "failed"}
                    ):
                        job["status"] = "superseded"
                        job["last_error"] = "A newer verified local backup replaced this pending copy."
                        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
            job_id = uuid4().hex
            jobs.append({
                "id": job_id,
                "type": "external_backup_copy",
                "payload": {
                    "source_path": source_path,
                    "source_job_id": source_job_id,
                    "latest_only": bool(latest_only),
                },
                "status": "queued",
                "attempts": 0,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "last_error": "",
            })
            self.save(jobs)
            return job_id

    def pending_external_source_paths(self) -> set[Path]:
        protected: set[Path] = set()
        for job in self.load():
            if (
                job.get("type") == "external_backup_copy"
                and job.get("status") in {"queued", "running", "waiting_external", "interrupted"}
            ):
                value = str((job.get("payload", {}) or {}).get("source_path", "")).strip()
                if value:
                    protected.add(Path(value).expanduser().resolve())
        return protected

    def migrate_legacy_external_waits(self) -> int:
        """Let checklists deferred by the former direct-drive design run locally."""
        with self._process_lock():
            jobs = self.load()
            migrated = 0
            for job in jobs:
                if job.get("status") == "waiting_external" and job.get("type") != "external_backup_copy":
                    job["status"] = "queued"
                    job["last_error"] = ""
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    migrated += 1
            if migrated:
                self.save(jobs)
            return migrated

    def claim_next(self) -> dict | None:
        with self._process_lock():
            jobs = self.load()
            for job in jobs:
                if job.get("status") in {"queued", "interrupted"}:
                    job["status"] = "running"
                    job["attempts"] = int(job.get("attempts", 0)) + 1
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self.save(jobs)
                    return dict(job)
            return None

    def finish(self, job_id: str, status: str, error: str = "", result: dict | None = None) -> None:
        with self._process_lock():
            jobs = self.load()
            finished_job = None
            for job in jobs:
                if job.get("id") == job_id:
                    job.update({
                    "status": status,
                    "last_error": error,
                    "result": result or {},
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    finished_job = dict(job)
                    break
            self.save(jobs)
            if finished_job and self._is_scheduled_execution(finished_job):
                self._write_execution_log(finished_job)

    def update_payload(self, job_id: str, values: dict) -> bool:
        with self._process_lock():
            jobs = self.load()
            for job in jobs:
                if job.get("id") == job_id:
                    payload = dict(job.get("payload", {}) or {})
                    payload.update(values)
                    job["payload"] = payload
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self.save(jobs)
                    return True
            return False

    def defer_for_external_drive(self, job_id: str, reason: str) -> bool:
        with self._process_lock():
            jobs = self.load()
            for job in jobs:
                if job.get("id") == job_id:
                    job["status"] = "waiting_external"
                    job["last_error"] = reason
                    job["external_wait_started_at"] = job.get(
                        "external_wait_started_at", datetime.now().isoformat(timespec="seconds")
                    )
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self.save(jobs)
                    return True
            return False

    def resume_external_jobs(self) -> int:
        with self._process_lock():
            jobs = self.load()
            resumed = 0
            for job in jobs:
                if job.get("status") != "waiting_external" or job.get("type") != "external_backup_copy":
                    continue
                job["status"] = "queued"
                job["last_error"] = ""
                payload = dict(job.get("payload", {}) or {})
                payload["external_reconnected"] = True
                job["payload"] = payload
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                resumed += 1
            if resumed:
                self.save(jobs)
            return resumed

    def mark_external_notice(self, job_id: str) -> None:
        with self._process_lock():
            jobs = self.load()
            for job in jobs:
                if job.get("id") == job_id:
                    job["last_external_notice_at"] = datetime.now().isoformat(timespec="seconds")
                    self.save(jobs)
                    return

    @staticmethod
    def _is_scheduled_execution(job: dict) -> bool:
        payload = job.get("payload", {}) or {}
        return bool(payload.get("schedule_id") or payload.get("schedule_kind"))

    def _write_execution_log(self, job: dict) -> Path:
        """Write one dated, atomic JSON log for a completed scheduled execution."""
        completed_at = datetime.fromisoformat(
            str(job.get("updated_at") or datetime.now().isoformat(timespec="seconds"))
        )
        dated_dir = self.execution_log_dir / completed_at.strftime("%Y-%m-%d")
        dated_dir.mkdir(parents=True, exist_ok=True)
        job_id = str(job.get("id", "unknown"))
        attempt = max(1, int(job.get("attempts", 1)))
        log_path = dated_dir / (
            f"{completed_at:%H%M%S}_{job_id}_attempt-{attempt}.json"
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=dated_dir,
                prefix="execution-", suffix=".tmp", delete=False,
            ) as stream:
                json.dump(job, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, log_path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return log_path

    def retry(self, job_id: str) -> bool:
        with self._process_lock():
            jobs = self.load()
            for job in jobs:
                if job.get("id") == job_id and job.get("status") in {"failed", "interrupted"}:
                    job["status"] = "queued"
                    job["last_error"] = ""
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self.save(jobs)
                    return True
            return False

    def recover_running(self) -> int:
        """Recover jobs left running after an unexpected service stop.

        File and cleanup jobs can safely continue through their normal retry and
        resumable-copy paths. Scheduled checklists restart from the beginning;
        their incomplete backup folder is removed separately during service
        startup. Manually queued checklists remain failed for operator review.
        """
        with self._process_lock():
            jobs = self.load()
            recovered = 0
            for job in jobs:
                if job.get("status") == "running":
                    if job.get("type") == "checklist" and self._is_scheduled_execution(job):
                        job["status"] = "queued"
                        job["last_error"] = (
                            "Scheduled checklist was interrupted by a service or server "
                            "restart and will restart from the beginning."
                        )
                        job["recovered_at"] = datetime.now().isoformat(timespec="seconds")
                    elif job.get("type") == "checklist":
                        job["status"] = "failed"
                        job["last_error"] = (
                            "Manually queued checklist was interrupted by a service stop "
                            "and was not automatically restarted. Retry it manually if required."
                        )
                    else:
                        job["status"] = "interrupted"
                        job["last_error"] = "Service stopped before the job completed"
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    if self._is_scheduled_execution(job):
                        self._write_execution_log(dict(job))
                    recovered += 1
            if recovered:
                self.save(jobs)
            return recovered
