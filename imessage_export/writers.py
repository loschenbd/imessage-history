"""Writers — one function per output format.

Each writer takes `(path, messages, metadata)` (or `(path, messages)` for
formats that don't need the metadata block) and writes its file from
scratch. Writers share no mutable state; re-running an export
overwrites the files.

The day / gap rendering helpers (`iter_render_events`,
`render_txt_message`, `_write_txt_event_stream`, etc.) are consumed by
the two plain-text writers (`write_txt`, `write_ai_ready`) and the
markdown writer. The markdown writer uses different separator syntax
and walks `iter_render_events` directly rather than re-using the txt
event stream.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import chat_label
from .models import Message, TAPBACK_GLYPHS


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
