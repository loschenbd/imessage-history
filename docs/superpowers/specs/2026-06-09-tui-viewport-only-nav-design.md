# TUI viewport-only navigation — design

**Date:** 2026-06-09
**Scope:** Drop the keyboard cursor from `HistoryView`. Arrows scroll the
viewport like every other chat app; range marks are click-driven.
Eliminate the entire class of "cursor and viewport desync" bugs by
collapsing two pieces of state into one.

## Goals

1. Arrows behave like a normal scroll surface — viewport moves by 1 row
   per press, no jumps, no off-screen state.
2. PageUp/PageDown scroll by one viewport height; Home/End jump to top
   of loaded / bottom respectively.
3. Range marks remain click-driven (click → endpoint, click another →
   second endpoint, click after both → move nearest endpoint, Esc →
   clear). Unchanged from today.
4. Scrolling within 5 rows of the top auto-loads the next older chunk;
   the existing scroll-restore logic keeps reading position stable.
5. No regression in: type-to-filter, theme switching, focus-region
   tracking on `StatusLine`, chunk caching, the `paint()` mark painter,
   `apply_marks` re-render after chat switch, the click-routing
   `style.meta` model.

## Non-goals

- No keyboard-only range marking. Space / Enter / Shift+arrow get
  unbound in the history pane. Considered (mark on topmost-visible
  with a gutter glyph) and rejected — the user picked click-only.
- No "jump to message by date / search text" feature. Separate spec
  if we ever want it.
- No touchpad inertia tuning, no smooth-scroll animation. Viewport
  moves discretely 1 row per arrow press; `animate=False` everywhere.
- No persisting scroll position across chat switches.
- No change to Sidebar, ChatHeader, WindowStrip, StatusLine, or the
  sidebar 2-line-card redesign that's currently WIP on this branch.

## Why this, not the alternatives

Three navigation models were considered in brainstorm:

- **A. Viewport-only (chosen).** Matches Slack, Discord, Telegram,
  Messages.app, weechat, irssi, slack-term, scli, toxic. By far the
  most familiar pattern for chat readers. One source of truth
  (`scroll_y`) eliminates the desync bug class entirely.
- **B. Cursor IS the position; viewport always follows it
  (vim/less/tmux-copy-mode).** Predictable for power users, but the
  cursor visual is always on-screen and mouse-scrolling silently
  mutates the cursor — surprising mid-selection.
- **C. Cursor is sticky off-screen; first arrow snaps without walking
  (VS Code).** Smallest behavioral change, but keeps two pieces of
  state and a hidden "is the cursor visible?" mode that influences
  the first-arrow result.

A wins on simplicity and on matching prior user expectation from
every other chat app on their machine. The current bugs (5-week
viewport yank, cursor bar clobbering "Ja" in "Jaime", off-screen
walks) all stem from the dual-state model that A removes.

## Current state (baseline)

`HistoryView` owns two pieces of "where am I" state:

- A keyboard cursor (`_cursor_msg_id`) — per-row marker the user moves
  with arrows. Painted as the B+D cursor visual (row tint + leading
  bar) by `paint()` Layer 2 + Layer 3 in `history_render.py`.
- A viewport scroll (`scroll_y`) — inherited from `VerticalScroll`,
  mouse-scrollable.

Plus a shift+arrow scratchpad (`_mark_anchor_id` / `_mark_active_id`)
and the `SelectionExtended` Textual message they emit.

The cursor and viewport can desync (user mouse-scrolls away → cursor
off-screen). `_scroll_cursor_into_view` snaps the view back to the
cursor on the next arrow press, which causes the dramatic yank when
the snap math hits an unmounted chunk or when `widget.region.y`
returns a stale value from a still-mounted-but-far-above chunk.

## Proposed state

### 1. Bindings

| Key | Behavior in history pane |
|---|---|
| **↑ / ↓** | Scroll viewport by 1 row |
| **PageUp / PageDown** | Scroll viewport by one viewport height |
| **Home** | Scroll to top of currently-loaded content |
| **End** | Scroll to bottom (latest message) |
| **← / →** | Unchanged — bridge to Sidebar / between panes |
| **Esc** | Clear marks (unchanged) |
| **o** | Load older (unchanged) |
| **Space / Enter** | Unbound (was: drop mark at cursor row) |
| **Shift+↑ / Shift+↓** | Unbound (was: extend selection from anchor) |
| **Click on a message row** | Drop a range-mark endpoint (unchanged) |
| **Click on "Load older" affordance** | Load older chunk (unchanged) |

