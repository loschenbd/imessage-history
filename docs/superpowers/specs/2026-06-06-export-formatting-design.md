# Export formatting improvements — design

**Date:** 2026-06-06
**Scope:** Single-file edits inside `imessage_export.py` to improve the rendered
look of all five export files (`conversation.{csv,json,txt,md}`,
`conversation_ai_ready.txt`) and the `analysis_prompt.txt` instructions.

## Goals

1. Make multi-day / multi-hour conversations navigable at a glance.
2. Preserve multi-paragraph message attribution in plain-text exports.
3. Eliminate empty-looking message rows when the underlying chat.db row was
   edited to nothing or could not be decoded.
4. Add lightweight machine-readable fields to CSV and JSON that mirror the
   visual day / gap structure (so downstream tools can group without
   re-parsing timestamps).

## Non-goals

- No URL linkification in markdown. Notion/Obsidian auto-link bare URLs;
  rewriting them is lossy and tradeoffs aren't clear.
- No collapsing of CSV multi-line cells. The current behavior is RFC 4180
  compliant; Excel/Sheets handle quoted multi-line cells.
- No JSON schema rewrites beyond a single additive field. Breaking changes
  to a schema documented in `sample_output_schema.md` are out of scope.
- No new dependencies. Project stays stdlib-only, Python 3.10+.

## New helpers

Added near the writers, before `format_message_body`:

```python
GAP_THRESHOLD_SECONDS = 30 * 60

def format_day_label(dt: datetime) -> str:
    """e.g. 'Saturday, June 6, 2026'. Avoids %-d (not portable on Windows
    Python; not relevant here, but uses string-concat to be safe)."""

def format_gap(seconds: int) -> str:
    """Human label for a silence >= GAP_THRESHOLD_SECONDS.
    Returns 'X min later' / 'Xh later' / 'Xh Ymin later' / 'X day(s) later'."""

def iter_render_events(messages: list[Message]):
    """Yield ('day', dt) / ('gap', seconds) / ('msg', m) tokens.

    A 'day' event fires when the calendar date changes (including for the
    first message). A 'gap' event fires only when the prior message is on
    the SAME calendar day and (current - prior) >= GAP_THRESHOLD_SECONDS —
    so we never double-mark with a day header AND a gap marker."""
```

## Per-format changes

### `conversation.txt`

- Insert `── Saturday, June 6, 2026 ──` before the first message of each
  calendar day.
- Insert `── 57 min later ──` mid-day when gap >= 30 min.
- Drop the date from each message's line prefix; use `[HH:MM:SS]` only.
- For multi-paragraph bodies, indent continuation paragraphs with 4 spaces.
  Preserve blank lines between paragraphs.

### `conversation_ai_ready.txt`

Same day-header + gap-marker + indented-continuation conventions as
`conversation.txt`, **but keep the full `[YYYY-MM-DD HH:MM:SS]` prefix on
each line**. The day header is a navigation aid; the per-line full
datetime stays so the LLM never has to scan upward for the date.

Update the header `Format:` line to mention day headers and indented
continuation paragraphs.

### `conversation.md`

- Day header rendered as `## Saturday, June 6, 2026`.
- Gap marker rendered as `_── 57 min later ──_`.
- Per-message bold header drops the date: `**09:00:08 · Mallory**`
  (the day header above carries the date).
- Empty-but-edited rows render the body as
  `_(edited; text not available)_` instead of a blank body following the
  speaker header.
- Otherwise existing markdown formatting preserved (attachment lines,
  unsent / app-payload italics, tapback target snippets).

### `conversation.csv`

Add a single new column `local_date` (YYYY-MM-DD), placed immediately
after `timestamp`. Populated by slicing `timestamp[:10]`. Multi-line
cells unchanged.

### `conversation.json`

Add a single per-message field `gap_seconds_before` (int; 0 for the
first message). Computed during write from successive
`timestamp_utc` values to avoid embedding render-time state in the
`Message` dataclass.

## Empty-message rendering

`format_message_body` (used by `.txt` and `_ai_ready.txt`) currently
returns the bare string `"[edited]"` for an edited row with no text and
no attachment. Update so that case returns
`"[edited; text not available]"`.

The markdown writer has a parallel rendering path that bypasses
`format_message_body`; mirror the same intent with
`_(edited; text not available)_`.

`[unsent]` and `[app payload: <bundle>]` already render visibly and need
no change.

## Analysis prompt

`ANALYSIS_PROMPT` adds two short notes:

1. `── Saturday, June 6, 2026 ──` and `── 57 min later ──` lines are
   navigation aids inserted by the exporter, not authored content.
2. A line indented under a `[time] Speaker: …` line is a continuation
   paragraph of that speaker's message.

## Migration

Existing exports under `exports/<contact>/<date>/` are not modified.
Re-running the exporter for the same window overwrites the old files
with the new format (existing behavior — no migration code needed).

## Risks

- **Concurrent edit by the other Claude session.** The other agent has
  been editing `imessage_export.py` in the last few minutes. The
  `Edit` tool's exact-match check protects against silent corruption,
  but a race could still require re-reading. Mitigation: do edits in
  small, well-anchored chunks; re-read if any `Edit` fails.
- **`gap_seconds_before` is computed from the in-window message list.**
  If a user re-runs the export with a different window, gap values
  for the first message of each run are 0 and downstream consumers
  must not interpret them globally. Documented in JSON metadata.

## Verification

- `python3 -m unittest discover -s tests` continues to pass (no test
  asserts on writer output).
- Manual: re-run the exporter for the existing Mallory window and
  diff the resulting `.md` / `.txt` / `_ai_ready.txt` against the
  pre-change versions to confirm day headers, gap markers, and
  indented continuation appear where expected.
