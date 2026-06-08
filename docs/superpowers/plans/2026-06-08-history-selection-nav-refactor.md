# HistoryView Selection + Nav Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `HistoryView` so click→repaint and cursor-move repaint complete in <5 ms on a 4k-message chat, the cursor row is unmissable (B+D visual scheme), arrow nav keeps the cursor in view with ≥2-row margin, and Shift+arrow extends a keyboard selection from a fixed anchor.

**Architecture:** Extract three pure units into a new `tui/app/history_render.py` module — `RowFormatter`, `_ChunkRender`, and `SelectionPainter`. Each mounted chunk caches its unstyled blob, byte-offsets per row, and line-count per row at build time. State changes layer Rich `Text` spans on a clone of the cached blob instead of rebuilding it. `HistoryView` shrinks to lifecycle + input wiring; every §5c invariant from `textual-app-scrollable-data-pitfalls` (`parent is self`, edge-triggered `watch_scroll_y`, `_topmost_widget`, anchor to prev_top) stays intact.

**Tech Stack:** Python 3.10+, Textual ~5.x, Rich, `unittest` (stdlib). No new dependencies.

---

## File Structure

| File | Status | Purpose |
|---|---|---|
| `imessage_export/tui/app/history_render.py` | **CREATE** | `format_row`, `_ChunkRender`, `selection_colors`, `MarkState`, `paint`. Pure, no Textual `App` access, no widget mutation. |
| `imessage_export/tui/app/widgets.py` | MODIFY | `HistoryView` shrinks: remove `_build_blob`/`_rerender_chunks`/`_selection_colors`; add `_id_to_index`/`_chunks`/`_repaint_for_ids`; new bindings (Shift+arrow, Home/End/PgUp/PgDn); cursor-follow scroll. |
| `imessage_export/tui/app/modals.py` | MODIFY | `HelpModal` docs the new bindings + the B+D cursor explanation. |
| `tests/test_history_render.py` | **CREATE** | Layer 1 pure unit tests for the three new units. |
| `tests/test_history_view_perf.py` | **CREATE** | Layer 3 perf budget pin (<2 ms per `_repaint_for_ids` call on a 4k-message chat). |
| `tests/test_history_view_cursor.py` | MODIFY | Replace `"▸"` gutter assertions with span-based checks; add Shift+arrow, Home/End/PgUp/PgDn cases. |
| `tests/test_history_view_range_marks.py` | MODIFY | Replace `"▸"` assertions with span-based checks; pin the new chunk-skip path. |

---

## Task 1: `format_row` — pure per-message formatter

**Files:**
- Create: `imessage_export/tui/app/history_render.py`
- Create: `tests/test_history_render.py`

- [ ] **Step 1: Write the failing tests**

Write to `tests/test_history_render.py`:

```python
"""Layer 1 pure unit tests for history_render module.

These tests run without a Textual app — they exercise the formatting,
caching, and span-overlay logic in isolation. Fast (< 50 ms total)
and catch regressions in pure-function semantics before the
HistoryView integration tests run.
"""
from __future__ import annotations

import unittest

from imessage_export.models import Message
from imessage_export.tui.app import history_render


def _msg(message_id: int, *, ts="2026-01-01 09:00:00", text="hello",
         speaker="Me", kind="message") -> Message:
    return Message(
        message_id=message_id,
        timestamp=ts,
        timestamp_utc=ts.replace(" ", "T") + "+00:00",
        chat_id=1,
        sender_handle=None,
        is_from_me=1,
        author_label=speaker,
        text=text,
        has_attachment=0,
        attachment_filenames=[],
        kind=kind,
        is_edited=0,
        reaction=None,
        app_bundle=None,
    )


class TestFormatRow(unittest.TestCase):

    def test_single_line_body(self):
        segments, line_count = history_render.format_row(_msg(1, text="hi"), {})
        # Segments: [gutter, ts, speaker, body, newline]
        self.assertEqual(len(segments), 5)
        gutter_text, _ = segments[0]
        self.assertEqual(gutter_text, "  ")  # always 2 spaces — no ▸ content
        ts_text, _ = segments[1]
        self.assertEqual(ts_text, "[9:00 AM] ")
        speaker_text, _ = segments[2]
        self.assertEqual(speaker_text, "Me: ")
        body_text, _ = segments[3]
        self.assertEqual(body_text, "hi")
        nl_text, _ = segments[4]
        self.assertEqual(nl_text, "\n")
        self.assertEqual(line_count, 1)

    def test_multi_line_body_wraps_with_12_col_indent(self):
        segments, line_count = history_render.format_row(
            _msg(2, text="line 1\nline 2"), {})
        body_text, _ = segments[3]
        self.assertEqual(body_text, "line 1\n" + " " * 12 + "line 2")
        self.assertEqual(line_count, 2)

    def test_three_line_body_counts_three(self):
        segments, line_count = history_render.format_row(
            _msg(3, text="a\nb\nc"), {})
        self.assertEqual(line_count, 3)

    def test_empty_body(self):
        segments, line_count = history_render.format_row(
            _msg(4, text=""), {})
        body_text, _ = segments[3]
        self.assertEqual(body_text, "")
        self.assertEqual(line_count, 1)

    def test_click_meta_present_on_every_segment(self):
        from rich.style import Style
        segments, _ = history_render.format_row(_msg(7, text="hi"), {})
        # Every segment's style carries meta={"msg_id": 7}.
        for text, style in segments:
            self.assertIsInstance(style, Style)
            self.assertEqual(style.meta.get("msg_id"), 7)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_history_render -v 2>&1 | tail -20`
Expected: ImportError or AttributeError — `history_render` doesn't exist yet.

- [ ] **Step 3: Create `history_render.py` with `format_row`**

