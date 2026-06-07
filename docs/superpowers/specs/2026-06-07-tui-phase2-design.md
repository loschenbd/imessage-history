# TUI makeover (Phase 2) — design

**Date:** 2026-06-07
**Scope:** Ship a full Textual app that becomes the default interactive
surface for `imessage-export` on a TTY. The Phase 1 wizard moves behind
`--wizard` and stays available unchanged. Headless flag surface unchanged.

## Goals

1. Bare `imessage-export` on a TTY opens a Textual app with three regions:
   a sidebar chat list, a scrollable message-history pane, and a footer
   action bar with status line.
2. The user picks a chat, picks a window either visually (click two
   messages or arrow-Enter to mark them) or numerically (footer Window
   modal), and triggers an export — without leaving the app.
3. Every configurable option the wizard exposes (contacts file, output
   directory, "me" label, redaction) is reachable from a footer button
   that opens a modal.
4. After a successful export the app stays open: a banner confirms what
   was written, the range marks and typed window clear, the chat
   selection and settings persist. Subsequent exports go through the
   same loop without restarting.
5. The wizard stays available behind `--wizard` for users who prefer a
   linear flow.
6. Bindings stay conventional (arrows, Enter, Tab, Esc, mouse).
   Single-letter accelerators tied to visible button labels layer on
   top for power users — never hidden behind a mode.

## Non-goals

- No vim-flavored grammar (`v`-mode, `:command`, `j/k/gg/G`). All
  interactions are discoverable from the UI.
- No changes to `db.py`, `decoder.py`, `writers.py`, `redactor.py`,
  `contacts.py`, or `contacts_macos.py`. Phase 2 is a new entrypoint
  and a new UI shell over the existing pipeline.
- No batch-export workbench. App stays open across exports for
  same-session iteration, but no queue, no multi-chat run-list.
- No search-within-chat in v1 (deferred to Phase 3).
- No lazy-windowed history for chats >20k messages in v1 (flagged in
  Risks; deferred to Phase 3).
- No mouse-only features. Every mouse action has a keyboard equivalent.
- No telemetry, no network calls, no auto-update.

## Dependencies and packaging

```toml
[project.optional-dependencies]
tui = [
  "rich>=13",
  "questionary>=2",
  "textual>=0.79,<1.0",
]
```

- Textual joins the existing `[tui]` extra; Rich is already a Textual
  transitive dep, so no extra wheel weight from listing it explicitly.
- Upper bound `<1.0` because Textual is pre-1.0 and breaks API between
  minors. Re-evaluate at 1.0.
- Headless install (`pip install imessage-history`) stays zero-deps.
- The wizard install (`pip install 'imessage-history[tui]'`) now also
  pulls Textual. Users who only want the wizard pay one extra ~5 MB
  dep — acceptable; the wizard is rarely the default after Phase 2.

README change: add an "Interactive app" section above the existing
"Wizard" section with a screenshot. Privacy posture sentences stay
unchanged.

CLAUDE.md change: replace the "TUI under `imessage_export/tui/` is
only importable when the `[tui]` extra is installed" sentence with
"TUI surfaces live under `imessage_export/tui/`. The wizard
(`tui/wizard.py`) and the Textual app (`tui/app/`) are both opt-in
behind the `[tui]` extra; core modules must not import either at
module top level."

## Entry behavior

```python
# imessage_export/cli.py
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    no_explicit_args = (argv is None or len(argv) == 0)
    is_tty = sys.stdout.isatty() and sys.stderr.isatty()
    is_ci = bool(os.environ.get("CI") or os.environ.get("NONINTERACTIVE"))
    has_action_flag = any([
        args.list, args.list_contacts, args.chat_id, args.chat_identifier,
        args.participant, args.from_date, args.to_date, args.date,
        args.build_contacts, args.suggest_names,
    ])

    if no_explicit_args and is_tty and not is_ci and not has_action_flag:
        if args.wizard:
            return _run_wizard()
        return _run_app()                     # NEW default

    if args.app:                              # explicit override
        return _run_app()

    if args.list and is_tty and not is_ci:
        return _list_with_rich_table()
    if args.list_contacts and is_tty and not is_ci:
        return _list_contacts_with_rich_table()

    return _run_headless(args)
```

