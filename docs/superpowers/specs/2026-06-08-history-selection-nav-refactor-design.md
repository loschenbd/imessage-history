# HistoryView selection + navigation refactor

**Date:** 2026-06-08
**Status:** Design — pending user approval
**Surface:** `imessage_export/tui/app/widgets.py` `HistoryView`

## Problem

Click-to-mark and arrow-key cursor navigation in `HistoryView` both feel sluggish, and the cursor indicator is too subtle to spot in a sub-second glance.

The two latencies share a root cause: every state change (cursor move, mark drop, mark extend) calls `_rerender_chunks(affected_ids)`, and each affected chunk runs `_build_blob` from scratch — formatting every visible message's timestamp, speaker, wrapped body, and per-span Rich styles. For a 2000-message chunk that's tens of thousands of `Text.append` calls per keypress.

The cursor visual today is a single `▸` glyph in bold accent on a constant 2-col gutter. On a light theme it disappears into the page; users lose track of where their keyboard focus is.

A separate UX gap: the viewport doesn't follow the cursor through arrow-key navigation. `_scroll_cursor_into_view` only kicks in when the cursor's chunk is fully off-screen; *within* a chunk the cursor can walk past the bottom edge of the visible window and effectively disappear.

## Goals

- Click→repaint and cursor-move repaint complete in < 5ms wall clock on a chat with 4000 loaded messages.
- The cursor row is identifiable in < 1 second of visual scan from any starting point on screen.
- Arrow-key navigation keeps the cursor row visible at all times — at least 2 rows of margin from the top and bottom edges of the viewport.
- Keyboard range selection feels editor-native: ↑/↓ moves the cursor; Shift+↑/↓ extends a selection from a fixed anchor; Space/Enter still drops endpoints for click-style flow; Esc clears.
- No regression on the existing Textual scrolling invariants (§5c of `textual-app-scrollable-data-pitfalls` skill): `parent is self`, `_topmost_widget` tracking, edge-triggered `watch_scroll_y`, anchor to prev_top after load-older.

## Non-goals

- Drag-to-select with the mouse. (Considered, parked — click→click polish covers the perceived-lag complaint without the implementation cost.)
- Native text selection for copy. (Out of scope; users can rely on terminal-level selection.)
- Per-row widget rendering. (Explicitly ruled out by the textual-pitfalls skill §5a — O(N²) mount cost on large chats.)
- DataTable swap. (Variable-height bodies don't fit DataTable's tabular model.)
- Changing PREVIEW_CAP or LOAD_MORE_CHUNK sizes. The architecture wins are big enough without that; the §5c load-older invariants stay untouched.

## Architecture

### `_ChunkRender` — pre-rendered cache per mounted chunk

A new dataclass that owns one chunk's render state:

```
_ChunkRender:
  widget:        Static
  msg_ids:       list[int]                      # in render order
  base:          rich.text.Text                  # unstyled, build-once
  row_offsets:   dict[msg_id, (start, end)]      # byte spans in `base.plain`
  row_line_counts: dict[msg_id, int]             # rendered line count per msg
```

`base` is the chunk's blob assembled *without* any cursor or selection styling — just the gutter (2 spaces), timestamp, speaker, body wrap, and per-span click meta. It's built exactly once at mount time. `row_offsets` lets the painter add spans at the right byte positions without re-walking the messages. `row_line_counts` lets the scroll-follow code compute a row's y-offset within the chunk without re-measuring layout.

### `RowFormatter` — pure formatter

A pure function `format_row(message, contacts) → (segments: list[(text, style)], rendered_lines: int)`. Encapsulates every string-formatting decision currently inlined in `_build_blob`: 12-hour timestamp, speaker label, body wrap (`\n` → `\n` + 12-space indent), click meta. Returns the raw segment list and a line-count.

Pure → no Textual, no Rich theme, no `self.app` access. Unit-tested in isolation with plain dicts.

### `SelectionPainter` — pure style overlay

A pure function `paint(chunk: _ChunkRender, cursor_id, mark_state, palette) → rich.text.Text`. Clones `chunk.base`, then for each row whose state differs from "unstyled" adds spans to the clone at `chunk.row_offsets[msg_id]`. Returns the decorated Text ready to hand to `Static.update()`.

