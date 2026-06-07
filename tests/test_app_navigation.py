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


class TestAutoFocusHistoryAfterChatSelect(unittest.IsolatedAsyncioTestCase):
    async def test_focus_moves_to_first_message_after_load(self):
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
                # After load + render, focus should be on the first message row.
                self.assertIs(app.focused, rows[0])


class TestActiveRegionBorder(unittest.IsolatedAsyncioTestCase):
    async def test_region_active_class_follows_focus(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar, ActionBar

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                sidebar = app.query_one(Sidebar)
                history = app.query_one(HistoryView)
                action_bar = app.query_one(ActionBar)

                # Right after chat load, history is the active region (Task 7).
                self.assertTrue(history.has_class("region-active"))
                self.assertFalse(sidebar.has_class("region-active"))
                self.assertFalse(action_bar.has_class("region-active"))

                # Focus the sidebar list — sidebar becomes active.
                sidebar.query_one("#sidebar-list").focus()
                await pilot.pause()
                self.assertTrue(sidebar.has_class("region-active"))
                self.assertFalse(history.has_class("region-active"))


class TestStatusLineFocusChip(unittest.IsolatedAsyncioTestCase):
    async def test_chip_reflects_focused_region(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar, StatusLine

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                status = app.query_one(StatusLine)
                # After chat load, focus is on history (Task 7).
                rendered = str(status.renderable)
                self.assertIn("[history]", rendered)

                sidebar = app.query_one(Sidebar)
                sidebar.query_one("#sidebar-list").focus()
                await pilot.pause()
                rendered = str(status.renderable)
                self.assertIn("[sidebar]", rendered)


class TestHelpModalText(unittest.IsolatedAsyncioTestCase):
    async def test_help_modal_documents_new_bindings(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.modals import HelpModal
            from textual.widgets import Static

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                app.push_screen(HelpModal())
                await pilot.pause()
                modal = app.screen
                self.assertIsInstance(modal, HelpModal)
                body_text = "\n".join(
                    str(s.renderable) for s in modal.query(Static)
                )
                # New navigation lines must appear.
                self.assertIn("Tab", body_text)
                self.assertIn("Sidebar", body_text)
                self.assertIn("History", body_text)
                self.assertIn("/", body_text)
                # Existing accelerator legend must still be there.
                self.assertIn("Export", body_text)
                self.assertIn("Redact", body_text)


class TestKeyboardOnlyHappyPath(unittest.IsolatedAsyncioTestCase):
    async def test_full_keyboard_flow_to_range_set_and_cleared(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar, HistoryView
            from textual.widgets import ListView

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

                # Pick the first chat (Alice) — no filtering needed for fixture.
                first_chat_id = sidebar._all_chats[0]["chat_id"]
                sidebar.post_message(Sidebar.ChatSelected(first_chat_id))
                await pilot.pause()
                for _ in range(40):
                    if not app.state.history_loading and app.state.selected_chat_messages:
                        break
                    await pilot.pause(delay=0.05)

                # Focus auto-moved to first message row (Task 7).
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                self.assertGreater(len(rows), 1)
                # Allow Pilot a moment for Task 7's focus-after-render to settle.
                for _ in range(10):
                    if app.focused is rows[0]:
                        break
                    await pilot.pause(delay=0.05)
                self.assertIs(app.focused, rows[0])

                # Enter marks the first row, then End + Enter marks the last.
                await pilot.press("enter")
                await pilot.pause()
                history.action_jump_end()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                # State should reflect a complete range.
                self.assertIsNotNone(app.state.range_start_msg_id)
                self.assertIsNotNone(app.state.range_end_msg_id)
                self.assertEqual(app.state.window_source, "selection")

                # Esc clears marks.
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsNone(app.state.range_start_msg_id)
                self.assertIsNone(app.state.range_end_msg_id)


if __name__ == "__main__":
    unittest.main()