New flags:
- `--wizard` — boolean, default False. Forces the Phase 1 questionary
  flow even on a fresh TTY.
- `--app` — boolean, default False. Forces the Textual app even if
  other args are present. Useful for `--app --chat-id 142` to launch
  the app pre-focused on a chat (post-1.0 nice-to-have; v1 may ignore
  the secondary flags and just open the app).

If `[tui]` is not installed and `_run_app()` is reached, the import of
`imessage_export.tui.app` raises `ImportError`. `cli.py` catches once
and prints the existing friendly install hint, exit 2.

## Layout

```
┌─ imessage-export ────────────────────────────────────────────────────────────┐
│ ┌────────────────────────┐  ┌────────────────────────────────────────────┐   │
│ │ Filter:  [__________]  │  │ Family (5)  ·  2026-06-06                  │   │
│ │ ──────────────────────  │  │                                            │   │
│ │ > Mallory     142      │  │  ── Saturday, June 6, 2026 ──              │   │
│ │   Family      312      │  │  [09:00:08] Mallory: Hey                   │   │
│ │   Work         45      │  │  [09:00:41] Me:      morning               │   │
│ │   Alex Chen   1.2k     │  │ ╔══════════════════════════════════════╗   │   │
│ │   ...                  │  │ ║ [10:30:12] Mallory: lunch?           ║   │   │
│ │                        │  │ ║ [10:31:55] Me:      yeah, where?     ║   │   │
│ │                        │  │ ║ [11:42:03] Mallory: noodle bar?      ║   │   │
│ │                        │  │ ╚══════════════════════════════════════╝   │   │
│ │                        │  │  [11:43:11] Me:      see you there         │   │
│ └────────────────────────┘  └────────────────────────────────────────────┘   │
│ window: 09:00 → 11:42 (2026-06-06) · 4 msgs · output: ./exports · redact: off│
│ [ Window… ]  [ Settings… ]  [ Redact… ]  [ Export ]  [ Wizard ]  [ Help ]  [ Quit ] │
└──────────────────────────────────────────────────────────────────────────────┘
```

Regions:

- **Sidebar (left, fixed ~28 cols):** Filter input at top, then a
  scrollable list of recent chats. Each row shows display name (or
  resolved participants via `contacts.csv`), message count.
  Selection follows arrow keys; Enter or click opens the chat in the
  history pane.
- **History pane (right, flex):** Header row with chat name, group
  size, and current date if a single day is being viewed. Below: a
  scrollable, styled message list. Day headers, gap markers, and
  speaker headers match the conventions from
  `docs/superpowers/specs/2026-06-06-export-formatting-design.md` so
  the preview reads exactly like the exported `conversation.md`.
- **Status line (one row above the action bar):** `window`,
  `<resolved msg count>`, `output`, `redact`. Updates on every state
  change. After a successful export, briefly shows
  `✓ Exported N msgs → <path>` until the next mutation.
- **Action bar (bottom row):** Six buttons separated by whitespace:
  `[ Window… ]  [ Settings… ]  [ Redact… ]  [ Export ]  [ Wizard ]
  [ Help ]  [ Quit ]`. Each has a single underlined accelerator
  letter — `W`, `S`, `R`, `E`, `Z`, `H`, `Q`.

## Bindings

Every action has at least one of: mouse, focus-then-Enter, accelerator.

| Input | Effect |
|---|---|
| Mouse click on a sidebar row | Select that chat, load its history |
| Mouse click on a message in history | Mark range start (first click) or end (second click) or clear (third click) |
| Mouse click on a button | Activate it |
| Mouse click in filter input | Focus the filter |
| `Tab` / `Shift-Tab` | Cycle focus: filter → sidebar → history → button row → wrap |
| `↑` / `↓` | Move within the focused region (sidebar selection, history scroll) |
| `Enter` | Activate focused thing: open chat (sidebar), mark range endpoint (history), click button |
| `Space` | Alias for Enter in the history pane only |
| `Esc` | Close any open modal; clear range marks in history; cancel filter input |
| `PgUp` / `PgDn` / `Home` / `End` | Page / jump in history |
| `W` / `S` / `R` / `E` / `Z` / `H` / `Q` | Activate the matching button (suppressed when a text input has focus) |

Accelerator letters are visibly underlined in their button labels.
Help modal (`H`) lists every binding above.

