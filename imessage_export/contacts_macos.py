"""Read macOS Contacts.app via osascript and write a handle,name CSV.

Triggers a one-time TCC permission prompt the first time it runs.
After the user clicks 'OK', subsequent runs are silent.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# AppleScript that walks every person and yields their phones + emails.
# Output format: one record per line, "value\tname".
_OSASCRIPT = r"""
tell application "Contacts"
    set output to ""
    repeat with p in (every person)
        set fullName to ""
        try
            set fullName to name of p
        end try
        if fullName is missing value then set fullName to ""
        try
            repeat with ph in (every phone of p)
                try
                    set v to value of ph
                    if v is not missing value then
                        set output to output & v & tab & fullName & linefeed
                    end if
                end try
            end repeat
        end try
        try
            repeat with em in (every email of p)
                try
                    set v to value of em
                    if v is not missing value then
                        set output to output & v & tab & fullName & linefeed
                    end if
                end try
            end repeat
        end try
    end repeat
    return output
end tell
"""


def fetch_contacts() -> list[tuple[str, str]]:
    """Return [(handle, name), ...]. Raises RuntimeError on osascript failure."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _OSASCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError("osascript not found — this command only works on macOS.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Reading Contacts timed out after 60s.")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not allowed assistive access" in stderr or "Not authorized" in stderr:
            raise RuntimeError(
                "Contacts access denied. Grant in System Settings ▸ "
                "Privacy & Security ▸ Contacts, then re-run."
            )
        raise RuntimeError(f"osascript failed: {stderr or result.stdout}")

    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        handle, name = line.split("\t", 1)
        rows.append((handle.strip(), name.strip()))
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
    skipped = 0
    for raw_handle, name in rows:
        if not raw_handle or not name:
            skipped += 1
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
    """End-to-end: fetch from Contacts.app, write CSV. Returns process exit code."""
    print(f"Reading macOS Contacts… (first run may prompt for permission)", file=sys.stderr)
    try:
        rows = fetch_contacts()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not rows:
        print("No contacts found (Contacts.app appears empty).", file=sys.stderr)
        return 1

    count = write_csv(rows, output_path)
    print(f"Wrote {count} unique handles → {output_path}", file=sys.stderr)
    return 0
