"""Pilot-based smoke tests for TUI navigation behavior.

Each test boots the app against the fixture chat.db, selects the first
chat, waits for messages to render, and then exercises a specific keyboard
flow. Same patching pattern as test_app_smoke.py.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_sample_db import build  # noqa: E402


@contextlib.contextmanager
def _patched_app(tmpdir_name: str):
    """Build a fixture chat.db at tmpdir_name and stack the three patches the
    Textual app needs to run hermetically: DEFAULT_DB, defaults path, and a
    no-op _offer_contacts_scan. Yields nothing — callers just `with _patched_app(...):`.
    """
    db_path = Path(tmpdir_name) / "chat.db"
    build(db_path)
    defaults_path = Path(tmpdir_name) / "recent.json"
    with mock.patch.multiple("imessage_export.tui.app.app", DEFAULT_DB=db_path), \
         mock.patch("imessage_export.tui.defaults.DEFAULT_PATH", defaults_path), \
         mock.patch(
             "imessage_export.tui.app.app.ImessageExportApp._offer_contacts_scan",
             return_value=None,
         ):
        yield


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
        with _patched_app(tmpdir.name):
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
        with _patched_app(tmpdir.name):
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


class TestHistoryJumpBindings(unittest.IsolatedAsyncioTestCase):
    async def test_end_focuses_last_message_home_focuses_first(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                # Focus the middle row to start somewhere non-trivial.
                rows[len(rows) // 2].focus()
                await pilot.pause()

                history.action_jump_end()
                await pilot.pause()
                self.assertIs(app.focused, rows[-1])

                history.action_jump_home()
                await pilot.pause()
                self.assertIs(app.focused, rows[0])


class TestHistorySearch(unittest.IsolatedAsyncioTestCase):
    async def test_slash_opens_search_input(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                rows[0].focus()
                await pilot.pause()
                await pilot.press("slash")
                await pilot.pause()
                search = history.query("#history-search")
                self.assertEqual(len(search), 1)
                self.assertEqual(app.focused, search[0])

    async def test_typing_in_search_filters_rendered_rows(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                full_messages = history._all_messages
                token = next((m.text[:4] for m in full_messages if m.text and len(m.text) >= 4), None)
                self.assertIsNotNone(token, "fixture should have at least one >=4-char message")

                history.open_search()
                await pilot.pause()
                history.apply_search(token)
                await pilot.pause()
                rendered_rows = list(history.query(".message-row"))
                ids = {getattr(r, "data_msg_id", None) for r in rendered_rows}
                expected_ids = {m.message_id for m in full_messages if m.text and token.lower() in m.text.lower()}
                self.assertEqual(ids, expected_ids)

    async def test_esc_in_search_closes_and_restores(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                full_count = len(history.query(".message-row"))
                history.open_search()
                history.apply_search("xyzzy-no-match")
                await pilot.pause()
                self.assertEqual(len(history.query(".message-row")), 0)
                history.close_search()
                await pilot.pause()
                self.assertEqual(len(history.query("#history-search")), 0)
                self.assertEqual(len(history.query(".message-row")), full_count)
                self.assertIsNone(app.state.history_search_query)


if __name__ == "__main__":
    unittest.main()
