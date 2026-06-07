#!/usr/bin/env python3
"""
imessage_export.py — Export a single iMessage conversation from the local
macOS Messages database (~/Library/Messages/chat.db) into AI-ready files
with explicit authorship on every message.

Outputs (under --output-dir, default ./exports/<contact>/<YYYY-MM-DD>/):
  - conversation.csv
  - conversation.json   (includes a `metadata` block with the resolved time window)
  - conversation.txt
  - conversation.md     (Notion/Obsidian-friendly)
  - conversation_ai_ready.txt
  - analysis_prompt.txt

Read-only: opens chat.db with mode=ro and `PRAGMA query_only=ON`.

REQUIRES macOS Full Disk Access for the process running this script
(Terminal.app, iTerm2, Ghostty, or python3 itself). System Settings ▸
Privacy & Security ▸ Full Disk Access ▸ add your terminal app.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Apple's epoch: 2001-01-01 00:00:00 UTC = Unix 978307200
APPLE_EPOCH_UNIX = 978307200
DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"


# ---------------------------------------------------------------------------
# Apple timestamp helpers
# ---------------------------------------------------------------------------

def detect_date_unit(conn: sqlite3.Connection) -> str:
    """Return 'ns' or 's'.
    macOS 10.13+ stores message.date as nanoseconds since 2001-01-01 UTC.
    Older macOS stored it as seconds. Detect by magnitude of a sample value.
    """
    row = conn.execute(
        "SELECT date FROM message WHERE date IS NOT NULL AND date > 0 "
        "ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row or row[0] is None:
        return "ns"  # safe default for modern macOS
    # Nanoseconds for any post-2001 timestamp are > ~10^16; seconds < ~10^10.
    return "ns" if row[0] > 10**12 else "s"


def apple_to_utc_datetime(apple_value: int, unit: str) -> datetime:
    if apple_value is None or apple_value == 0:
        return None
    if unit == "ns":
        unix = apple_value / 1_000_000_000 + APPLE_EPOCH_UNIX
    else:
        unix = apple_value + APPLE_EPOCH_UNIX
    return datetime.fromtimestamp(unix, tz=timezone.utc)


def local_dt_to_apple(dt_local: datetime, unit: str) -> int:
    """Convert an aware local datetime to Apple's epoch units."""
    dt_utc = dt_local.astimezone(timezone.utc)
    unix = dt_utc.timestamp()
    if unit == "ns":
        return int((unix - APPLE_EPOCH_UNIX) * 1_000_000_000)
    return int(unix - APPLE_EPOCH_UNIX)


def attach_local_tz(dt_naive: datetime) -> datetime:
    """Attach the system's local timezone to a naive datetime."""
    local_tz = datetime.now().astimezone().tzinfo
    return dt_naive.replace(tzinfo=local_tz)


# ---------------------------------------------------------------------------
# attributedBody decoder (best-effort, no external deps)
# ---------------------------------------------------------------------------

def decode_attributed_body(blob: bytes) -> Optional[str]:
    """Best-effort extraction of message text from the NSAttributedString
    typedstream blob used by modern macOS Messages when `message.text` is NULL.

    Format (simplified): ...NSString...+<length-prefix><utf-8 bytes>
    The length prefix uses Apple's typedstream encoding:
      first byte < 0x80     => length is that byte
      first byte == 0x81    => next 2 bytes are length (little-endian uint16)
      first byte == 0x82    => next 4 bytes are length (little-endian uint32)
    Wider forms (0x83/0x84) are not observed in practice on chat.db and
    are not implemented; they would indicate strings >4GiB.
    """
    if not blob:
        return None
    try:
        idx = blob.find(b"NSString")
        if idx == -1:
            return None
        plus = blob.find(b"\x2b", idx)  # '+' marks the value start
        if plus == -1 or plus + 1 >= len(blob):
            return None
        pos = plus + 1
        first = blob[pos]; pos += 1
        if first < 0x80:
            length = first
        elif first == 0x81 and pos + 1 < len(blob):
            length = int.from_bytes(blob[pos:pos+2], "little"); pos += 2
        elif first == 0x82 and pos + 3 < len(blob):
            length = int.from_bytes(blob[pos:pos+4], "little"); pos += 4
        else:
            return None
        if length <= 0 or pos + length > len(blob):
            return None
        text = blob[pos:pos+length].decode("utf-8", errors="replace")
        # Strip Unicode object-replacement character used as attachment placeholder
        return text.replace("￼", "").strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Message:
    message_id: int
    timestamp: str           # ISO8601 in local tz
    timestamp_utc: str       # ISO8601 in UTC
    chat_id: int
    sender_handle: Optional[str]
    is_from_me: int
    author_label: str
    text: str
    has_attachment: int
    attachment_filenames: list = field(default_factory=list)
    # "message" (default), "tapback", "unsent", or "app"
    kind: str = "message"
    is_edited: int = 0
    # For kind == "tapback":
    reaction: Optional[dict] = None       # {"type": "Loved", "target_message_id": int, "target_text": str, "target_author": str}
    # For kind == "app" (link preview / iMessage app payload with no plain text):
    app_bundle: Optional[str] = None


# Tapback type codes (from associated_message_type).
# 2000-2006 = added; 3000-3006 = removed (display as "removed-Loved" etc.)
TAPBACK_NAMES = {
    2000: "Loved", 2001: "Liked", 2002: "Disliked",
    2003: "Laughed", 2004: "Emphasized", 2005: "Questioned",
    2006: "Sticker",
}
TAPBACK_GLYPHS = {
    "Loved": "♡", "Liked": "👍", "Disliked": "👎",
    "Laughed": "😂", "Emphasized": "‼️", "Questioned": "❓",
    "Sticker": "🩹",
}


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RedactionConfig:
    me_name: str
    extra_names: list[str] = field(default_factory=list)
    redact_phones: bool = True
    redact_emails: bool = True
    redact_urls: bool = True
    case_sensitive: bool = False


def _excel_letters(n: int) -> str:
    """Spreadsheet-column-style letters: 0→A, 25→Z, 26→AA, 27→AB, …, 701→ZZ."""
    if n < 0:
        raise ValueError("_excel_letters requires n >= 0")
    s = ""
    n += 1  # shift to 1-indexed so the math works cleanly
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


