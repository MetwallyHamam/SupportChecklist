from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.single_instance import SingleInstanceGuard


class SingleInstanceTests(unittest.TestCase):
    def test_second_guard_cannot_acquire_until_first_releases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            first = SingleInstanceGuard(app_dir)
            second = SingleInstanceGuard(app_dir)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())

            first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
