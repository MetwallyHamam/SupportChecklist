from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from modules.cleanup_manager import detect_iis_cleanup_tasks, detect_service_cleanup_tasks
from modules.iis_manager import IISManager
from modules.service_backup import ServiceBackup


class CleanupDetectionWorker(QObject):
    """Detect IIS cleanup folders without blocking the UI thread."""

    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, source: str, selected_items: set[str], folder_names: Any) -> None:
        super().__init__()
        self.source = source
        self.selected_items = selected_items
        self.folder_names = folder_names

    def run(self) -> None:
        try:
            if self.source == "iis":
                applications = IISManager().list_applications()
                tasks = detect_iis_cleanup_tasks(applications, self.selected_items, self.folder_names)
            elif self.source == "services":
                services = ServiceBackup().list_services()
                tasks = detect_service_cleanup_tasks(services, self.selected_items, self.folder_names)
            else:
                raise ValueError(f"Unsupported cleanup detection source: {self.source}")
            self.finished.emit(tasks)
        except Exception as exc:
            self.failed.emit(str(exc))
