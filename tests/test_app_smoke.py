"""End-to-end smoke test for the Textual app.

Uses Textual's `Pilot` harness against the fixture chat.db. Not asserting
rendering pixel-by-pixel — only that the wiring carries data from
sidebar click to export completion.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the fixtures importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_sample_db import build  # noqa: E402


def _open_db_threadsafe(path: Path) -> sqlite3.Connection:
    """Thin wrapper around open_db that sets check_same_thread=False.

    Textual's `@work(thread=True)` hands self.conn to a worker thread.
    The real chat.db opens with `mode=ro&immutable=1` (URI-mode, read-only)
    so multi-thread safety is fine in practice; SQLite's default thread check
    just rejects the cross-thread usage. This helper re-opens with the same
    read-only URI flags but without the thread guard so the Pilot test works.
    """
    uri = f"file:{path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


class TestAppSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_select_chat_loads_history_and_marks_range(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "chat.db"
        build(db_path)

        # Point DEFAULT_DB at the fixture (patch the name as imported into app.py)
        # and the defaults file at a temp path so the test doesn't read or write
        # the real ~/.config/imessage-export.
        # Also mock _offer_contacts_scan to prevent the ContactsScanModal from
        # being pushed during the test (no real contacts on disk).
        # Patch open_db to use check_same_thread=False so the @work(thread=True)
        # worker can share the connection that was created on the main thread.
        defaults_path = Path(tmpdir.name) / "recent.json"
        with mock.patch("imessage_export.tui.app.app.DEFAULT_DB", db_path), \
             mock.patch("imessage_export.tui.defaults.DEFAULT_PATH", defaults_path), \
             mock.patch(
                 "imessage_export.tui.app.app.ImessageExportApp._offer_contacts_scan",
                 return_value=None,
             ), \
             mock.patch(
                 "imessage_export.tui.app.app.open_db",
                 side_effect=_open_db_threadsafe,
             ):

            from imessage_export.tui.app.app import ImessageExportApp

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                # Sidebar should have at least one chat.
                from imessage_export.tui.app.widgets import Sidebar, HistoryView
                sidebar = app.query_one(Sidebar)
                self.assertGreater(len(sidebar._all_chats), 0)

                # Simulate selecting the first chat by posting ChatSelected
                # directly.  Setting lv.index + pilot.press("enter") does not
                # fire ListView.Selected in Textual's headless Pilot mode
                # reliably, but posting the sidebar's own message works.
                from imessage_export.tui.app.widgets import Sidebar as _Sidebar  # noqa: F811
                sidebar.post_message(_Sidebar.ChatSelected(sidebar._all_chats[0]["chat_id"]))
                await pilot.pause()

                # Wait for the history-load worker to finish.
                for _ in range(40):
                    if not app.state.history_loading and app.state.selected_chat_messages:
                        break
                    await pilot.pause(delay=0.05)
                self.assertTrue(app.state.selected_chat_messages)

                # Mark first and last message.
                first_id = app.state.selected_chat_messages[0]["message_id"]
                last_id = app.state.selected_chat_messages[-1]["message_id"]
                history = app.query_one(HistoryView)
                history.post_message(HistoryView.RangeMarkRequested(first_id))
                await pilot.pause()
                history.post_message(HistoryView.RangeMarkRequested(last_id))
                await pilot.pause()

                self.assertEqual(app.state.range_start_msg_id, first_id)
                self.assertEqual(app.state.range_end_msg_id, last_id)
                self.assertEqual(app.state.window_source, "selection")


if __name__ == "__main__":
    unittest.main()
