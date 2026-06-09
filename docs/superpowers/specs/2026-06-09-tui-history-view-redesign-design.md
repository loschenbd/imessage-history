# TUI history view redesign — design

**Date:** 2026-06-09
**Scope:** Rebuild how `HistoryView` renders message rows so the chat
reads as grouped speaker runs instead of a wall of `[H:MM AM/PM]
Speaker: body` lines. Adopt full-width day rules, footnote-style
reactions / empty / unsent / app rows, and a 2-cell body indent that
makes wrap continuations cheap.

## Goals

1. Speaker label appears once per run, not on every message. Consecutive
   messages from the same speaker (within a 30-minute gap, on the same
   calendar day) collapse under a single header.
2. Timestamps are quieter and on-line — plain `7:10 PM` in muted color,
   no brackets. First message of a run carries time in the speaker
   header; continuations get time inline at the body indent.
3. Day separators read as boundaries — full-width horizontal rules left
   and right of the date label, using `border_soft` for the rule and
   the existing `day_header` color for the label.
4. Reactions, empty/unsent rows, and app payloads look distinct from
   real messages — italic + muted, with placeholder text where there
   is none.
5. Body indent is 2 cells; wrap continuations align to the body
   indent so long messages don't burn a third of the viewport on the
   left margin (today's `_WRAP_INDENT = " " * 12`).
6. No regression in: cursor + range-mark selection painting,
   click-to-mark routing through `style.meta`, scroll-into-view math,
   chunk caching, lazy load-older, day-header dedup, perf budget
   (`HistoryView._repaint_for_ids < 2 ms/call on 4k messages`).

## Non-goals

- No bubbles, no right-alignment, no per-speaker columns (the
  "side-shifted" and "quote-bar" brainstorm options were rejected for
  being too busy for a TUI).
- No avatar / initials gutter.
- No reaction inline-decoration on the **target** message. Reactions
  stay on their own row, in chronological position, italic+muted —
  cheaper and avoids the "row mutates after the fact on scrollback"
  surprise of an overlay model.
- No re-architecting of the `_ChunkRender` / `paint` separation. The
  painter contract stays the same; only `_ChunkRender.build` and
  `format_row` change shape.
- No changes to how the Markdown / TXT / CSV / AI-ready writers
  render. Those produce export files and stay format-stable.
- No new theme colors.
- Reaction overlay on the **target** message, per-day collapsibility,
  "jump to date" picker, hyperlink coloring, attachment thumbnails —
  all deferred to follow-ups.

## Locked decisions (from brainstorm)

- **Run-break time gap:** 30 minutes. Same speaker after a ≥30-min
  silence starts a new run with its own header + blank separator
  above. Module-level constant `_RUN_TIME_GAP_MINUTES = 30`.
- **Body indent:** 2 cells. Module-level constant `_BODY_INDENT = 2`
  (replaces today's `_WRAP_INDENT = " " * 12`).
- **Reactions:** own line, italic + muted, body text
  `<glyph> to "<target snippet>"` using the existing `TAPBACK_GLYPHS`
  table; snippet capped at ~40 chars with trailing `…`.

## Per-message classification

Single pure function in `history_render.py`:

```python
class RowKind(Enum):
    NORMAL              = auto()   # plain message with body text
    REACTION            = auto()   # kind == "tapback"
    UNSENT              = auto()   # kind == "unsent"
    APP                 = auto()   # kind == "app"
    EMPTY_ATTACHMENT    = auto()   # kind=="message", empty text, has_attachment
    EMPTY_NO_CONTENT    = auto()   # kind=="message", empty text, no attachment

def classify(message: Message) -> RowKind: ...
```

Body rendering by kind:

| Kind | Body string | Style |
|---|---|---|
| `NORMAL` | `message.text` | default (palette `fg`) |
| `REACTION` | `<glyph> to "<snippet>"` (snippet ≤40 chars + `…`) | italic + muted |
| `UNSENT` | `(unsent)` | italic + muted |
| `APP` | `(<short_bundle> · app payload)` — see mapping below | italic + muted |
| `EMPTY_ATTACHMENT` | `(attachment)` | italic + muted |
| `EMPTY_NO_CONTENT` | `(no content)` | italic + muted |

If `is_edited == 1` (any kind), append ` (edited)` after the body in
muted (non-italic).

App bundle mapping (small table inside `history_render.py` — exact
match first, then a single startswith-`com.apple.messages.MSMessageExtensionBalloonPlugin`
prefix check, then fall back to the bare bundle id):

```
com.apple.messages.URLBalloonProvider                       → "URL preview"
com.apple.PassbookUIService.PeerPaymentMessagesExtension    → "Apple Pay"
com.apple.DigitalTouchBalloonProvider                       → "Digital Touch"
com.apple.Handwriting.HandwritingProvider                   → "Handwriting"
prefix com.apple.messages.MSMessageExtensionBalloonPlugin   → "App message"
```

## Run-grouping algorithm

A "run" is a maximal sequence of consecutive messages such that:

- all messages share the same `author_label`,
- all messages share the same calendar day (day-boundary always
  starts a fresh run),
- no two adjacent messages in the run are separated by ≥30 minutes
  (`abs(t_n - t_{n-1}) >= 30 min` ⇒ start a new run).

`_ChunkRender.build` walks the message slice once, accumulating
messages into a `current_run: list[Message]`, flushing the run via
`format_run(run, ...)` whenever the next message fails any condition.

## Emit shape (per run)

For the first run after a day-header, omit the leading blank line
(the day-header already provides separation). Otherwise:

```
<blank line>
<speaker>  ·  <h:mm AM/PM>          ← header line, msg_id = run[0].id
<indent><body>                       ← run[0] body line(s)
<indent><indent-time>  <body>        ← run[1] continuation (if any)
<indent><indent-time>  <body>        ← run[2] continuation (if any)
…
```

Where:

- `<speaker>` is rendered in **bold + speaker color**: `accent_alt`
  when `is_from_me` else `accent` (mirrors today's
  `HistoryView .speaker-me / .speaker-other` palette mapping).
- `·` is muted.
- `<h:mm AM/PM>` is muted.
- `<indent>` is `" " * _BODY_INDENT` (= 2 spaces).
- `<indent-time>` is `<h:mm AM/PM>` in muted, right-padded so the
  body column on every continuation in the run aligns. The pad
  math: continuation body column =
  `_BODY_INDENT + max(len(time_str) for time_str in run) + 2`.
  A single pre-pass over the run's times computes this. All
  continuations in the run share that body column deterministically.

Header line's segments carry `meta={"msg_id": run[0].message_id}` so
click routing on a header behaves identically to clicking the first
message body (matches user mental model: "I clicked on Beautiful Wife
at 7:10 PM").

## Wrap continuation

When a body line wraps within Rich's word-wrap, continuation visual
rows inside the same logical line indent to the same body column as
the line they belong to. Computed per-message via the same
"body column" the emit shape above derives. (Today this is
`_WRAP_INDENT = " " * 12` — flat, doesn't account for the speaker
header structure.)

## Day-header — full-width rule

Today: `── <Weekday>, <Month> <D>, <YYYY> ──` in bold cyan.

Proposed:

```
─────────────  Saturday, June 6, 2026  ─────────────
```

Computed in `_ChunkRender.build` from the `width` parameter it already
receives:

```python
label = f"  {dt.strftime('%A, %B %-d, %Y')}  "
total_rule_chars = max(0, width - len(label))
left = total_rule_chars // 2
right = total_rule_chars - left
header = f"{'─' * left}{label}{'─' * right}\n"
```

When `width is None` (cold cache before mount), fall back to the
current short-rule format. The date label uses the existing
`day_header` palette color; the `─` rule itself uses `border_soft`.

## API change: format_row → format_run

`format_row` (per-message) becomes a private helper consumed inside
`format_run` (per-run). The exported builder API becomes:

```python
def format_run(
    run: list[Message],
    contacts: dict,
    *,
    width: int | None,
    suppress_leading_blank: bool,  # True for the first run after a day header
    palette: dict,
) -> tuple[list[tuple[str, Style]], dict[int, int]]:
    """Return (segments, line_count_per_msg_id) for one speaker run."""
```

The chunk builder calls `format_run` per detected run instead of
`format_row` per message, then stitches the per-run `line_count_per_msg_id`
back into `_ChunkRender.row_line_counts` and the per-message header /
body byte offsets into `row_offsets`. `prefix_lines_above` accounts for
day-headers, the day-header's trailing newline, the blank separator
before each non-first run, and the run's speaker header line.

The painter contract `paint(chunk, cursor_id, marks, palette) -> Text`
is unchanged. Its row_offsets + msg_ids stay the same shape; the
mapping is just produced by `format_run` instead of `format_row`.

## Files touched

| File | Change |
|---|---|
| `imessage_export/tui/app/history_render.py` | Add `RowKind` enum + `classify` function; add `_render_body(message, kind) -> (text, style_spec)` helper; replace `format_row` with `format_run`; rewrite `_ChunkRender.build` to detect speaker runs and emit blank separators + speaker headers + continuation lines; day-header gains full-width rule when `width` is known. `_ChunkRender.build` signature gains a required `palette: dict` parameter (speaker color baked into the cached base Text — see "Theme switching" below). Module-level constants `_RUN_TIME_GAP_MINUTES = 30`, `_BODY_INDENT = 2`, `_APP_BUNDLE_NAMES`. |
| `imessage_export/tui/app/widgets.py` | `HistoryView.DEFAULT_CSS` — drop now-unused `.message-row` rules (selection bg moves to the painter); keep `.history-placeholder`, `.load-more-affordance`, `.beginning-marker` intact. Remove `_WRAP_INDENT` references — wrap indent is now per-message. |
| `imessage_export/tui/app/app.py` | App CSS — drop the `.speaker-other` / `.speaker-me` Rich-class selectors now that styling happens in `_render_body` / `format_run` directly using palette hex from `theme.PALETTES`. No structural change. |
| `tests/test_history_render.py` *(new)* | `classify` per RowKind branch; `format_run` segment shapes for single-message run, multi-message run, run that spans 30-min gap (must split), run that crosses day boundary (must split); reaction / unsent / app / empty rendering; day-header full-width rule width math; meta routing (run header carries first message's msg_id). |
| `tests/test_history_view_cursor.py` | Existing cursor + scroll-into-view tests — verify `row_offsets` + `prefix_lines_above` math still works under the new emit shape; update any assertions that walked the old `[h:mm pm] speaker: body` segment layout. |
| `tests/test_app_navigation.py` | Update any assertions that walked the literal flat single-line message format. |

## Edge cases and failure modes

- **Run that ends at the end of a chunk and continues at the start of
  the next loaded older chunk.** Each chunk is built independently
  from its own slice, so the same logical run emits two headers (one
  per chunk). Acceptable — looks like two adjacent runs separated by
  the chunk boundary, matching how the user mentally pages older
  content. No cross-chunk dedup.
- **Very long runs (50+ messages).** Speaker header at top, 50
  continuation lines under it. No visual problem; scroll math
  unchanged.
- **Tapback target message missing from the current chunk.**
  `Message.reaction["target_text"]` was captured at export time, so
  the snippet is in-hand — no live-lookup needed across chunks.
- **Edited messages.** `is_edited == 1` ⇒ append muted ` (edited)`
  after the body. Doesn't change classification — an edited reaction
  is still rendered as a reaction with `(edited)` appended.
- **Header-line click routing.** Header line's `meta` carries the
  first message of the run. Click-to-mark on the header behaves
  identically to clicking the first message body.
- **Cursor on header line vs body line.** `_cursor_msg_id` is still
  per-`message_id`. The painter paints the cursor bar on whichever
  rendered line carries that `msg_id`'s `meta`. For the first message
  of a run, that's both the header line and the body line(s) — they
  highlight together, which reads as "the cursor is on this message".
- **Range selection across runs.** The painter's row_offsets covers
  the **body span** of each message. Selection bg paints across body
  spans as today. Header lines and blank separators do **not** carry
  selection bg (sub-row furniture, not part of the message body) —
  selecting a range of messages shouldn't repaint the speaker header
  above it.
- **scroll-into-view math.** `_scroll_cursor_into_view` consumes
  `prefix_lines_above` to compute y. Header lines and blank separators
  must be counted in `prefix_lines_above`. Existing scroll-pinning
  tests pin this.
- **30-min gap at midnight.** Day boundary fires first; speaker run
  resets at the day-header regardless of the time gap. The 30-min
  rule only matters within a single calendar day.
- **`width` unknown at first build.** Falls back to the short-rule
  day header and to "explicit newlines only" line counts (today's
  behavior path). On the next render where width is known, the chunk
  rebuilds — the cached `base` is regenerated by `_ChunkRender.build`,
  not mutated in place.
- **Theme switching.** Speaker color is baked into the cached base
  Text at build time (Rich Text inline style with explicit palette
  hex). Switching themes via the Settings modal triggers
  `HistoryView.render_messages` on next chat selection, which rebuilds
  all `_ChunkRender` instances. Until then, already-mounted chunks
  keep the old theme's speaker color. Acceptable — theme switching
  is a rare action and the rest of the surface (Textual CSS-driven)
  updates immediately, so the discrepancy is contained to mounted
  chunks. A follow-up could invalidate on theme change explicitly;
  out of scope here.

## Performance

- `_ChunkRender.build` stays O(N) over the message slice. Run
  detection is a single forward pass with O(1) per-message state
  (prev author, prev timestamp).
- `classify` is a constant-time switch.
- The style cache (`_STYLE_CACHE`) absorbs ~6 new style strings
  (`speaker-me bold`, `speaker-other bold`, `muted`, `muted italic`,
  `day_header bold`, `border_soft`).
- `paint()` is unchanged.
- Pinned perf budget (`HistoryView._repaint_for_ids < 2 ms/call on
  4k messages`) is unaffected.

## Testing strategy

- **Unit tests** in `tests/test_history_render.py`: each `RowKind`
  branch, each transition case (speaker change, day boundary, 30-min
  gap), reaction snippet capping, app bundle mapping, `is_edited`
  append, day-header rule width math.
- **Updated integration tests** in `tests/test_history_view_cursor.py`
  and `tests/test_app_navigation.py` walk the new emit shape.
- **Manual smoke** (PR description): load a long chat, verify (a)
  speaker headers appear once per run, (b) 30-min gaps split runs,
  (c) reactions read as footnotes, (d) empty rows show
  `(attachment)`/`(no content)`, (e) day headers stretch full-width,
  (f) cursor + range-mark selection still highlight correctly across
  the new emit shape, (g) scroll-into-view still lands the cursor at
  the 30/70% sweet spots.
