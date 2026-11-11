from __future__ import annotations

from contextlib import contextmanager

from utils.process_lock import ProcessLockTimeout, process_lock
from utils.paths import get_app_dir


class ChecklistExecutionBusy(RuntimeError):
    pass


@contextmanager
def checklist_execution_lock(timeout_seconds: float = 1.0):
    try:
        with process_lock(get_app_dir() / "service" / "checklist-execution", timeout_seconds):
            yield
    except ProcessLockTimeout as exc:
        raise ChecklistExecutionBusy(
            "Another MiChecklist operation is already running. Wait for it to finish and retry."
        ) from exc
