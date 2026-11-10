from __future__ import annotations

import sys
from pathlib import Path


def get_app_dir() -> Path:
    """Return the writable application directory beside the packaged EXE."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_resource_path(file_name: str) -> Path:
    """Return a bundled resource path for source and packaged executions."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / file_name
    return Path(__file__).resolve().parent.parent / file_name


def ensure_app_directories() -> None:
    """Create writable runtime folders on first launch."""
    for folder_name in ("config", "logs", "reports", "queue", "service"):
        (get_app_dir() / folder_name).mkdir(parents=True, exist_ok=True)
