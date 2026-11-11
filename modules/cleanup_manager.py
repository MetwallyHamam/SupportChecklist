from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Iterable


DEFAULT_LOG_EXTENSIONS = [".json", ".log", ".txt"]
LOG_FOLDER_NAMES = {"exception", "exceptions", "logs"}


def normalize_extensions(value: Any) -> list[str]:
    """Return a validated, de-duplicated list of file extensions."""
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []

    extensions: list[str] = []
    for raw_value in values:
        extension = str(raw_value).strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension}"
        if extension == "." or any(character in extension for character in ("*", "?", "/", "\\")):
            raise ValueError(f"Invalid cleanup file extension: {raw_value}")
        if extension not in extensions:
            extensions.append(extension)
    return extensions


def format_extensions(value: Any) -> str:
    return ", ".join(normalize_extensions(value))


def normalize_folder_names(value: Any) -> set[str]:
    """Return lower-case cleanup folder names from comma-separated input."""
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    return {str(folder_name).strip().lower() for folder_name in values if str(folder_name).strip()}


def detect_iis_cleanup_tasks(
    applications: Iterable[dict[str, str]],
    selected_apps: set[str],
    folder_names: Any = LOG_FOLDER_NAMES,
) -> list[dict[str, Any]]:
    """Find requested cleanup directories below selected IIS application paths."""
    normalized_folder_names = normalize_folder_names(folder_names)
    tasks: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for app in applications:
        app_key = f"{app.get('site', '')}|{app.get('path', '')}"
        if app_key not in selected_apps:
            continue
        app_root = Path(str(app.get("physical_path", ""))).expanduser()
        if not app_root.is_dir():
            continue
        candidates = [app_root]
        try:
            candidates.extend(path for path in app_root.rglob("*") if path.is_dir())
        except OSError:
            continue
        for folder in candidates:
            if folder.name.lower() not in normalized_folder_names:
                continue
            path_key = str(folder.resolve()).lower()
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            tasks.append({
                "name": f"{app.get('site', '')} > {app.get('path', '')} > {folder.name}",
                "path": str(folder),
                "extensions": list(DEFAULT_LOG_EXTENSIONS),
                "enabled": True,
                "source": "IIS Auto Detect",
                "owner_key": app_key,
            })
    return tasks


def detect_service_cleanup_tasks(
    services: Iterable[Any],
    selected_services: set[str],
    folder_names: Any = LOG_FOLDER_NAMES,
) -> list[dict[str, Any]]:
    """Find requested cleanup directories below selected service folders."""
    normalized_folder_names = normalize_folder_names(folder_names)
    tasks: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for service in services:
        if service.name not in selected_services or not service.executable_path:
            continue
        service_root = Path(service.executable_path).expanduser().parent
        if not service_root.is_dir():
            continue
        candidates = [service_root]
        try:
            candidates.extend(path for path in service_root.rglob("*") if path.is_dir())
        except OSError:
            continue
        for folder in candidates:
            if folder.name.lower() not in normalized_folder_names:
                continue
            path_key = str(folder.resolve()).lower()
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            tasks.append({
                "name": f"{service.name} > {folder.name}",
                "path": str(folder),
                "extensions": list(DEFAULT_LOG_EXTENSIONS),
                "enabled": True,
                "source": "Service Auto Detect",
            })
    return tasks


class CleanupManager:
    """Delete matched files, or all files when a cleanup task has no extensions."""

    def cleanup_tasks(self, tasks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for task in tasks:
            if not task.get("enabled", True):
                continue
            folder = Path(str(task.get("path", ""))).expanduser()
            extensions = set(normalize_extensions(task.get("extensions", [])))
            minimum_age_days = max(0, int(task.get("minimum_age_days", 0)))
            modified_before = datetime.now() - timedelta(days=minimum_age_days) if minimum_age_days else None
            if not folder.is_dir():
                results.append({
                    "name": str(task.get("name", folder.name)),
                    "path": str(folder),
                    "status": "Skipped",
                    "detail": "Folder does not exist",
                    "deleted_files": 0,
                })
                continue
            deleted_files = 0
            failed_files = 0
            in_use_files = 0
            failed_items: list[dict[str, str]] = []
            for file_path in folder.rglob("*"):
                if not file_path.is_file():
                    continue
                if extensions and file_path.suffix.lower() not in extensions:
                    continue
                if modified_before:
                    try:
                        if datetime.fromtimestamp(file_path.stat().st_mtime) >= modified_before:
                            continue
                    except OSError:
                        pass
                try:
                    file_path.unlink()
                    deleted_files += 1
                except OSError as exc:
                    if getattr(exc, "winerror", None) == 32:
                        in_use_files += 1
                        continue
                    failed_files += 1
                    if len(failed_items) < 20:
                        failed_items.append({"file": str(file_path), "error": str(exc)})
            detail_parts = [f"{deleted_files} files deleted"]
            if in_use_files:
                detail_parts.append(f"{in_use_files} active files skipped")
            if failed_files:
                detail_parts.append(f"{failed_files} files failed")
            results.append({
                "name": str(task.get("name", folder.name)),
                "path": str(folder),
                "status": "Completed" if not failed_files else "Partial",
                "detail": ", ".join(detail_parts),
                "deleted_files": deleted_files,
                "in_use_files": in_use_files,
                "failed_files": failed_files,
                "failed_items": failed_items,
            })
        return results
