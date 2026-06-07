"""Build a synthetic `chat.db` for end-to-end tests.

This file deliberately does NOT ship a pre-built `.db` binary in git. Binary
fixtures rot whenever the SQLite write format or our schema assumptions
change. Building from source code with stdlib `sqlite3` is reproducible and
doubles as documentation of which `chat.db` columns we actually read.

Schema is a minimal subset of the real macOS Messages DB — only the columns
referenced by `imessage_export.py` queries. Apple's real DB has many more
columns we ignore.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def to_apple_ns(dt: datetime) -> int:
    """Convert an aware UTC datetime to Apple's 2001-epoch nanoseconds."""
    delta = dt.astimezone(timezone.utc) - APPLE_EPOCH
    return int(delta.total_seconds() * 1_000_000_000)


def _typedstream_with_text(text: str) -> bytes:
    """Build a minimal NSAttributedString typedstream blob whose first
    NSString contains `text`. Mirrors the layout the decoder expects.
    Matches `tests/test_decoder.py::make_typedstream` exactly.
    """
    body = text.encode("utf-8")
    n = len(body)
    if n < 0x80:
        prefix = bytes([n])
    elif n < 0x10000:
        prefix = b"\x81" + n.to_bytes(2, "little")
    else:
        prefix = b"\x82" + n.to_bytes(4, "little")
    return (
        b"streamtyped\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x95\x84\x01+"
        + prefix + body
    )