State inputs are just plain ids and a palette dict — no widget access. The clone is O(N) but cheap (Rich `Text.copy` is one string copy + one span list copy). Each span addition is O(1).

### HistoryView shrinks to lifecycle + wiring

HistoryView keeps:
- Chunk lifecycle (mount, prune, anchor) — every §5c invariant from the textual-pitfalls skill stays unchanged.
- Input state: `_cursor_msg_id`, `_mark_anchor_id`, `_mark_active_id`, `_in_range_ids`.
- `_id_to_index: dict[msg_id, int]` is the only data structure besides `_chunks` that has to stay in sync with `_all_messages`. Rebuilt from scratch in `render_messages` (cold path; cheap on small chats, ≈ a single dict comprehension on big ones). Extended in `action_load_older` by merging the freshly-loaded older slice's ids at the *front* of the index space — every existing id's index shifts up by the new chunk's length. Used for O(1) cursor-move arithmetic and stale-id detection.

HistoryView's `_chunks: list[_ChunkRender]` replaces the loose `_chunk_messages` / `_chunk_ids` attributes that today hang off raw widgets. `_topmost_widget` becomes `self._chunks[0].widget` (derived); the load-older code paths see no difference.

`_build_blob` and `_rerender_chunks` are removed. Their replacement: a single internal `_repaint_for_ids(affected_ids: set[int])` that walks `_chunks`, finds those whose `msg_ids` intersect `affected_ids`, calls `SelectionPainter.paint(chunk, ...)` for each, and feeds the result to `chunk.widget.update(...)`.

### File layout

- `tui/app/history_render.py` — **NEW.** `_ChunkRender`, `RowFormatter`, `SelectionPainter`, `_selection_colors` (moved out of HistoryView for encapsulation).
- `tui/app/widgets.py` — HistoryView becomes thinner; orchestration only.

## Components

### `history_render.py` (new module)

```python
@dataclass(slots=True)
class _ChunkRender:
    widget: Static
    msg_ids: list[int]
    base: Text
    row_offsets: dict[int, tuple[int, int]]
    row_line_counts: dict[int, int]
    day_header_prefix_count: list[int]   # day-header lines emitted before msg_ids[i]

    @classmethod
    def build(cls, messages: list, contacts: dict, palette: dict) -> "_ChunkRender":
        """One-shot construction. Runs RowFormatter over every message
        and assembles the unstyled blob + offset table + line-count table."""

def format_row(message, contacts: dict) -> tuple[list[tuple[str, Style]], int]:
    """Pure formatter. Returns (segments, line_count) for one message."""

def selection_colors(palette: dict) -> SelectionColors:
    """Endpoint bg, range bg, cursor bg, cursor bar, contrast fg.
    All from the existing palette keys: accent_alt, accent, bg_alt, accent, bg."""

@dataclass(slots=True, frozen=True)
class MarkState:
    anchor_id: int | None
    active_id: int | None
    in_range_ids: frozenset[int]

def paint(
    chunk: _ChunkRender,
    cursor_id: int | None,
    marks: MarkState,
    palette: dict,
) -> Text:
    """Pure style overlay. Clones chunk.base, layers cursor + mark spans."""
```

### `widgets.py` HistoryView changes

Bindings:
```
up           → action_cursor_up         (move cursor, anchor cleared if not shift)
down         → action_cursor_down
shift+up     → action_extend_up         (extend selection from anchor)
shift+down   → action_extend_down
home         → action_cursor_to_start   (first loaded message)
end          → action_cursor_to_end     (last loaded message)
pageup       → action_page_up           (cursor by viewport_height)
pagedown     → action_page_down
space        → action_mark_row          (drop endpoint at cursor — unchanged)
enter        → action_mark_row
escape       → action_clear_marks       (clears anchor + active + in_range)
o            → action_load_older        (unchanged)
```

