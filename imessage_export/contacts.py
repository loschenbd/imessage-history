"""Contacts mapping: handle (phone/email) → human name.

`normalize_handle` produces the canonical form used as the dict key in
`contacts.csv` (emails lower-cased; phones reduced to digits with an
optional leading `+`). `load_contacts` reads the CSV. `resolve_author_label`
turns a `handle.id` value into the speaker label rendered in every export.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Optional


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
