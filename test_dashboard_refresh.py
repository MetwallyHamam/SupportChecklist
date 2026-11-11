from gui.main_window import latest_completed_checklist


def test_latest_checklist_includes_running_state_and_ignores_other_jobs():
    jobs = [
        {"id": "old", "type": "checklist", "status": "completed", "updated_at": "2026-01-01T10:00:00", "result": {"status": "Completed"}},
        {"id": "module", "type": "module_schedule", "status": "completed", "updated_at": "2026-01-01T13:00:00", "result": {"status": "Completed"}},
        {"id": "running", "type": "checklist", "status": "running", "updated_at": "2026-01-01T14:00:00", "result": {}},
        {"id": "new", "type": "checklist", "status": "completed", "updated_at": "2026-01-01T12:00:00", "result": {"status": "Completed with Warnings"}},
    ]

    assert latest_completed_checklist(jobs)["id"] == "running"


def test_latest_checklist_returns_failed_job_without_result():
    assert latest_completed_checklist([
        {"id": "failed", "type": "checklist", "status": "failed", "updated_at": "2026-01-01"},
    ])["id"] == "failed"
