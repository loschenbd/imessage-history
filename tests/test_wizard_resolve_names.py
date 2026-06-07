"""Unit tests for the wizard's _resolve_names() helper."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib
try:
    wizard = importlib.import_module("imessage_export.tui.wizard")
    HAS_TUI = True
except ImportError:
    HAS_TUI = False


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
class ResolveNamesTests(unittest.TestCase):
    def test_single_handle_with_name(self):
        out = wizard._resolve_names("+14026608922", {"+14026608922": "Ben"})
        self.assertEqual(out, "Ben (+14026608922)")

    def test_single_handle_no_match(self):
        out = wizard._resolve_names("+14026608922", {})
        self.assertEqual(out, "+14026608922")

    def test_group_mixed_known_and_unknown(self):
        contacts = {"+14026608922": "Ben", "+15037031457": "Carol"}
        out = wizard._resolve_names("+14026608922,+15037031457,+15307806768", contacts)
        self.assertEqual(out, "Ben (+14026608922), Carol (+15037031457), +15307806768")

    def test_dedup_two_handles_same_name(self):
        contacts = {"+14026608922": "Ben", "ben@example.com": "Ben"}
        out = wizard._resolve_names("+14026608922,ben@example.com", contacts)
        self.assertEqual(out, "Ben (+14026608922, ben@example.com)")


if __name__ == "__main__":
    unittest.main()