class Redactor:
    """Build a deterministic alias→pseudonym map for one conversation.

    Inputs:
      messages : list[Message]    (timeline order, as produced by export())
      metadata : dict             (the metadata dict produced by export())
      contacts : dict[str, str]   (handle → name, as loaded from contacts.csv)
      config   : RedactionConfig

    The map is built once at __init__ time and re-used by the redact_* methods.
    """

    def __init__(self, messages, metadata, contacts, config):
        if not config.me_name:
            raise ValueError("RedactionConfig.me_name must be non-empty")
        self._messages  = messages
        self._metadata  = metadata
        self._contacts  = contacts or {}
        self._config    = config
        self._alias_to_pseudonym: dict[str, str] = {}
        self._pseudonym_to_aliases: dict[str, list[str]] = {}
        self._build_pseudonym_map()

    def _assign_pseudonym(self, alias: str, pseudonym: str) -> None:
        """Map alias → pseudonym. No-op if alias already mapped."""
        if not alias or alias in self._alias_to_pseudonym:
            return
        self._alias_to_pseudonym[alias] = pseudonym
        self._pseudonym_to_aliases.setdefault(pseudonym, []).append(alias)

    def _new_pseudonym(self) -> str:
        n = len(self._pseudonym_to_aliases)
        return f"Person {_excel_letters(n)}"

    def _ensure_person(self, primary_alias: str, *aliases: str) -> str:
        """Get (or create) the pseudonym for primary_alias, registering aliases under it."""
        existing = self._alias_to_pseudonym.get(primary_alias)
        if existing is None:
            existing = self._new_pseudonym()
        self._assign_pseudonym(primary_alias, existing)
        for a in aliases:
            self._assign_pseudonym(a, existing)
        return existing

    def _build_pseudonym_map(self) -> None:
        # 1. Device owner is always Person A.
        self._ensure_person(self._config.me_name)

        # 2. Walk the message timeline assigning new speakers.
        for m in self._messages:
            if m.is_from_me:
                # Outgoing — author is me; nothing new to register.
                continue
            label  = m.author_label
            handle = m.sender_handle
            # Both label and handle (when present) belong to the same person.
            if label:
                self._ensure_person(label, *([handle] if handle else []))
            elif handle:
                self._ensure_person(handle)

        # 3. Register all contact names (even ones not in this conversation —
        #    they may be mentioned in body text from third-party speakers).
        for handle, name in self._contacts.items():
            if name:
                self._ensure_person(name, handle)

        # 4. Register --redact-names-file extras.
        for extra in self._config.extra_names:
            if extra:
                self._ensure_person(extra)

    def pseudonym_map(self) -> dict:
        def _sort_key(item):
            pseudonym = item[0]
            letters = pseudonym.removeprefix("Person ")
            return (len(letters), letters)
        people = [
            {"pseudonym": p, "aliases": list(aliases)}
            for p, aliases in sorted(self._pseudonym_to_aliases.items(), key=_sort_key)
        ]
        return {
            "aliases_to_pseudonym": dict(self._alias_to_pseudonym),
            "people": people,
        }

    # PII regexes. Conservative; documented as best-effort in README.
    # Phone uses a negative lookbehind for word chars so a leading "+" at the
    # start of a token (e.g. "+15551234567" after a space) matches cleanly —
    # \b doesn't sit between a non-word space and the non-word "+".
    _PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\-().]{6,}\d(?!\w)")
    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _URL_RE   = re.compile(r"https?://[^\s<>\"'`]+?(?=[.,;!?)\]\}>]*(?:\s|$))")

    def _ordered_aliases(self) -> list[str]:
        """Aliases ordered longest-first so 'Alice Smith' wins over 'Alice'."""
        return sorted(self._alias_to_pseudonym.keys(), key=len, reverse=True)

    def _redact_text(self, s: str) -> str:
        if not s:
            return s
        out = s
        # Scrub PII first so an alias inside an email local-part
        # ("alice@example.com") doesn't get partially substituted before the
        # email regex can match the whole address.
        if self._config.redact_phones:
            out = self._PHONE_RE.sub("[PHONE]", out)
        if self._config.redact_emails:
            out = self._EMAIL_RE.sub("[EMAIL]", out)
        if self._config.redact_urls:
            out = self._URL_RE.sub("[URL]", out)
        case_sensitive = self._config.case_sensitive
        for alias in self._ordered_aliases():
            pseudonym = self._alias_to_pseudonym[alias]
            if case_sensitive:
                out = out.replace(alias, pseudonym)
            else:
                # Case-insensitive literal replace. Loop so all occurrences fire.
                # We rebuild lowercased indexes each pass since `out` shrinks/grows.
                lower_alias = alias.lower()
                start = 0
                while True:
                    idx = out.lower().find(lower_alias, start)
                    if idx == -1:
                        break
                    out = out[:idx] + pseudonym + out[idx + len(alias):]
                    start = idx + len(pseudonym)
        return out

    def redact_messages(self) -> list[Message]:
        out = []
        for m in self._messages:
            new = copy.deepcopy(m)
            if new.author_label in self._alias_to_pseudonym:
                new.author_label = self._alias_to_pseudonym[new.author_label]
            if new.sender_handle and new.sender_handle in self._alias_to_pseudonym:
                new.sender_handle = self._alias_to_pseudonym[new.sender_handle]
            new.text = self._redact_text(new.text)
            if new.reaction:
                rdict = dict(new.reaction)
                if rdict.get("target_text"):
                    rdict["target_text"] = self._redact_text(rdict["target_text"])
                if rdict.get("target_author") in self._alias_to_pseudonym:
                    rdict["target_author"] = self._alias_to_pseudonym[rdict["target_author"]]
                new.reaction = rdict
            out.append(new)
        return out

    def redact_metadata(self) -> dict:
        out = copy.deepcopy(self._metadata)
        for p in out.get("participants", []):
            for key in ("handle", "resolved_name"):
                v = p.get(key)
                if v and v in self._alias_to_pseudonym:
                    p[key] = self._alias_to_pseudonym[v]
        # Chat headers carry the raw chat_identifier (a phone/email for 1:1s)
        # and an optional display_name (a free-text group name). Run both
        # through _redact_text so participant handles + PII regexes scrub
        # them — otherwise the redacted JSON metadata leaks the real handle.
        for c in out.get("chats", []):
            for key in ("chat_identifier", "display_name"):
                v = c.get(key)
                if v:
                    c[key] = self._redact_text(v)
        # me_name in metadata stays as the original label so the AI-ready header
        # accurately describes who "Person A" is in the redacted view.
        if out.get("me_name") in self._alias_to_pseudonym:
            out["me_name"] = self._alias_to_pseudonym[out["me_name"]]
        return out

    def chat_label(self) -> str:
        # 1:1 → the other participant's pseudonym.
        # Group → fall back to the existing chat_label() applied to redacted metadata.
        red_md = self.redact_metadata()
        return chat_label(red_md)


# Token patterns + stopwords for --suggest-names.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b")
_SUGGEST_STOPWORDS = {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
    "The", "This", "That", "What", "Who", "Why", "How", "When", "Where",
    "I", "My", "Me", "We", "Our", "Us", "He", "She", "It", "They", "Them",
    "But", "And", "So", "Or", "If", "Yes", "No", "OK", "Okay", "Just",
    "Hi", "Hey", "Hello", "Thanks", "Thank", "Sorry",
}


def suggest_names(messages: list[Message], contacts: dict[str, str]) -> int:
    """Print proper-noun candidates not already in `contacts`.

    Output format: comment-prefixed lines for context, one candidate per line.
    User redirects to a file, deletes false positives, passes via
    --redact-names-file.
    """
    known = {name.lower() for name in contacts.values() if name}
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}

    for m in messages:
        if not m.text:
            continue
        for match in _PROPER_NOUN_RE.finditer(m.text):
            tok = match.group(0)
            if tok in _SUGGEST_STOPWORDS:
                continue
            if tok.lower() in known:
                continue
            counts[tok] = counts.get(tok, 0) + 1
            if tok not in samples:
                start = max(0, match.start() - 60)
                end   = min(len(m.text), match.end() + 60)
                samples[tok] = m.text[start:end].replace("\n", " ").strip()

    # Drop singletons.
    counts = {k: v for k, v in counts.items() if v >= 2}

    print("# Proper-noun candidates not in contacts.csv.")
    print("# Review and remove false positives, then pass via --redact-names-file.")
    print("")
    for tok in sorted(counts, key=lambda t: (-counts[t], t)):
        print(f"# {counts[tok]}× — {samples[tok]!r}")
        print(tok)
    return 0