### 2. Auto-load on near-top scroll

When the user scrolls (arrow / PgUp / Home / mouse-wheel) such that
`scroll_y < AUTOLOAD_TOP_MARGIN` (5 rows) AND
`_shown_count < len(_all_messages)`, fire `action_load_older` once.
The existing `_preserve_position_with_peek` logic keeps the user's
reading position stable across the chunk mount; from the user's
perspective the scroll just keeps going through newly-revealed older
content.

```python
AUTOLOAD_TOP_MARGIN = 5

def _check_autoload_threshold(self) -> None:
    if self._shown_count >= len(self._all_messages):
        return
    if self.scroll_y < self.AUTOLOAD_TOP_MARGIN:
        self.action_load_older()
```

Called from `action_scroll_up`, `action_page_up`, `action_scroll_top`,
and from a `Scroll` event handler so mouse-wheel scrolling also
auto-loads.

### 3. State that dies on `HistoryView`

- `_cursor_msg_id`
- `_id_to_index`
- `_mark_anchor_id`, `_mark_active_id`
- `SelectionExtended` message class
- Methods: `action_cursor_up`, `action_cursor_down`,
  `action_extend_up`, `action_extend_down`,
  `action_cursor_to_start`, `action_cursor_to_end`,
  `action_mark_row`,
  `_move_cursor`, `_extend_selection`, `_jump_cursor_to`,
  `_scroll_cursor_into_view`, `_ensure_id_rendered`,
  `_nearest_loaded_by_timestamp`.
- `action_page_up` and `action_page_down` are reused as method names
  but their bodies are completely rewritten (cursor walk →
  viewport scroll). See §5 below.

### 4. State that stays

- `_mark_start_id`, `_mark_end_id`, `_in_range_ids` — committed
  range marks driven by clicks.
- `_all_messages`, `_unfiltered_messages`, `_shown_count` — chunk
  lazy-load infrastructure.
- `_topmost_widget`, `_load_more_widget`, per-child
  `_chunk_render` — chunk mounting + the "Load older" affordance.
- `apply_marks(start_id, end_id, messages)` — the app→view bridge
  for committed marks.

### 5. State that's new

- `AUTOLOAD_TOP_MARGIN = 5` class constant.
- Methods: `action_scroll_up`, `action_scroll_down`,
  `action_page_up`, `action_page_down`, `action_scroll_top`,
  `action_scroll_bottom`, `_check_autoload_threshold`.
- A `Scroll` event listener to fire the autoload check on mouse-wheel.

### 6. `paint()` in `history_render.py`

Drop the `cursor_id` parameter and Layers 2 & 3:

```python
def paint(
    chunk: _ChunkRender,
    marks: MarkState,
    palette: dict,
) -> Text:
    """Clone the chunk's cached base and overlay selection spans.

    Endpoint vs in-range: endpoint bg always wins, in-range layers
    under it. No cursor visual — viewport scroll position is the
    "where am I" indicator.
    """
    out = chunk.base.copy()
    colors = selection_colors(palette)
    endpoints = {marks.anchor_id, marks.active_id} - {None}
    for msg_id in chunk.msg_ids:
        start, end = chunk.row_offsets[msg_id]
        if msg_id in endpoints and colors.endpoint_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.endpoint_bg}"),
                start, end,
            )
        elif msg_id in marks.in_range_ids and colors.range_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.range_bg}"),
                start, end,
            )
    return out
```

`header_offsets` stays on `_ChunkRender` — speaker-header clicks
still need to route to the run's first message via `style.meta`.
`MarkState` keeps `anchor_id` and `active_id` fields; they map to
`_mark_start_id` / `_mark_end_id` (the committed endpoints) and
they happen to be named "anchor/active" for historical reasons —
rename optional, not required for this refactor.

### 7. Data flow — click

The stale-id check in `on_click` currently uses `self._id_to_index`,
which dies with the cursor. Replace it with a `self._loaded_ids: set[int]`
built in `render_messages` from `_all_messages` — the only thing the
click handler needs from it is "is this id present?", which is `O(1)`
either way.

