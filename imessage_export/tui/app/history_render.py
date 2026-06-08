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


@dataclass(slots=True)
class _ChunkRender:
    """One mounted chunk's cached render state.

    `widget` is filled in by HistoryView after mount — `build` returns
    the dataclass with `widget=None` and the caller stitches it on.
    Everything else is fully precomputed so paint() never needs to
    re-walk the messages.
    """
    msg_ids: list[int]
    base: Text                                  # unstyled blob — build-once
    row_offsets: dict[int, tuple[int, int]]     # (start, end) byte offsets in base.plain
    row_line_counts: dict[int, int]              # rendered line count per msg_id
    day_header_prefix_count: list[int]           # day-header lines before msg_ids[i]
    widget: object | None = None                 # Static; injected after mount

    @classmethod
    def build(cls, messages: Iterable, contacts: dict) -> "_ChunkRender":
        """Run format_row over every message in one pass and assemble
        the unstyled blob + offset/line-count/day-header tables.

        Day-header rows are emitted at the top and at every calendar
        boundary, styled `bold cyan` (same as the legacy _build_blob).
        They contribute to the base text but not to `row_offsets` —
        clicks on a header line carry no msg_id meta, which matches
        today's behavior.
        """
        msgs = list(messages)
        base = Text()
        msg_ids: list[int] = []
        row_offsets: dict[int, tuple[int, int]] = {}
        row_line_counts: dict[int, int] = {}
        day_header_prefix_count: list[int] = []

        last_date: str | None = None
        headers_so_far = 0
        for m in msgs:
            day = m.timestamp[:10]
            if day != last_date:
                if last_date is not None:
                    base.append("\n")
                dt = datetime.strptime(day, "%Y-%m-%d")
                base.append(
                    f"── {dt.strftime('%A, %B %-d, %Y')} ──\n",
                    style=_parse_style("bold cyan"),
                )
                headers_so_far += 1
                last_date = day
            day_header_prefix_count.append(headers_so_far)
            row_start = len(base.plain)
            segments, line_count = format_row(m, contacts)
            for text, style in segments:
                base.append(text, style=style)
            row_end = len(base.plain)
            row_offsets[m.message_id] = (row_start, row_end)
            row_line_counts[m.message_id] = line_count
            msg_ids.append(m.message_id)

        return cls(
            msg_ids=msg_ids,
            base=base,
            row_offsets=row_offsets,
            row_line_counts=row_line_counts,
            day_header_prefix_count=day_header_prefix_count,
        )


@dataclass(slots=True, frozen=True)
class SelectionColors:
    """Hex codes consumed by `paint`. Empty strings mean "skip this
    layer" — keeps `paint` defensive against partial palettes.

    The cursor bar has three context-dependent colors because the
    default `accent_alt` would be invisible against an endpoint row
    (already `accent_alt` bg). On an in-range row (already `accent` bg)
    the bar reverts to `accent_alt`.
    """
    endpoint_bg: str
    range_bg: str
    cursor_tint_bg: str            # B — subtle row tint
    cursor_bar_default: str        # D — bar color on unselected rows
    cursor_bar_on_endpoint: str    # D — bar color over an endpoint bg
    cursor_bar_on_in_range: str    # D — bar color over an in-range bg
    contrast_fg: str               # text fg used on selection-bg rows


def selection_colors(palette: dict) -> SelectionColors:
    """Read the five palette keys we need and pack into SelectionColors."""
    accent = palette.get("accent", "")
    accent_alt = palette.get("accent_alt", "")
    return SelectionColors(
        endpoint_bg=accent_alt,
        range_bg=accent,
        cursor_tint_bg=palette.get("bg_alt", ""),
        cursor_bar_default=accent_alt,
        cursor_bar_on_endpoint=accent,
        cursor_bar_on_in_range=accent_alt,
        contrast_fg=palette.get("bg", ""),
    )