## Range selection

State on `AppState` (see State machine):

- `range_start_msg_id: int | None`
- `range_end_msg_id: int | None`
- `typed_window: dict | None` — the same shape the wizard produces:
  `{"mode": "day", "date": "...", "start_time": "...", "end_time": "..."}`
  or `{"mode": "range", "from_date": "...", "to_date": "..."}` or
  `{"mode": "all"}`.
- `window_source: Literal["selection", "typed", "all"]`

Mutations:

- Clicking / Enter on a history message that is not already a range
  endpoint:
  - If `range_start_msg_id is None` → set start.
  - Else if `range_end_msg_id is None` → set end. If the new message
    is earlier than start, swap.
  - Else → clear both, set start to the clicked message.
  - In all cases set `window_source = "selection"`.
- Pressing Esc while focus is in history → clear both endpoints. If
  `typed_window` is set, `window_source` falls back to `"typed"`;
  otherwise `"all"`.
- Saving the Window modal → set `typed_window`, set
  `window_source = "typed"`. Does NOT clear range marks (the user can
  still see what they marked); they're just no longer the active
  source.
- Any successful export → clear both endpoints, clear `typed_window`,
  reset `window_source = "all"`.

Resolved window for export:

```python
def resolved_window(state) -> dict:
    if state.window_source == "selection" and state.range_start_msg_id:
        # convert two message ids to a {mode: "range", from_date, to_date,
        # start_time, end_time} dict, inclusive on both ends.
        return _bracket_to_window(state, ...)
    if state.window_source == "typed" and state.typed_window:
        return state.typed_window
    return {"mode": "all"}
```

The status line always quotes the source: `(from selection)`,
`(from Window modal)`, or `(everything)`.

## Modals

All modals are centered overlays. Esc closes; `Tab` cycles inputs
inside the modal; the first focusable input is auto-focused on open.

### Window modal

```
┌─ Set window ──────────────────────────────────────┐
│ ( ) Single day                                    │
│ (•) Date range                                    │
│ ( ) Everything                                    │
│                                                   │
│ From:  [ 2026-06-01 ]    To:  [ 2026-06-06 ]      │
│ Start: [ 09:00     ]    End: [ 17:00     ]        │
│                                                   │
│         [ Cancel ]    [ Save ]                    │
└───────────────────────────────────────────────────┘
```

Radio buttons swap which date/time fields are enabled. Time inputs
accept the same formats the wizard does (`9am`, `14:30`, `noon`,
`midnight`) via the existing `window.parse_time_12h`. Save validates
and either closes-and-applies or shows an inline error.

### Settings modal

```
┌─ Settings ────────────────────────────────────────┐
│ Contacts file:  [ ./contacts.csv  ] [ Browse… ]  │
│ Output dir:     [ ./exports       ] [ Browse… ]  │
│ Your label:     [ Me              ]              │
│                                                   │
│         [ Cancel ]    [ Save ]                    │
└───────────────────────────────────────────────────┘
```

`[ Browse… ]` opens a Textual directory/file picker. Save validates
the contacts file (running the existing CSV loader; bad rows raise the
malformed-contacts modal — see Errors) and persists all three to
`defaults.json`.

### Redact modal

```
┌─ Redact ──────────────────────────────────────────┐
│ ( ) Off                                           │
│ (•) Keep both versions                            │
│ ( ) Redacted only (folder name pseudonymized)     │
│                                                   │
│ Extra names file: [ ____________ ]  [ Browse… ]  │
│                                                   │
│ Scrub from message bodies:                        │
│   [x] Phones                                      │
│   [x] Emails                                      │
│   [x] URLs                                        │
│                                                   │
│         [ Cancel ]    [ Save ]                    │
└───────────────────────────────────────────────────┘
```

Same shape as the wizard's seventh step. Save updates in-app state
only — redaction is per-export, not persisted to `defaults.json`.

### Export confirm modal

Triggered by `[ Export ]` or `E`. Shows resolved chat, window,
message count, output path, redaction summary. Two buttons:
`[ Cancel ]` and `[ Run ]`. Enter on `[ Run ]` (or `Y`) starts the
export.

### Contacts-scan modal (first-run only)

If `defaults.contacts_path` is unset AND no `./contacts.csv` exists,
this modal opens after the app mounts:

