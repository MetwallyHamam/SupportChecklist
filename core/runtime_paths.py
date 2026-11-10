import sys
from pathlib import Path


def application_directory() -> Path:
    """Directory used for persistent files in source and packaged builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def logs_directory() -> Path:
    path = application_directory() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
