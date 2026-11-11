from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from utils.process_lock import process_lock


class HistoryManager:
    def __init__(self, root_path: Path) -> None:
        self.history_path = root_path / "history.json"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> list[dict[str, Any]]:
        with process_lock(self.history_path):
            if not self.history_path.exists():
                return []
            try:
                with self.history_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, list) else []
            except (OSError, ValueError):
                return []

    def save_history(self, history: list[dict[str, Any]]) -> None:
        with process_lock(self.history_path):
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.history_path.parent,
                    prefix="history-", suffix=".tmp", delete=False,
                ) as handle:
                    json.dump(history, handle, indent=4)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                os.replace(temporary, self.history_path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()

    def add_record(self, record: dict[str, Any]) -> None:
        with process_lock(self.history_path):
            history = self.load_history()
            history.insert(0, record)
            self.save_history(history)
