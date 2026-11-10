from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


INCOMPLETE_MARKER = ".supportassistant_incomplete.json"
LEGACY_INTERRUPTION_TEXT = (
    "Checklist was interrupted by a service stop and was not automatically restarted"
)


def mark_backup_started(backup_path: Path) -> Path:
    """Persist a crash marker inside a newly-created automated backup folder."""
    backup_path.mkdir(parents=True, exist_ok=True)
    marker = backup_path / INCOMPLETE_MARKER
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=backup_path,
            prefix="backup-state-", suffix=".tmp", delete=False,
        ) as stream:
            json.dump({
                "state": "running",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "backup_path": str(backup_path),
            }, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, marker)
        temporary = None
        return marker
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def mark_backup_completed(backup_path: Path) -> None:
    """Remove the crash marker only after the backup has passed final checks."""
    marker = backup_path / INCOMPLETE_MARKER
    if marker.exists():
        marker.unlink()


def remove_incomplete_backups(backup_root: Path) -> list[Path]:
    """Delete only automated backup folders carrying an incomplete marker."""
    if not backup_root.is_dir():
        return []
    resolved_root = backup_root.resolve()
    removed: list[Path] = []
    for folder in backup_root.glob("Automatedbackup_*"):
        if not folder.is_dir() or not (folder / INCOMPLETE_MARKER).is_file():
            continue
        resolved_folder = folder.resolve()
        if resolved_folder.parent != resolved_root:
            continue
        shutil.rmtree(resolved_folder)
        removed.append(resolved_folder)
    return removed


def is_legacy_interrupted_scheduled_checklist(job: dict) -> bool:
    """Identify checklist failures written by releases before restart recovery."""
    payload = job.get("payload", {}) or {}
    scheduled = bool(payload.get("schedule_id") or payload.get("schedule_kind"))
    return bool(
        scheduled
        and job.get("type") == "checklist"
        and job.get("status") == "failed"
        and LEGACY_INTERRUPTION_TEXT in str(job.get("last_error", ""))
    )


def remove_legacy_interrupted_backup(backup_root: Path, job: dict) -> Path | None:
    """Remove the exact pre-marker backup folder associated with a legacy job."""
    if not is_legacy_interrupted_scheduled_checklist(job):
        return None
    try:
        started = datetime.fromisoformat(str(job.get("created_at", "")))
    except ValueError:
        return None
    candidate = backup_root / f"Automatedbackup_{started:%Y%m%d_%H%M%S}"
    if not candidate.is_dir():
        return None
    resolved_root = backup_root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate.parent != resolved_root:
        return None
    shutil.rmtree(resolved_candidate)
    return resolved_candidate