New helpers:
- `_repaint_for_ids(affected_ids)` — replaces `_rerender_chunks`.
- `_scroll_cursor_into_view()` — rewritten to use `row_line_counts` for row-level y precision.
- `_extend_selection(delta)` — sets `_mark_anchor_id` on first call, then keeps it fixed while `_mark_active_id` follows the cursor.

Removed:
- `_build_blob` (logic split between `RowFormatter` and `_ChunkRender.build`).
- `_rerender_chunks` (replaced by `_repaint_for_ids`).
- `_chunk_messages` / `_chunk_ids` raw attributes on Static widgets (rolled into `_ChunkRender`).

## Data flow

### Cold load (new chat picked)

1. `render_messages(messages)` clears `_chunks`, rebuilds `_id_to_index`, computes the latest-N slice (PREVIEW_CAP=2000, unchanged).
2. `_ChunkRender.build(slice, contacts, palette)` runs `RowFormatter` over each message in one pass. This is the one expensive walk per chunk — same cost as today's first `_build_blob`.
3. Mount `Static(chunk.base)` (no styling yet — chunk has no cursor or marks at construction). Store the `_ChunkRender` on `self._chunks`.
4. Seed cursor to latest message id (current behavior); `_repaint_for_ids({cursor_id})` paints the cursor styling onto the freshly mounted chunk.
5. `call_after_refresh(self.scroll_end, animate=False)` (current behavior).

### Cursor move (Up / Down / Home / End / PgUp / PgDn)

1. Action computes `new_cursor_id` from `_all_messages` via `_id_to_index` (O(1)).
2. `affected = {old_cursor, new_cursor}`. Anchor is cleared (Up/Down without Shift means "cancel any in-progress extension").
3. `_repaint_for_ids(affected)` — at most two chunks repaint, usually just one. Each painter call clones the chunk's base and adds ≤ 6 spans (cursor row + any selection row that overlapped). Sub-millisecond.
4. `_scroll_cursor_into_view()`:
   - Find the chunk holding the new cursor: `next(c for c in self._chunks if cursor_id in c.row_offsets)`.
   - Compute cursor's y inside that chunk: `chunk.widget.region.y + sum(chunk.row_line_counts[m] for m in chunk.msg_ids until cursor_idx) + (day_header_lines_above)`.
   - If that y is within `[viewport.top + 2, viewport.bottom - 2]`: no scroll.
   - Else: `self.scroll_to(y=target, animate=False)` where target lands the cursor row at ~30% from the leading edge. Wrap in `call_after_refresh` if the chunk's region.y might not be settled yet (post-load case).

### Shift+arrow (extend selection)

1. If `_mark_anchor_id is None`: set it to current cursor id. This anchors the selection.
2. Move cursor by delta (same as plain Up/Down).
3. `_mark_active_id = new_cursor_id`.
4. Recompute `_in_range_ids` from `(anchor, active)` via id_to_index range lookup.
5. `affected = {old_cursor, new_cursor} ∪ (old_in_range △ new_in_range)`.
6. `_repaint_for_ids(affected)`. Scroll-follow runs same as cursor move.

### Click / Space / Enter (mark via click-style flow)

1. Click meta or action posts `RangeMarkRequested(msg_id)` (unchanged).
2. App's `_mark_message` runs `apply_click_mark` (unchanged — nearest-endpoint extension).
3. `history.apply_marks(start, end, messages)` — same signature, internally diffs old vs new highlighted-id set (current logic) and calls `_repaint_for_ids(affected)` instead of `_rerender_chunks`.

### Esc (clear)

1. `_mark_anchor_id = _mark_active_id = None`, `_in_range_ids = frozenset()`.
2. `affected = old_in_range ∪ {old_anchor, old_active} - {None}`.
3. `_repaint_for_ids(affected)`.
4. Cursor stays put; only the selection visuals clear.

### Load older (scroll-up — §5c path)

1. Build `_ChunkRender` for the older slice via `_ChunkRender.build(...)` — one `RowFormatter` walk.
2. Mount before previous topmost: `self.mount(new_chunk.widget, before=prev_top)` with `parent is self` check preserved.
3. Anchor: `self.scroll_to_widget(prev_top, top=True, animate=False)` inside `call_after_refresh` — current pattern, unchanged.
4. `self._chunks.insert(0, new_chunk)`.
5. `_id_to_index` extended for the new ids.
6. No repaint trigger — the new chunk's base already shows nothing styled (no cursor, no marks in its msg_ids); the existing chunks below it are unaffected.

