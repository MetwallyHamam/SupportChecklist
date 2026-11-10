from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path


def apply_retention(
    root: Path, max_age_days: int = 90, dry_run: bool = False, min_keep: int = 0,
    protected_paths: set[Path] | None = None,
) -> list[str]:
    """Delete aged backups while always preserving the newest generations."""
    if not root.is_dir():
        return []
    folders = sorted(
        (item for item in root.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    cutoff = datetime.now() - timedelta(days=max(1, max_age_days))
    removed: list[str] = []
    protected = set(folders[:max(0, int(min_keep))])
    protected.update(path.resolve() for path in (protected_paths or set()))
    for folder in folders:
        if folder in protected or (folder / ".supportassistant_incomplete.json").exists():
            continue
        modified = datetime.fromtimestamp(folder.stat().st_mtime)
        if modified >= cutoff:
            continue
        removed.append(str(folder))
        if not dry_run:
            shutil.rmtree(folder)
    return removed
