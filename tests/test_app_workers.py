"""Worker functions that wrap the existing export pipeline for the Textual app."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Make the fixtures importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_sample_db import build  # noqa: E402

from imessage_export.db import open_db, resolve_chat_ids  # noqa: E402
from imessage_export.tui.app.workers import load_chat_messages  # noqa: E402


class TestLoadChatMessages(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "chat.db"
        build(self.db_path)
        self.conn = open_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_returns_all_messages_when_no_window(self):
        chat_ids = resolve_chat_ids(self.conn, chat_id=1, chat_identifier=None, participant=None)
        msgs = load_chat_messages(
            self.conn,
            chat_id=chat_ids[0],
            contacts={},
            me_name="Me",
        )
        self.assertGreater(len(msgs), 0)

    def test_messages_are_in_timestamp_ascending_order(self):
        chat_ids = resolve_chat_ids(self.conn, chat_id=1, chat_identifier=None, participant=None)
        msgs = load_chat_messages(
            self.conn,
            chat_id=chat_ids[0],
            contacts={},
            me_name="Me",
        )
        timestamps = [m.timestamp for m in msgs]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_empty_chat_returns_empty_list(self):
        # build_sample_db only populates chat 1; chat id 9999 has no messages.
        msgs = load_chat_messages(
            self.conn,
            chat_id=9999,
            contacts={},
            me_name="Me",
        )
        self.assertEqual(msgs, [])


if __name__ == "__main__":
    unittest.main()
