# TUI makeover (Phase 1) — design

**Date:** 2026-06-06
**Scope:** Add a polished interactive TUI to `imessage-export` as an opt-in
extra, restructure the single-file module into a small package, preserve
every current CLI flag and headless behavior. Phase 2 (a full Textual app)
is out of scope and will get its own spec.

## Goals

1. Bare `imessage-export` on a terminal launches a guided wizard that picks
   chat, time window, contacts file, and output dir — no flag hunting.
2. `--list` and `--list-contacts` render as Rich tables on a TTY and as
   plain machine-parseable text when piped.
3. After export, optionally render `conversation.md` inline with paging so
   the output can be sanity-checked without opening another tool.
4. Remember last-used answers so repeat runs need almost no typing.
5. Keep the default `pip install` zero-deps; gate everything new behind an
   optional `[tui]` extra.
6. Keep the existing headless flag surface working unchanged so muscle
   memory and any scripts the author already has keep working.

## Non-goals

- No changes to chat.db access, the decoder, query logic, writers, or
  export file formats. This is UX-only.
- No back-compat re-exports for the old `imessage_export` (file) import
  path. This tool has a single user; restructure cleanly.
- No telemetry, network calls, or auto-update — privacy posture unchanged.
- No full Textual app (sidebar, live preview, footer command bar). That is
  Phase 2 and gets its own spec.
- No mouse support, no async DB scanning, no live filtering.

## Dependencies and install

```toml
[project]
dependencies = []                                  # unchanged

[project.optional-dependencies]
tui = ["rich>=13", "questionary>=2"]

[project.scripts]
imessage-export = "imessage_export.cli:main"
```

- `pip install imessage-history` — zero deps; headless CLI works as today.
- `pip install imessage-history[tui]` — adds Rich + Questionary, unlocks the
  wizard, Rich tables, and the Markdown preview.
- Defaults file uses JSON (not TOML) because `tomllib` is read-only and
  Python 3.10 doesn't have it. JSON gives stdlib read+write across the
  supported Python range.

README badge change: `Stdlib only` → `Zero required deps · optional TUI
extra`. The privacy claim ("no network calls") stays prominent and
unchanged.

CLAUDE.md update: replace "One file, no packages" with "Core lives in
`imessage_export/`; TUI under `imessage_export/tui/` is only importable
when the `[tui]` extra is installed."

## Package layout

```
imessage_export/
├── __init__.py        # package marker; no re-exports
├── __main__.py        # `python -m imessage_export`
├── cli.py             # argparse + TTY-detect entry; current main() lives here
├── db.py              # SQLite open + queries
├── decoder.py         # attributedBody NSAttributedString parser + tapback resolution
├── contacts.py        # CSV loader
├── writers.py         # csv/json/txt/md/ai-ready writers + ANALYSIS_PROMPT
└── tui/               # only imported when [tui] extra installed
    ├── __init__.py    # package marker; imports rich/questionary at top
    ├── wizard.py      # Questionary chat picker, date, contacts, output dir
    ├── tables.py      # Rich-styled --list / --list-contacts
    ├── preview.py     # Rich Markdown preview after export
    ├── defaults.py    # ~/.config/imessage-export/recent.json read/write
    └── errors.py      # styled FDA-denied, missing-contacts, etc.
```

The existing `imessage_export.py` is deleted in the same commit the
package is created. `pyproject.toml` switches from
`py-modules = ["imessage_export"]` to
`[tool.setuptools.packages.find] include = ["imessage_export*"]`.

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
    ])

    if no_explicit_args and is_tty and not is_ci and not has_action_flag:
        return _run_wizard()              # requires [tui]

    if args.list and is_tty and not is_ci:
        return _list_with_rich_table()    # piped or CI → plain output
    if args.list_contacts and is_tty and not is_ci:
        return _list_contacts_with_rich_table()

    return _run_headless(args)            # current code path, unchanged
```

If the user is on a TTY without `[tui]` installed, the `import
imessage_export.tui` inside `_run_wizard` / `_list_with_rich_table` raises
`ImportError`. `cli.py` catches it once and prints a stdlib-only message,
then exits 2:

```
imessage-export: interactive mode needs the [tui] extra.
  pip install 'imessage-history[tui]'