def classify_tapback(amt: int) -> Optional[tuple[str, bool]]:
    """Return (name, removed) or None for non-tapbacks."""
    if 2000 <= amt <= 2006:
        return (TAPBACK_NAMES.get(amt, f"type{amt}"), False)
    if 3000 <= amt <= 3006:
        return (TAPBACK_NAMES.get(amt - 1000, f"type{amt}"), True)
    return None


def strip_target_guid(assoc_guid: str) -> Optional[str]:
    """associated_message_guid is `p:N/<GUID>` or `bp:<GUID>`. Return bare <GUID>."""
    if not assoc_guid:
        return None
    if "/" in assoc_guid:
        return assoc_guid.rsplit("/", 1)[-1]
    if assoc_guid.startswith("bp:"):
        return assoc_guid[3:]
    return assoc_guid


# ---------------------------------------------------------------------------
# DB open + introspection
# ---------------------------------------------------------------------------

def open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"chat.db not found at {path}")
    uri = f"file:{path}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        raise SystemExit(
            f"Cannot open {path}: {e}\n"
            "If this says 'authorization denied', grant Full Disk Access to your "
            "terminal app in System Settings ▸ Privacy & Security ▸ Full Disk Access."
        )
    conn.execute("PRAGMA query_only = ON")
    # Belt and suspenders: confirm read-only guards took. If a future refactor
    # drops the PRAGMA or opens the file writable, fail loudly instead of
    # silently shipping a write-capable connection.
    qo = conn.execute("PRAGMA query_only").fetchone()[0]
    if not qo:
        raise SystemExit("Refusing to proceed: PRAGMA query_only did not take.")
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


# ---------------------------------------------------------------------------
# Chat discovery
# ---------------------------------------------------------------------------

def list_recent_chats(conn, limit: int = 30):
    unit = detect_date_unit(conn)
    sql = """
      SELECT c.ROWID                 AS chat_id,
             c.guid                  AS chat_guid,
             c.chat_identifier       AS chat_identifier,
             c.display_name          AS display_name,
             c.style                 AS style,
             MAX(m.date)             AS last_date,
             COUNT(m.ROWID)          AS msg_count,
             GROUP_CONCAT(DISTINCT h.id) AS participants
        FROM chat c
        LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        LEFT JOIN message m            ON m.ROWID    = cmj.message_id
        LEFT JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
        LEFT JOIN handle h             ON h.ROWID    = chj.handle_id
       GROUP BY c.ROWID
       ORDER BY last_date DESC
       LIMIT ?
    """
    out = []
    for r in conn.execute(sql, (limit,)).fetchall():
        last = apple_to_utc_datetime(r["last_date"], unit) if r["last_date"] else None
        out.append({
            "chat_id": r["chat_id"],
            "chat_identifier": r["chat_identifier"],
            "display_name": r["display_name"],
            "style": "group" if r["style"] == 43 else "1:1",
            "last_message_local": last.astimezone().strftime("%Y-%m-%d %H:%M") if last else "",
            "msg_count": r["msg_count"],
            "participants": r["participants"] or "",
        })
    return out


def resolve_chat_ids(conn, *,
                     chat_id: Optional[int],
                     chat_identifier: Optional[str],
                     participant: Optional[str]) -> list[int]:
    if chat_id is not None:
        return [chat_id]
    if chat_identifier:
        rows = conn.execute(
            "SELECT ROWID FROM chat WHERE chat_identifier = ? OR guid = ?",
            (chat_identifier, chat_identifier),
        ).fetchall()
        return [r["ROWID"] for r in rows]
    if participant:
        like = f"%{participant}%"
        rows = conn.execute(
            """SELECT DISTINCT c.ROWID
                 FROM chat c
                 JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
                 JOIN handle h            ON h.ROWID = chj.handle_id
                WHERE h.id LIKE ? OR c.display_name LIKE ? OR c.chat_identifier LIKE ?""",
            (like, like, like),
        ).fetchall()
        return [r["ROWID"] for r in rows]
    return []


def chat_participants(conn, chat_id: int):
    rows = conn.execute(
        """SELECT h.id AS handle, h.service AS service
             FROM chat_handle_join chj
             JOIN handle h ON h.ROWID = chj.handle_id
            WHERE chj.chat_id = ?""",
        (chat_id,),
    ).fetchall()
    return [{"handle": r["handle"], "service": r["service"]} for r in rows]


def chat_info(conn, chat_id: int) -> dict:
    r = conn.execute(
        "SELECT display_name, style, chat_identifier FROM chat WHERE ROWID = ?",
        (chat_id,),
    ).fetchone()
    if not r:
        return {"display_name": "", "style": 0, "chat_identifier": "", "is_group": False}
    return {
        "display_name": r["display_name"] or "",
        "style": r["style"],
        "chat_identifier": r["chat_identifier"] or "",
        "is_group": r["style"] == 43,
    }


def list_contacts_csv(conn, unit: str, existing: Optional[Path]) -> int:
    """Print one row per distinct handle in chat.db: `handle,name`.

    Pre-fills `name` from an existing contacts CSV when one is provided so the
    user can re-run after editing and only see fresh handles. Sorted by most
    recent message first so the people you actually talk to come out on top.
    """
    known = load_contacts(existing) if existing else {}
    rows = conn.execute(
        """SELECT h.id                AS handle,
                  MAX(m.date)         AS last_date,
                  COUNT(m.ROWID)      AS msg_count
             FROM handle h
             LEFT JOIN message m ON m.handle_id = h.ROWID
            GROUP BY h.id
            ORDER BY last_date DESC NULLS LAST"""
    ).fetchall()
    print("# handle,name  — paste into contacts.csv and fill in the blank names.")
    print(f"# {len(rows)} distinct handles, ordered by last-seen desc.")
    print("handle,name")
    for r in rows:
        h = r["handle"] or ""
        name = known.get(normalize_handle(h), "")
        # CSV-quote the name if it contains a comma or quote
        if "," in name or '"' in name:
            name = '"' + name.replace('"', '""') + '"'
        print(f"{h},{name}")
    return 0


def chat_label(metadata: dict) -> str:
    """Folder-name label for the chat.

    1:1 → the one other participant's resolved name.
    Group with display_name → display_name.
    Group without display_name → sorted resolved participant names joined by '+'.
    """
    chats = metadata.get("chats") or []
    participants = metadata.get("participants") or []
    is_group = any(c.get("is_group") for c in chats)
    if is_group:
        for c in chats:
            if c.get("display_name"):
                return c["display_name"]
        names = sorted({p["resolved_name"] for p in participants if p.get("resolved_name")})
        return "+".join(names) if names else "group"
    return participants[0]["resolved_name"] if participants else "chat"


# ---------------------------------------------------------------------------
# Contacts mapping (handle -> human name)
# ---------------------------------------------------------------------------