```
event → HistoryView.on_click
      → style.meta["msg_id"]
      → drop if msg_id not in _loaded_ids       ← new: was _id_to_index
      → post RangeMarkRequested(msg_id)
      → app updates state.range_*_msg_id
      → app calls history.apply_marks(start, end, messages)
      → repaint affected chunks via _repaint_for_ids
```

The "click also moves the keyboard cursor" branch added during the
cursor work goes away with the cursor.

### 8. Data flow — arrow / PgUp / mouse-wheel

```
arrow key
  → action_scroll_up/down/page_up/page_down/scroll_top/scroll_bottom
  → self.scroll_relative(y=delta, animate=False)
     (scroll_to(y=0) for Home; scroll_end() for End)
  → self._check_autoload_threshold()

mouse wheel
  → built-in VerticalScroll.scroll_y mutation
  → on_scroll event
  → self._check_autoload_threshold()
```

### 9. Files touched

| File | Change |
|---|---|
| `imessage_export/tui/app/widgets.py` | Strip `HistoryView` cursor state + cursor actions. Rewrite `BINDINGS`. Add `action_scroll_*` + `_check_autoload_threshold` + `on_scroll`. Delete `SelectionExtended` inner class. Simplify `on_click` (no cursor move branch). |
| `imessage_export/tui/app/history_render.py` | `paint()` drops `cursor_id` arg + Layers 2 & 3. `_ChunkRender.header_offsets` stays. `MarkState` keeps both fields. |
| `imessage_export/tui/app/app.py` | Delete `on_history_view_selection_extended` handler. No CSS changes (the `.speaker-other` / `.speaker-me` selectors were already dead — speaker color is baked into segments). |
| `imessage_export/tui/app/modals.py` | `HelpModal` text rewrite to match viewport-only bindings: drop Shift+arrow / Space-marks-cursor / cursor-bar wording. |
| `tests/test_history_view_cursor.py` | **Delete entire file** — every test pins behavior that's going away. |
| `tests/test_history_render.py` | Drop `cursor_id` arg from all `paint()` calls; delete four cursor-visual tests. ~60 of 68 tests survive. |
| `tests/test_app_navigation.py` | Drop sidebar↔history shift+arrow tests if any; keep the chat-list↔history bridge tests. |
| `tests/test_history_view_scroll.py` *(new)* | Pin the new viewport-only model — 12 tests, listed below. |

**Net code delta:** roughly −500 lines, +120 lines. The cursor model
was the dominant source of complexity in `HistoryView`.

## Edge cases

- **Chat switch with marks set.** App-level mark cleanup already
  clears `range_*_msg_id` on `Sidebar.ChatSelected`. `apply_marks(None,
  None, [])` clears visual highlights. No cursor state to reset.

- **Type-to-filter narrows the message set.** Committed endpoints may
  now be in or out of the filtered subset. `apply_marks`'s defensive
  branch (clears visual marks when `start_id` or `end_id` isn't in
  `messages`) handles both cases — unchanged from today.

- **Filter clears, marks restore.** `apply_marks` re-runs after the
  filter clears; committed marks paint back. Unchanged.

- **Auto-load fires repeatedly near top.** `action_load_older` is
  guarded by `if self._shown_count >= len(self._all_messages): return`,
  so over-load is impossible. Mounting is synchronous; `_shown_count`
  mutates before `_check_autoload_threshold` returns, so the next arrow
  press sees the updated count.

- **Mark routing on the "Load older" affordance.** `on_click` checks
  `target is self._load_more_widget` before reading `style.meta`.
  Unchanged.

- **Speaker-header click.** Header line carries `meta={"msg_id":
  run[0].message_id}`. Click on a header drops a mark on the run's
  first message — matches the existing
  `test_run_header_meta_still_routes_to_run_head_only`.

- **Empty chat / placeholder state.** Arrow keys hit a placeholder
  Static. `scroll_relative` on a non-scrollable view is a no-op.
  No crash.

- **Mounted-chunk widget swap during `remove_children()`.** Not a
  concern anymore — no `_scroll_cursor_into_view` walking children
  looking for a chunk. `_check_autoload_threshold` only reads
  `self.scroll_y` and `self._shown_count`.

- **Theme switching.** Mark colors come from `selection_colors(palette)`
  composed at paint time, so theme switches repaint marks correctly on
  the next `apply_marks` call. Unchanged.

