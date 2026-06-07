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
            "message_id", "timestamp", "local_date", "timestamp_utc", "chat_id",
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
        self.assertEqual(md["message_count"], 7)
        self.assertEqual(len(md["participants"]), 1)
        self.assertEqual(md["participants"][0]["handle"], "+15551234567")

    def test_exports_are_tight_permissions(self):
        self._run()
        out = self._resolve_export_dir()
        # Dir is 700, files are 600
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        for f in out.iterdir():
            self.assertEqual(f.stat().st_mode & 0o777, 0o600, f"{f.name} not 600")


class RedactionEndToEndTests(EndToEndExportTests):
    """Run the existing fixture export with --redact / --redact-only."""

    def setUp(self):
        super().setUp()
        # The adversarial sweep tests need a real contact name registered so
        # we can prove name-based redaction actually scrubs it. The base
        # fixture's handle is +15551234567; map it to "Alice".
        contacts = self.tmp_path / "contacts.csv"
        contacts.write_text("handle,name\n+15551234567,Alice\n")
        self._contacts_path = contacts

    def _redacted_run(self, extra=()):
        argv = [
            "--db", str(self.db_path),
            "--chat-id", "1",
            "--me-name", "Tester",
            "--output-dir", str(self.out_dir),
            "--contacts", str(self._contacts_path),
            *extra,
        ]
        rc = ie.main(argv)
        self.assertEqual(rc, 0)

    def _resolve_first_export_dir(self) -> Path:
        contacts = list(self.out_dir.iterdir())
        self.assertGreaterEqual(len(contacts), 1)
        dates = list(contacts[0].iterdir())
        return dates[0]

    def test_redact_flag_produces_both_versions(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        for name in (
            "conversation.txt", "conversation_redacted.txt",
            "conversation.json", "conversation_redacted.json",
            "conversation.csv", "conversation_redacted.csv",
            "conversation.md", "conversation_redacted.md",
            "conversation_ai_ready.txt", "conversation_redacted_ai_ready.txt",
            "pseudonym_map.json",
        ):
            self.assertTrue((out / name).exists(), f"{name} missing")

    def test_redact_only_skips_originals(self):
        self._redacted_run(extra=["--redact-only"])
        out = self._resolve_first_export_dir()
        for name in ("conversation.txt", "conversation.json", "conversation.csv",
                     "conversation.md", "conversation_ai_ready.txt"):
            self.assertFalse((out / name).exists(), f"{name} should not exist in redact-only mode")
        for name in ("conversation_redacted.txt", "pseudonym_map.json"):
            self.assertTrue((out / name).exists(), f"{name} missing")

    def test_redact_only_folder_includes_hash(self):
        self._redacted_run(extra=["--redact-only"])
        contacts = list(self.out_dir.iterdir())
        self.assertEqual(len(contacts), 1)
        name = contacts[0].name
        self.assertTrue(name.startswith("Person"), f"unexpected folder name: {name}")
        self.assertRegex(name, r"^Person [A-Z]+-[0-9a-f]{4}$")

    def test_redacted_csv_has_no_real_handle(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        red = (out / "conversation_redacted.csv").read_text()
        self.assertNotIn("+15551234567", red)
        self.assertIn("Person",          red)

    def test_pseudonym_map_perms_are_600(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        pmap = out / "pseudonym_map.json"
        self.assertEqual(pmap.stat().st_mode & 0o777, 0o600)

    def test_redact_only_folder_safe_for_unsanitized_label(self):
        """If chat_label() ever returns something with path metacharacters,
        --redact-only must still produce a safe folder name."""
        # Patch redactor.chat_label to return a malicious string.
        from unittest.mock import patch
        with patch.object(ie.Redactor, "chat_label", lambda self: "../etc/passwd"):
            self._redacted_run(extra=["--redact-only"])
        contacts = list(self.out_dir.iterdir())
        self.assertEqual(len(contacts), 1)
        name = contacts[0].name
        # Must not contain a literal slash and must not start with ".."
        self.assertNotIn("/", name)
        self.assertFalse(name.startswith(".."))

    def test_no_real_handle_appears_in_any_redacted_file(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        for name in ("conversation_redacted.txt", "conversation_redacted.csv",
                     "conversation_redacted.json", "conversation_redacted.md",
                     "conversation_redacted_ai_ready.txt"):
            text = (out / name).read_text()
            self.assertNotIn("+15551234567", text, f"{name} leaked the real handle")

    def test_no_real_contact_name_appears_in_any_redacted_file(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        for name in ("conversation_redacted.txt", "conversation_redacted.csv",
                     "conversation_redacted.json", "conversation_redacted.md",
                     "conversation_redacted_ai_ready.txt"):
            text = (out / name).read_text()
            self.assertNotIn("Alice", text, f"{name} leaked the real name")

    def test_redacted_body_pii_scrubbed(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        red_text = (out / "conversation_redacted.txt").read_text()
        self.assertNotIn("+15557654321",        red_text)
        self.assertNotIn("alice@example.com",   red_text)
        self.assertNotIn("https://example.com", red_text)
        self.assertIn("[PHONE]", red_text)
        self.assertIn("[EMAIL]", red_text)
        self.assertIn("[URL]",   red_text)


class SuggestNamesEndToEndTests(EndToEndExportTests):
    def test_suggest_names_finds_carol(self):
        from io import StringIO
        old = sys.stdout
        try:
            sys.stdout = buf = StringIO()
            rc = ie.main([
                "--db", str(self.db_path),
                "--chat-id", "1",
                "--me-name", "Tester",
                "--suggest-names",
            ])
            self.assertEqual(rc, 0)
            self.assertIn("Carol", buf.getvalue())
        finally:
            sys.stdout = old


if __name__ == "__main__":
    unittest.main()