def normalize_handle(h: str) -> str:
    if not h:
        return ""
    h = h.strip()
    # Email: lower-case
    if "@" in h:
        return h.lower()
    # Phone: keep digits, prepend '+' if it had one
    digits = re.sub(r"\D", "", h)
    return ("+" + digits) if digits else h


def load_contacts(path: Optional[Path]) -> dict[str, str]:
    if not path:
        return {}
    if not path.exists():
        print(f"WARN: contacts file not found: {path}", file=sys.stderr)
        return {}
    mapping = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        # Accept 'handle,name' OR 'phone_or_email,name'
        for row in reader:
            key = row.get("handle") or row.get("phone_or_email") or row.get("id")
            name = row.get("name") or row.get("display_name")
            if key and name:
                mapping[normalize_handle(key)] = name.strip()
    return mapping


def resolve_author_label(handle: Optional[str],
                         contacts: dict[str, str],
                         is_from_me: int,
                         me_name: str) -> str:
    """Return the human-facing speaker label for a message."""
    if is_from_me:
        return me_name
    if not handle:
        return "Unknown"
    norm = normalize_handle(handle)
    return contacts.get(norm) or contacts.get(handle) or handle


# ---------------------------------------------------------------------------
# Time window resolution
# ---------------------------------------------------------------------------

@dataclass
class TimeWindow:
    apple_start: Optional[int]
    apple_end: Optional[int]            # exclusive upper bound
    local_start: Optional[str]
    local_end: Optional[str]
    utc_start: Optional[str]
    utc_end: Optional[str]
    tz: str
    input: dict


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def parse_time(s: str) -> datetime:
    # Accept HH:MM or HH:MM:SS
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid time {s!r}; expected HH:MM or HH:MM:SS")


def parse_datetime(s: str) -> datetime:
    # Accept 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD HH:MM:SS' / ISO 'YYYY-MM-DDTHH:MM:SS'
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime {s!r}")


def resolve_window(args, unit: str) -> TimeWindow:
    """Resolve the time-window arguments into Apple-epoch bounds.

    Precedence:
      1) --start-datetime / --end-datetime
      2) --date + --start-time / --end-time
      3) --from-date / --to-date (existing day-granularity flags)
    """
    local_tz = datetime.now().astimezone().tzinfo
    tz_name = str(local_tz)
    input_record = {
        "from_date": args.from_date,
        "to_date": args.to_date,
        "date": args.date,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "start_datetime": args.start_datetime,
        "end_datetime": args.end_datetime,
    }

    start_local = end_local = None

    if args.start_datetime or args.end_datetime:
        if args.start_datetime:
            start_local = attach_local_tz(parse_datetime(args.start_datetime))
        if args.end_datetime:
            end_local = attach_local_tz(parse_datetime(args.end_datetime))

    elif args.date and (args.start_time or args.end_time):
        day = parse_date(args.date)
        if args.start_time:
            t = parse_time(args.start_time)
            start_local = attach_local_tz(day.replace(
                hour=t.hour, minute=t.minute, second=t.second))
        if args.end_time:
            t = parse_time(args.end_time)
            end_local = attach_local_tz(day.replace(
                hour=t.hour, minute=t.minute, second=t.second))

    elif args.date:
        # Entire day, local
        day = parse_date(args.date)
        start_local = attach_local_tz(day)
        end_local = attach_local_tz(day + timedelta(days=1))

    else:
        if args.from_date:
            start_local = attach_local_tz(parse_date(args.from_date))
        if args.to_date:
            # to_date is inclusive of that calendar day → upper bound is next day 00:00
            end_local = attach_local_tz(parse_date(args.to_date) + timedelta(days=1))

    apple_start = local_dt_to_apple(start_local, unit) if start_local else None
    apple_end = local_dt_to_apple(end_local, unit) if end_local else None

    return TimeWindow(
        apple_start=apple_start,
        apple_end=apple_end,
        local_start=start_local.strftime("%Y-%m-%d %H:%M:%S") if start_local else None,
        local_end=end_local.strftime("%Y-%m-%d %H:%M:%S") if end_local else None,
        utc_start=start_local.astimezone(timezone.utc).isoformat() if start_local else None,
        utc_end=end_local.astimezone(timezone.utc).isoformat() if end_local else None,
        tz=tz_name,
        input=input_record,
    )


# ---------------------------------------------------------------------------
# Core export
# ---------------------------------------------------------------------------

MESSAGE_SQL_TEMPLATE = """
SELECT m.ROWID                  AS message_id,
       m.guid                   AS message_guid,
       m.date                   AS apple_date,
       m.text                   AS text,
       m.attributedBody         AS attributed_body,
       m.is_from_me             AS is_from_me,
       m.cache_has_attachments  AS has_attachment,
       m.associated_message_type AS amt,
       m.associated_message_guid AS amg,
       m.date_edited            AS date_edited,
       m.date_retracted         AS date_retracted,
       m.balloon_bundle_id      AS balloon_bundle_id,
       h.id                     AS sender_handle,
       cmj.chat_id              AS chat_id
  FROM message m
  LEFT JOIN handle h               ON h.ROWID = m.handle_id
  LEFT JOIN chat_message_join cmj  ON cmj.message_id = m.ROWID
 WHERE cmj.chat_id IN ({placeholders})
   {date_filter}
 ORDER BY m.date ASC
 {limit_clause}
"""


