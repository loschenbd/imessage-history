"""End-to-end test: build a synthetic chat.db and run the full export
pipeline against it, asserting every writer produces sensible output.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make the script + fixtures importable without packaging.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import imessage_export as ie
from fixtures import build_sample_db


class EndToEndExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.db_path = self.tmp_path / "chat.db"
        self.out_dir = self.tmp_path / "exports"
        build_sample_db.build(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra):
        argv = [
            "--db", str(self.db_path),
            "--chat-id", "1",
            "--me-name", "Tester",
            "--output-dir", str(self.out_dir),
            *extra,
        ]
        rc = ie.main(argv)
        self.assertEqual(rc, 0, "export should exit 0")

    def _resolve_export_dir(self) -> Path:
        # exports/<contact>/<YYYY-MM-DD>/
        contacts = list(self.out_dir.iterdir())
        self.assertEqual(len(contacts), 1, "expected exactly one contact folder")
        dates = list(contacts[0].iterdir())
        self.assertEqual(len(dates), 1, "expected exactly one date folder")
        return dates[0]

    def test_all_six_files_emitted(self):
        self._run()
        out = self._resolve_export_dir()
        for name in (
            "conversation.csv",
            "conversation.json",
            "conversation.txt",
            "conversation.md",
            "conversation_ai_ready.txt",
            "analysis_prompt.txt",
        ):
            self.assertTrue((out / name).exists(), f"{name} missing")

    def test_long_attributedbody_decoded_in_full(self):
        # Regresses the 255-char truncation bug. rid 3 has a 200-char body.
        self._run()
        out = self._resolve_export_dir()
        j = json.loads((out / "conversation.json").read_text())
        rid3 = next(m for m in j["messages"] if m["message_id"] == 3)
        self.assertEqual(len(rid3["text"]), 200)
        self.assertTrue(rid3["text"].startswith("A"))
        self.assertTrue(rid3["text"].endswith("A"))

    def test_outgoing_sender_handle_is_null(self):
        # Regresses the "handle_id is recipient on outgoing" bug.
        self._run()
        out = self._resolve_export_dir()
        j = json.loads((out / "conversation.json").read_text())
        outgoing = [m for m in j["messages"] if m["is_from_me"] == 1]
        self.assertGreater(len(outgoing), 0)
        for m in outgoing:
            self.assertIsNone(m["sender_handle"], f"rid {m['message_id']} has non-null sender_handle on outgoing")

    def test_tapback_renders_with_target_text(self):
        self._run()
        out = self._resolve_export_dir()
        j = json.loads((out / "conversation.json").read_text())
        tapback = next(m for m in j["messages"] if m["kind"] == "tapback")
        self.assertIsNotNone(tapback["reaction"])
        self.assertEqual(tapback["reaction"]["type"], "Loved")
        self.assertEqual(tapback["reaction"]["target_text"], "Hey, are you free later?")
        self.assertEqual(tapback["reaction"]["target_message_id"], 1)
        # And it should render in the txt file as something readable
        txt = (out / "conversation.txt").read_text()
        self.assertIn("Loved", txt)
        self.assertIn("Hey, are you free later?", txt)

    def test_edited_message_flagged(self):
        self._run()
        out = self._resolve_export_dir()
        j = json.loads((out / "conversation.json").read_text())
        edited = [m for m in j["messages"] if m["is_edited"] == 1]
        self.assertEqual(len(edited), 1)
        self.assertEqual(edited[0]["message_id"], 5)
        txt = (out / "conversation.txt").read_text()
        self.assertIn("[edited]", txt)

    def test_csv_has_expected_columns(self):
        self._run()
        out = self._resolve_export_dir()
        header = (out / "conversation.csv").read_text().splitlines()[0]
        # Column set should match the documented schema
        expected_cols = {
            "message_id", "timestamp", "timestamp_utc", "chat_id",
            "sender_handle", "is_from_me", "author_label",
            "kind", "is_edited", "reaction_type", "reaction_target",
            "app_bundle",
            "text", "has_attachment", "attachment_filenames",
        }
        self.assertEqual(set(header.split(",")), expected_cols)
        # sender_name must be gone (the redundant-field cleanup)
        self.assertNotIn("sender_name", header)

    def test_markdown_has_title_and_messages(self):
        self._run()
        out = self._resolve_export_dir()
        md = (out / "conversation.md").read_text()
        self.assertIn("# iMessage conversation:", md)
        self.assertIn("Yes, after 6 works.", md)
        self.assertIn("Hey, are you free later?", md)

    def test_metadata_has_resolved_window_and_participants(self):
        self._run()
        out = self._resolve_export_dir()
        j = json.loads((out / "conversation.json").read_text())
        md = j["metadata"]
        self.assertEqual(md["me_name"], "Tester")
        self.assertEqual(md["message_count"], 5)
        self.assertEqual(len(md["participants"]), 1)
        self.assertEqual(md["participants"][0]["handle"], "+15551234567")

    def test_exports_are_tight_permissions(self):
        self._run()
        out = self._resolve_export_dir()
        # Dir is 700, files are 600
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        for f in out.iterdir():
            self.assertEqual(f.stat().st_mode & 0o777, 0o600, f"{f.name} not 600")


if __name__ == "__main__":
    unittest.main()
