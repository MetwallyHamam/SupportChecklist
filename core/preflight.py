from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from core.storage_paths import classify_storage_path, storage_warnings


@dataclass
class PreflightIssue:
    severity: str
    code: str
    message: str


def validate_paths(source: str | None, destination: str | None, required_bytes: int = 0) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    for value in (source, destination):
        if value:
            for warning in storage_warnings(value, service_mode=True):
                severity = "error" if classify_storage_path(value) == "cloud_url" else "warning"
                issues.append(PreflightIssue(severity, "storage_path", warning))
    source_path = Path(source).expanduser().resolve() if source else None
    destination_path = Path(destination).expanduser().resolve() if destination else None
    if source_path and not source_path.exists():
        issues.append(PreflightIssue("error", "source_missing", f"Source does not exist: {source_path}"))
    if destination_path:
        destination_path.mkdir(parents=True, exist_ok=True)
        if source_path:
            try:
                destination_path.relative_to(source_path)
                issues.append(PreflightIssue("error", "destination_inside_source", "Destination cannot be inside the source."))
            except ValueError:
                pass
            if source_path == destination_path:
                issues.append(PreflightIssue("error", "same_path", "Source and destination must be different."))
        try:
            free = shutil.disk_usage(destination_path).free
            if required_bytes and free < required_bytes:
                issues.append(PreflightIssue("error", "insufficient_space", f"Destination needs {required_bytes - free:,} more bytes."))
            elif required_bytes and free < required_bytes * 1.1:
                issues.append(PreflightIssue("warning", "low_space_margin", "Destination has less than 10% free-space margin."))
        except OSError as exc:
            issues.append(PreflightIssue("error", "destination_unavailable", str(exc)))
        if not os.access(destination_path, os.W_OK):
            issues.append(PreflightIssue("error", "destination_not_writable", f"Destination is not writable: {destination_path}"))
    return issues


def serialize_issues(issues: list[PreflightIssue]) -> list[dict]:
    return [asdict(issue) for issue in issues]