```
┌─ Set up contacts ─────────────────────────────────┐
│ No contacts file found. You can populate one in   │
│ seconds by scanning macOS Contacts.                │
│                                                   │
│ First scan triggers a one-time Contacts           │
│ permission prompt.                                │
│                                                   │
│         [ Skip ]    [ Scan now ]                  │
└───────────────────────────────────────────────────┘
```

Same `contacts_macos.fetch_contacts` + `write_csv` underneath as the
wizard's recent `_step_offer_build_contacts`. On success, the new
`contacts.csv` path is stored in `AppState` and persisted to
`defaults.json`.

## Package layout

Adds one subpackage. Phase 1 files are unchanged.

```
imessage_export/
├── cli.py                     # entrypoint changes (above)
├── tui/
│   ├── wizard.py              # unchanged
│   ├── tables.py              # unchanged
│   ├── preview.py             # unchanged
│   ├── defaults.py            # unchanged
│   ├── errors.py              # unchanged
│   └── app/                   # NEW
│       ├── __init__.py        # re-exports run() for cli.py
│       ├── app.py             # ImessageExportApp(App) — root screen, key bindings
│       ├── widgets.py         # Sidebar, FilterInput, HistoryView, StatusLine, ActionBar
│       ├── modals.py          # WindowModal, SettingsModal, RedactModal, ExportConfirmModal,
│       │                      # ContactsScanModal, ErrorModal
│       ├── state.py           # AppState dataclass + resolved_window helper
│       └── workers.py         # @work-decorated wrappers around db.list_recent_chats etc.
```

`tui/app/__init__.py` exposes a single `run() -> int` that `cli.py`
imports lazily.

## Data flow

- **Chat list:** loaded synchronously on `App.on_mount` via
  `db.list_recent_chats(conn, limit=100)`. Cheap (~30 ms typical).
  Stored in `AppState.chats` and rendered into the Sidebar widget.
  Filter input narrows the existing list in-memory — no per-keystroke
  DB hit.
- **History on chat selection:** dispatches a Textual
  `@work(thread=True, exclusive=True)` worker that calls the existing
  `export.export(conn, chat_ids=[chat_id], contacts=state.contacts,
  me_name=state.me_name, window=TimeWindow(apple_start=None,
  apple_end=None, ...), limit=None, include_attachments=False, unit=unit)`.
  The returned `(messages, _metadata)` tuple: messages get posted as a
  `HistoryLoaded(chat_id, messages)` Textual message; the metadata is
  discarded (the app builds its own header from `chat_info`).
  `exclusive=True` means switching chats mid-load cancels the
  in-flight worker. Empty chats render the history pane's "No
  messages in this chat." hint.
- **DB connection:** opened once in `on_mount` with
  `db.open_db(DEFAULT_DB)`, stored on `self.conn`, closed in
  `on_unmount`. The existing read-only / immutable / `query_only=ON`
  guards apply. The worker thread shares the conn — sqlite3's
  `check_same_thread=False` is set in `open_db` already.
- **Export:** runs in another `@work(thread=True, exclusive=True)`
  worker that calls the existing `cli._run` with an
  `argparse.Namespace` constructed from `AppState`. Same code path as
  the wizard uses for export. On completion the worker posts an
  `ExportFinished(rc, output_path, msg_count)` message; the app
  updates the status line and resets the range/window state.

## State machine

```python
# imessage_export/tui/app/state.py
@dataclass
class AppState:
    # data loaded from DB / defaults
    chats: list[dict] = field(default_factory=list)
    contacts: dict = field(default_factory=dict)

    # selection
    selected_chat_id: int | None = None
    selected_chat_messages: list = field(default_factory=list)

    # range
    range_start_msg_id: int | None = None
    range_end_msg_id: int | None = None
    typed_window: dict | None = None
    window_source: Literal["selection", "typed", "all"] = "all"

    # settings (mirror defaults.json plus redaction)
    contacts_path: Path | None = None
    output_dir: Path = field(default_factory=lambda: Path.cwd() / "exports")
    me_name: str = "Me"
    redact: dict = field(default_factory=dict)

    # ephemeral
    last_export_status: str | None = None  # status line tag; cleared on next mutation
    history_loading: bool = False
```

