from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from core.script_runner import build_script_command, run_script


def test_builds_explicit_commands_without_shell_execution(tmp_path):
    ps1 = tmp_path / "cleanup.ps1"
    cmd = tmp_path / "cleanup.cmd"

    assert build_script_command(ps1)[-2:] == ["-File", str(ps1)]
    assert build_script_command(cmd)[:5] == ["cmd.exe", "/d", "/s", "/c", "call"]


def test_successful_script_captures_result(tmp_path):
    script = tmp_path / "cleanup.ps1"
    script.write_text("Write-Output 'done'", encoding="utf-8")
    completed = CompletedProcess([], 0, stdout="done\n", stderr="")

    with patch("core.script_runner.subprocess.run", return_value=completed) as run:
        result = run_script(str(script), timeout_minutes=5)

    assert result["status"] == "Completed"
    assert result["exit_code"] == 0
    assert result["stdout"] == "done\n"
    assert run.call_args.kwargs["timeout"] == 300
    assert "shell" not in run.call_args.kwargs


def test_nonzero_script_exit_is_a_failed_job(tmp_path):
    script = tmp_path / "cleanup.cmd"
    script.write_text("exit /b 7", encoding="utf-8")
    completed = CompletedProcess([], 7, stdout="", stderr="cleanup failed")

    with patch("core.script_runner.subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError, match="code 7"):
            run_script(str(script))
