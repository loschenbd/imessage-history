"""Unit tests for the redaction component.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the script importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import imessage_export as ie


class ExcelLettersTests(unittest.TestCase):
    def test_zero_is_a(self):
        self.assertEqual(ie._excel_letters(0), "A")

    def test_twenty_five_is_z(self):
        self.assertEqual(ie._excel_letters(25), "Z")

    def test_twenty_six_is_aa(self):
        self.assertEqual(ie._excel_letters(26), "AA")

    def test_twenty_seven_is_ab(self):
        self.assertEqual(ie._excel_letters(27), "AB")

    def test_701_is_zz(self):
        self.assertEqual(ie._excel_letters(701), "ZZ")


class RedactionConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = ie.RedactionConfig(me_name="Ben")
        self.assertEqual(cfg.me_name, "Ben")
        self.assertEqual(cfg.extra_names, [])
        self.assertTrue(cfg.redact_phones)
        self.assertTrue(cfg.redact_emails)
        self.assertTrue(cfg.redact_urls)
        self.assertFalse(cfg.case_sensitive)

    def test_disable_phones(self):
        cfg = ie.RedactionConfig(me_name="Ben", redact_phones=False)
        self.assertFalse(cfg.redact_phones)


if __name__ == "__main__":
    unittest.main()
