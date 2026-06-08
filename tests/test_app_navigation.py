"""Pilot-based smoke tests for TUI navigation behavior.

This file covers the architecture-independent navigation features that landed
on top of main's blob-rendered HistoryView (PRs #22, #24, #25):

- Sidebar fzf-style filter (Task 6)
- Active-region border via `region-active` CSS class (Task 8)
- StatusLine focus chip showing the active region (Task 9)
- HelpModal text refresh documenting the bindings (Task 10)

Per-row history navigation (Tasks 2-5, 7, 11) is NOT included — those tasks
were designed against the prior per-message-row Static rendering model and
need redesign for the current single-Static blob model. See the spec at
docs/superpowers/specs/2026-06-07-tui-navigation-design.md for the full intent.
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_sample_db import build  # noqa: E402

try:
    importlib.import_module("imessage_export.tui.app.app")
    HAS_TUI = True
except ImportError:
    HAS_TUI = False


@contextlib.contextmanager
def _patched_app(tmpdir_name: str):
    """Build a fixture chat.db and stack the three patches the app needs to
    run hermetically: DEFAULT_DB, defaults path, and a no-op contacts scan."""
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
    """Post ChatSelected for the first chat and wait for history to load."""
    from imessage_export.tui.app.widgets import Sidebar
    sidebar = app.query_one(Sidebar)
    sidebar.post_message(Sidebar.ChatSelected(sidebar._all_chats[0]["chat_id"]))
    await pilot.pause()
    for _ in range(40):
        if not app.state.history_loading and app.state.selected_chat_messages:
            break
        await pilot.pause(delay=0.05)


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
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

    async def test_up_at_top_of_list_focuses_filter(self):
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
                lv.index = 0
                lv.focus()
                await pilot.pause()
                await pilot.press("up")
                await pilot.pause()
                filter_input = sidebar.query_one("#sidebar-filter", Input)
                self.assertEqual(app.focused, filter_input)

    async def test_up_mid_list_stays_in_list(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar
            from textual.widgets import ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                for _ in range(20):
                    sidebar = app.query_one(Sidebar)
                    if sidebar._all_chats and len(sidebar._all_chats) >= 2:
                        break
                    await pilot.pause(delay=0.05)
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                if len(sidebar._all_chats) < 2:
                    self.skipTest("fixture needs at least 2 chats")
                lv.index = 1
                lv.focus()
                await pilot.pause()
                await pilot.press("up")
                await pilot.pause()
                self.assertEqual(app.focused, lv)
                self.assertEqual(lv.index, 0)

    async def test_down_in_filter_focuses_list(self):
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
                filter_input = sidebar.query_one("#sidebar-filter", Input)
                filter_input.focus()
                await pilot.pause()
                await pilot.press("down")
                await pilot.pause()
                self.assertEqual(app.focused, lv)

    async def test_right_from_list_focuses_history(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar
            from textual.widgets import ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                lv.focus()
                await pilot.pause()
                await pilot.press("right")
                await pilot.pause()
                self.assertIs(app.focused, app.query_one(HistoryView))

    async def test_left_from_history_focuses_list(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar
            from textual.widgets import ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                history.focus()
                await pilot.pause()
                await pilot.press("left")
                await pilot.pause()
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                self.assertIs(app.focused, lv)

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


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
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

                # Focusing the sidebar list flips region-active onto Sidebar
                # and clears it on the other two regions.
                sidebar.query_one("#sidebar-list").focus()
                await pilot.pause()
                self.assertTrue(sidebar.has_class("region-active"))
                self.assertFalse(history.has_class("region-active"))
                self.assertFalse(action_bar.has_class("region-active"))

                # Focusing the action bar flips region-active onto ActionBar.
                from textual.widgets import Button
                action_bar.query(Button).first().focus()
                await pilot.pause()
                self.assertTrue(action_bar.has_class("region-active"))
                self.assertFalse(sidebar.has_class("region-active"))
                self.assertFalse(history.has_class("region-active"))


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
class TestStatusLineFocusChip(unittest.IsolatedAsyncioTestCase):
    async def test_chip_reflects_focused_region(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar, ActionBar, StatusLine

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                status = app.query_one(StatusLine)
                sidebar = app.query_one(Sidebar)

                sidebar.query_one("#sidebar-list").focus()
                await pilot.pause()
                self.assertIn("[sidebar]", str(status.renderable))

                from textual.widgets import Button
                app.query_one(ActionBar).query(Button).first().focus()
                await pilot.pause()
                self.assertIn("[actions]", str(status.renderable))


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
class TestChatHeader(unittest.IsolatedAsyncioTestCase):
    async def test_header_populates_on_chat_select(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import ChatHeader

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                header = app.query_one(ChatHeader)
                rendered = str(header.renderable)
                # Whatever name resolution path the fixture takes, the
                # selected chat's identifier (or the resolved name +
                # message-count summary) has to be visible.
                self.assertTrue(rendered.strip(), "header should not be empty")
                self.assertIn("messages", rendered)
                # Layout sanity: the header's DEFAULT_CSS sets height: 1
                # but App.CSS also applies `border-bottom: solid $panel`,
                # which adds a row. Without `height: 2` in App.CSS, the
                # widget would lay out at height 0 (border consumes the
                # only row, content is squeezed out). Pin the actual
                # rendered size here so a regression makes the header
                # invisible to the user gets caught immediately.
                self.assertGreater(
                    header.size.height, 0,
                    "ChatHeader must take at least one row in the layout — "
                    "otherwise the chat title/count/last-message text is invisible",
                )

    async def test_header_empty_when_no_selection(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with _patched_app(tmpdir.name):
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import ChatHeader

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                # Drop the selection AND clear the header in the same
                # tick so no subsequent ChatSelected can re-populate it
                # mid-assertion. We're verifying _refresh_chat_header()
                # picks the empty branch when selected_chat_id is None,
                # not the steady-state of the app.
                app.state.selected_chat_id = None
                app._refresh_chat_header()
                header = app.query_one(ChatHeader)
                self.assertEqual(str(header.renderable).strip(), "")


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
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
                self.assertIn("Tab", body_text)
                self.assertIn("Sidebar", body_text)
                self.assertIn("History", body_text)
                self.assertIn("/", body_text)
                self.assertIn("Export", body_text)
                self.assertIn("Redact", body_text)


if __name__ == "__main__":
    unittest.main()
