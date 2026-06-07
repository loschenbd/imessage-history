"""Unit tests for the contacts_macos module (CSV writer + normalizer)."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imessage_export.contacts_macos import _normalize_handle, _normalize_phone, write_csv


class NormalizeTests(unittest.TestCase):
    def test_phone_strip_punctuation(self):
        self.assertEqual(_normalize_phone("+1 (555) 123-4567"), "+15551234567")
        # 10-digit US numbers gain a "+1" prefix so they match how iMessage
        # stores handles (E.164 with country code).
        self.assertEqual(_normalize_phone("(555) 123 4567"), "+15551234567")
        self.assertEqual(_normalize_phone("+44.20.7946.0958"), "+442079460958")

    def test_email_lowercased(self):
        self.assertEqual(_normalize_handle("Alice@Example.COM"), "alice@example.com")

    def test_phone_via_handle(self):
        self.assertEqual(_normalize_handle("+1 (555) 123-4567"), "+15551234567")


class WriteCsvTests(unittest.TestCase):
    def test_dedup_and_normalize(self):
        rows = [
            ("+1 (555) 123-4567", "Alice"),
            ("+15551234567",      "Alice"),  # dup after normalize
            ("Alice@Example.com", "Alice"),
            ("",                  "Empty"),  # filtered (blank handle)
            ("+15557654321",      ""),       # filtered (blank name)
            ("+15557654321",      "Bob"),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            out = Path(f.name)
        try:
            count = write_csv(rows, out)
            self.assertEqual(count, 3)
            with open(out) as f:
                reader = csv.reader(f)
                header = next(reader)
                data = list(reader)
            self.assertEqual(header, ["handle", "name"])
            handles = [r[0] for r in data]
            self.assertIn("+15551234567",      handles)
            self.assertIn("alice@example.com", handles)
            self.assertIn("+15557654321",      handles)
        finally:
            out.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