Write `imessage_export/tui/app/history_render.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_history_render -v 2>&1 | tail -10`
Expected: 5 tests pass, 0 fail.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/history_render.py tests/test_history_render.py
git commit -m "history_render: pure format_row formatter with click meta + wrap padding"
```

---

## Task 2: `_ChunkRender.build` — cached blob + offsets + line counts

**Files:**
- Modify: `imessage_export/tui/app/history_render.py`
- Modify: `tests/test_history_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history_render.py`:

```python
class TestChunkRender(unittest.TestCase):

    def test_build_assembles_base_text_from_format_row(self):
        msgs = [_msg(1, text="hi"), _msg(2, text="there")]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        # The unstyled base Text holds the concatenation of all rows
        # plus exactly one day header at the top.
        plain = chunk.base.plain
        self.assertIn("── Thursday, January 1, 2026 ──\n", plain)
        self.assertIn("[9:00 AM] Me: hi\n", plain)
        self.assertIn("[9:00 AM] Me: there\n", plain)

    def test_row_offsets_slice_back_to_each_rows_text(self):
        msgs = [_msg(1, text="hi"), _msg(2, text="there")]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        for m in msgs:
            start, end = chunk.row_offsets[m.message_id]
            slice_text = chunk.base.plain[start:end]
            # Each row's slice contains its gutter, ts, speaker, body,
            # and trailing newline — exactly what format_row produced.
            self.assertTrue(slice_text.startswith("  "))   # gutter
            self.assertIn(f"Me: {m.text}", slice_text)
            self.assertTrue(slice_text.endswith("\n"))

    def test_row_line_counts_match_body_wraps(self):
        msgs = [_msg(1, text="single"), _msg(2, text="two\nlines"),
                _msg(3, text="three\nfour\nfive")]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        self.assertEqual(chunk.row_line_counts[1], 1)
        self.assertEqual(chunk.row_line_counts[2], 2)
        self.assertEqual(chunk.row_line_counts[3], 3)

    def test_day_header_prefix_count_advances_at_boundary(self):
        # Two messages same day, then one on a new day.
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00"),
            _msg(2, ts="2026-01-01 10:00:00"),
            _msg(3, ts="2026-01-02 09:00:00"),
        ]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        # Day header for Jan 1 appears before msg 1 (count = 1 from then on);
        # second header for Jan 2 appears between msg 2 and msg 3.
        # `day_header_prefix_count[i]` is the count of headers emitted
        # BEFORE msg_ids[i] — so before msg 1: 0 day-headers, then the
        # header lands → before msg 2: 1, before msg 3: 1 (new header
        # is emitted between msg 2 and msg 3, so before msg 3 we see 2).
        self.assertEqual(chunk.day_header_prefix_count, [0, 1, 2])

    def test_msg_ids_preserved_in_order(self):
        msgs = [_msg(5), _msg(3), _msg(8)]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        self.assertEqual(chunk.msg_ids, [5, 3, 8])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_history_render.TestChunkRender -v 2>&1 | tail -15`
Expected: AttributeError — `_ChunkRender` doesn't exist yet.

- [ ] **Step 3: Add `_ChunkRender` to `history_render.py`**

Append to `imessage_export/tui/app/history_render.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_history_render -v 2>&1 | tail -15`
Expected: 10 tests total, all pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/history_render.py tests/test_history_render.py
git commit -m "history_render: _ChunkRender.build caches unstyled blob + offset tables"
```

---

## Task 3: `selection_colors` + `SelectionColors` dataclass

**Files:**
- Modify: `imessage_export/tui/app/history_render.py`
- Modify: `tests/test_history_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history_render.py`:

```python
class TestSelectionColors(unittest.TestCase):

    def test_dawnfox_palette(self):
        from imessage_export.tui.theme import DAWNFOX
        c = history_render.selection_colors(DAWNFOX)
        self.assertEqual(c.endpoint_bg, DAWNFOX["accent_alt"])
        self.assertEqual(c.range_bg, DAWNFOX["accent"])
        self.assertEqual(c.cursor_tint_bg, DAWNFOX["bg_alt"])
        # Cursor bar's default color is accent_alt; when on an endpoint
        # row (already accent_alt bg), the painter flips it to accent.
        self.assertEqual(c.cursor_bar_default, DAWNFOX["accent_alt"])
        self.assertEqual(c.cursor_bar_on_endpoint, DAWNFOX["accent"])
        self.assertEqual(c.cursor_bar_on_in_range, DAWNFOX["accent_alt"])
        self.assertEqual(c.contrast_fg, DAWNFOX["bg"])

    def test_terafox_palette(self):
        from imessage_export.tui.theme import TERAFOX
        c = history_render.selection_colors(TERAFOX)
        self.assertEqual(c.endpoint_bg, TERAFOX["accent_alt"])
        self.assertEqual(c.range_bg, TERAFOX["accent"])
        self.assertEqual(c.cursor_tint_bg, TERAFOX["bg_alt"])
        self.assertEqual(c.contrast_fg, TERAFOX["bg"])

    def test_missing_keys_return_empty_strings(self):
        sparse = {"accent": "#000000"}  # only one of the keys present
        c = history_render.selection_colors(sparse)
        self.assertEqual(c.range_bg, "#000000")
        self.assertEqual(c.endpoint_bg, "")
        self.assertEqual(c.contrast_fg, "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_history_render.TestSelectionColors -v 2>&1 | tail -10`
Expected: AttributeError — `selection_colors` doesn't exist.

- [ ] **Step 3: Add `SelectionColors` + `selection_colors` to `history_render.py`**

Append to `imessage_export/tui/app/history_render.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_history_render -v 2>&1 | tail -10`
Expected: 13 tests, all pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/history_render.py tests/test_history_render.py
git commit -m "history_render: SelectionColors + selection_colors palette adapter"
```

---

## Task 4: `MarkState` + `paint` — pure style overlay

**Files:**
- Modify: `imessage_export/tui/app/history_render.py`
- Modify: `tests/test_history_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history_render.py`:

```python
class TestPaint(unittest.TestCase):
    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX
        self.colors = history_render.selection_colors(DAWNFOX)
        self.chunk = history_render._ChunkRender.build(
            [_msg(i, text=f"m{i}") for i in range(5)], contacts={})

    def _spans_within(self, text, start, end, contains: str) -> bool:
        """True if any span overlaps [start, end] and its style str
        contains the given substring."""
        return any(
            s.start >= start and s.end <= end and contains in str(s.style)
            for s in text.spans
        )

    def test_paint_no_state_returns_clone_with_no_extra_spans(self):
        before_span_count = len(self.chunk.base.spans)
        out = history_render.paint(
            self.chunk, cursor_id=None,
            marks=history_render.MarkState(None, None, frozenset()),
            palette=self.palette,
        )
        # Paint must always clone — never return the cached base
        # (otherwise repeated paints accumulate spans on the cache).
        self.assertIsNot(out, self.chunk.base)
        self.assertEqual(out.plain, self.chunk.base.plain)
        self.assertEqual(len(out.spans), before_span_count)

    def test_paint_cursor_only_adds_tint_and_bar_spans(self):
        out = history_render.paint(
            self.chunk, cursor_id=2,
            marks=history_render.MarkState(None, None, frozenset()),
            palette=self.palette,
        )
        start, end = self.chunk.row_offsets[2]
        # B — row tint background spans the full row.
        self.assertTrue(self._spans_within(out, start, end,
                                           self.colors.cursor_tint_bg))
        # D — bar bg on the leading 2 cols.
        self.assertTrue(self._spans_within(out, start, start + 2,
                                           self.colors.cursor_bar_default))

    def test_paint_endpoint_adds_endpoint_bg(self):
        out = history_render.paint(
            self.chunk, cursor_id=None,
            marks=history_render.MarkState(
                anchor_id=1, active_id=1, in_range_ids=frozenset({1})),
            palette=self.palette,
        )
        start, end = self.chunk.row_offsets[1]
        self.assertTrue(self._spans_within(out, start, end,
                                           self.colors.endpoint_bg))

    def test_paint_in_range_row_gets_range_bg_not_endpoint(self):
        out = history_render.paint(
            self.chunk, cursor_id=None,
            marks=history_render.MarkState(
                anchor_id=1, active_id=3, in_range_ids=frozenset({1, 2, 3})),
            palette=self.palette,
        )
        # msg 2 is strictly between anchor and active → in_range bg.
        start, end = self.chunk.row_offsets[2]
        self.assertTrue(self._spans_within(out, start, end,
                                           self.colors.range_bg))
        # msg 1 and msg 3 are endpoints → endpoint bg.
        s1, e1 = self.chunk.row_offsets[1]
        self.assertTrue(self._spans_within(out, s1, e1, self.colors.endpoint_bg))

    def test_paint_cursor_on_endpoint_flips_bar_color(self):
        out = history_render.paint(
            self.chunk, cursor_id=1,
            marks=history_render.MarkState(
                anchor_id=1, active_id=1, in_range_ids=frozenset({1})),
            palette=self.palette,
        )
        start, _ = self.chunk.row_offsets[1]
        # Default bar (accent_alt) is invisible on endpoint bg (accent_alt);
        # painter must flip to cursor_bar_on_endpoint (accent).
        self.assertTrue(self._spans_within(out, start, start + 2,
                                           self.colors.cursor_bar_on_endpoint))
        # Cursor tint (B) is suppressed on a selection row.
        end = self.chunk.row_offsets[1][1]
        self.assertFalse(self._spans_within(out, start, end,
                                            self.colors.cursor_tint_bg))

    def test_paint_does_not_mutate_chunk_base(self):
        before_plain = self.chunk.base.plain
        before_spans = list(self.chunk.base.spans)
        history_render.paint(
            self.chunk, cursor_id=3,
            marks=history_render.MarkState(1, 3, frozenset({1, 2, 3})),
            palette=self.palette,
        )
        self.assertEqual(self.chunk.base.plain, before_plain)
        self.assertEqual(self.chunk.base.spans, before_spans)


