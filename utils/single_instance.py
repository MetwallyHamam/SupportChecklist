from __future__ import annotations

import ctypes
import msvcrt
from pathlib import Path


LOCK_FILE_NAME = "support_checklist.lock"
RUNNING_MESSAGE = "MiChecklist is already running."


class SingleInstanceGuard:
    """Keep only one Support Checklist process active at a time."""

    def __init__(self, app_dir: Path) -> None:
        self.lock_path = app_dir / LOCK_FILE_NAME
        self._handle = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.lock_path.open("a+b")
        try:
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self._handle.close()
            self._handle = None
            return False
        return True

    def release(self) -> None:
        if not self._handle:
            return
        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None


def show_already_running_message() -> None:
        ctypes.windll.user32.MessageBoxW(None, RUNNING_MESSAGE, "MiChecklist", 0x40)
