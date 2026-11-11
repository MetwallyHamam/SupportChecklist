from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from core.execution_lock import checklist_execution_lock
from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager


class ChecklistWorker(QObject):
    progress_changed = Signal(int, str)
    task_updated = Signal(dict)
    finished = Signal(dict)
    failed = Signal(str)
    started = Signal()

    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager
        self.runner = ChecklistRunner(config_manager)
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        self.started.emit()
        try:
            with checklist_execution_lock():
                result = self.runner.run(progress_callback=self._emit_progress, stop_check=self._is_stopped)
            if result.get("status") == "Failed":
                self.failed.emit(str(result.get("error", "Checklist execution failed.")))
            else:
                self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _is_stopped(self) -> bool:
        return self._stop_requested

    def _emit_progress(self, value: int, task: str) -> None:
        self.progress_changed.emit(value, task)
