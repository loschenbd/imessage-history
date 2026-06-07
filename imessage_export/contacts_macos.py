"""Read macOS Contacts from the AddressBook SQLite stores and write a
handle,name CSV.

macOS keeps each Contacts source (iCloud, on-this-Mac, Google, Exchange...)
in its own SQLite file at:
    ~/Library/Application Support/AddressBook/Sources/<UUID>/AddressBook-v22.abcddb

The directory is FDA-protected, so the user's existing Full Disk Access
grant (the one that lets us read chat.db) covers this too. Read-only mode
is enforced the same way: `mode=ro&immutable=1` URI flags.

The previous AppleScript-based reader timed out on ~2000-contact address
books because every property access is an Apple Event roundtrip. Direct
SQLite reads finish in milliseconds.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

ADDRESSBOOK_DIR = (
    Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources"
)


def _addressbook_dbs() -> list[Path]:
    """Return every AddressBook-v22.abcddb under the user's Sources folder."""
    if not ADDRESSBOOK_DIR.exists():
        return []
    return sorted(ADDRESSBOOK_DIR.glob("*/AddressBook-v22.abcddb"))


def _open_ro(path: Path) -> sqlite3.Connection:
    """Open a SQLite file read-only with the same hardening we use for chat.db."""
    uri = f"file:{path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _person_name(first: str | None, last: str | None,
                 organization: str | None, nickname: str | None) -> str:
    """Compose a display name from the available ZABCDRECORD columns."""
    first = (first or "").strip()
    last = (last or "").strip()
    full = (first + " " + last).strip()
    if full:
        return full
    if nickname:
        return nickname.strip()
    if organization:
        return organization.strip()
    return ""


def fetch_contacts() -> list[tuple[str, str]]:
    """Return [(handle, name), ...] from every AddressBook source. Each phone /
    email yields one row; the writer dedupes on normalized handle later.

    Raises RuntimeError if no AddressBook stores are accessible (usually FDA).
    """
    dbs = _addressbook_dbs()
    if not dbs:
        raise RuntimeError(
            f"No AddressBook stores found under {ADDRESSBOOK_DIR}. "
            "Open Contacts.app once and ensure your terminal has Full Disk Access."
        )

    rows: list[tuple[str, str]] = []
    errors: list[str] = []

    for db in dbs:
        try:
            conn = _open_ro(db)
        except sqlite3.OperationalError as e:
            # Most common cause: terminal lacks Full Disk Access.
            errors.append(f"{db.parent.name[:8]}: {e}")
            continue
        try:
            try:
                phone_rows = conn.execute("""
                    select
                        p.ZFULLNUMBER,
                        r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, r.ZNICKNAME
                    from ZABCDPHONENUMBER p
                    join ZABCDRECORD r on p.ZOWNER = r.Z_PK
                    where p.ZFULLNUMBER is not null
                """).fetchall()
            except sqlite3.OperationalError:
                phone_rows = []  # source has the table missing — skip cleanly
            try:
                email_rows = conn.execute("""
                    select
                        e.ZADDRESS,
                        r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, r.ZNICKNAME
                    from ZABCDEMAILADDRESS e
                    join ZABCDRECORD r on e.ZOWNER = r.Z_PK
                    where e.ZADDRESS is not null
                """).fetchall()
            except sqlite3.OperationalError:
                email_rows = []
        finally:
            conn.close()

        for handle, first, last, org, nick in phone_rows + email_rows:
            name = _person_name(first, last, org, nick)
            if name:
                rows.append((handle, name))

    if not rows and errors:
        raise RuntimeError(
            "Could not read any AddressBook stores. Errors:\n  "
            + "\n  ".join(errors)
            + "\n\nMost likely cause: Full Disk Access not granted for this "
              "terminal. System Settings ▸ Privacy & Security ▸ Full Disk "
              "Access ▸ add your terminal app, then re-run."
        )

    return rows


def _normalize_phone(s: str) -> str:
    """Strip spaces, dashes, parens, dots. Keep a leading +."""
    s = s.strip()
    if not s:
        return s
    keep_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    return ("+" + digits) if keep_plus else digits


def _normalize_handle(raw: str) -> str:
    """Phones → digits with optional +. Emails → lowercased."""
    if "@" in raw:
        return raw.lower().strip()
    return _normalize_phone(raw)


def write_csv(rows: Iterable[tuple[str, str]], path: Path) -> int:
    """Dedup, normalize, write `handle,name` CSV. Returns count written."""
    seen: dict[str, str] = {}
    for raw_handle, name in rows:
        if not raw_handle or not name:
            continue
        h = _normalize_handle(raw_handle)
        if not h or h in seen:
            continue
        seen[h] = name

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["handle", "name"])
        for handle, name in sorted(seen.items(), key=lambda kv: kv[1].lower()):
            w.writerow([handle, name])
    return len(seen)


def build_contacts_csv(output_path: Path) -> int:
    """End-to-end: fetch from AddressBook stores, write CSV. Returns exit code."""
    print("Reading macOS Contacts (AddressBook SQLite)…", file=sys.stderr)
    try:
        rows = fetch_contacts()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not rows:
        print("No contacts found in any AddressBook store.", file=sys.stderr)
        return 1

    count = write_csv(rows, output_path)
    print(f"Wrote {count} unique handles → {output_path}", file=sys.stderr)
    return 0