Or run headless:  imessage-export --list
```

The `tui/__init__.py` itself is just a package marker — it does not guard
imports. Keeping the friendly message in one place (`cli.py`) avoids
divergence.

## Wizard flow (`imessage_export/tui/wizard.py`)

A welcome panel followed by six interactive steps. Each step uses
Questionary; Rich draws the surrounding panels and status text.

**Welcome panel** (passive, not a step) — short Rich Panel: tool name,
privacy reminder, hint that `--help` prints the headless flag surface.

1. **Chat picker** — Questionary `autocomplete` over the chat list. Items
   rendered as
   `[142] Mallory (mallory@example.com) · 1:1 · 312 msgs · last 2026-05-30`
   for 1:1 chats and
   `[91] Family (group · 5 people: ben@, alice@, …) · 1240 msgs · last 2026-05-27`
   for groups (group title falls back to participants list when
   `display_name` is empty — same fallback the current plain `--list` uses).
   Backed by the existing `list_recent_chats()` query, sorted by
   `last_date DESC`. Type-to-filter matches across display name, handle,
   participants list, and chat identifier. If the defaults file has
   `last_chat_id`, offer "Use last chat (Mallory)?" as the first option.
2. **Time window** — Questionary `select`:
   - *Single day* → `text` prompt for date (default = today), then optional
     `text` prompts for start/end times.
   - *Date range* → two `text` prompts for `from-date` / `to-date`.
   - *Everything* → no further prompts. If the chat has >5k messages, show
     a Rich warning panel with the count and ask for confirmation.
3. **Contacts file** — Questionary `path` prompt, pre-filled from defaults
   or `./contacts.csv` if present. Empty input = no contacts file
   (same as omitting `--contacts` today).
4. **Output dir** — Questionary `path` prompt, pre-filled from defaults
   (default `./exports`).
5. **Me-name** — Questionary `text`, pre-filled from defaults
   (default `Me`).
6. **Confirm** — Rich `Panel` summary of resolved choices plus a chat
   preview (first/last message timestamps, count). Questionary `confirm`.

After confirm, fall through to the existing export pipeline. On success,
save answers to the defaults file and prompt:
`Preview conversation.md? (y/N)`.

## Rich tables for `--list` / `--list-contacts`

`--list` on a TTY renders as:

```
                                       Recent chats
┏━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃   ID ┃ Kind  ┃ Identifier / Participants         ┃   Msgs ┃ Last             ┃
┡━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│  142 │ 1:1   │ Mallory  ·  mallory@example.com   │    312 │ 2026-05-30 14:02 │
│   87 │ 1:1   │ Alex Chen  ·  +1 415 555 0142     │     89 │ 2026-05-29 19:48 │
│   91 │ group │ Family  ·  5 people               │   1240 │ 2026-05-27 22:10 │
└──────┴───────┴───────────────────────────────────┴────────┴──────────────────┘
  Showing 3 of 30 — use --list-limit to change.