## Testing strategy

### Delete

- `tests/test_history_view_cursor.py` — 26 tests, all pinning cursor
  walk / extend / shift+arrow / off-screen-snap behavior.

### Trim

- `tests/test_history_render.py` — drop `cursor_id` arg from all
  `paint()` calls; delete four cursor-visual tests
  (`test_cursor_visual_renders_on_exactly_one_row`,
  `test_cursor_tint_on_speaker_header`,
  `test_cursor_bar_on_speaker_header`,
  `test_cursor_bar_color_against_selection_bg`).
- `tests/test_app_navigation.py` — drop any shift+arrow assertions;
  keep the chat-list↔history focus-bridge tests.

### Add `tests/test_history_view_scroll.py`

1. `test_up_arrow_scrolls_one_row` — render 200 messages, focus
   history, scroll to middle, press Up, assert `scroll_y` decreased
   by 1 (or close — Textual's row math is fractional).
2. `test_down_arrow_scrolls_one_row` — symmetric.
3. `test_page_up_scrolls_one_viewport` — assert `scroll_y` decreased
   by ~`_viewport_height_lines()`.
4. `test_page_down_scrolls_one_viewport` — symmetric.
5. `test_home_scrolls_to_top_of_loaded` — render 3 chunks worth,
   press Home, assert `scroll_y == 0`, assert `_shown_count`
   unchanged (no autoload-all-the-way).
6. `test_end_scrolls_to_bottom` — render, scroll up, press End,
   assert at bottom.
7. `test_autoload_fires_within_5_rows_of_top` — render
   `PREVIEW_CAP * 3` messages (one chunk visible), scroll near top,
   press Up, assert `_shown_count` increased by `LOAD_MORE_CHUNK`.
8. `test_autoload_no_op_at_chat_start` — render full chat
   (`_shown_count == len(_all_messages)`), scroll to top, press Up,
   assert no crash, `_shown_count` unchanged.
9. `test_no_cursor_state_after_render` — assert `_cursor_msg_id`
   attribute does not exist on the instance. Guards against
   accidental reintroduction during future edits.
10. `test_space_does_nothing_in_history_pane` — focus history, press
    Space, assert no `RangeMarkRequested` posted.
11. `test_shift_arrow_does_nothing` — focus history, press
    Shift+Down, assert no posted messages, `scroll_y` unchanged.
12. `test_click_drops_mark_and_nothing_else` — render, click a
    message, assert exactly one `RangeMarkRequested` posted, no
    cursor side effect, no `SelectionExtended`.
13. `test_left_arrow_bridges_to_sidebar` — focus history, press
    Left, assert focus is on sidebar list (existing behavior, guard
    against accidental removal).

### Manual smoke test (PR description)

1. Launch the TUI on a 70k-message chat.
2. Press ↓ — viewport scrolls down 1 row, no jump.
3. Press ↑ many times — viewport scrolls up 1 row each, never yanks.
4. PageUp/PageDown — viewport scrolls by ~viewport height each.
5. Home — viewport at top of loaded; "Load older" affordance visible.
6. End — viewport at bottom showing latest message.
7. Scroll near top with arrows — older chunk auto-mounts, reading
   position stable, viewport keeps moving through newly-revealed
   content.
8. Click two messages — range highlights between them.
9. Click a third message — nearest endpoint moves.
10. Esc — marks clear.
11. Theme switch via Settings — marks repaint in new theme on next
    chat selection.
12. Type-to-filter from sidebar — message set narrows, marks clear or
    paint correctly depending on whether endpoints survive the filter.

### Acceptance criteria

1. The full unit-test suite passes.
2. Manual smoke checklist above passes.
3. `HistoryView` no longer has `_cursor_msg_id`, `_id_to_index`,
   `_mark_anchor_id`, `_mark_active_id`, or `SelectionExtended`.

## Out of scope (deferred)

- "Jump to message by date / search text" picker.
- Keyboard-only range marking (Space-on-topmost-visible with gutter
  glyph). Considered, rejected for now.
- Persisting scroll position across chat switches.
- Smooth-scroll animation. Discrete steps are predictable; animation
  adds rendering work for unclear UX gain in a TUI.
- Touchscreen / trackpad inertia tuning.