def build(path: Path) -> None:
    """Build a fully-populated sample `chat.db` at `path`.

    Layout:
      Primary 1:1 chat between Me and +15551234567 (Alice), 7 messages on 2025-05-01.
      Secondary 1:1 chat between Me and +15557654321 (Bob), 1 message on 2025-04-01.
      Tertiary 1:1 chat between Me and +15550001111 (Charlie), EMPTY (no msgs).

      Order from list_recent_chats: Alice (newest msg) → Bob (older msg) →
      Charlie (NULL last_date sorts last). Tests that index `_all_chats[0]`
      keep seeing Alice. Tests that need a chat switch use `_all_chats[1]`
      (Bob). Tests that need an EMPTY-chat switch use `_all_chats[2]` (Charlie).
    """
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE handle (
            ROWID   INTEGER PRIMARY KEY,
            id      TEXT,
            service TEXT
        );
        CREATE TABLE chat (
            ROWID            INTEGER PRIMARY KEY,
            guid             TEXT,
            chat_identifier  TEXT,
            display_name     TEXT,
            style            INTEGER
        );
        CREATE TABLE chat_handle_join (
            chat_id   INTEGER,
            handle_id INTEGER
        );
        CREATE TABLE message (
            ROWID                    INTEGER PRIMARY KEY,
            guid                     TEXT,
            date                     INTEGER,
            text                     TEXT,
            attributedBody           BLOB,
            is_from_me               INTEGER,
            cache_has_attachments    INTEGER,
            associated_message_type  INTEGER DEFAULT 0,
            associated_message_guid  TEXT,
            date_edited              INTEGER DEFAULT 0,
            date_retracted           INTEGER DEFAULT 0,
            balloon_bundle_id        TEXT,
            handle_id                INTEGER
        );
        CREATE TABLE chat_message_join (
            chat_id    INTEGER,
            message_id INTEGER
        );
        CREATE TABLE attachment (
            ROWID         INTEGER PRIMARY KEY,
            filename      TEXT,
            transfer_name TEXT
        );
        CREATE TABLE message_attachment_join (
            message_id    INTEGER,
            attachment_id INTEGER
        );
        """
    )

    # Handle
    conn.execute(
        "INSERT INTO handle VALUES (?, ?, ?)",
        (1, "+15551234567", "iMessage"),
    )
    # Chat (1:1 → style = 45)
    conn.execute(
        "INSERT INTO chat VALUES (?, ?, ?, ?, ?)",
        (1, "iMessage;-;+15551234567", "+15551234567", None, 45),
    )
    conn.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (1, 1))

    base = datetime(2025, 5, 1, 14, 30, 0, tzinfo=timezone.utc)

    def insert_message(rid, offset_sec, text, body, is_from_me,
                       amt=0, amg=None, edited=0, guid=None):
        conn.execute(
            """INSERT INTO message (
                 ROWID, guid, date, text, attributedBody, is_from_me,
                 cache_has_attachments, associated_message_type,
                 associated_message_guid, date_edited, date_retracted,
                 balloon_bundle_id, handle_id
               ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0, NULL, ?)""",
            (rid,
             guid or f"GUID-{rid:08d}",
             to_apple_ns(base.replace(second=offset_sec % 60,
                                      minute=base.minute + offset_sec // 60)),
             text, body, is_from_me, amt, amg, edited, 1),
        )
        conn.execute("INSERT INTO chat_message_join VALUES (?, ?)", (1, rid))

    # rid 1 — Alice's incoming with plain m.text
    insert_message(
        rid=1, offset_sec=0,
        text="Hey, are you free later?", body=None,
        is_from_me=0,
    )
    # rid 2 — Me outgoing, plain m.text
    insert_message(
        rid=2, offset_sec=30,
        text="Yes, after 6 works.", body=None,
        is_from_me=1,
    )
    # rid 3 — Alice incoming, text is NULL, body is a typedstream blob with a
    #         200-char string (forces the 0x81 + 2-byte LE branch)
    long_text = "A" * 200
    insert_message(
        rid=3, offset_sec=60,
        text=None, body=_typedstream_with_text(long_text),
        is_from_me=0,
    )
    # rid 4 — Me, Loved tapback on rid 1
    insert_message(
        rid=4, offset_sec=90,
        text=None, body=None,
        is_from_me=1, amt=2000, amg="p:0/GUID-00000001",
    )
    # rid 5 — Me, edited message (still has current text)
    insert_message(
        rid=5, offset_sec=120,
        text="I edited this one.", body=None,
        is_from_me=1, edited=to_apple_ns(base.replace(minute=base.minute + 3)),
    )
    # rid 6 — Alice incoming, mentions a third party ("Carol") by name only.
    #         Carol is not in any handle row. Used by --suggest-names tests
    #         and by tests that verify body-text names get redacted only when
    #         supplied via --redact-names-file.
    insert_message(
        rid=6, offset_sec=150,
        text="Carol said she'd be here by 7. Carol is bringing dessert.",
        body=None,
        is_from_me=0,
    )
    # rid 7 — Me outgoing, body contains a phone, an email, and a URL.
    #         Used by tests that verify PII regex redaction.
    insert_message(
        rid=7, offset_sec=180,
        text="Hit me at +15557654321 or alice@example.com — see https://example.com/page?x=1",
        body=None,
        is_from_me=1,
    )

    # ---- Secondary chat: Me ↔ Bob, single message on an older day. ----
    # Older date so list_recent_chats sorts it AFTER the primary chat — tests
    # that index `_all_chats[0]` keep seeing Alice; chat-switch tests can use
    # `_all_chats[1]` to land on Bob.
    conn.execute(
        "INSERT INTO handle VALUES (?, ?, ?)",
        (2, "+15557654321", "iMessage"),
    )
    conn.execute(
        "INSERT INTO chat VALUES (?, ?, ?, ?, ?)",
        (2, "iMessage;-;+15557654321", "+15557654321", None, 45),
    )
    conn.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (2, 2))
    bob_when = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
    conn.execute(
        """INSERT INTO message (
             ROWID, guid, date, text, attributedBody, is_from_me,
             cache_has_attachments, associated_message_type,
             associated_message_guid, date_edited, date_retracted,
             balloon_bundle_id, handle_id
           ) VALUES (?, ?, ?, ?, NULL, ?, 0, 0, NULL, 0, 0, NULL, ?)""",
        (101, "GUID-00000101", to_apple_ns(bob_when), "Bob says hi.", 0, 2),
    )
    conn.execute("INSERT INTO chat_message_join VALUES (?, ?)", (2, 101))

    # ---- Tertiary chat: Me ↔ Charlie, empty (no messages). ----
    # list_recent_chats LEFT-JOINs through chat_message_join, so an empty chat
    # still appears with last_date=NULL and sorts last under ORDER BY DESC.
    # Tests that switch to this chat exercise the empty-message render path.
    conn.execute(
        "INSERT INTO handle VALUES (?, ?, ?)",
        (3, "+15550001111", "iMessage"),
    )
    conn.execute(
        "INSERT INTO chat VALUES (?, ?, ?, ?, ?)",
        (3, "iMessage;-;+15550001111", "+15550001111", None, 45),
    )
    conn.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (3, 3))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample.db")
    build(out)
    print(f"Built sample chat.db at {out}")
