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


class TestSearchClearsOnChatSwitch(unittest.IsolatedAsyncioTestCase):
    async def test_switching_chats_closes_search_and_clears_query(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                history.open_search()
                history.apply_search("hello")
                await pilot.pause()
                self.assertEqual(app.state.history_search_query, "hello")

                sidebar = app.query_one(Sidebar)
                if len(sidebar._all_chats) < 2:
                    self.skipTest("fixture has fewer than 2 chats")
                second_chat_id = sidebar._all_chats[1]["chat_id"]
                sidebar.post_message(Sidebar.ChatSelected(second_chat_id))
                await pilot.pause()
                for _ in range(40):
                    if app.state.selected_chat_id == second_chat_id and not app.state.history_loading:
                        break
                    await pilot.pause(delay=0.05)

                self.assertEqual(len(history.query("#history-search")), 0)
                self.assertIsNone(app.state.history_search_query)


class TestSwitchToEmptyChat(unittest.IsolatedAsyncioTestCase):
    """Regression: switching from a populated chat to an empty chat must
    show the 'No messages in this chat.' placeholder, not leave a blank pane.

    Bug history: render_messages used to call self.remove_children() (async)
    then immediately show_placeholder(), which collided with the still-present
    'Loading…' placeholder mounted moments earlier by on_sidebar_chat_selected.
    """

    async def test_empty_chat_shows_placeholder(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                self.assertGreater(len(history.query(".message-row")), 0)

                sidebar = app.query_one(Sidebar)
                if len(sidebar._all_chats) < 3:
                    self.skipTest("fixture needs an empty chat at index 2")
                empty_chat_id = sidebar._all_chats[2]["chat_id"]
                sidebar.post_message(Sidebar.ChatSelected(empty_chat_id))
                await pilot.pause()
                for _ in range(40):
                    if app.state.selected_chat_id == empty_chat_id and not app.state.history_loading:
                        break
                    await pilot.pause(delay=0.05)

                # After loading an empty chat: no rows, placeholder visible.
                self.assertEqual(len(history.query(".message-row")), 0)
                placeholders = list(history.query("#history-placeholder"))
                self.assertEqual(len(placeholders), 1)
                self.assertIn("No messages in this chat", str(placeholders[0].renderable))


class TestSidebarTypeToFilter(unittest.IsolatedAsyncioTestCase):
    async def test_typing_letter_when_list_focused_filters(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar
            from textual.widgets import Input, ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                for _ in range(20):
                    sidebar = app.query_one(Sidebar)
                    if sidebar._all_chats:
                        break
                    await pilot.pause(delay=0.05)
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                lv.focus()
                await pilot.pause()
                await pilot.press("a")
                await pilot.pause()
                filter_input = sidebar.query_one("#sidebar-filter", Input)
                self.assertEqual(app.focused, filter_input)
                self.assertEqual(filter_input.value, "a")

    async def test_esc_in_filter_clears_and_refocuses_list(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar
            from textual.widgets import Input, ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                for _ in range(20):
                    sidebar = app.query_one(Sidebar)
                    if sidebar._all_chats:
                        break
                    await pilot.pause(delay=0.05)
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                lv.focus()
                await pilot.pause()
                await pilot.press("x")
                await pilot.pause()
                filter_input = sidebar.query_one("#sidebar-filter", Input)
                self.assertEqual(filter_input.value, "x")
                await pilot.press("escape")
                await pilot.pause()
                self.assertEqual(filter_input.value, "")
                self.assertEqual(app.focused, lv)


if __name__ == "__main__":
    unittest.main()