### Filter / chat switch (cache invalidation)

1. Full `render_messages` rebuild — `_chunks` reset, every cached `_ChunkRender` dropped.
2. Cursor: preserve across `render_messages(_from_filter=True)` if its id is still in the new id_to_index; otherwise fall back to the *nearest still-visible* message (binary-search by timestamp, not always "latest" — avoids disorienting jumps when filtering narrows a window the user was already inside).
3. Marks preserved or cleared by the app-level logic that already handles this; HistoryView just renders whatever `apply_marks` is called with.

## Error handling

All failure modes are silent recoveries that re-establish a sane cursor/marks state.

- **Stale cursor id (chat-switch race)**: cursor-move actions look up `_cursor_msg_id` via `_id_to_index.get(...)`. On `None`, reset to `_all_messages[-1].message_id` and repaint. No raise.
- **Stale mark ids**: `apply_marks` already wipes state if either endpoint is missing from `id_to_index` (current behavior). Unchanged.
- **Selection across chunk boundaries**: `SelectionPainter` runs per-chunk and reads the global `_in_range_ids` — each chunk shows its overlap. No cross-chunk coupling needed.
- **scroll_to(y=target) after mount race**: wrap in `call_after_refresh` whenever the cursor's chunk was mounted in the same frame. Same pattern as §5c's anchor scroll.
- **Filter excludes the cursor id**: `render_messages(_from_filter=True)` finds the nearest still-visible message by timestamp and parks the cursor there. Never silently jumps to "latest" when the user was upstream.
- **Theme switch mid-session**: triggers a full re-render via Textual's theme reactive — that path already calls `render_messages` so cache rebuilds from scratch.
- **Click on stale meta after partial unmount**: `on_click` looks up `event.style.meta["msg_id"]` in `_id_to_index`; on miss, silently drop without posting `RangeMarkRequested`.

No new error modals; no user-facing failure UI introduced.

## Cursor + selection visual

### B+D scheme

