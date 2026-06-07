"""Defaults file roundtrip + schema-version handling."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imessage_export.tui import defaults


class DefaultsRoundtripTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "recent.json"

    def test_save_and_load_roundtrip(self):
        d = defaults.Defaults(
            contacts_path="/tmp/contacts.csv",
            output_dir="/tmp/exports",
            me_name="Ben",
            last_chat_id=42,
        )
        defaults.save(d, self.path)
        loaded = defaults.load(self.path)
        self.assertEqual(loaded.contacts_path, "/tmp/contacts.csv")
        self.assertEqual(loaded.output_dir, "/tmp/exports")
        self.assertEqual(loaded.me_name, "Ben")
        self.assertEqual(loaded.last_chat_id, 42)

    def test_missing_file_returns_empty_defaults(self):
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.contacts_path)
        self.assertIsNone(loaded.output_dir)
        self.assertIsNone(loaded.me_name)
        self.assertIsNone(loaded.last_chat_id)

    def test_unknown_version_returns_empty_defaults(self):
        self.path.write_text(json.dumps({
            "version": 999,
            "contacts_path": "/should/be/ignored",
        }))
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.contacts_path)

    def test_save_writes_0600_perms(self):
        d = defaults.Defaults(me_name="Ben")
        defaults.save(d, self.path)
        mode = self.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"got {oct(mode)}")

    def test_ensure_dir_creates_with_0700(self):
        nested = self.path.parent / "nested" / "deeper"
        out = nested / "recent.json"
        defaults.save(defaults.Defaults(me_name="Ben"), out)
        self.assertEqual(nested.stat().st_mode & 0o777, 0o700)


class ThemeOverrideTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "recent.json"

    def test_theme_override_roundtrip(self):
        from imessage_export.tui import defaults
        d = defaults.Defaults(me_name="Ben", theme_override="dawnfox")
        defaults.save(d, self.path)
        loaded = defaults.load(self.path)
        self.assertEqual(loaded.theme_override, "dawnfox")

    def test_old_file_without_theme_override_loads_cleanly(self):
        import json as jsonmod
        self.path.write_text(jsonmod.dumps({
            "version": 1,
            "contacts_path": "/tmp/contacts.csv",
            "output_dir":    "/tmp/exports",
            "me_name":       "Ben",
            "last_chat_id":  42,
            "last_used":     "2026-06-06T12:00:00-04:00",
        }))
        from imessage_export.tui import defaults
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.theme_override)
        self.assertEqual(loaded.me_name, "Ben")

    def test_bad_theme_override_value_coerced_to_none(self):
        import json as jsonmod
        self.path.write_text(jsonmod.dumps({
            "version": 1,
            "theme_override": "catppuccin-mocha",
        }))
        from imessage_export.tui import defaults
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.theme_override)


if __name__ == "__main__":
    unittest.main()
