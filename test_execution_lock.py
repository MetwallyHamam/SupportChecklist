import threading

import pytest

from core.execution_lock import ChecklistExecutionBusy, checklist_execution_lock


def test_second_checklist_cannot_overlap_active_execution():
    entered = threading.Event()
    release = threading.Event()

    def hold_lock():
        with checklist_execution_lock():
            entered.set()
            release.wait(2)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert entered.wait(1)
    try:
        with pytest.raises(ChecklistExecutionBusy):
            with checklist_execution_lock(timeout_seconds=0.01):
                pass
    finally:
        release.set()
        thread.join(timeout=2)