def fetch_attachments(conn, message_ids: list[int]) -> dict[int, list[str]]:
    if not message_ids:
        return {}
    # Chunk to avoid SQLite's variable limit
    out: dict[int, list[str]] = {}
    CHUNK = 500
    for i in range(0, len(message_ids), CHUNK):
        chunk = message_ids[i:i+CHUNK]
        ph = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""SELECT maj.message_id AS mid,
                       a.filename     AS filename,
                       a.transfer_name AS transfer_name
                  FROM message_attachment_join maj
                  JOIN attachment a ON a.ROWID = maj.attachment_id
                 WHERE maj.message_id IN ({ph})""",
            chunk,
        ).fetchall()
        for r in rows:
            name = r["transfer_name"] or (
                Path(r["filename"]).name if r["filename"] else "(attachment)"
            )
            out.setdefault(r["mid"], []).append(name)
    return out


def export(conn,
           chat_ids: list[int],
           contacts: dict[str, str],
           me_name: str,
           window: TimeWindow,
           limit: Optional[int],
           include_attachments: bool,
           unit: str) -> tuple[list[Message], dict]:
    placeholders = ",".join("?" * len(chat_ids))
    date_filter_parts = []
    params: list = list(chat_ids)
    if window.apple_start is not None:
        date_filter_parts.append("AND m.date >= ?")
        params.append(window.apple_start)
    if window.apple_end is not None:
        date_filter_parts.append("AND m.date < ?")
        params.append(window.apple_end)
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    sql = MESSAGE_SQL_TEMPLATE.format(
        placeholders=placeholders,
        date_filter=" ".join(date_filter_parts),
        limit_clause=limit_clause,
    )

    rows = conn.execute(sql, params).fetchall()
    message_ids = [r["message_id"] for r in rows]
    attachments_by_msg = fetch_attachments(conn, message_ids) if include_attachments else {}

    # Pass 1: build Message objects, classify kind, defer tapback target lookup.
    messages: list[Message] = []
    tapback_target_guids: set[str] = set()
    for r in rows:
        text = r["text"]
        if (text is None or text == "") and r["attributed_body"] is not None:
            text = decode_attributed_body(r["attributed_body"])
        text = text or ""
        text = text.replace("￼", "").strip() if text else ""

        ts_utc = apple_to_utc_datetime(r["apple_date"], unit)
        if ts_utc is None:
            continue
        ts_local = ts_utc.astimezone()

        author_label = resolve_author_label(
            r["sender_handle"], contacts, r["is_from_me"], me_name
        )
        attaches = attachments_by_msg.get(r["message_id"], [])
        sender_handle_out = None if r["is_from_me"] else r["sender_handle"]

        # Classify special message kinds.
        kind = "message"
        reaction = None
        app_bundle = None
        tapback = classify_tapback(r["amt"]) if r["amt"] else None
        if tapback:
            name, removed = tapback
            target_guid = strip_target_guid(r["amg"])
            if target_guid:
                tapback_target_guids.add(target_guid)
            kind = "tapback"
            reaction = {
                "type": ("removed-" + name) if removed else name,
                "target_guid": target_guid,
                "target_message_id": None,
                "target_text": None,
                "target_author": None,
            }
        elif r["date_retracted"]:
            kind = "unsent"
            text = ""
        elif not text and not attaches and r["balloon_bundle_id"]:
            kind = "app"
            app_bundle = r["balloon_bundle_id"]

        messages.append(Message(
            message_id=r["message_id"],
            timestamp=ts_local.strftime("%Y-%m-%d %H:%M:%S"),
            timestamp_utc=ts_utc.isoformat(),
            chat_id=r["chat_id"],
            sender_handle=sender_handle_out,
            is_from_me=r["is_from_me"],
            author_label=author_label,
            text=text,
            has_attachment=1 if (r["has_attachment"] or attaches) else 0,
            attachment_filenames=attaches,
            kind=kind,
            is_edited=1 if r["date_edited"] else 0,
            reaction=reaction,
            app_bundle=app_bundle,
        ))

    # Pass 2: resolve tapback targets in a single lookup.
    if tapback_target_guids:
        ph = ",".join("?" * len(tapback_target_guids))
        target_rows = conn.execute(
            f"""SELECT m.guid           AS guid,
                       m.ROWID          AS rowid,
                       m.text           AS text,
                       m.attributedBody AS attributed_body,
                       m.is_from_me     AS is_from_me,
                       h.id             AS sender_handle
                  FROM message m
                  LEFT JOIN handle h ON h.ROWID = m.handle_id
                 WHERE m.guid IN ({ph})""",
            tuple(tapback_target_guids),
        ).fetchall()
        target_by_guid = {}
        for tr in target_rows:
            ttext = tr["text"]
            if (ttext is None or ttext == "") and tr["attributed_body"]:
                ttext = decode_attributed_body(tr["attributed_body"]) or ""
            ttext = (ttext or "").replace("￼", "").strip()
            target_by_guid[tr["guid"]] = {
                "rowid": tr["rowid"],
                "text": ttext,
                "author": resolve_author_label(
                    tr["sender_handle"], contacts, tr["is_from_me"], me_name
                ),
            }
        for m in messages:
            if m.kind == "tapback" and m.reaction and m.reaction["target_guid"]:
                tinfo = target_by_guid.get(m.reaction["target_guid"])
                if tinfo:
                    m.reaction["target_message_id"] = tinfo["rowid"]
                    m.reaction["target_text"] = tinfo["text"]
                    m.reaction["target_author"] = tinfo["author"]

    participants_set = []
    seen = set()
    for cid in chat_ids:
        for p in chat_participants(conn, cid):
            key = (p["handle"] or "").lower()
            if key and key not in seen:
                seen.add(key)
                participants_set.append({
                    "handle": p["handle"],
                    "service": p["service"],
                    "resolved_name": contacts.get(normalize_handle(p["handle"])) or p["handle"],
                })

    actual_first = messages[0].timestamp if messages else None
    actual_last = messages[-1].timestamp if messages else None
    chats_info = [chat_info(conn, cid) for cid in chat_ids]

    metadata = {
        "exported_at": datetime.now().astimezone().isoformat(),
        "me_name": me_name,
        "chat_ids": chat_ids,
        "chats": chats_info,
        "participants": participants_set,
        "message_count": len(messages),
        "actual_first_local": actual_first,
        "actual_last_local": actual_last,
        "window": asdict(window),
        "timestamp_unit_detected": unit,
        "attribution_note": (
            "is_from_me=1 → author_label is the --me-name value. "
            "is_from_me=0 → author_label is resolved from the contacts file by "
            "normalized handle (email lowercased; phone digits with optional '+'). "
            "If unmapped, the raw handle is used. 'Unknown' only appears when the "
            "underlying database has no handle for the row."
        ),
    }
    return messages, metadata


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-")
    return s[:60] or "chat"


def write_csv(path: Path, messages: list[Message]):
    fields = [
        "message_id", "timestamp", "local_date", "timestamp_utc", "chat_id",
        "sender_handle", "is_from_me", "author_label",
        "kind", "is_edited", "reaction_type", "reaction_target",
        "app_bundle",
        "text", "has_attachment", "attachment_filenames",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for m in messages:
            row = asdict(m)
            row["attachment_filenames"] = "|".join(m.attachment_filenames)
            row["local_date"] = m.timestamp[:10] if m.timestamp else ""
            if m.reaction:
                row["reaction_type"] = m.reaction.get("type", "")
                target = m.reaction.get("target_text") or ""
                row["reaction_target"] = (target[:120] + "…") if len(target) > 120 else target
            else:
                row["reaction_type"] = ""
                row["reaction_target"] = ""
            w.writerow(row)


def write_json(path: Path, messages: list[Message], metadata: dict):
    """JSON export. Adds a `gap_seconds_before` field per message (computed
    from successive `timestamp_utc` values). First message has 0. Lets
    downstream consumers mirror the gap markers without re-parsing
    timestamps."""
    msg_dicts = []
    prev_dt = None
    for m in messages:
        d = asdict(m)
        gap = 0
        # Silently fall back to gap=0 on unparseable timestamp_utc — gap
        # markers are a navigation aid, not load-bearing data. Failing the
        # whole export would lose more value than a single wrong gap.
        if m.timestamp_utc:
            try:
                dt = datetime.fromisoformat(m.timestamp_utc.replace("Z", "+00:00"))
                if prev_dt is not None:
                    gap = int((dt - prev_dt).total_seconds())
                prev_dt = dt
            except (ValueError, TypeError):
                pass
        d["gap_seconds_before"] = gap
        msg_dicts.append(d)
    payload = {"metadata": metadata, "messages": msg_dicts}
    with path.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Day / gap rendering helpers (used by .txt, _ai_ready.txt, .md)
# ---------------------------------------------------------------------------

GAP_THRESHOLD_SECONDS = 30 * 60


def format_day_label(dt: datetime) -> str:
    """Human-readable day label, e.g. 'Saturday, June 6, 2026'.

    Uses string concatenation around %A / %B / %Y to avoid relying on
    the %-d strftime token (GNU extension; not portable to Windows libc).
    """
    return dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(", %Y")


def format_gap(seconds: int) -> str:
    """Human label for a silence between two messages.

    '45 min later' / '1h later' / '2h 15min later' / '1 day later' /
    '3 days later'. Negative inputs clamp to 0 (defensive — should never
    happen because messages are read ORDER BY date ASC).
    """
    if seconds < 0:
        seconds = 0
    days = seconds // 86400
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} later"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours >= 1:
        return f"{hours}h later" if minutes == 0 else f"{hours}h {minutes}min later"
    return f"{minutes} min later"


def _parse_local_ts(ts: str) -> Optional[datetime]:
    """Parse the writer-side 'YYYY-MM-DD HH:MM:SS' local timestamp string.
    Returns None for anything that doesn't match — callers fall back to
    yielding the message verbatim with no day/gap framing."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def iter_render_events(messages):
    """Walk messages and emit ('day', dt) / ('gap', seconds) / ('msg', m).

    - 'day' fires every time the calendar date changes, including before
      the very first message.
    - 'gap' fires only when the prior message is on the SAME calendar day
      AND (current - prior) >= GAP_THRESHOLD_SECONDS, so we never emit
      a gap marker right after a day header.
    """
    prev_dt = None
    prev_date = None
    for m in messages:
        dt = _parse_local_ts(m.timestamp)
        if dt is None:
            yield ("msg", m)
            continue
        date = dt.date()
        if date != prev_date:
            yield ("day", dt)
            prev_date = date
        elif prev_dt is not None:
            delta = int((dt - prev_dt).total_seconds())
            if delta >= GAP_THRESHOLD_SECONDS:
                yield ("gap", delta)
        yield ("msg", m)
        prev_dt = dt