`resolved_window(state) -> dict` — the precedence helper described in
Range selection.

`reset_after_export(state)` — clears `range_*`, `typed_window`, sets
`window_source = "all"`, sets `last_export_status` to a success tag.

## First-run / pre-selection

On `on_mount`:

1. Load `~/.config/imessage-export/recent.json` via the existing
   `defaults.load()`. Populate `AppState.contacts_path`, `output_dir`,
   `me_name`.
2. Run the chat-list query synchronously, populate `AppState.chats`,
   populate Sidebar widget.
3. Resolve contacts: if `defaults.contacts_path` is set and exists,
   load it; else if `./contacts.csv` exists, load it; else leave
   `AppState.contacts = {}` and queue the ContactsScanModal.
4. If `defaults.last_chat_id` is set AND that chat is in
   `AppState.chats`, set `selected_chat_id`, mark the Sidebar
   selection, kick off the history-load worker. Else focus the
   Sidebar with no chat selected and the history pane shows a
   "Pick a chat from the left." hint.

## Defaults file

No schema change. Phase 2 reads and writes the same
`~/.config/imessage-export/recent.json` Phase 1 introduced. The
Settings modal's Save updates `contacts_path`, `output_dir`,
`me_name`, `last_used` (timestamp). `last_chat_id` is updated whenever
the user switches the active chat (not only at export time). File
mode remains `0600`, parent dir `0700`.

## Errors

All renders as Textual modals (no scrolling tracebacks on stderr).

1. **FDA denied** at `open_db` time → ErrorModal with the same
   copy-paste System Settings instructions as Phase 1's
   `errors.fda_denied`. Single `[ Quit ]` button.
2. **Malformed contacts file** when Settings modal save runs the CSV
   loader and a row parses badly → ErrorModal showing path, row
   number, parse error. `[ OK ]` returns to the Settings modal with
   the contacts path field still focused.
3. **No chats in DB** at startup → ErrorModal "chat.db has no chats.
   Make sure Messages is set up on this Mac." Single `[ Quit ]`.
4. **Unexpected worker exception** → ErrorModal with the traceback in
   an expandable section, `[ Copy traceback ]` and `[ Quit ]`.

## Testing strategy

**Existing tests stay green.** The Phase 1 unit tests (decoder,
defaults roundtrip, CLI TTY detect, no-rich-import-on-headless) are
unaffected by Phase 2.

**New unit tests:**

- `tests/test_app_state.py` — covers `resolved_window()` for every
  branch (selection-only, typed-only, both-with-typed-last,
  both-with-selection-last, neither), the swap-when-end-is-earlier
  rule, and `reset_after_export()`.
- `tests/test_app_workers.py` — calls the history-load worker against
  the fixture chat.db with a no-bounds `TimeWindow`, asserts the
  message count matches the fixture row count and that ordering is
  by timestamp ascending. No Textual involvement (the underlying
  function is `export.export`, already pure).

**One pilot test:**

- `tests/test_app_smoke.py` — uses Textual's `Pilot`:
  1. Mount the app against the fixture chat.db.
  2. Assert the chat list populates with the expected fixture rows.
  3. Simulate `Pilot.press("down", "enter")` to select a chat.
  4. Wait for `HistoryLoaded`, simulate clicking the first and last
     message rows.
  5. Assert `app.state.range_start_msg_id` and `range_end_msg_id`
     are set.
  6. Simulate `Pilot.press("e")` to open the export-confirm modal,
     then `Pilot.press("enter")` to run.
  7. Wait for `ExportFinished`, assert output files exist on disk.
  8. Assert range marks are cleared, status line shows the success
     tag.

  One end-to-end test that catches "I broke the wiring" without
  descending into per-frame snapshot testing.

**Not tested (deliberate YAGNI):**

- Exact rendering of any modal or widget.
- Mouse coordinate input beyond what Pilot supports.
- Performance on huge chats (manual verification only).

