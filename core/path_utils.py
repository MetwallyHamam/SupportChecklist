import os
import sys
from pathlib import Path

MAX_WIN_PATH = 260


# =========================
# INTERNAL (LOGIC LAYER)
# =========================
def normalize_path(path: str) -> str:
    """
    Convert path to long-path-safe format (Windows).
    Used ONLY internally (copy, scan, ftp, etc).
    """
    if not path:
        return ""

    path = os.path.abspath(path)

    if sys.platform == "win32":
        if path.startswith("\\\\?\\"):
            return path

        # UNC path
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]

        return "\\\\?\\" + path

    return path


# =========================
# UI / DISPLAY LAYER
# =========================
def denormalize_path(path: str) -> str:
    """
    Convert internal long path to user-friendly path.
    Used ONLY for UI display.
    """
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def display_path(path: str, max_len: int = 120) -> str:
    """
    Shorten path for UI display.
    """
    if len(path) <= max_len:
        return path
    return "..." + path[-max_len:]


# =========================
# UTILITIES
# =========================
def path_length(path: str) -> int:
    return len(path)


def is_long_path(path: str) -> bool:
    return sys.platform == "win32" and len(path) >= MAX_WIN_PATH