```

When piped (`isatty()==False`) or `CI=true`, output is plain text columns
the author can pipe into other tools (`grep`, `awk`, `> file`). Format is
free to evolve; the only contract is "no terminal escape codes."

`--list-contacts` follows the same pattern: Rich table on a TTY,
header-then-rows CSV (`handle,name`) when piped — that one is intentionally
CSV so the existing `--list-contacts > /tmp/handles.csv` workflow keeps
working.

## Markdown preview (`imessage_export/tui/preview.py`)

After a successful export, prompt `Preview conversation.md? (y/N)`. If
yes, read the generated `conversation.md`, render it with Rich's
`Markdown` widget, and page through it (honor `$PAGER`, fall back to
Rich's built-in pager). Day headers, gap markers, speaker headers, and
attachment lines all render through Rich's existing Markdown handling.

## Defaults file (`~/.config/imessage-export/recent.json`)

```json
{
  "version": 1,
  "contacts_path": "/Users/ben/Projects/imessage-history/contacts.csv",
  "output_dir": "/Users/ben/Projects/imessage-history/exports",
  "me_name": "Ben",
  "last_chat_id": 142,
  "last_used": "2026-06-06T14:23:11-04:00"
}
```

- File mode `0600`, directory mode `0700` — matches the existing
  `os.umask(0o077)` discipline.
- Wizard pre-fills the corresponding prompt with the saved value; user
  hits Enter to accept, or types to override.
- `last_chat_id` powers the "Use last chat (X)?" shortcut at the
  chat-picker step.
- `version` field is read on load; unknown versions silently fall back to
  first-run defaults rather than crashing.
- Missing file = first run; never an error.
- Stale `contacts_path` / `output_dir` (file moved/deleted between runs)
  → silently drop and re-prompt rather than failing.

## Styled error screens (`imessage_export/tui/errors.py`)

Three named error paths get Rich panels with copy-pasteable next steps:

1. **FDA denied** — when SQLite open raises `authorization denied`. Panel
   shows the System Settings path (Privacy & Security → Full Disk Access)
   and which binary needs adding (`sys.executable`).
2. **No chats match `<query>`** — when `--participant` / wizard search
   returns zero hits. Suggests running `--list` to see all chats.
3. **`contacts.csv` malformed** — when the contacts loader hits a bad row.
   Shows the file path, the offending row number, and the parse error.

Other errors keep the existing argparse/exception flow.

## Testing strategy

**Existing tests stay green.** The package restructure rewires imports but
not behavior:
- `tests/test_decoder.py` →
  `from imessage_export.decoder import decode_attributed_body`.
- The existing read-only DB guards (`mode=ro&immutable=1` +
  `PRAGMA query_only=ON` + every-write-statement-raises tests) move with
  `db.py` and run unchanged.
- Gate: `python3 -m unittest discover -s tests -v` passes before and after
  the restructure with identical test counts.

**New tests:**
- `tests/test_tui_defaults.py` — defaults file roundtrip. Write JSON,
  read back, verify schema versioning ignores unknown version. No deps
  required (uses stdlib `json` and a temp dir).
- `tests/test_cli_tty_detect.py` — patches `sys.stdout.isatty()` and
  `os.environ` and verifies: piped `--list` produces plain output (no ANSI
  escapes), TTY `--list` calls into the `tui.tables` path (mocked), and
  `CI=1` forces plain output.
- `tests/test_no_rich_import_on_headless.py` — smoke test that
  `python3 -c "import imessage_export.cli; imessage_export.cli.build_parser()"`
  does not pull in `rich` or `questionary`. Asserts neither module name
  appears in `sys.modules` after import.

**Not tested (deliberate YAGNI):**
- Questionary prompt internals. Questionary has its own test suite;
  mocking it adds brittleness without real coverage.
- Rich rendering byte-for-byte. Terminal width and color settings make
  snapshots flaky.
- Markdown preview pager interaction. Manual verification only.

**Manual verification checklist** (run before merging):

1. `pip install -e .` (no extras) — `imessage-export --chat-id N --date Y`
   works.
2. `pip install -e '.[tui]'` — `imessage-export` (no args) on a TTY
   enters the wizard.
3. `imessage-export | cat` (piped) — prints help, zero ANSI escapes in
   the output.
4. `imessage-export --list | head` — plain output, no escapes;
   `imessage-export --list` on TTY — Rich table renders.
5. `imessage-export --list-contacts > /tmp/handles.csv` — valid CSV the
   existing flow can re-consume.
6. Wizard end-to-end on a real conversation; confirm defaults file lands
   at `~/.config/imessage-export/recent.json` with `0600` perms and
   `0700` parent dir.
7. Delete `~/.config/imessage-export/recent.json`; rerun wizard — first
   run works (no error, no pre-fills).
8. Wizard launched without `[tui]` installed → friendly error, exit 2.
9. `CI=1 imessage-export` → prints help, no wizard, no Rich.

## Risks

- **Import-time cost of Rich.** Rich is heavy to import (~150-250ms). The
  headless code path must not import it. Mitigation: import
  `imessage_export.tui` only inside the wizard / tables / preview entry
  functions, never at module top-level. Enforced by
  `tests/test_no_rich_import_on_headless.py`.
- **TTY detection false positives in CI.** Some CI environments
  (GitHub Actions with `tty: true`) report a TTY. Mitigation: check
  `CI` / `NONINTERACTIVE` env vars and bail to help if set.
- **Questionary in degraded terminals.** Dumb terminals, mosh sessions,
  some tmux+SSH combos render Questionary poorly. Mitigation: respect
  `$TERM=dumb` and fall back to bare `input()` prompts. Add a
  `--no-color` escape hatch.
- **Stale paths in defaults file.** Mitigated by validating paths on load
  and silently re-prompting on miss.

## Migration

- `imessage_export.py` (file) → `imessage_export/` (package). The file is
  deleted in the same commit the package is created.
- `pyproject.toml`: `py-modules` → `packages.find`, console script entry
  changes from `imessage_export:main` to `imessage_export.cli:main`.
- README + CLAUDE.md badge/identity sentences updated (see Dependencies
  section).
- No data migration: the defaults file is new; absent on first run is
  expected.
- Existing exports under `exports/<contact>/<date>/` are unchanged in
  format — this spec touches only the interactive path.
- `contacts.csv` schema unchanged.

## Interaction with the redaction spec

The redaction spec at `docs/superpowers/specs/2026-06-06-redaction-design.md`
is approved but unimplemented. It adds an opt-in redactor with these
flags: `--redact`, `--redact-only`, `--redact-names-file`,
`--no-redact-phones`, `--no-redact-emails`, `--no-redact-urls`,
`--suggest-names`. It expects to land as ~120 lines added to the
existing single-file `imessage_export.py`.

The two specs do not block each other, but whichever lands second has to
rebase on the other's structural changes. The cleanest combination:

### Recommended order — TUI restructure first, redactor second

1. Implement this spec. `imessage_export.py` becomes the
   `imessage_export/` package. Wizard ships with six steps and no
   redaction awareness.
2. Implement the redactor as a new module in the package:
   `imessage_export/redactor.py` (replaces the redaction spec's
   "add ~120 lines to `imessage_export.py`" with "create a new file").
   `cli.py` wires the six new flags into the existing `build_parser()`.
3. Add a seventh wizard step (in `wizard.py`) between **Me-name** and
   **Confirm**:

   7. **Redact?** — Questionary `select`:
      - *No* (default) — skip the rest of the redaction prompts.
      - *Yes — keep both originals and redacted files* → maps to
        `--redact`.
      - *Yes — redacted only (folder name is pseudonymized)* → maps to
        `--redact-only`.

      If "Yes" is chosen, follow up with:
      - **Extra names file?** — Questionary `path`, empty input skips.
        Maps to `--redact-names-file`.
      - **PII categories** — Questionary `checkbox` with `Phones`,
        `Emails`, `URLs` pre-checked. Unchecking maps to the
        corresponding `--no-redact-*` flag.

   `--suggest-names` stays headless-only — it's a diagnostic that
   bypasses export and doesn't fit the wizard's "run an export" shape.

   The Markdown preview after export reads `conversation_redacted.md`
   (under `--redact`), `conversation.md` (under `--redact-only`, where
   no `_redacted` suffix exists), or `conversation.md` (no redaction).

### Alternative order — redactor first, TUI restructure second

Also viable. The redactor lands in single-file `imessage_export.py`
exactly as its spec describes. When this TUI spec then promotes the
file to a package, the redactor's `RedactionConfig`/`Redactor`/
`suggest_names()` move to `imessage_export/redactor.py` and the wizard
gains the same seventh step as above. The structural cost is the same;
just paid in the opposite order.

This spec's recommendation is **TUI first** only because restructuring
once (with no redactor-aware code to move) is simpler than restructuring
once and then immediately editing the redactor's home in the same PR.

## Out of scope (Phase 2, future spec)

- Full Textual app: sidebar chat list, live message preview, footer
  command bar, fuzzy search.
- Mouse support, async DB scanning, live filtering.
- The Phase 2 spec will reuse `imessage_export.db`, `decoder`, `writers`
  unchanged and add `imessage_export/tui/textual_app.py` alongside the
  Phase 1 wizard. The wizard and the Textual app coexist; user picks via
  `--app` or env var.
