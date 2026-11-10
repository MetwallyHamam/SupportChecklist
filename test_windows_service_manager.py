from types import SimpleNamespace

from core.windows_service_manager import WindowsServiceManager


def test_service_status_parses_running(monkeypatch):
    monkeypatch.setattr("core.windows_service_manager.subprocess.run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="SERVICE_NAME: MiChecklistService\n        STATE              : 4  RUNNING\n", stderr="",
    ))
    status = WindowsServiceManager().status()
    assert status["installed"] == "true"
    assert "RUNNING" in status["state"]


def test_service_status_handles_not_installed(monkeypatch):
    monkeypatch.setattr("core.windows_service_manager.subprocess.run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1060, stdout="", stderr="The specified service does not exist",
    ))
    status = WindowsServiceManager().status()
    assert status["installed"] == "false"


def test_install_removes_legacy_service_first(monkeypatch):
    manager = WindowsServiceManager()
    calls = []
    monkeypatch.setattr(manager, "remove_legacy_service", lambda: calls.append("legacy") or "removed")
    monkeypatch.setattr(manager, "_run", lambda args: calls.append(args) or "installed")

    output = manager.install()

    assert calls == ["legacy", ["--startup", "auto", "install"]]
    assert output == "removed\ninstalled"