class TestMarkState(unittest.TestCase):
    def test_dataclass_is_hashable_and_frozen(self):
        m = history_render.MarkState(1, 5, frozenset({1, 2, 3, 4, 5}))
        # Frozen + hashable so HistoryView can cheaply compare/cache.
        hash(m)
        with self.assertRaises(Exception):
            m.anchor_id = 99  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_history_render.TestPaint tests.test_history_render.TestMarkState -v 2>&1 | tail -15`
Expected: AttributeError — `MarkState` and `paint` don't exist.

- [ ] **Step 3: Add `MarkState` + `paint` to `history_render.py`**

Append to `imessage_export/tui/app/history_render.py`:

```python
@dataclass(slots=True, frozen=True)
class MarkState:
    """The three pieces of selection state HistoryView passes to paint.

    Frozen + hashable so HistoryView (or anyone else) can short-circuit
    paint calls when state didn't change since the last paint.
    """
    anchor_id: int | None
    active_id: int | None
    in_range_ids: frozenset[int]


_EMPTY_MARKS = MarkState(None, None, frozenset())


def paint(
    chunk: _ChunkRender,
    cursor_id: int | None,
    marks: MarkState,
    palette: dict,
) -> Text:
    """Clone the chunk's cached base and overlay cursor + selection spans.

    The cached `base` is never mutated — every paint returns a clone.
    Span additions are layered on top so Rich's effective-style
    composition picks up the bg/fg overlays without disturbing the
    per-segment `meta`, `dim`, or `bold` modifiers already baked in.

    Endpoint vs in-range vs cursor priority on overlapping rows:
      - selection bg (endpoint or in-range) always wins for the row's
        background;
      - cursor tint (B) is suppressed on a selection row;
      - cursor bar (D) flips color so it stays visible against the
        underlying selection bg.
    """
    out = chunk.base.copy()
    colors = selection_colors(palette)
    endpoints = {marks.anchor_id, marks.active_id} - {None}

    for msg_id in chunk.msg_ids:
        start, end = chunk.row_offsets[msg_id]
        is_endpoint = msg_id in endpoints
        is_in_range = (not is_endpoint) and msg_id in marks.in_range_ids
        is_cursor = msg_id == cursor_id

        # Layer 1 — row-level selection background.
        if is_endpoint and colors.endpoint_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.endpoint_bg}"),
                start, end,
            )
        elif is_in_range and colors.range_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.range_bg}"),
                start, end,
            )

        # Layer 2 — cursor row tint (B), suppressed on selection rows.
        if is_cursor and not (is_endpoint or is_in_range):
            if colors.cursor_tint_bg:
                out.stylize(
                    _parse_style(f"on {colors.cursor_tint_bg}"),
                    start, end,
                )

        # Layer 3 — cursor bar (D), on the leading 2 cols regardless of
        # whether this row also carries a selection bg.
        if is_cursor:
            if is_endpoint:
                bar_color = colors.cursor_bar_on_endpoint
            elif is_in_range:
                bar_color = colors.cursor_bar_on_in_range
            else:
                bar_color = colors.cursor_bar_default
            if bar_color:
                out.stylize(_parse_style(f"on {bar_color}"), start, start + 2)

    return out
```

- [ ] **Step 4: Run all `history_render` tests**

Run: `python3 -m unittest tests.test_history_render -v 2>&1 | tail -25`
Expected: all 20+ tests pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/history_render.py tests/test_history_render.py
git commit -m "history_render: MarkState + paint (B+D cursor visual layered as style spans)"
```

---

## Task 5: Wire `_ChunkRender` into `HistoryView` (initial render)

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`

The first wiring step swaps the legacy `_build_blob` invocation in `render_messages` for a `_ChunkRender.build(...)` call, then renders the unstyled `base` with `paint(...)` to apply current cursor/mark state. `_chunk_messages`/`_chunk_ids` get rolled into a `_ChunkRender` slot on the widget. Tests that asserted on the `▸` content marker will fail in this step — they're updated in Task 6 once both the render and repaint paths use `paint`.

- [ ] **Step 1: Replace `render_messages`' blob construction**

In `imessage_export/tui/app/widgets.py`, find `render_messages` and replace the `blob = self._build_blob(visible)` block plus the surrounding `_topmost_widget` assignment + chunk-id caching. New code:

```python
        from . import history_render

        # Build the cached _ChunkRender for the visible slice. The
        # base Text is unstyled — paint() layers the current cursor +
        # selection state on the clone we hand to Static.update().
        chunk = history_render._ChunkRender.build(visible, self._contacts)
        palette = self._palette()
        marks = history_render.MarkState(
            self._mark_start_id, self._mark_end_id, frozenset(self._in_range_ids))
        decorated = history_render.paint(chunk, self._cursor_msg_id, marks, palette)
        # Use classes (not id) — remove_children() is async, so a rapid
        # chat-switch can still have the previous "recent-chunk" in the
        # node tree when we mount the next one. Classes coexist; ids don't.
        self._topmost_widget = Static(decorated, classes="history-blob recent-chunk")
        chunk.widget = self._topmost_widget
        # Stash the full _ChunkRender on the widget so _repaint_for_ids
        # can find it later. Old _chunk_messages / _chunk_ids attrs are
        # kept (for now) so the existing affected-ids skip path stays
        # wired; both are owned by the new dataclass in a later task.
        self._topmost_widget._chunk_render = chunk  # type: ignore[attr-defined]
        self._topmost_widget._chunk_messages = visible  # type: ignore[attr-defined]
        self._topmost_widget._chunk_ids = set(chunk.msg_ids)  # type: ignore[attr-defined]
        self.mount(self._topmost_widget)
