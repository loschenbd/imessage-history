"""Pilot-based smoke tests for TUI navigation behavior.

Each test boots the app against the fixture chat.db, selects the first
chat, waits for messages to render, and then exercises a specific keyboard
flow. Same patching pattern as test_app_smoke.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_sample_db import build  # noqa: E402


def _patched_app_context(tmpdir_name: str):
    db_path = Path(tmpdir_name) / "chat.db"
    build(db_path)
    defaults_path = Path(tmpdir_name) / "recent.json"
    return mock.patch.multiple(
        "imessage_export.tui.app.app",
        DEFAULT_DB=db_path,
    ), mock.patch(
        "imessage_export.tui.defaults.DEFAULT_PATH", defaults_path,
    ), mock.patch(
        "imessage_export.tui.app.app.ImessageExportApp._offer_contacts_scan",
        return_value=None,
    )


async def _boot_and_select_first_chat(pilot, app):
    """Helper: post ChatSelected for the first chat and wait for history to load."""
    from imessage_export.tui.app.widgets import Sidebar
    sidebar = app.query_one(Sidebar)
    sidebar.post_message(Sidebar.ChatSelected(sidebar._all_chats[0]["chat_id"]))
    await pilot.pause()
    for _ in range(40):
        if not app.state.history_loading and app.state.selected_chat_messages:
            break
        await pilot.pause(delay=0.05)


class TestHistoryRowFocus(unittest.IsolatedAsyncioTestCase):
    async def test_message_rows_are_focusable(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                self.assertGreater(len(rows), 0)
                # Every rendered row must declare itself focusable.
                for row in rows:
                    self.assertTrue(row.can_focus, msg=f"row {row} should be focusable")

    async def test_enter_on_focused_row_marks_range(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                self.assertGreater(len(rows), 0)
                # Focus the first row, then press Enter.
                rows[0].focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                # First Enter sets range_start_msg_id.
                expected_id = getattr(rows[0], "data_msg_id", None)
                self.assertIsNotNone(expected_id)
                self.assertEqual(app.state.range_start_msg_id, expected_id)


if __name__ == "__main__":
    unittest.main()
