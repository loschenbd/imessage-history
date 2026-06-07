"""Apple timestamp helpers.

macOS Messages stores `message.date` as either nanoseconds (modern) or
seconds (pre-10.13) since 2001-01-01 UTC. These helpers detect the unit
and convert between Apple-epoch integers and Python `datetime`s.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


# Apple's epoch: 2001-01-01 00:00:00 UTC = Unix 978307200
APPLE_EPOCH_UNIX = 978307200


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
