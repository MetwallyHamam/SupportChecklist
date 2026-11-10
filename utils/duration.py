from __future__ import annotations

import re
from typing import Any


def format_duration(total_seconds: float) -> str:
    """Format elapsed seconds as hours, minutes, and seconds."""
    total_seconds = max(0.0, float(total_seconds))
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    return f"{hours}h {minutes}m {seconds:.1f}s"


def format_duration_value(value: Any) -> str:
    """Normalize legacy second-only duration values for display."""
    text = str(value).strip()
    if not text:
        return ""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:seconds?|s)?", text, re.IGNORECASE)
    if not match:
        return text
    return format_duration(float(match.group(1)))
