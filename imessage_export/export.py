"""Core export pipeline: SQL → `Message` list + metadata dict.

Two-pass over the `message` table:
  1. Build `Message` objects, classifying tapback / unsent / app-payload
     rows; collect the GUIDs of tapback targets so we can resolve them.
  2. Fetch the target messages in one batch and back-fill
     `Message.reaction["target_text"]` / `target_author`.

Attachment filenames are fetched separately (lazy: only when
`include_attachments=True`).

`export()` returns `(messages, metadata)` — every writer downstream
takes that pair.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .contacts import normalize_handle, resolve_author_label
from .db import chat_info, chat_participants
from .decoder import classify_tapback, decode_attributed_body, strip_target_guid
from .models import Message
from .timestamps import apple_to_utc_datetime
from .window import TimeWindow


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