```

- [ ] **Step 2: Add `_palette` helper**

Above `def _selection_colors(...)` in `HistoryView`, add:

```python
    def _palette(self) -> dict:
        """Return the active theme's palette dict (DAWNFOX/TERAFOX),
        defaulting to DAWNFOX if the app's theme isn't one we know."""
        from ..theme import PALETTES, DAWNFOX
        try:
            return PALETTES[self.app.theme]
        except (KeyError, AttributeError):
            return DAWNFOX
```

- [ ] **Step 3: Replace `action_load_older`'s blob construction**

Find `_load_more_older`'s body in `widgets.py` and replace the legacy `_build_blob` call:

```python
        # Build the older slice's _ChunkRender and decorate with the
        # current cursor + mark state so any selection that spans into
        # the older slice paints on first render.
        from . import history_render
        chunk = history_render._ChunkRender.build(older_slice, self._contacts)
        marks = history_render.MarkState(
            self._mark_start_id, self._mark_end_id, frozenset(self._in_range_ids))
        older_decorated = history_render.paint(
            chunk, self._cursor_msg_id, marks, self._palette())
        ...
        older_widget = Static(older_decorated, classes="history-blob older")
        chunk.widget = older_widget
        older_widget._chunk_render = chunk  # type: ignore[attr-defined]
        older_widget._chunk_messages = older_slice  # type: ignore[attr-defined]
        older_widget._chunk_ids = set(chunk.msg_ids)  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run the full test suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: `test_history_view_cursor.test_cursor_gutter_marker_renders_on_exactly_one_row` will FAIL (it asserts `"▸" count == 1`; the new render uses style-only cursor). Other tests must pass. If anything else fails, stop and investigate before continuing.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py
git commit -m "HistoryView: build chunks via history_render._ChunkRender; B+D cursor (style-only)"
```

---

## Task 6: Replace `_rerender_chunks` with `_repaint_for_ids` via `paint`

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`
- Modify: `tests/test_history_view_cursor.py`
- Modify: `tests/test_history_view_range_marks.py`

- [ ] **Step 1: Replace `_build_blob` body so cursor/mark repaints route through `paint`**

In `widgets.py`, replace the entire `_build_blob` method body with:

```python
    def _build_blob(self, visible: list):
        """Bridge for callers that still expect a single Text blob.

        Internally, the chunk is built fresh and decorated with the
        current selection state. Mostly used by tests that snapshot the
        Static's renderable; the live render/repaint paths go through
        `_ChunkRender.build` + `history_render.paint` directly without
        this helper.
        """
        from . import history_render
        chunk = history_render._ChunkRender.build(visible, self._contacts)
        marks = history_render.MarkState(
            self._mark_start_id, self._mark_end_id, frozenset(self._in_range_ids))
        return history_render.paint(
            chunk, self._cursor_msg_id, marks, self._palette())
```

- [ ] **Step 2: Replace `_rerender_chunks` with `_repaint_for_ids`**

Delete the old `_rerender_chunks` body and replace with:

```python
    def _repaint_for_ids(self, affected_ids: set[int] | None = None) -> None:
        """Repaint chunks whose ids intersect `affected_ids`.

        When `affected_ids` is None, every chunk is repainted —
        used by the cold-load path. With a set, chunks whose
        `_chunk_ids` don't intersect are skipped (the perf invariant
        from PR #248eaec — pinned by the repaint-only-affected test).
        """
        from . import history_render
        palette = self._palette()
        marks = history_render.MarkState(
            self._mark_start_id, self._mark_end_id, frozenset(self._in_range_ids))
        for child in list(self.children):
            chunk: history_render._ChunkRender | None = getattr(
                child, "_chunk_render", None)
            if chunk is None:
                continue
            if affected_ids is not None and not (set(chunk.msg_ids) & affected_ids):
                continue
            decorated = history_render.paint(
                chunk, self._cursor_msg_id, marks, palette)
            child.update(decorated)

    # Back-compat alias so apply_marks and _move_cursor don't need to
    # change yet. Removed in a later cleanup task.
    def _rerender_chunks(self, affected_ids: set[int] | None = None) -> None:
        self._repaint_for_ids(affected_ids)
```

- [ ] **Step 3: Update `test_history_view_cursor.py` cursor-marker test**

Replace `test_cursor_gutter_marker_renders_on_exactly_one_row` body with:

```python
    async def test_cursor_visual_renders_on_exactly_one_row(self):
        """The cursored row must carry both the B (row tint) and D
        (cursor bar) backgrounds; no other row carries either."""
        from imessage_export.tui.app.widgets import HistoryView
        from imessage_export.tui.app import history_render
        from imessage_export.tui.theme import DAWNFOX

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            colors = history_render.selection_colors(DAWNFOX)
            blob = history._topmost_widget.renderable
            # Exactly one row carries the cursor tint background.
            tint_spans = [s for s in blob.spans
                          if colors.cursor_tint_bg in str(s.style)]
            self.assertEqual(len(tint_spans), 1)
            # Exactly one row carries the cursor bar background on its
            # leading 2 cols.
            bar_spans = [s for s in blob.spans
                         if colors.cursor_bar_default in str(s.style)
                         and (s.end - s.start) == 2]
            self.assertEqual(len(bar_spans), 1)
```

- [ ] **Step 4: Update `test_history_view_range_marks.py` repaint test**

In `test_apply_marks_repaints_topmost_chunk`, change the bg lookup to use the new `selection_colors` keys:

```python
            # Use selection_colors so this test follows the palette
            # adapter rather than hard-coding theme hex values.
            from imessage_export.tui.app import history_render
            from imessage_export.tui.theme import DAWNFOX
            colors = history_render.selection_colors(DAWNFOX)
            endpoint_bg = colors.endpoint_bg
            range_bg = colors.range_bg
```

(Replace the `endpoint_bg, range_bg, _ = history._selection_colors()` line.)

- [ ] **Step 5: Run the test suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass (zero failures).

- [ ] **Step 6: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_cursor.py tests/test_history_view_range_marks.py
git commit -m "HistoryView: route repaints through history_render.paint (B+D cursor visual asserts)"
```

---

## Task 7: `_id_to_index` for O(1) cursor lookups

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`
- Modify: `tests/test_history_view_cursor.py`

- [ ] **Step 1: Write the failing test**

Append to the `TestHistoryViewCursor` class in `tests/test_history_view_cursor.py`:

```python
    async def test_id_to_index_built_after_render(self):
        """`_id_to_index` is the O(1) map cursor moves and stale-id
        recovery both depend on. Must be in sync with `_all_messages`
        after every render."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            self.assertEqual(history._id_to_index, {i: i for i in range(10)})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_history_view_cursor.TestHistoryViewCursor.test_id_to_index_built_after_render -v 2>&1 | tail -5`