def format_message_body(m: Message) -> str:
    """The body portion of a rendered message (without the [time] author: prefix).

    Combines kind-specific labels (tapbacks, unsent, app payloads) with the
    plain text and attachment markers. Used by all writers.
    """
    parts = []
    if m.kind == "tapback" and m.reaction:
        r = m.reaction
        glyph = TAPBACK_GLYPHS.get(r["type"].replace("removed-", ""), "•")
        prefix = "removed " if r["type"].startswith("removed-") else ""
        rtype = r["type"].replace("removed-", "")
        target_text = r.get("target_text") or ""
        target_author = r.get("target_author") or ""
        if target_text:
            snippet = target_text if len(target_text) <= 80 else target_text[:77] + "…"
            target_clause = f' "{snippet}" — {target_author}' if target_author else f' "{snippet}"'
        else:
            target_clause = f" (target message not in window)"
        parts.append(f"[{prefix}{glyph} {rtype}]{target_clause}")
    elif m.kind == "unsent":
        parts.append("[unsent]")
    elif m.kind == "app":
        parts.append(f"[app payload: {m.app_bundle}]")
    elif m.text:
        parts.append(m.text)

    if m.is_edited and m.kind == "message":
        has_attachment_to_render = bool(m.attachment_filenames) or bool(m.has_attachment)
        if parts or has_attachment_to_render:
            parts.insert(0, "[edited]")
        else:
            parts.append("[edited; text not available]")

    if m.attachment_filenames:
        parts.append(f"[Attachments: {', '.join(m.attachment_filenames)}]")
    elif m.has_attachment and not m.text and m.kind == "message":
        parts.append("[Attachment]")
    return " ".join(parts).strip()


def render_txt_message(m: Message, *, time_format: str = "full",
                       indent: str = "    ") -> str:
    """Render one message as one or more plain-text lines.

    time_format:
      'full' → '[YYYY-MM-DD HH:MM:SS] Author: body'
      'time' → '[HH:MM:SS] Author: body'

    Multi-paragraph bodies leave the first line on the speaker line and
    indent each subsequent non-blank paragraph by `indent`. Blank lines
    between paragraphs stay literally blank for visual separation.
    """
    if time_format == "time" and " " in m.timestamp:
        time_part = m.timestamp.split(" ", 1)[1]
    else:
        time_part = m.timestamp
    prefix = f"[{time_part}] {m.author_label}: "
    body = format_message_body(m)
    if "\n" not in body:
        return (prefix + body).rstrip()
    first, *rest = body.split("\n")
    lines = [(prefix + first).rstrip()]
    for line in rest:
        lines.append((indent + line) if line.strip() else "")
    return "\n".join(lines).rstrip()


def _write_txt_event_stream(f, messages, *, time_format: str) -> None:
    """Shared event-stream walker for the two plain-text writers.

    Both `write_txt` and `write_ai_ready` emit the same `── ... ──` day
    headers and gap markers; they differ only in whether each message
    line carries `[YYYY-MM-DD HH:MM:SS]` (AI-ready) or `[HH:MM:SS]`
    (human txt). The markdown writer uses different separator syntax
    and is intentionally NOT consolidated here.
    """
    first = True
    for event in iter_render_events(messages):
        kind = event[0]
        if kind == "day":
            if not first:
                f.write("\n")
            f.write(f"── {format_day_label(event[1])} ──\n\n")
            first = False
        elif kind == "gap":
            f.write(f"\n── {format_gap(event[1])} ──\n\n")
        else:
            f.write(render_txt_message(event[1], time_format=time_format) + "\n")
            first = False


def write_txt(path: Path, messages: list[Message]):
    """Plain-text export with day headers, gap markers, and time-only line
    prefixes. The full date for each line is carried by the day header
    above it."""
    with path.open("w") as f:
        _write_txt_event_stream(f, messages, time_format="time")


def write_ai_ready(path: Path, messages: list[Message], metadata: dict):
    """LLM-fed export. Same day-header / gap-marker / indented-continuation
    conventions as conversation.txt, but EACH MESSAGE LINE keeps the full
    [YYYY-MM-DD HH:MM:SS] prefix so an LLM never has to scan upward for the
    date when reasoning about attribution."""
    parts = metadata["participants"]
    participant_list = ", ".join(
        f"{p['resolved_name']} <{p['handle']}>" for p in parts
    ) or "(none resolved)"
    win = metadata["window"]
    header = [
        "iMessage conversation export — AI-ready",
        f"Participants (excluding 'Me'): {participant_list}",
        f"Me label: {metadata['me_name']}",
        f"Message count: {metadata['message_count']}",
        f"Date range (local): {metadata['actual_first_local']} → {metadata['actual_last_local']}",
        f"Requested window (local, {win['tz']}): {win['local_start']} → {win['local_end']}",
        f"Requested window (UTC): {win['utc_start']} → {win['utc_end']}",
        "Format: [YYYY-MM-DD HH:MM:SS] <Speaker>: <message>",
        "Day headers (── Day, Month D, Year ──) and gap markers "
        "(── X min later ──) are navigation aids inserted by the exporter, "
        "not authored content.",
        "Indented continuation lines (4 spaces) belong to the speaker on "
        "the line above.",
        "-" * 72,
        "",
    ]
    footer = [
        "",
        "-" * 72,
        "All messages above are attributed by exported sender metadata from "
        "iMessage where available. 'Me' = sender is the device owner "
        f"({metadata['me_name']}). Other speakers were resolved from the "
        "Messages handle table and, where provided, a local contacts CSV. "
        "Unmapped handles fall back to phone/email; truly missing handles "
        "appear as 'Unknown'.",
    ]
    with path.open("w") as f:
        f.write("\n".join(header))
        _write_txt_event_stream(f, messages, time_format="full")
        f.write("\n".join(footer) + "\n")


