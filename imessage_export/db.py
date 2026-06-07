"""DB open + introspection + chat discovery.

Read-only access to `chat.db`. `open_db` enforces `mode=ro&immutable=1`
plus `PRAGMA query_only=ON` (with a belt-and-suspenders check that the
PRAGMA actually took). The rest of this module is small SQL helpers
for chat discovery (`list_recent_chats`, `resolve_chat_ids`,
`chat_participants`, `chat_info`) and a `chat_label` helper that
collapses metadata into a single folder-name label.

Note: `list_contacts_csv` lives here because it queries `handle` /
`message` directly, even though it imports `normalize_handle` /
`load_contacts` from `.contacts`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .contacts import load_contacts, normalize_handle
from .timestamps import apple_to_utc_datetime, detect_date_unit


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
