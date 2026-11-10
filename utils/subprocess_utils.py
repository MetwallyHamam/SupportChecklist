from __future__ import annotations

import subprocess


def hidden_window_kwargs() -> dict[str, int]:
    """Prevent console windows from flashing for child processes on Windows."""
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