def write_markdown(path: Path, messages: list[Message], metadata: dict):
    """Notion/Obsidian-friendly markdown. Day headers (## Day, Mon D, Year),
    italic gap markers, per-message bold header with TIME-only prefix
    (the day is already in the header above), and an explicit fallback
    when an edited message has nothing else to anchor on (no text, no
    attachment)."""
    parts = metadata["participants"]
    participant_list = ", ".join(
        f"{p['resolved_name']} `<{p['handle']}>`" for p in parts
    ) or "_(none resolved)_"
    win = metadata["window"]
    title = chat_label(metadata)
    lines = [
        f"# iMessage conversation: {title}",
        "",
        f"**Participants** (excluding _Me_): {participant_list}  ",
        f"**Me label:** {metadata['me_name']}  ",
        f"**Messages:** {metadata['message_count']}  ",
        f"**Date range (local):** {metadata['actual_first_local']} → "
        f"{metadata['actual_last_local']}  ",
        f"**Window (local, {win['tz']}):** {win['local_start']} → {win['local_end']}  ",
        f"**Window (UTC):** {win['utc_start']} → {win['utc_end']}",
        "",
        "---",
        "",
    ]
    with path.open("w") as f:
        f.write("\n".join(lines))
        for event in iter_render_events(messages):
            kind = event[0]
            if kind == "day":
                f.write(f"\n## {format_day_label(event[1])}\n\n")
                continue
            if kind == "gap":
                f.write(f"_── {format_gap(event[1])} ──_\n\n")
                continue
            m = event[1]
            time_str = m.timestamp.split(" ", 1)[1] if " " in m.timestamp else m.timestamp
            f.write(f"**{time_str} · {m.author_label}**")
            if m.is_edited and m.kind == "message":
                f.write(" _(edited)_")
            f.write("\n\n")
            if m.kind == "tapback" and m.reaction:
                r = m.reaction
                rtype = r["type"]
                target_text = r.get("target_text") or ""
                target_author = r.get("target_author") or ""
                if target_text:
                    snippet = target_text if len(target_text) <= 120 else target_text[:117] + "…"
                    f.write(f"_{rtype}_ → **{target_author}**: > {snippet}\n\n")
                else:
                    f.write(f"_{rtype}_ (target message not in window)\n\n")
            elif m.kind == "unsent":
                f.write("_(unsent)_\n\n")
            elif m.kind == "app":
                f.write(f"_(app payload: `{m.app_bundle}`)_\n\n")
            elif m.text:
                f.write(m.text.rstrip() + "\n\n")
            elif m.is_edited and not (m.attachment_filenames or m.has_attachment):
                f.write("_(edited; text not available)_\n\n")
            if m.attachment_filenames:
                f.write(f"_Attachments: {', '.join(m.attachment_filenames)}_\n\n")
            elif m.has_attachment and not m.text and m.kind == "message":
                f.write("_(attachment)_\n\n")


ANALYSIS_PROMPT = """\
Analyze this iMessage conversation. Respect speaker attribution exactly as
labeled — each line is prefixed [YYYY-MM-DD HH:MM:SS] <Speaker>: <message>
and the speaker label is authoritative; do not relabel speakers or guess
the author of a line. Identify major themes, emotional shifts, conflict
patterns, communication habits, unanswered bids for connection, and any
notable timeline changes. Quote sparingly and only when a specific phrase
matters; prefer paraphrase. If a message is empty but marked
[Attachment], treat it as a non-text exchange of the labeled speaker.
Timestamps are in the exporter's local timezone as noted in the header.

Day headers (── Saturday, June 6, 2026 ──) and gap markers
(── 57 min later ──) are navigation aids inserted by the exporter — they
are not authored content; ignore them when quoting. An indented line
under a [time] Speaker: line is a continuation paragraph of that same
speaker's preceding message.
"""


def write_prompt(path: Path):
    path.write_text(ANALYSIS_PROMPT)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export one iMessage conversation to AI-ready files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_argument_group("source selection (choose one)")
    src.add_argument("--chat-id", type=int, help="Numeric chat.ROWID")
    src.add_argument("--chat-identifier", help="chat.chat_identifier or chat.guid")
    src.add_argument("--participant", help="Substring match against handle.id, "
                                           "chat.display_name, or chat.chat_identifier")
    src.add_argument("--list", action="store_true", help="List recent chats and exit")
    src.add_argument("--list-limit", type=int, default=30)
    src.add_argument("--list-contacts", action="store_true",
                     help="Print every distinct handle in chat.db as a starter "
                          "contacts.csv (handle,name) and exit. Use to bootstrap "
                          "your contacts.csv.")

    win = p.add_argument_group("time window (local time; all bounds optional)")
    win.add_argument("--from-date", help="YYYY-MM-DD (inclusive)")
    win.add_argument("--to-date", help="YYYY-MM-DD (inclusive)")
    win.add_argument("--date", help="YYYY-MM-DD — single day; combine with --start-time/--end-time")
    win.add_argument("--start-time", help="HH:MM or HH:MM:SS (requires --date)")
    win.add_argument("--end-time", help="HH:MM or HH:MM:SS (requires --date)")
    win.add_argument("--start-datetime", help="YYYY-MM-DD HH:MM[:SS]")
    win.add_argument("--end-datetime", help="YYYY-MM-DD HH:MM[:SS]")

    out = p.add_argument_group("output / formatting")
    out.add_argument("--output-dir", default="./exports", help="Default: ./exports")
    out.add_argument("--me-name", default="Me", help="Label for messages where is_from_me=1")
    out.add_argument("--contacts", help="CSV with columns: handle,name "
                                        "(maps handle.id → human name)")
    out.add_argument("--include-attachments", action="store_true",
                     help="Resolve attachment filenames per message")
    out.add_argument("--limit", type=int, help="Cap number of messages")

    red = p.add_argument_group("redaction / pseudonymization")
    red.add_argument("--redact", action="store_true",
                     help="Also write a parallel set of redacted files "
                          "(conversation_redacted.* + pseudonym_map.json).")
    red.add_argument("--redact-only", action="store_true",
                     help="Write ONLY the redacted set. Folder name uses the "
                          "pseudonymized label + a stable 4-char chat-id hash.")
    red.add_argument("--redact-names-file", default=None,
                     help="Flat text file, one extra name per line. All "
                          "pseudonymized into the same Person X namespace.")
    red.add_argument("--no-redact-phones", action="store_true",
                     help="Disable phone-number scrubbing in body text.")
    red.add_argument("--no-redact-emails", action="store_true",
                     help="Disable email-address scrubbing in body text.")
    red.add_argument("--no-redact-urls", action="store_true",
                     help="Disable URL scrubbing in body text.")
    red.add_argument("--suggest-names", action="store_true",
                     help="Scan the selected window for proper-noun "
                          "candidates and print them. Skips export.")

    p.add_argument("--db", default=str(DEFAULT_DB),
                   help=f"Path to chat.db (default: {DEFAULT_DB})")
    return p


