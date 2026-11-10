from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager


class CleanupOnlyWorker(QObject):
    """Run configured cleanup tasks without starting any backups."""

    progress_changed = Signal(int, str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.runner = ChecklistRunner(config_manager)
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            result = self.runner.run_cleanup_only(
                progress_callback=self.progress_changed.emit,
                stop_check=lambda: self._stop_requested,
            )
            if result.get("status") == "Failed":
                self.failed.emit(str(result.get("error", "Cleanup-only execution failed.")))
            else:
                self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
