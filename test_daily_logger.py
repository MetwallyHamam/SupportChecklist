from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import utils.logger as logger


class DailyLoggerTests(unittest.TestCase):
    def test_handler_switches_files_when_record_date_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = logger.DailyFileHandler(Path(temp_dir))
            handler.setFormatter(logging.Formatter("%(message)s"))
            first = logging.LogRecord("Test", logging.INFO, "", 0, "first", (), None)
            first.created = datetime(2026, 6, 1, 23, 59, 59).timestamp()
            second = logging.LogRecord("Test", logging.INFO, "", 0, "second", (), None)
            second.created = datetime(2026, 6, 2, 0, 0, 1).timestamp()

            handler.emit(first)
            handler.emit(second)
            handler.close()

            root = Path(temp_dir)
            self.assertEqual((root / "support_checklist_2026-06-01.log").read_text(encoding="utf-8"), "first\n")
            self.assertEqual((root / "support_checklist_2026-06-02.log").read_text(encoding="utf-8"), "second\n")

    def test_handler_supports_a_separate_service_log_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = logger.DailyFileHandler(
                Path(temp_dir), filename_prefix="support_assistant_service"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            record = logging.LogRecord("Service", logging.INFO, "", 0, "started", (), None)
            record.created = datetime(2026, 9, 28, 14, 0, 0).timestamp()
            handler.emit(record)
            handler.close()

            log_file = Path(temp_dir) / "support_assistant_service_2026-09-28.log"
            self.assertEqual(log_file.read_text(encoding="utf-8"), "started\n")

    def test_recent_logs_reads_requested_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_log_dir = logger.LOG_DIR
            logger.LOG_DIR = Path(temp_dir)
            try:
                log_file = logger.get_daily_log_file(date(2026, 6, 2))
                log_file.write_text(
                    "2026-06-02 10:00:00 | INFO | Test | today\n",
                    encoding="utf-8",
                )

                entries = logger.get_recent_logs(log_date=date(2026, 6, 2))

                self.assertEqual(entries[0]["message"], "today")
            finally:
                logger.LOG_DIR = original_log_dir

    def test_export_logs_uses_requested_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_log_dir = logger.LOG_DIR
            logger.LOG_DIR = Path(temp_dir)
            try:
                logger.get_daily_log_file(date(2026, 6, 1)).write_text(
                    "2026-06-01 10:00:00 | INFO | Test | first day\n",
                    encoding="utf-8",
                )
                logger.get_daily_log_file(date(2026, 6, 2)).write_text(
                    "2026-06-02 10:00:00 | INFO | Test | second day\n",
                    encoding="utf-8",
                )
                export_file = Path(temp_dir) / "export.csv"

                logger.export_logs_to_csv(export_file, log_date=date(2026, 6, 1))

                exported = export_file.read_text(encoding="utf-8")
                self.assertIn("first day", exported)
                self.assertNotIn("second day", exported)
            finally:
                logger.LOG_DIR = original_log_dir


if __name__ == "__main__":
    unittest.main()