**Manual verification checklist** (additive to Phase 1's checklist):

1. `pip install -e '.[tui]'` — `imessage-export` (no args) on a TTY
   enters the Textual app.
2. App pre-selects the chat in `defaults.last_chat_id` and pre-loads
   its history.
3. Click two messages → status line shows `(from selection)` with the
   resolved date/time window.
4. Open Window modal, type a range, Save → status line shows
   `(from Window modal)`.
5. `[ Export ]` opens confirm modal; Run writes the expected files;
   app stays open with success banner on status line.
6. After export, range marks and typed window are cleared; settings
   persist.
7. `[ Wizard ]` quits cleanly and re-launches `imessage-export
   --wizard` in the same terminal.
8. Delete `~/.config/imessage-export/recent.json` and `./contacts.csv`;
   rerun — ContactsScanModal appears.
9. With a chat containing 20k+ messages, UI stays responsive while
   history loads (worker thread, no UI freeze).
10. Tab through every focusable widget — focus order is sensible
    (filter → sidebar → history → button row → wrap).
11. Every modal closes with Esc; every text input swallows accelerator
    letters (typing "Window" in the filter does not open the Window
    modal).
12. `imessage-export --wizard` still works exactly as before.
13. `imessage-export --chat-id N --date Y` headless path is unchanged.
14. `CI=1 imessage-export` prints help (no app, no wizard).

## Risks

- **Textual pre-1.0 churn.** Pinned `>=0.79,<1.0`. Re-evaluate at 1.0;
  if breaking changes land mid-1.x, pin tighter.
- **Large-chat memory.** Loading a 50k-message chat fully into a
  `RichLog`-style widget may use ~50-100 MB. Acceptable for v1; v1
  manual verification covers a 20k+ chat. Phase 3 will add lazy
  windowing if real-world chats trip on this.
- **sqlite3 + worker threads.** Connection is `check_same_thread=False`
  (existing) and only one worker runs at a time (`exclusive=True`).
  Safe for serialized cross-thread use.
- **Accelerator collisions.** Single-letter shortcuts must be
  suppressed whenever any text input has focus. Enforced in the app's
  global `on_key` handler — accelerators are matched only when
  `focused.allow_select` is False (i.e. focus is not on an input).
- **Wizard divergence.** Two configure-then-export UIs now exist.
  Mitigation: both call into the same underlying helpers
  (`contacts_macos.fetch_contacts`, `window.parse_time_12h`,
  `cli._run`); only the prompt copy differs.
- **Tab traps in modals.** Each modal explicitly sets focus on its
  first input and constrains Tab to its own children until dismissed.
  Verified by the Pilot smoke test (cycle Tab inside a modal, assert
  focus stays inside).

## Migration

- `pyproject.toml`: `[tui]` extra gains `textual>=0.79,<1.0`.
- `imessage_export/cli.py`: gains `--wizard` and `--app` flags;
  bare-TTY default switches from `_run_wizard()` to `_run_app()`.
- `imessage_export/tui/app/` is new. Phase 1 files in `tui/` are
  unchanged.
- No data migration: `defaults.json` schema is unchanged.
- Existing scripts pinned to `--chat-id …` and other headless flags
  see no behavior change. The only returning-user-visible change is
  that bare `imessage-export` on a TTY opens the app instead of the
  wizard.
- The branch carrying Phase 2 work should not start until the
  in-flight `wizard-offer-build-contacts` polish has landed on main
  — Phase 2 needs Phase 1 as a stable base, and the
  ContactsScanModal reuses logic introduced by that branch.

## Interaction with the export-formatting spec

`docs/superpowers/specs/2026-06-06-export-formatting-design.md` is an
in-progress spec that improves the rendered layout of all writer
outputs (day headers, gap markers, indented continuation paragraphs).

Phase 2's history pane should mirror those formatting rules — day
headers and gap markers in the preview match what the export will
produce. If the export-formatting spec lands first, Phase 2 uses its
helpers (`iter_render_events`, `format_day_label`, `format_gap`)
directly. If Phase 2 lands first, the history pane implements an
inline equivalent and gets refactored to share helpers when the
formatting spec ships.

The two specs do not block each other. The cleanest order is
export-formatting first (it's small) then Phase 2.

## Out of scope (Phase 3, future)

- Lazy-windowed history for chats >20k messages (load only
  visible-screen + buffer, page on scroll).
- Search-within-chat (Ctrl-F on the history pane).
- Per-chat metadata sidebar tab (participants, first-message date,
  attachment count).
- `--app --chat-id N` headless pre-focus.
- Multi-chat batch-export queue.
- Theme customization beyond Textual defaults.
