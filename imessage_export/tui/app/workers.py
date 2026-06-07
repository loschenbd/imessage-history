"""Worker functions for the Textual app.

These are plain synchronous functions. The Textual layer wraps them with
`@work(thread=True, exclusive=True)` so the UI stays responsive.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ...export import export as _export
from ...models import Message
from ...timestamps import detect_date_unit
from ...window import TimeWindow


def _everything_window() -> TimeWindow:
    """A TimeWindow with no bounds — matches every message in the chat."""
    return TimeWindow(
        apple_start=None,
        apple_end=None,
        local_start=None,
        local_end=None,
        utc_start=None,
        utc_end=None,
        tz="local",
        input={"mode": "all"},
    )


def load_chat_messages(
    conn: sqlite3.Connection,
    *,
    chat_id: int,
    contacts: dict,
    me_name: str,
    unit: Optional[str] = None,
) -> list[Message]:
    """Fetch every message for `chat_id`, in timestamp-ascending order.

    Thin wrapper over `export.export()` with a no-bounds TimeWindow. The
    returned metadata is discarded — the app builds its own header from
    `db.chat_info`.
    """
    if unit is None:
        unit = detect_date_unit(conn)
    messages, _metadata = _export(
        conn,
        chat_ids=[chat_id],
        contacts=contacts,
        me_name=me_name,
        window=_everything_window(),
        limit=None,
        include_attachments=False,
        unit=unit,
    )
    return messages
