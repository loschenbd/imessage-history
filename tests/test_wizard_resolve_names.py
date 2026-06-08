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

    def test_terse_resolved_drops_handle_suffix(self):
        """Matched contact + terse_when_resolved=True → just the name. The
        TUI sidebar + ChatHeader rely on this for a clean read."""
        out = wizard._resolve_names(
            "+14026608922", {"+14026608922": "Ben"}, terse_when_resolved=True,
        )
        self.assertEqual(out, "Ben")

    def test_terse_unresolved_keeps_handle(self):
        """No contact match + terse → still show the raw handle. Otherwise
        the user has no idea which unknown number they're looking at."""
        out = wizard._resolve_names(
            "+14026608922", {}, terse_when_resolved=True,
        )
        self.assertEqual(out, "+14026608922")

    def test_terse_group_mix(self):
        """Group chat with mixed resolved/unresolved members: names appear
        without handle, unknowns appear as bare numbers, dedupe still
        collapses two handles for the same person to one name."""
        contacts = {
            "+14026608922": "Ben",
            "+15037031457": "Carol",
            "ben@example.com": "Ben",
        }
        out = wizard._resolve_names(
            "+14026608922,+15037031457,+15307806768,ben@example.com",
            contacts,
            terse_when_resolved=True,
        )
        self.assertEqual(out, "Ben, Carol, +15307806768")

    def test_format_chat_row_terse_names_drops_handle(self):
        """_format_chat_row threads terse_names through to _resolve_names —
        sidebar rows for matched contacts should not show the number."""
        row = {
            "chat_id": 7,
            "display_name": "+14026608922",
            "participants": "+14026608922",
            "style": "message",
            "msg_count": 12,
            "last_message_local": "2026-01-01 10:00",
        }
        out = wizard._format_chat_row(
            row, {"+14026608922": "Ben"}, include_id=False, terse_names=True,
        )
        self.assertIn("Ben", out)
        self.assertNotIn("+14026608922", out)


if __name__ == "__main__":
    unittest.main()
