"""Pure rendering helpers for HistoryView.

This module owns every format / cache / style decision that doesn't
need access to a Textual app or to mutate a widget. The three units
are:

  * `format_row` — one message in, list of (text, Style) segments out.
  * `_ChunkRender` — one mounted chunk's cached state (unstyled blob,
    per-row byte offsets, per-row line counts, day-header prefix count).
  * `paint` — clone a chunk's cached `base` Text and layer the current
    cursor + selection spans on top.

All inputs are plain dicts and message dataclasses; all outputs are
Rich `Text` or simple dataclasses. No Textual import here — the units
are easy to unit-test without an `App.run_test()` harness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from rich.style import Style
from rich.text import Text


# Wrapped body lines indent 12 cols so they line up under the message
# body (2 cols gutter + 10 cols ≈ "[12:34 pm] "). Mirrors HistoryView's
# old _WRAP_INDENT — kept here so the formatter is self-contained.
_WRAP_INDENT = " " * 12
_GUTTER_BLANK = "  "  # always 2 spaces — cursor is style only, never content


# Per-module cache for Style.parse — formatter is called once per
# message per render, so memoizing the ~5 distinct style specs saves
# repeated parse work on cache builds.
_STYLE_CACHE: dict[str, Style] = {}


def _parse_style(spec: str) -> Style:
    cached = _STYLE_CACHE.get(spec)
    if cached is None:
        cached = Style.parse(spec) if spec else Style()
        _STYLE_CACHE[spec] = cached
    return cached


def _format_time_12h(ts: str) -> str:
    hh = ts[11:13] if len(ts) >= 19 else ts[:2]
    mm = ts[14:16] if len(ts) >= 19 else ts[3:5]
    h = int(hh)
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{mm} {period}"


def format_row(message, contacts: dict) -> tuple[list[tuple[str, Style]], int]:
    """Render one message to (segments, rendered_line_count).

    Segments: [gutter, ts, speaker, body, newline] — five entries
    every time, even when body is empty. Each segment's style carries
    `meta={"msg_id": message.message_id}` so any click anywhere on the
    line routes back to its message id.

    `rendered_line_count` is `1 + body.count("\n")` after the wrap
    substitution — used by `_ChunkRender` to compute row y-offsets
    without re-measuring the rendered text.

    `contacts` is accepted for forward-compat (future: speaker-name
    swap-in based on handle); currently unused.
    """
    line_meta = Style(meta={"msg_id": message.message_id})
    ts_str = _format_time_12h(message.timestamp)
    speaker = message.author_label or ""
    body = (message.text or "").replace("\n", "\n" + _WRAP_INDENT)
    line_count = 1 + body.count("\n")
    return (
        [
            (_GUTTER_BLANK, line_meta + _parse_style("")),
            (f"[{ts_str}] ", line_meta + _parse_style("dim")),
            (f"{speaker}: ", line_meta + _parse_style("bold")),
            (body, line_meta + _parse_style("")),
            ("\n", line_meta + _parse_style("")),
        ],
        line_count,
    )
