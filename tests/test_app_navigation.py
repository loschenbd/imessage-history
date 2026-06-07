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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_sample_db import build  # noqa: E402


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