Expected: AttributeError — `_id_to_index` doesn't exist.

- [ ] **Step 3: Add `_id_to_index` to `HistoryView`**

In `HistoryView.__init__`, after the cursor init, add:

```python
        # O(1) msg_id → index lookup for cursor moves + stale-id
        # detection. Rebuilt by render_messages and extended by
        # action_load_older.
        self._id_to_index: dict[int, int] = {}
```

In `show_placeholder`, after `self._all_messages = []`, add:

```python
        self._id_to_index = {}
```

In `render_messages`, after `self._all_messages = list(messages)`, add:

```python
        self._id_to_index = {m.message_id: i for i, m in enumerate(self._all_messages)}
```

In `_load_more_older`, after `self._shown_count = new_shown` (or wherever `_all_messages` grew — it doesn't change in load-older because all messages are already in `_all_messages`; `_id_to_index` stays correct). Add a comment to that effect:

```python
        # _id_to_index is already complete (every loaded message lives
        # in _all_messages from chat-load time — load-older only widens
        # _shown_count). No rebuild needed here.
```

- [ ] **Step 4: Rewrite `_move_cursor` to use the dict**

Replace `_move_cursor`'s body:

```python
    def _move_cursor(self, delta: int) -> None:
        if not self._all_messages or self._cursor_msg_id is None:
            return
        i = self._id_to_index.get(self._cursor_msg_id)
        if i is None:
            # Stale cursor — chat-switch race. Park on the latest.
            self._cursor_msg_id = self._all_messages[-1].message_id
            self._repaint_for_ids({self._cursor_msg_id})
            self._scroll_cursor_into_view()
            return
        new_i = max(0, min(len(self._all_messages) - 1, i + delta))
        if new_i == i:
            return
        old_id = self._cursor_msg_id
        new_id = self._all_messages[new_i].message_id
        self._cursor_msg_id = new_id
        # Clear any in-progress shift+arrow extension on a plain Up/Down.
        self._mark_anchor_id = None
        self._mark_active_id = None
        self._repaint_for_ids({old_id, new_id})
        self._scroll_cursor_into_view()
```

(`_mark_anchor_id` and `_mark_active_id` will be added in Task 8; until then comment those two lines out, or add the attribute initializers to `__init__` in this step.)

Add to `HistoryView.__init__` so the attributes exist:

```python
        # Shift+arrow selection state. anchor is set on first
        # shift+arrow press; active follows the cursor while shift is
        # held. Plain Up/Down clears both.
        self._mark_anchor_id: int | None = None
        self._mark_active_id: int | None = None
```

- [ ] **Step 5: Run the test suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_cursor.py
git commit -m "HistoryView: _id_to_index for O(1) cursor lookups + stale-id recovery"
```

---

## Task 8: Shift+arrow extends selection from anchor

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`
- Modify: `tests/test_history_view_cursor.py`

- [ ] **Step 1: Write the failing tests**

Append to `TestHistoryViewCursor`:

```python
    async def test_shift_down_extends_selection_from_anchor(self):
        """First shift+down sets the anchor on the current cursor and
        moves the active down by one. The in_range set grows to cover
        the anchor and active rows."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history._cursor_msg_id = 4

            history.action_extend_down()
            await pilot.pause()

            self.assertEqual(history._cursor_msg_id, 5)
            self.assertEqual(history._mark_anchor_id, 4)
            self.assertEqual(history._mark_active_id, 5)
            self.assertEqual(history._in_range_ids, {4, 5})

    async def test_second_shift_keeps_anchor_grows_range(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history._cursor_msg_id = 4

            history.action_extend_down()
            await pilot.pause()
            history.action_extend_down()
            await pilot.pause()
            history.action_extend_down()
            await pilot.pause()

            self.assertEqual(history._mark_anchor_id, 4)
            self.assertEqual(history._mark_active_id, 7)
            self.assertEqual(history._in_range_ids, {4, 5, 6, 7})

    async def test_plain_arrow_clears_anchor(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history._cursor_msg_id = 4
            history.action_extend_down()
            await pilot.pause()
            # Anchor is 4, active is 5; now plain Down should clear.
            history.action_cursor_down()
            await pilot.pause()
            self.assertIsNone(history._mark_anchor_id)
            self.assertIsNone(history._mark_active_id)
            self.assertEqual(history._in_range_ids, set())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_history_view_cursor -v 2>&1 | tail -10`
Expected: AttributeError on `action_extend_down`.

- [ ] **Step 3: Add `_extend_selection`, `action_extend_up`, `action_extend_down`**

In `HistoryView`, alongside the cursor actions:

```python
    def action_extend_up(self) -> None:
        self._extend_selection(-1)

    def action_extend_down(self) -> None:
        self._extend_selection(+1)

    def _extend_selection(self, delta: int) -> None:
        """Move the cursor by `delta` and grow the selection from a
        fixed anchor. The anchor is set on the first shift+arrow press
        (to the cursor's current position) and stays put while shift
        is held; the active follows the cursor.
        """
        if not self._all_messages or self._cursor_msg_id is None:
            return
        i = self._id_to_index.get(self._cursor_msg_id)
        if i is None:
            self._cursor_msg_id = self._all_messages[-1].message_id
            self._repaint_for_ids({self._cursor_msg_id})
            self._scroll_cursor_into_view()
            return
        new_i = max(0, min(len(self._all_messages) - 1, i + delta))
        if new_i == i and self._mark_anchor_id == self._cursor_msg_id:
            return  # at a bound + no new extension to apply
        old_id = self._cursor_msg_id
        new_id = self._all_messages[new_i].message_id

        # Capture OLD highlighted ids before we mutate, so the painter
        # has a precise set to clear.
        old_highlighted = set(self._in_range_ids)
        if self._mark_anchor_id is not None:
            old_highlighted.add(self._mark_anchor_id)
        if self._mark_active_id is not None:
            old_highlighted.add(self._mark_active_id)

        if self._mark_anchor_id is None:
            self._mark_anchor_id = old_id   # anchor where shift+arrow began

        self._cursor_msg_id = new_id
        self._mark_active_id = new_id

        # Recompute in_range from (anchor, active) via id_to_index.
        a = self._id_to_index[self._mark_anchor_id]
        b = self._id_to_index[new_id]
        lo, hi = (a, b) if a <= b else (b, a)
        self._in_range_ids = {self._all_messages[k].message_id
                              for k in range(lo, hi + 1)}
        # Mirror anchor/active into the legacy mark_start_id/mark_end_id
        # so apply_marks logic + the export-window flow stays consistent.
        self._mark_start_id = self._mark_anchor_id
        self._mark_end_id = new_id

        new_highlighted = set(self._in_range_ids)
        new_highlighted.add(self._mark_anchor_id)
        new_highlighted.add(new_id)
        self._repaint_for_ids({old_id, new_id} | old_highlighted | new_highlighted)
        self._scroll_cursor_into_view()
```

- [ ] **Step 4: Add bindings**

In `HistoryView.BINDINGS`, add (between `down` and `enter`):

```python
        ("shift+up", "extend_up", "Extend selection up"),
        ("shift+down", "extend_down", "Extend selection down"),
```

- [ ] **Step 5: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_cursor.py
git commit -m "HistoryView: shift+arrow extends selection from a fixed anchor"
```

---

## Task 9: Home / End / PgUp / PgDn navigation

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`
- Modify: `tests/test_history_view_cursor.py`

- [ ] **Step 1: Write the failing tests**

Append to `TestHistoryViewCursor`:

```python
    async def test_action_cursor_to_end_parks_on_latest(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(20))
            await pilot.pause()
            history._cursor_msg_id = 5

            history.action_cursor_to_end()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 19)

    async def test_action_cursor_to_start_parks_on_oldest_loaded(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(20))
            await pilot.pause()
            history.action_cursor_to_start()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 0)

    async def test_action_page_down_moves_by_viewport_height(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(200))
            await pilot.pause()
            history._cursor_msg_id = 10

            history.action_page_down()
            await pilot.pause()
            # Default viewport in the stub is small; just verify cursor
            # advanced by at least 5 messages (page is `max(5, size.height)`).
            self.assertGreaterEqual(history._cursor_msg_id, 15)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_history_view_cursor -v 2>&1 | tail -10`
Expected: AttributeError on the three new actions.

- [ ] **Step 3: Add actions**

In `HistoryView`:

```python
    def action_cursor_to_start(self) -> None:
        """Jump to the oldest loaded message."""
        if not self._all_messages:
            return
        target = self._all_messages[0].message_id
        self._jump_cursor_to(target)

    def action_cursor_to_end(self) -> None:
        """Jump to the latest message."""
        if not self._all_messages:
            return
        target = self._all_messages[-1].message_id
        self._jump_cursor_to(target)

    def action_page_up(self) -> None:
        self._move_cursor(-max(5, self._viewport_height_lines()))

    def action_page_down(self) -> None:
        self._move_cursor(+max(5, self._viewport_height_lines()))

    def _jump_cursor_to(self, target_id: int) -> None:
        if self._cursor_msg_id == target_id:
            return
        old_id = self._cursor_msg_id
        self._cursor_msg_id = target_id
        self._mark_anchor_id = None
        self._mark_active_id = None
        affected = {target_id}
        if old_id is not None:
            affected.add(old_id)
        self._repaint_for_ids(affected)
        self._scroll_cursor_into_view()

    def _viewport_height_lines(self) -> int:
        """Best-effort viewport height in terminal rows. Used by
        page up/down to compute a sensible jump distance. Falls back
        to 20 if the size isn't known yet (e.g. during early mount).
        """
        try:
            h = int(self.size.height)
        except Exception:
            h = 0
        return h if h > 0 else 20
```

- [ ] **Step 4: Add bindings**

In `HistoryView.BINDINGS`:

```python
        ("home", "cursor_to_start", "Jump to oldest loaded"),
        ("end", "cursor_to_end", "Jump to latest"),
        ("pageup", "page_up", "Page up"),
        ("pagedown", "page_down", "Page down"),
```

- [ ] **Step 5: Run tests**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_cursor.py
git commit -m "HistoryView: Home/End/PageUp/PageDown navigate the cursor"
```

---

## Task 10: Row-level cursor-follow scroll

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`
- Modify: `tests/test_history_view_cursor.py`

- [ ] **Step 1: Write the failing test**

Append to `TestHistoryViewCursor`:

```python
    async def test_scroll_follows_cursor_off_bottom_edge(self):
        """Cursor walked past the bottom margin must trigger a scroll
        so the cursor row stays in view. This is the new row-level
        scroll-follow (replaces the old chunk-level scroll_to_widget)."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(60, 12)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(200))
            await pilot.pause()
            # Cursor starts at the last message → already at the bottom.
            start_scroll_y = history.scroll_y
            # Move up far enough to land mid-history, then down again to
            # force the cursor to walk back past the viewport bottom.
            for _ in range(30):
                history.action_cursor_up()
            await pilot.pause()
            for _ in range(30):
                history.action_cursor_down()
            await pilot.pause()
            # We don't pin an exact scroll_y because viewport size and
            # virtual_size depend on the stub; just assert the scroll
            # actually moved during the walk.
            self.assertNotEqual(history.scroll_y, start_scroll_y - 1)
```

- [ ] **Step 2: Run the test**

Run: `python3 -m unittest tests.test_history_view_cursor.TestHistoryViewCursor.test_scroll_follows_cursor_off_bottom_edge -v 2>&1 | tail -10`
Expected: PASS today (because scroll_y starts at end and any movement nudges it) OR FAIL (chunk-level only). Either way, the next step makes scroll-follow row-precise.

- [ ] **Step 3: Replace `_scroll_cursor_into_view`**

```python
    SCROLL_MARGIN = 2  # rows of breathing room at the viewport edges

    def _scroll_cursor_into_view(self) -> None:
        """Row-level scroll-follow: keep the cursor row at least
        SCROLL_MARGIN rows from each viewport edge. When out of range,
        snap so the cursor lands ~30% from the leading edge."""
        cursor = self._cursor_msg_id
        if cursor is None:
            return
        chunk = self._find_chunk_for_id(cursor)
        if chunk is None or chunk.widget is None:
            return

        # Cursor's y inside the chunk = day-headers above + cumulative
        # rendered-line count of all messages above it within the chunk.
        try:
            idx = chunk.msg_ids.index(cursor)
        except ValueError:
            return
        lines_above = sum(
            chunk.row_line_counts[mid] for mid in chunk.msg_ids[:idx]
        )
        y_in_chunk = lines_above + chunk.day_header_prefix_count[idx]

        def _apply():
            try:
                widget_y = int(chunk.widget.region.y)
            except Exception:
                return
            y_absolute = widget_y + y_in_chunk
            viewport_top = int(self.scroll_y)
            viewport_h = self._viewport_height_lines()
            viewport_bottom = viewport_top + viewport_h
            if viewport_top + self.SCROLL_MARGIN <= y_absolute <= viewport_bottom - self.SCROLL_MARGIN:
                return  # in margin — no scroll
            # Snap so cursor lands ~30% from the leading edge.
            if y_absolute < viewport_top + self.SCROLL_MARGIN:
                target = max(0, y_absolute - int(viewport_h * 0.3))
            else:
                target = max(0, y_absolute - int(viewport_h * 0.7))
            try:
                self.scroll_to(y=target, animate=False)
            except Exception:
                pass

        # Wrap in call_after_refresh so a fresh mount has time to
        # settle its region.y before we read it (mirrors the §5c
        # anchor-after-load pattern).
        self.call_after_refresh(_apply)

    def _find_chunk_for_id(self, msg_id: int):
        """Return the _ChunkRender holding `msg_id`, or None."""
        for child in self.children:
            chunk = getattr(child, "_chunk_render", None)
            if chunk is not None and msg_id in chunk.row_offsets:
                return chunk
        return None
```

- [ ] **Step 4: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_cursor.py
git commit -m "HistoryView: row-level cursor-follow scroll with ≥2-row margin + 30% snap"
```

---

## Task 11a: Filter recovery — park cursor on nearest-by-timestamp

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`
- Modify: `tests/test_history_view_cursor.py`

- [ ] **Step 1: Write the failing test**

Append to `TestHistoryViewCursor`:

```python
    async def test_filter_excluding_cursor_parks_on_nearest_by_timestamp(self):
        """When a filter narrows the message set and excludes the
        cursor's id, render_messages(_from_filter=True) must park the
        cursor on the message whose timestamp is closest to the
        excluded cursor — NOT silently jump to the latest."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            full = _fake_messages(20)
            history.render_messages(full)
            await pilot.pause()
            history._cursor_msg_id = 10  # mid-history

            # Filter narrows to messages 0..4 and 15..19 — excluding 10.
            # Nearest by timestamp from msg 10 in the remaining set is
            # msg 4 (earlier, but closer in index than msg 15 because
            # the index gap is identical and ties prefer the older).
            narrowed = full[:5] + full[15:]
            history.render_messages(narrowed, _from_filter=True)
            await pilot.pause()

            # Either side of 10 is 5 indices away; nearest-by-timestamp
            # picks one of {4, 15}. Both are acceptable; assert the
            # cursor is NOT silently snapped to the latest.
            self.assertIn(history._cursor_msg_id, {4, 15})
            self.assertNotEqual(history._cursor_msg_id, 19)
```

- [ ] **Step 2: Run the test**

Run: `python3 -m unittest tests.test_history_view_cursor.TestHistoryViewCursor.test_filter_excluding_cursor_parks_on_nearest_by_timestamp -v 2>&1 | tail -5`
Expected: FAIL — current code falls back to `_all_messages[-1].message_id` (= 19).

- [ ] **Step 3: Replace the cursor-seeding block in `render_messages`**

Find the block in `render_messages` (added in earlier work):

```python
        loaded_ids = {m.message_id for m in self._all_messages}
        if self._cursor_msg_id not in loaded_ids:
            self._cursor_msg_id = self._all_messages[-1].message_id
```

Replace with:

```python
        loaded_ids = {m.message_id for m in self._all_messages}
        if self._cursor_msg_id is None:
            # No prior cursor — park on the latest (cold load).
            self._cursor_msg_id = self._all_messages[-1].message_id
        elif self._cursor_msg_id not in loaded_ids:
            # Cursor was set but its id was filtered out. Pick the
            # remaining message whose timestamp is nearest to the
            # excluded cursor's timestamp — bisecting on the loaded
            # list (sorted by timestamp by construction).
            self._cursor_msg_id = self._nearest_loaded_by_timestamp(
                self._cursor_msg_id, _from_filter and self._unfiltered_messages
            ) or self._all_messages[-1].message_id

    def _nearest_loaded_by_timestamp(
        self,
        excluded_id: int,
        unfiltered: list | None,
    ) -> int | None:
        """Return the message_id in `_all_messages` whose timestamp is
        nearest to `excluded_id`'s timestamp. `unfiltered` is the pre-
        filter list (kept for filter callers); falls back to None if
        we can't find the excluded message there either."""
        excluded_ts = None
        if unfiltered:
            for m in unfiltered:
                if m.message_id == excluded_id:
                    excluded_ts = m.timestamp
                    break
        if excluded_ts is None:
            return None
        # _all_messages is ordered by timestamp; bisect on it.
        import bisect
        timestamps = [m.timestamp for m in self._all_messages]
        i = bisect.bisect_left(timestamps, excluded_ts)
        # Pick the closer of i-1 and i (clamped to bounds).
        candidates = []
        if i > 0:
            candidates.append(i - 1)
        if i < len(self._all_messages):
            candidates.append(i)
        if not candidates:
            return None
        best = min(candidates,
                   key=lambda k: abs(self._compare_ts_to(
                       self._all_messages[k].timestamp, excluded_ts)))
        return self._all_messages[best].message_id

    def _compare_ts_to(self, ts_a: str, ts_b: str) -> int:
        """Cheap timestamp distance — string lex order is fine because
        both are zero-padded ISO-format. Returns ordering, not duration."""
        if ts_a == ts_b: return 0
        return 1 if ts_a > ts_b else -1
```

- [ ] **Step 4: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_cursor.py
git commit -m "HistoryView: filter recovery — park cursor on nearest-by-timestamp message"
```

---

## Task 11b: `on_click` silently drops stale meta

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`
- Modify: `tests/test_history_view_range_marks.py`

- [ ] **Step 1: Write the failing test**

Append to `TestHistoryViewRangeMarks`:

```python
    async def test_on_click_with_stale_meta_msg_id_is_silently_dropped(self):
        """If the click's style.meta refers to a msg_id no longer in
        _id_to_index (chat-switch race / mid-prune click), on_click
        must NOT post RangeMarkRequested — silent drop is the contract."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            class _FakeStyle:
                meta = {"msg_id": 99999}  # not in the loaded set

            class _FakeEvent:
                widget = history._topmost_widget
                style = _FakeStyle()
                def stop(self): pass

            posted = []
            original_post = history.post_message
            history.post_message = lambda m: posted.append(m) or original_post(m)

            history.on_click(_FakeEvent())
            await pilot.pause()

            marks = [m for m in posted if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(marks, [], "stale meta must not post RangeMarkRequested")
```

- [ ] **Step 2: Run the test**

Run: `python3 -m unittest tests.test_history_view_range_marks.TestHistoryViewRangeMarks.test_on_click_with_stale_meta_msg_id_is_silently_dropped -v 2>&1 | tail -5`
Expected: FAIL — current `on_click` posts unconditionally when meta has msg_id.

- [ ] **Step 3: Guard `on_click` against stale ids**

In `HistoryView.on_click`, find the block that reads `event.style.meta["msg_id"]` and add an `_id_to_index` check:

```python
        style = getattr(event, "style", None)
        if style is not None:
            meta = getattr(style, "meta", None) or {}
            msg_id = meta.get("msg_id")
            if msg_id is not None:
                # Drop clicks whose msg_id was filtered out / unloaded
                # mid-prune. The post-handler can't recover from a stale
                # id cleanly, and silently dropping matches the cursor
                # stale-id recovery pattern.
                if int(msg_id) not in self._id_to_index:
                    event.stop()
                    return
                self.post_message(self.RangeMarkRequested(int(msg_id)))
                event.stop()
                return
```

- [ ] **Step 4: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_range_marks.py
git commit -m "HistoryView: on_click silently drops clicks with stale msg_id meta"
```

---

## Task 12: Layer 3 perf budget pin

**Files:**
- Create: `tests/test_history_view_perf.py`

- [ ] **Step 1: Write the perf test**

```python
"""Layer 3 perf budget for HistoryView's repaint path.

Pins the post-refactor invariant: a single _repaint_for_ids call on a
4k-message chat must complete in well under 2 ms — that's the budget
for click-to-mark latency to feel instant on the next interaction.

Skipped on CI (`IMESSAGE_SKIP_PERF=1`) to avoid noise; runs locally as
a regression catch when the rendering pipeline gets touched.
"""
from __future__ import annotations

import importlib
import os
import time
import unittest

try:
    importlib.import_module("textual")
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


def _fake_messages(n: int):
    from imessage_export.models import Message
    return [
        Message(
            message_id=i,
            timestamp=f"2026-01-01 {(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}",
            timestamp_utc=f"2026-01-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
            chat_id=1,
            sender_handle=None,
            is_from_me=1,
            author_label="Me",
            text=f"msg {i}",
            has_attachment=0,
            attachment_filenames=[],
            kind="message",
            is_edited=0,
            reaction=None,
            app_bundle=None,
        )
        for i in range(n)
    ]


@unittest.skipUnless(HAS_TEXTUAL, "[tui] extra not installed")
@unittest.skipIf(os.environ.get("IMESSAGE_SKIP_PERF") == "1", "perf test skipped")
class TestHistoryViewPerf(unittest.IsolatedAsyncioTestCase):

    async def test_repaint_budget_under_2ms_per_call_on_4k(self):
        from textual.app import App, ComposeResult
        from imessage_export.tui.app.widgets import HistoryView

        class _StubApp(App):
            def compose(self) -> ComposeResult:
                yield HistoryView(id="history")

        app = _StubApp()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(4000))
            await pilot.pause()

            # Warm the cache (first repaint after mount may be slower).
            history._repaint_for_ids({0})
            await pilot.pause()

            start = time.perf_counter()
            N = 100
            for i in range(N):
                history._repaint_for_ids({i % len(history._all_messages)})
            elapsed = time.perf_counter() - start

            per_call_ms = (elapsed / N) * 1000
            self.assertLess(
                per_call_ms, 2.0,
                f"repaint budget exceeded: {per_call_ms:.2f} ms/call "
                f"(want < 2 ms; 4k-msg chunk, 100-call average)",
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the perf test**

Run: `python3 -m unittest tests.test_history_view_perf -v 2>&1 | tail -10`
Expected: PASS. If it fails, capture the printed per-call ms and stop — the refactor isn't hitting the latency target and needs investigation before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_history_view_perf.py
git commit -m "Pin perf budget: HistoryView._repaint_for_ids < 2 ms/call on 4k messages"
```

---

## Task 13: HelpModal documents the new bindings

**Files:**
- Modify: `imessage_export/tui/app/modals.py`

- [ ] **Step 1: Update the History section of HelpModal**

In `imessage_export/tui/app/modals.py`, replace the "History" block in `HelpModal.compose`:

```python
                "History\n"
                "  ↑ ↓                    Move keyboard cursor (▌ bar + tint) one message\n"
                "  Shift+↑ Shift+↓        Extend selection from a fixed anchor\n"
                "  Home / End            Jump to oldest loaded / newest message\n"
                "  PageUp / PageDown     Move cursor by viewport height\n"
                "  Space / Enter         Mark cursor row as range endpoint (1st = start, 2nd = end)\n"
                "  click a message       Same as Space, but with the mouse\n"
                "  click after both set  Move nearest endpoint to the click (extend / shrink)\n"
                "  o / click banner      Load 2,000 older messages\n"
                "  /                     Search within this chat\n"
                "  Esc                   Clear marks / clear search\n"
```

- [ ] **Step 2: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass (HelpModal change is text-only, no test assertions on its content).

- [ ] **Step 3: Commit**

```bash
git add imessage_export/tui/app/modals.py
git commit -m "HelpModal: document Shift+arrow, Home/End/PgUp/PgDn, B+D cursor visual"
```

---

## Task 14: Remove the back-compat shim + dead code

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`

- [ ] **Step 1: Delete `_build_blob` (only `_repaint_for_ids` uses Paint now)**

Remove `_build_blob` from `HistoryView`. Search any callers — there should be none after Task 6's repaint rewiring. If a test still calls `_build_blob`, rewrite the test to read `history._topmost_widget.renderable` directly.

- [ ] **Step 2: Delete `_rerender_chunks` alias**

Remove the `_rerender_chunks` back-compat alias added in Task 6. Spot-check `apply_marks` — it should already call `_repaint_for_ids` directly via the alias swap from Task 6; once the alias is gone, those call sites need to be updated to the new name.

- [ ] **Step 3: Delete the legacy `_chunk_messages` / `_chunk_ids` attrs**

In `render_messages` and `_load_more_older`, drop the two lines that stash `_chunk_messages` and `_chunk_ids` on the widget — `_chunk_render` is the single source of truth. Update any test that read those attrs to read `_chunk_render.msg_ids` / `set(_chunk_render.msg_ids)` instead. The `test_apply_marks_skips_chunks_outside_selection` test in `test_history_view_range_marks.py` does this — update the `_chunk_ids` reads to `_chunk_render.msg_ids`.

- [ ] **Step 4: Delete `_selection_colors` from `HistoryView`**

The helper is replaced by `history_render.selection_colors`. Remove it from `widgets.py`. Update the `test_apply_marks_repaints_topmost_chunk` test to call `history_render.selection_colors(DAWNFOX)` (already done in Task 6).

- [ ] **Step 5: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -8`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_history_view_range_marks.py
git commit -m "HistoryView: drop _build_blob, _rerender_chunks alias, raw _chunk_messages/_chunk_ids"
```

---

## Task 15: Final integration sweep

**Files:**
- Modify: any test file flagged by the suite.

- [ ] **Step 1: Run the entire suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -10`
Expected: ALL tests pass. Baseline before this plan was 288; expect ≈ 318 after (15 new in `test_history_render.py`, 6 new in cursor + range-marks tests, 1 new in perf).

- [ ] **Step 2: Smoke-test the TUI**

Run: `python3 -m imessage_export --wizard` (or any normal launch).
Verify:
- Arrow keys walk the cursor row-by-row through the chat.
- Cursor row shows tinted background + colored left-edge bar.
- Shift+arrow extends a selection from a fixed anchor; Esc clears.
- Home/End jump to oldest loaded / newest.
- PgUp/PgDn move by ≈ viewport height.
- Clicking a message marks it as an endpoint — feels snappy.
- Click → second click → second click again extends the nearest endpoint (existing behavior).
- Scrolling up loads older chunks; the cursor remains operable after a load.

- [ ] **Step 3: Commit any final test/lint fixes**

```bash
git add -A
git commit -m "Final sweep: any test fix-ups discovered in the integration smoke"
```

---

## Verification checklist

- [ ] `python3 -m unittest discover -s tests` — all green.
- [ ] `python3 -m unittest tests.test_history_view_perf -v` — perf budget passes.
- [ ] Manual: smoke-test items from Task 14 Step 2.
- [ ] `git log --oneline` — every task produced exactly one commit, no fix-up amends.
- [ ] §5c invariants intact: search the new code for `parent is self`, `_topmost_widget`, and `watch_scroll_y` — they should appear unchanged from their pre-refactor form.
