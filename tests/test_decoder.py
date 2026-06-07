"""Regression tests for imessage_export internals.

Run from the repo root:
    python3 -m unittest discover -s tests -v
or as a plain script:
    python3 tests/test_decoder.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Make the script importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import imessage_export as ie


def make_typedstream(text: str) -> bytes:
    """Build a minimal `attributedBody`-shaped blob whose first NSString
    contains `text`. Mirrors the layout the real decoder expects:

        ...NSString<class-marker bytes>+<length-prefix><utf-8 bytes>

    Length prefix:
      - len < 0x80           => single byte
      - len < 0x10000        => 0x81 + 2 bytes little-endian
      - len < 0x100000000    => 0x82 + 4 bytes little-endian
    """
    body = text.encode("utf-8")
    n = len(body)
    if n < 0x80:
        prefix = bytes([n])
    elif n < 0x10000:
        prefix = b"\x81" + n.to_bytes(2, "little")
    else:
        prefix = b"\x82" + n.to_bytes(4, "little")
    # The bytes between "NSString" and "+" are class-version metadata in the
    # real format. For decoder testing, any short filler is fine; the parser
    # searches for "+" forward from "NSString".
    return b"streamtyped\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x95\x84\x01+" + prefix + body


class DecodeAttributedBodyTests(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(ie.decode_attributed_body(None))

    def test_empty_returns_none(self):
        self.assertIsNone(ie.decode_attributed_body(b""))

    def test_no_nsstring_returns_none(self):
        self.assertIsNone(ie.decode_attributed_body(b"random garbage"))

    def test_short_string(self):
        self.assertEqual(ie.decode_attributed_body(make_typedstream("hello")), "hello")

    def test_127_char_boundary(self):
        # Last length that fits in a single byte
        s = "a" * 127
        self.assertEqual(ie.decode_attributed_body(make_typedstream(s)), s)

    def test_128_char_crosses_into_x81_branch(self):
        # First length that requires the 0x81 + 2-byte LE form
        s = "b" * 128
        self.assertEqual(ie.decode_attributed_body(make_typedstream(s)), s)

    def test_long_message_regression_for_truncation_bug(self):
        # Regression for the 255-char cutoff bug — pre-fix, this returned 255 chars.
        s = "x" * 1500
        self.assertEqual(ie.decode_attributed_body(make_typedstream(s)), s)

    def test_exact_256_chars(self):
        # 0x100 = first value where the high byte of the LE length is non-zero
        s = "y" * 256
        self.assertEqual(ie.decode_attributed_body(make_typedstream(s)), s)

    def test_attachment_placeholder_stripped(self):
        # U+FFFC (object replacement) is iMessage's inline-attachment placeholder
        s = "before￼after"
        self.assertEqual(ie.decode_attributed_body(make_typedstream(s)), "beforeafter")

    def test_unicode_preserved(self):
        s = "I’m sorry 💖 — это работает"
        self.assertEqual(ie.decode_attributed_body(make_typedstream(s)), s)

    def test_huge_x82_branch(self):
        # > 65535 bytes triggers the 0x82 + 4-byte LE branch
        s = "z" * (0x10001)
        self.assertEqual(ie.decode_attributed_body(make_typedstream(s)), s)


class TapbackClassificationTests(unittest.TestCase):
    def test_zero_is_not_a_tapback(self):
        self.assertIsNone(ie.classify_tapback(0))

    def test_normal_message_type_is_not_a_tapback(self):
        self.assertIsNone(ie.classify_tapback(1000))

    def test_added_tapbacks(self):
        cases = {
            2000: "Loved", 2001: "Liked", 2002: "Disliked",
            2003: "Laughed", 2004: "Emphasized", 2005: "Questioned",
            2006: "Sticker",
        }
        for amt, name in cases.items():
            with self.subTest(amt=amt):
                result = ie.classify_tapback(amt)
                self.assertEqual(result, (name, False))

    def test_removed_tapbacks(self):
        result = ie.classify_tapback(3000)
        self.assertEqual(result, ("Loved", True))
        result = ie.classify_tapback(3003)
        self.assertEqual(result, ("Laughed", True))

    def test_out_of_range_is_none(self):
        self.assertIsNone(ie.classify_tapback(1999))
        self.assertIsNone(ie.classify_tapback(4000))


class StripTargetGuidTests(unittest.TestCase):
    def test_per_part_format(self):
        guid = "p:0/CA6D41E4-6BE6-4B10-A9F6-62304B9BB297"
        self.assertEqual(ie.strip_target_guid(guid), "CA6D41E4-6BE6-4B10-A9F6-62304B9BB297")

    def test_bp_prefix(self):
        guid = "bp:CA6D41E4-6BE6-4B10-A9F6-62304B9BB297"
        self.assertEqual(ie.strip_target_guid(guid), "CA6D41E4-6BE6-4B10-A9F6-62304B9BB297")

    def test_bare_guid_passes_through(self):
        guid = "CA6D41E4-6BE6-4B10-A9F6-62304B9BB297"
        self.assertEqual(ie.strip_target_guid(guid), guid)

    def test_none_and_empty(self):
        self.assertIsNone(ie.strip_target_guid(None))
        self.assertIsNone(ie.strip_target_guid(""))


class NormalizeHandleTests(unittest.TestCase):
    def test_email_lowercased(self):
        self.assertEqual(ie.normalize_handle("Alice@Example.COM"), "alice@example.com")

    def test_phone_digits_with_plus(self):
        self.assertEqual(ie.normalize_handle("+1 (555) 123-4567"), "+15551234567")

    def test_phone_digits_without_plus(self):
        self.assertEqual(ie.normalize_handle("5551234567"), "+5551234567")


class ChatLabelTests(unittest.TestCase):
    def test_one_to_one_uses_participant(self):
        md = {
            "chats": [{"is_group": False, "display_name": ""}],
            "participants": [{"resolved_name": "Alice"}],
        }
        self.assertEqual(ie.chat_label(md), "Alice")

    def test_group_with_display_name(self):
        md = {
            "chats": [{"is_group": True, "display_name": "Family"}],
            "participants": [{"resolved_name": "Mom"}, {"resolved_name": "Dad"}],
        }
        self.assertEqual(ie.chat_label(md), "Family")

    def test_group_without_display_name_joins_sorted_participants(self):
        md = {
            "chats": [{"is_group": True, "display_name": ""}],
            "participants": [{"resolved_name": "Mallory"}, {"resolved_name": "Shannon"}],
        }
        self.assertEqual(ie.chat_label(md), "Mallory+Shannon")


class OpenDbReadOnlyTests(unittest.TestCase):
    """Prove `open_db()` returns a connection that cannot be used to write.

    Defends against a future refactor that accidentally drops the PRAGMA, the
    URI mode, or the immutable flag.
    """

    def _make_sample_db(self, path: Path) -> None:
        # Build a tiny DB with the same connection mechanism real chat.db uses.
        c = sqlite3.connect(str(path))
        c.execute("CREATE TABLE t (x INTEGER PRIMARY KEY, y TEXT)")
        c.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b')")
        c.commit()
        c.close()

    def test_reads_succeed_writes_raise(self):
        with tempfile.TemporaryDirectory() as d:
            sample = Path(d) / "sample.db"
            self._make_sample_db(sample)
            conn = ie.open_db(sample)

            # Read works.
            self.assertEqual(conn.execute("SELECT y FROM t WHERE x = 1").fetchone()[0], "a")

            # Every write surface must raise.
            write_statements = [
                "DELETE FROM t",
                "UPDATE t SET y = 'z' WHERE x = 1",
                "INSERT INTO t VALUES (3, 'c')",
                "CREATE TABLE t2 (a)",
                "DROP TABLE t",
                "ALTER TABLE t RENAME TO t2",
                "REPLACE INTO t VALUES (1, 'z')",
            ]
            for stmt in write_statements:
                with self.subTest(stmt=stmt):
                    with self.assertRaises(sqlite3.OperationalError):
                        conn.execute(stmt)

            # And the file on disk is byte-for-byte unchanged.
            before = sample.read_bytes()
            conn.close()
            self.assertEqual(sample.read_bytes(), before)

    def test_query_only_pragma_is_actually_on(self):
        with tempfile.TemporaryDirectory() as d:
            sample = Path(d) / "sample.db"
            self._make_sample_db(sample)
            conn = ie.open_db(sample)
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
