from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class HistoryManager:
    def __init__(self, root_path: Path) -> None:
        self.history_path = root_path / "history.json"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        try:
            with self.history_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return []

    def save_history(self, history: list[dict[str, Any]]) -> None:
        with self.history_path.open("w", encoding="utf-8") as handle:
            json.dump(history, handle, indent=4)

    def add_record(self, record: dict[str, Any]) -> None:
        history = self.load_history()
        history.insert(0, record)
        self.save_history(history)
