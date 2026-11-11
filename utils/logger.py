import csv
import logging
import re
from collections import deque
from datetime import date, datetime
from pathlib import Path

from .paths import get_app_dir

LOG_DIR = get_app_dir() / "logs"
LOG_LEVEL = logging.INFO
DEFAULT_RECENT_LOG_LIMIT = 1000
IN_MEMORY_LOG_HANDLER: "InMemoryLogHandler" | None = None
LOG_LINE_PATTERN = re.compile(r"^(?P<timestamp>[^|]+) \| (?P<status>[^|]+) \| (?P<module>[^|]+) \| (?P<message>.*)$")


def get_daily_log_file(log_date: date | None = None) -> Path:
    """Return the dated log file path for a local calendar day."""
    return LOG_DIR / f"support_checklist_{log_date or date.today():%Y-%m-%d}.log"


class DailyFileHandler(logging.Handler):
    """Write records to one file per local day, switching files at midnight."""

    def __init__(self, log_dir: Path, filename_prefix: str = "support_checklist") -> None:
        super().__init__()
        self.log_dir = log_dir
        self.filename_prefix = filename_prefix
        self._stream = None
        self._stream_date: date | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            record_date = datetime.fromtimestamp(record.created).date()
            if self._stream is None or record_date != self._stream_date:
                self._open_stream(record_date)
            self._stream.write(f"{self.format(record)}\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()

    def _open_stream(self, record_date: date) -> None:
        if self._stream is not None:
            self._stream.close()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._stream = (
            self.log_dir / f"{self.filename_prefix}_{record_date:%Y-%m-%d}.log"
        ).open("a", encoding="utf-8")
        self._stream_date = record_date


class InMemoryLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(
            {
                "timestamp": self.format(record).split(" | ", 1)[0],
                "module": record.name,
                "status": record.levelname,
                "message": record.getMessage(),
            }
        )

    def get_entries(self) -> list[dict[str, str]]:
        return list(self.records)

    def clear(self) -> None:
        self.records.clear()


def setup_logging(filename_prefix: str = "support_checklist", include_memory: bool = True) -> None:
    """Configure structured logging for the application."""
    global IN_MEMORY_LOG_HANDLER
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = DailyFileHandler(LOG_DIR, filename_prefix=filename_prefix)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)
    root_logger.handlers = []
    root_logger.addHandler(file_handler)
    IN_MEMORY_LOG_HANDLER = None
    if include_memory:
        memory_handler = InMemoryLogHandler()
        memory_handler.setFormatter(formatter)
        IN_MEMORY_LOG_HANDLER = memory_handler
        root_logger.addHandler(memory_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def get_recent_logs(limit: int | None = DEFAULT_RECENT_LOG_LIMIT, log_date: date | None = None) -> list[dict[str, str]]:
    """Return persisted logs for one day so entries remain visible after restarts."""
    log_file = get_daily_log_file(log_date)
    if not log_file.exists():
        return []

    entries: deque[dict[str, str]] = deque(maxlen=limit)
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = LOG_LINE_PATTERN.match(line.rstrip())
                if not match:
                    continue
                entries.append({
                    key: value.strip()
                    for key, value in match.groupdict().items()
                })
    except OSError:
        return []
    return list(entries)


def export_logs_to_csv(path: Path, log_date: date | None = None) -> None:
    entries = get_recent_logs(limit=None, log_date=log_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "module", "status", "message"])
        writer.writeheader()
        writer.writerows(entries)
