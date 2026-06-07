"""Permissive 12-hour time parser tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imessage_export.window import parse_time_12h


class Parse12hTimeTests(unittest.TestCase):
    def test_24h_passthrough(self):
        self.assertEqual(parse_time_12h("14:30"), "14:30")
        self.assertEqual(parse_time_12h("00:00"), "00:00")
        self.assertEqual(parse_time_12h("23:59"), "23:59")

    def test_am_pm(self):
        self.assertEqual(parse_time_12h("9am"), "09:00")
        self.assertEqual(parse_time_12h("9 am"), "09:00")
        self.assertEqual(parse_time_12h("9:30am"), "09:30")
        self.assertEqual(parse_time_12h("12pm"), "12:00")  # noon
        self.assertEqual(parse_time_12h("12am"), "00:00")  # midnight
        self.assertEqual(parse_time_12h("11:30pm"), "23:30")
        self.assertEqual(parse_time_12h("1pm"), "13:00")

    def test_keywords(self):
        self.assertEqual(parse_time_12h("noon"), "12:00")
        self.assertEqual(parse_time_12h("midnight"), "00:00")
        self.assertEqual(parse_time_12h("NOON"), "12:00")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_time_12h("13:00pm")  # 13 + pm
        with self.assertRaises(ValueError):
            parse_time_12h("25:00")    # hour out of range
        with self.assertRaises(ValueError):
            parse_time_12h("garbage")


if __name__ == "__main__":
    unittest.main()
