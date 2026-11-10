from __future__ import annotations

import subprocess
import time
from pathlib import Path

from utils.subprocess_utils import hidden_window_kwargs


SUPPORTED_SCRIPT_EXTENSIONS = {".ps1", ".cmd", ".bat"}
MAX_CAPTURED_OUTPUT = 16_000


def build_script_command(script_path: Path) -> list[str]:
    suffix = script_path.suffix.casefold()
    if suffix == ".ps1":
        return [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(script_path),
        ]
    if suffix in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", "call", str(script_path)]
    raise ValueError("Only PowerShell (.ps1), command (.cmd), and batch (.bat) files are supported.")


def run_script(script: str, timeout_minutes: int = 60) -> dict:
    path = Path(str(script)).expanduser()
    if not path.is_absolute():
        raise ValueError("Scheduled script path must be absolute.")
    if not path.is_file():
        raise FileNotFoundError(f"Scheduled script is unavailable: {path}")
    if path.suffix.casefold() not in SUPPORTED_SCRIPT_EXTENSIONS:
        raise ValueError(f"Unsupported scheduled script type: {path.suffix or '(none)'}")

    started = time.perf_counter()
    timeout_seconds = max(1, int(timeout_minutes)) * 60
    try:
        result = subprocess.run(
            build_script_command(path),
            cwd=path.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            **hidden_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Script exceeded its {timeout_minutes}-minute timeout: {path}"
        ) from exc

    stdout = (result.stdout or "")[-MAX_CAPTURED_OUTPUT:]
    stderr = (result.stderr or "")[-MAX_CAPTURED_OUTPUT:]
    duration = round(time.perf_counter() - started, 2)
    if result.returncode != 0:
        details = stderr.strip() or stdout.strip() or "No output was captured."
        raise RuntimeError(
            f"Script exited with code {result.returncode}: {path}\n{details}"
        )
    return {
        "status": "Completed",
        "script": str(path),
        "exit_code": result.returncode,
        "duration": f"{duration:.2f}s",
        "stdout": stdout,
        "stderr": stderr,
    }
