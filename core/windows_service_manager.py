from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from utils.paths import get_app_dir
from utils.subprocess_utils import hidden_window_kwargs


SERVICE_NAME = "MiChecklistService"
LEGACY_SERVICE_NAME = "SupportAssistantService"


class WindowsServiceManager:
    def _command_prefix(self) -> list[str]:
        if getattr(sys, "frozen", False):
            executable = get_app_dir() / "MiChecklistService.exe"
            if not executable.exists():
                raise FileNotFoundError(f"Service executable not found: {executable}")
            return [str(executable)]
        service_script = Path(__file__).resolve().parent.parent / "windows_service.py"
        return [sys.executable, str(service_script)]

    def _run(self, arguments: list[str]) -> str:
        result = subprocess.run(
            self._command_prefix() + arguments,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, **hidden_window_kwargs(),
        )
        output = "\n".join(value.strip() for value in (result.stdout, result.stderr) if value and value.strip())
        if result.returncode:
            raise RuntimeError(output or f"Service command failed with exit code {result.returncode}")
        return output

    def install(self) -> str:
        migration = self.remove_legacy_service()
        installed = self._run(["--startup", "auto", "install"])
        return "\n".join(part for part in (migration, installed) if part)

    def start(self) -> str:
        return self._sc_control("start", "RUNNING")

    def stop(self) -> str:
        return self._sc_control("stop", "STOPPED")

    def _sc_control(
        self, action: str, expected_state: str, timeout: int = 20,
        service_name: str = SERVICE_NAME,
    ) -> str:
        result = subprocess.run(
            ["sc.exe", action, service_name], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, **hidden_window_kwargs(),
        )
        output = "\n".join(value.strip() for value in (result.stdout, result.stderr) if value and value.strip())
        harmless = action == "stop" and ("not been started" in output.lower() or result.returncode == 1062)
        if result.returncode and not harmless:
            raise RuntimeError(output or f"Unable to {action} service ({result.returncode})")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._status(service_name)
            state = status.get("state", "")
            if expected_state in state or (expected_state == "STOPPED" and status.get("installed") == "false"):
                return output
            time.sleep(0.25)
        raise TimeoutError(f"Service did not reach {expected_state} within {timeout} seconds")

    def remove_legacy_service(self) -> str:
        """Remove the former SupportAssistant service before MiChecklist installation."""
        if self._status(LEGACY_SERVICE_NAME).get("installed") != "true":
            return ""
        try:
            self._sc_control("stop", "STOPPED", service_name=LEGACY_SERVICE_NAME)
        except RuntimeError:
            pass
        result = subprocess.run(
            ["sc.exe", "delete", LEGACY_SERVICE_NAME], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, **hidden_window_kwargs(),
        )
        output = "\n".join(
            value.strip() for value in (result.stdout, result.stderr) if value and value.strip()
        )
        if result.returncode and result.returncode != 1060:
            raise RuntimeError(output or "Unable to remove the legacy SupportAssistant service")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._status(LEGACY_SERVICE_NAME).get("installed") == "false":
                return "Legacy SupportAssistantService removed." + (f"\n{output}" if output else "")
            time.sleep(0.25)
        raise RuntimeError(
            "SupportAssistantService is marked for deletion. Close Service Manager and retry."
        )

    def remove(self) -> str:
        try:
            self.stop()
        except RuntimeError:
            pass
        output = self._run(["remove"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.status().get("installed") == "false":
                return output
            time.sleep(0.25)
        raise RuntimeError(
            "Windows marked the service for deletion. Close Service Manager and other tools holding a service handle, then refresh."
        )

    def status(self) -> dict[str, str]:
        return self._status(SERVICE_NAME)

    def _status(self, service_name: str) -> dict[str, str]:
        result = subprocess.run(
            ["sc.exe", "query", service_name], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, **hidden_window_kwargs(),
        )
        output = "\n".join(value.strip() for value in (result.stdout, result.stderr) if value and value.strip())
        if result.returncode == 1060 or "does not exist" in output.lower():
            return {"installed": "false", "state": "Not installed", "detail": output}
        if result.returncode:
            raise RuntimeError(output or f"Unable to query service ({result.returncode})")
        state = "Unknown"
        for line in output.splitlines():
            if "STATE" in line and ":" in line:
                state = line.split(":", 1)[1].strip()
                break
        return {"installed": "true", "state": state, "detail": output}
