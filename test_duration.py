from __future__ import annotations

import unittest

from utils.duration import format_duration, format_duration_value


class DurationTests(unittest.TestCase):
    def test_formats_hours_minutes_and_seconds(self) -> None:
        self.assertEqual(format_duration(3723.4), "1h 2m 3.4s")

    def test_normalizes_legacy_seconds_value(self) -> None:
        self.assertEqual(format_duration_value("65.2 seconds"), "0h 1m 5.2s")

    def test_preserves_existing_formatted_value(self) -> None:
        self.assertEqual(format_duration_value("1h 2m 3.4s"), "1h 2m 3.4s")


if __name__ == "__main__":
    unittest.main()