Each rendered row carries a constant 2-col gutter (today's behavior, preserved). The *content* of the gutter is two spaces for every row — same characters whether cursored or not. The cursor visual is overlaid as **style only**, never content.

- **Cursor row** (when not on a selection row): the leading 2 gutter columns get background `accent_alt` (the colored bar — option D). The whole row gets background `bg_alt` (subtle row tint — option B).
- **Cursor row on a selection endpoint**: the row already has the endpoint background (`accent_alt`). B (the row tint) is suppressed because it would clash with the endpoint bg. The 2-col bar is repainted as `accent` (darker accent, still distinct against the endpoint's `accent_alt`) so the cursor stays visible.
- **Cursor row on an in-range row**: row already has `accent` background. Bar repainted as `accent_alt`; B suppressed.
- **Non-cursor rows**: unchanged from today (selection backgrounds where applicable; otherwise unstyled).

The "leading 2 columns get bg accent_alt" is achieved with a Rich span at `(row_start, row_start + 2)` carrying `on {accent_alt_hex}`. The row tint is a span at `(row_start, row_end)` carrying `on {bg_alt_hex}`. Both spans live on top of the cached `base` Text — they are added by `SelectionPainter` and discarded with the clone on the next paint.

### Cursor-follow scroll

After every cursor move, compute the cursor's absolute y in the scroll container:

```
y_in_chunk = sum(chunk.row_line_counts[m] for m in chunk.msg_ids[:cursor_idx_in_chunk])
           + n_day_headers_before_cursor
y_absolute = chunk.widget.region.y + y_in_chunk
```

`n_day_headers_before_cursor` is the count of distinct calendar days in `chunk.msg_ids[:cursor_idx_in_chunk]` (one extra rendered line per day boundary, matching how `_ChunkRender.build` emits day-header rows). Pre-compute a parallel `chunk.day_header_prefix_count: list[int]` at build time so the lookup is O(1).

Margin = 2 rows. If `viewport.top + 2 <= y_absolute <= viewport.bottom - 2`: no scroll. Otherwise snap so the cursor lands ~30% from the leading edge (top when moving up, bottom when moving down). `scroll_to(y=target, animate=False)`.

Edge cases:
- Cursor on the chunk that was just mounted (load-older + cursor at top): wrap in `call_after_refresh` so `region.y` is settled.
- Cursor at first message: `target = 0` (scroll fully to top).
- Cursor at last message: `target = max(0, virtual_size.height - viewport.height)` (scroll fully to bottom).

## Testing

### Layer 1 — pure unit tests in `tests/test_history_render.py`

- `format_row` against canned message dicts: single-line body, multi-line body (verifies wrap padding), edited message, empty body, attachment row, click meta presence.
- `_ChunkRender.build` round-trip: `base.plain[start:end] == expected_row_text(msg_id)` for every msg_id in `row_offsets`.
- `selection_colors(palette)` for both DAWNFOX and TERAFOX palettes; returns the expected (endpoint_bg, range_bg, cursor_bg, cursor_bar, contrast_fg) tuples.
- `paint(chunk, cursor, marks, palette)` cases: cursor only; cursor + single endpoint; cursor + active range; cursor on an endpoint row (B suppressed, D repaints); cursor on an in-range row; empty MarkState (no spans added beyond cursor).
- `paint` does not mutate `chunk.base` (asserts identity equality of `chunk.base` before and after, and content-equality of `chunk.base.plain` before and after).

These run in milliseconds; no Textual app needed.

### Layer 2 — integration in `tests/test_history_view_cursor.py` and `tests/test_history_view_range_marks.py`

- Existing tests adapted: gutter-glyph check (`▸` count) replaced with span check (cursor row has accent_alt bg on first 2 cols + bg_alt tint on full row).
- Shift+arrow extends selection from a fixed anchor across multiple presses; non-shift arrow afterward drops the anchor and just moves cursor.
- Home/End/PgUp/PgDn move cursor and trigger scroll-follow.
- Cursor-follow scroll: cursor at viewport bottom + Down → assert `scroll_y` increased; cursor in middle + Down → assert `scroll_y` unchanged.
- Stale cursor id on chat-switch race → recovers to latest with no raise.
- Selection spanning 2 chunks: paint runs on both chunks; both show overlap.
- Filter that excludes the cursor id parks cursor on nearest-by-timestamp message, not "latest."

### Layer 3 — perf pinning in `tests/test_history_view_perf.py` (new)

- Build a 4000-message chat in-memory; mount; call `_repaint_for_ids({some_id})` 100 times in a tight loop; assert total elapsed < 200 ms (2 ms/click budget).
- Skipped on CI if `IMESSAGE_SKIP_PERF=1`; runs locally.
- Pins "click latency stays under 5 ms" so future refactors can't silently reintroduce per-keypress `_build_blob` rebuilds.

### TDD order

1. Layer 1 first — write `test_history_render.py` against the planned interfaces, watch it fail, then implement `history_render.py` until it passes.
2. Layer 2 second — adapt existing tests to the new architecture before wiring HistoryView's actions.
3. Layer 3 last — run on the finished implementation to establish the baseline budget.

## Migration

The refactor is internal to HistoryView. App-level callers (`apply_marks`, `render_messages`, `filter_messages`) keep their existing signatures. No changes needed in `app.py` or anywhere outside `widgets.py` + the new `history_render.py`.

`tests/test_history_view_lazy_load.py`, `tests/test_history_view_range_marks.py`, `tests/test_history_view_cursor.py` all need targeted updates for the new visual scheme (B+D instead of `▸`) and the new bindings (Shift+arrow, Home/End/PgUp/PgDn). Existing perf invariant — "untouched chunks aren't repainted on a click" — is preserved verbatim.

## Out of scope (parked)

- Drag-to-select with the mouse.
- Native text selection (browser-style highlight to copy text out of a message).
- Animated scroll on cursor moves.
- Cursor wrap at top/bottom bounds.
- "Jump to next match" once chat-wide search lands.