def validate_args(args):
    if args.start_time or args.end_time:
        if not args.date and not (args.start_datetime or args.end_datetime):
            raise SystemExit("--start-time / --end-time require --date")
    if args.date and (args.start_datetime or args.end_datetime):
        raise SystemExit("Use either --date+--start-time/--end-time OR "
                         "--start-datetime/--end-datetime, not both.")
    if args.suggest_names and (args.redact or args.redact_only):
        raise SystemExit("--suggest-names cannot be combined with --redact / --redact-only")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    validate_args(args)
    # Exported message bodies are private. Make new dirs 700 and new files 600
    # by default — protects against group/other read on any filesystem that
    # honors POSIX modes (most local disks; some network shares ignore it).
    os.umask(0o077)

    conn = open_db(Path(args.db))
    try:
        return _run(args, conn)
    finally:
        conn.close()


def _run(args, conn) -> int:
    unit = detect_date_unit(conn)

    if args.list:
        chats = list_recent_chats(conn, args.list_limit)
        if not chats:
            print("(no chats found)")
            return 0
        print(f"{'ID':>5}  {'KIND':<5}  {'LAST':<16}  {'MSGS':>5}  IDENTIFIER / PARTICIPANTS")
        for c in chats:
            who = c["display_name"] or c["participants"] or c["chat_identifier"] or ""
            print(f"{c['chat_id']:>5}  {c['style']:<5}  {c['last_message_local']:<16}  "
                  f"{c['msg_count']:>5}  {c['chat_identifier'] or ''}  |  {who}")
        return 0

    if args.list_contacts:
        return list_contacts_csv(conn, unit, Path(args.contacts) if args.contacts else None)

    if args.suggest_names:
        chat_ids = resolve_chat_ids(
            conn,
            chat_id=args.chat_id,
            chat_identifier=args.chat_identifier,
            participant=args.participant,
        )
        if not chat_ids:
            print("ERROR: no matching chat found.", file=sys.stderr)
            return 1
        contacts = load_contacts(Path(args.contacts)) if args.contacts else {}
        window = resolve_window(args, unit)
        messages, _ = export(
            conn,
            chat_ids=chat_ids,
            contacts=contacts,
            me_name=args.me_name,
            window=window,
            limit=args.limit,
            include_attachments=False,
            unit=unit,
        )
        return suggest_names(messages, contacts)

    if not (args.chat_id or args.chat_identifier or args.participant):
        print("ERROR: choose --chat-id, --chat-identifier, --participant, --list, "
              "or --list-contacts",
              file=sys.stderr)
        return 2

    chat_ids = resolve_chat_ids(
        conn,
        chat_id=args.chat_id,
        chat_identifier=args.chat_identifier,
        participant=args.participant,
    )
    if not chat_ids:
        print("ERROR: no matching chat found.", file=sys.stderr)
        return 1
    if len(chat_ids) > 1 and not args.participant:
        print(f"NOTE: identifier matched {len(chat_ids)} chat rows — exporting all.",
              file=sys.stderr)

    contacts = load_contacts(Path(args.contacts)) if args.contacts else {}
    window = resolve_window(args, unit)

    messages, metadata = export(
        conn,
        chat_ids=chat_ids,
        contacts=contacts,
        me_name=args.me_name,
        window=window,
        limit=args.limit,
        include_attachments=args.include_attachments,
        unit=unit,
    )

    # Build the redactor if asked. Both --redact and --redact-only enable it.
    redactor = None
    red_messages: list[Message] | None = None
    red_metadata: dict | None = None
    if args.redact or args.redact_only:
        extra_names = []
        if args.redact_names_file:
            try:
                with open(args.redact_names_file) as f:
                    extra_names = [ln.strip() for ln in f
                                   if ln.strip() and not ln.lstrip().startswith("#")]
            except OSError as e:
                raise SystemExit(f"Cannot read --redact-names-file {args.redact_names_file}: {e}")
        rcfg = RedactionConfig(
            me_name=args.me_name,
            extra_names=extra_names,
            redact_phones=not args.no_redact_phones,
            redact_emails=not args.no_redact_emails,
            redact_urls=not args.no_redact_urls,
        )
        redactor     = Redactor(messages, metadata, contacts, rcfg)
        red_messages = redactor.redact_messages()
        red_metadata = redactor.redact_metadata()

    # Output directory: exports/<label>/<YYYY-MM-DD>/.
    # Date = window start if a window was given, else the actual first message's
    # date, else today. Re-exporting the same (label, date) overwrites.
    # In --redact-only mode, folder uses the pseudonymized label + a stable
    # 4-char hash of chat_ids so distinct chats don't collide on "Person B".
    # slugify() would mash the space into a dash; for the redacted folder name
    # we bypass it so "Person B-a3f9" stays readable.
    win = metadata["window"]
    date_str = (
        (win.get("local_start") or "")[:10]
        or (metadata.get("actual_first_local") or "")[:10]
        or datetime.now().strftime("%Y-%m-%d")
    )
    if args.redact_only and redactor is not None:
        chat_ids_str = ",".join(str(c) for c in metadata["chat_ids"])
        chash = hashlib.sha1(chat_ids_str.encode()).hexdigest()[:4]
        base = redactor.chat_label()
        # Permit "Person X" verbatim (1:1 chat with one pseudonym). Permit
        # "Person A+Person B+..." (group with joined pseudonyms). Otherwise
        # — e.g., a raw group display_name slipped through — slugify for
        # filesystem safety, and strip leading dots so traversal-style
        # prefixes ("../") can't survive as a leading "..-" segment.
        if not re.match(r"^Person [A-Z]+(\+Person [A-Z]+)*$", base):
            base = slugify(base).lstrip(".")
            if not base:
                base = "chat"
        folder_name = f"{base}-{chash}"
    else:
        folder_name = slugify(chat_label(metadata))
    out_dir = Path(args.output_dir) / folder_name / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write the originals UNLESS in --redact-only mode.
    if not args.redact_only:
        write_csv      (out_dir / "conversation.csv",          messages)
        write_json     (out_dir / "conversation.json",         messages, metadata)
        write_txt      (out_dir / "conversation.txt",          messages)
        write_markdown (out_dir / "conversation.md",           messages, metadata)
        write_ai_ready (out_dir / "conversation_ai_ready.txt", messages, metadata)

    # Write the redacted set if we built a redactor. We keep the "_redacted"
    # suffix even in --redact-only mode so the file names always signal that
    # the contents have been pseudonymized.
    if redactor is not None:
        write_csv      (out_dir / "conversation_redacted.csv",          red_messages)
        write_json     (out_dir / "conversation_redacted.json",         red_messages, red_metadata)
        write_txt      (out_dir / "conversation_redacted.txt",          red_messages)
        write_markdown (out_dir / "conversation_redacted.md",           red_messages, red_metadata)
        write_ai_ready (out_dir / "conversation_redacted_ai_ready.txt", red_messages, red_metadata)
        with open(out_dir / "pseudonym_map.json", "w") as f:
            json.dump(redactor.pseudonym_map(), f, indent=2, ensure_ascii=False)

    write_prompt(out_dir / "analysis_prompt.txt")

    print(f"Exported {len(messages)} messages → {out_dir}")
    if win["local_start"] or win["local_end"]:
        print(f"Window (local, {win['tz']}): {win['local_start']} → {win['local_end']}")
        print(f"Window (UTC):                 {win['utc_start']} → {win['utc_end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
