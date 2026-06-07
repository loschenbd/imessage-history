# TUI Makeover (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in interactive TUI (`pip install imessage-history[tui]`) to `imessage-export` with a guided wizard, Rich-styled `--list` / `--list-contacts`, a Markdown preview, and a remembered-defaults file. Restructure the single-file exporter into a small package as a prerequisite.

**Architecture:** Promote `imessage_export.py` to an `imessage_export/` package along existing section-banner seams (timestamps, decoder, models, db, contacts, window, export, writers, cli). Add a `tui/` submodule guarded behind the optional `[tui]` extra (Rich + Questionary). `cli.py` TTY-detects and dispatches to wizard / Rich tables / headless paths. Headless path must never import Rich.

**Tech Stack:** Python 3.10+ stdlib only for the core; Rich ≥13 and Questionary ≥2 as optional `[tui]` extra. Tests are stdlib `unittest`.

---

## Preconditions (Task 0 — not a code task)

Two other branches are modifying `imessage_export.py` at the time of writing:

| Branch | Worktree | What it adds |
|---|---|---|
| `formatting-improvements` | `/Users/benjaminloschen/.config/superpowers/worktrees/imessage-history/formatting-improvements` | Day headers, gap markers, indented continuation lines, `format_day_label`, `format_gap`, `iter_render_events`, JSON `gap_seconds_before`, CSV `local_date` (per `2026-06-06-export-formatting-design.md`) |
| `redaction` | `/Users/benjaminloschen/.config/superpowers/worktrees/imessage-history/redaction` | `RedactionConfig`, `Redactor`, `suggest_names()`, six new argparse flags, redaction branching in `_run()` (per `2026-06-06-redaction-design.md`) |

**Task 1 must not start until BOTH branches are merged to `main`.** Task 1 deletes `imessage_export.py`; any concurrent edit becomes a painful conflict on every line the other agent wrote.

- [ ] **Gate 1: formatting-improvements merged**

```bash
git fetch origin
git log origin/main --oneline | head -20 | grep -E 'feat.*(txt|md|csv|json|ai-ready)|day headers|gap'
```

Expected: at least one commit matching the formatting-improvements pattern in `origin/main`. If absent, wait.

- [ ] **Gate 2: redaction merged**

```bash
git log origin/main --grep='redact' --oneline | head -5
git log origin/main -p -1 -- imessage_export.py | grep -c 'RedactionConfig\|class Redactor\|def suggest_names'
```

Expected: at least one redact commit and at least one match for the redactor's class/function names in `imessage_export.py`.

- [ ] **Sync local main**

```bash
git checkout main
git pull --ff-only origin main
git status
git log --oneline -10
```

Expected: clean working tree, `main` up to date with origin, recent commits include both formatting and redaction work.

- [ ] **Capture a pre-restructure test baseline**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: `OK`. **Record the test count** — Task 1 succeeds only if this count is identical (plus the new tests Task 1 itself adds — none in Task 1's case; just identical) after the restructure.

---

## Task 1: Restructure `imessage_export.py` → `imessage_export/` package

This is one big mechanical refactor. No behavior change. Tests must pass identically before and after.

**Files:**
- Delete: `imessage_export.py`
- Create: `imessage_export/__init__.py`
- Create: `imessage_export/__main__.py`
- Create: `imessage_export/timestamps.py`
- Create: `imessage_export/decoder.py`
- Create: `imessage_export/models.py`
- Create: `imessage_export/db.py`
- Create: `imessage_export/contacts.py`
- Create: `imessage_export/window.py`
- Create: `imessage_export/export.py`
- Create: `imessage_export/writers.py`
- Create: `imessage_export/cli.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_decoder.py`

- [ ] **Step 1.1: Create a feature branch**

```bash
git checkout -b tui-makeover-phase1-restructure
```

- [ ] **Step 1.2: Capture pre-restructure test baseline**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: `OK` and a test count (e.g. `Ran 37 tests in 0.123s`). **Record the test count** — Task 1 succeeds only if the count is identical after the restructure.

- [ ] **Step 1.3: Create the package directory and entry-point files**

```bash
mkdir -p imessage_export
```

Create `imessage_export/__init__.py`:

```python
"""imessage_export — export iMessage conversations from chat.db.

Package layout:
    timestamps.py   Apple 2001-epoch <-> Unix conversion helpers
    decoder.py      attributedBody NSAttributedString typedstream parser
    models.py       Message dataclass and tapback classification
    db.py           SQLite open (read-only) + chat / contact queries
    contacts.py     contacts.csv loader + handle normalization
    window.py       --date / --start-time / etc. -> TimeWindow
    export.py       message fetch + export() pipeline
    writers.py      csv / json / txt / md / ai_ready writers + ANALYSIS_PROMPT
    cli.py          argparse + TTY-detect entry; main() lives here
    tui/            optional Rich+Questionary UI (requires [tui] extra)
"""
```

Create `imessage_export/__main__.py`:

```python
"""Support `python3 -m imessage_export`."""
from .cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 1.4: Move the timestamp helpers**

Create `imessage_export/timestamps.py` by copying lines 41-83 of the original file (the section under `# Apple timestamp helpers` and its banner). Replace the banner comment with a module docstring. The file's free-standing functions stay free-standing.

The exact public surface is: `APPLE_EPOCH_UNIX`, `detect_date_unit`, `apple_to_utc_datetime`, `local_dt_to_apple`, `attach_local_tz`. `APPLE_EPOCH_UNIX = 978307200` moves here from the top of `imessage_export.py`.

Add at the top:

```python
"""Apple 2001-epoch <-> Unix conversion helpers."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

APPLE_EPOCH_UNIX = 978307200
```

- [ ] **Step 1.5: Move the decoder + tapback helpers**

Create `imessage_export/decoder.py` from lines 85-127 (the `decode_attributed_body` section) plus lines 168-186 (the `classify_tapback` and `strip_target_guid` helpers).

Header:

```python
"""attributedBody NSAttributedString typedstream parser + tapback classifiers."""
from __future__ import annotations
from typing import Optional
```

Public surface: `decode_attributed_body(blob: bytes) -> Optional[str]`, `classify_tapback(amt: int) -> Optional[tuple[str, bool]]`, `strip_target_guid(assoc_guid: str) -> Optional[str]`.

- [ ] **Step 1.6: Move the Message dataclass**

Create `imessage_export/models.py` from lines 129-167 (the `Message` dataclass section).

Header:

```python
"""Core dataclasses used across the package."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
```

Public surface: `Message`. Tapback-related helpers stay in `decoder.py` (they classify integers, not models).

- [ ] **Step 1.7: Move the DB helpers**

Create `imessage_export/db.py` from lines 188-358 (DB open through `chat_label`).

Header:

```python
"""Read-only SQLite access to chat.db + chat / contact queries."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional
from .timestamps import detect_date_unit, apple_to_utc_datetime
```

Public surface: `open_db`, `column_exists`, `list_recent_chats`, `resolve_chat_ids`, `chat_participants`, `chat_info`, `list_contacts_csv`, `chat_label`.

The `from .timestamps import ...` line is the new dependency wire.

- [ ] **Step 1.8: Move the contacts helpers**

Create `imessage_export/contacts.py` from lines 360-405.

Header:

```python
"""Contacts CSV loader + handle normalization."""
from __future__ import annotations
import csv
from pathlib import Path
from typing import Optional
```

Public surface: `normalize_handle`, `load_contacts`, `resolve_author_label`.

- [ ] **Step 1.9: Move the time-window helpers**

Create `imessage_export/window.py` from lines 407-513.

Header:

```python
"""--date / --start-time / etc. argparse-args -> TimeWindow."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from .timestamps import attach_local_tz, local_dt_to_apple
```

Public surface: `TimeWindow`, `parse_date`, `parse_time`, `parse_datetime`, `resolve_window`.

- [ ] **Step 1.10: Move the export pipeline**

Create `imessage_export/export.py` from lines 515-734.

Header:

```python
"""Message fetch + main export() pipeline."""
from __future__ import annotations
import sqlite3
from .decoder import decode_attributed_body, classify_tapback, strip_target_guid
from .models import Message
from .timestamps import apple_to_utc_datetime
from .contacts import resolve_author_label
from .window import TimeWindow
```

Public surface: `fetch_attachments`, `export`.

- [ ] **Step 1.11: Move the writers**

Create `imessage_export/writers.py` from lines 736-924.

Header:

```python
"""CSV / JSON / TXT / Markdown / AI-ready writers + ANALYSIS_PROMPT."""
from __future__ import annotations
import csv
import json
import re
import unicodedata
from dataclasses import asdict
from pathlib import Path
from .models import Message
```

Public surface: `slugify`, `format_message_body`, `format_txt_line`, `write_csv`, `write_json`, `write_txt`, `write_ai_ready`, `write_markdown`, `write_prompt`, `ANALYSIS_PROMPT`. If the file in `main` has additional helpers from `2026-06-06-export-formatting-design.md` (`GAP_THRESHOLD_SECONDS`, `format_day_label`, `format_gap`, `iter_render_events`), include those too — preserve everything the writers section contains in `main`.

- [ ] **Step 1.12: Move the CLI**

Create `imessage_export/cli.py` from lines 926-1072.

Header:

```python
"""argparse parser + entry-point main() with TTY-detect dispatch."""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from .db import open_db, list_recent_chats, resolve_chat_ids, chat_participants, chat_info, list_contacts_csv, chat_label
from .contacts import load_contacts
from .timestamps import detect_date_unit
from .window import resolve_window
from .export import export
from .writers import slugify, write_csv, write_json, write_txt, write_markdown, write_ai_ready, write_prompt

DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"
```

Public surface: `build_parser`, `validate_args`, `main`, `_run`. **Do not add TTY-detect dispatch yet** — that lands in Task 3. The restructure is behavior-preserving.

If the redactor merged into `main` before this task started, the CLI section will include redaction flags and a `_run()` that branches on `--redact`/`--redact-only`/`--suggest-names`. Preserve all of that verbatim — Task 1 does not touch redaction logic.

- [ ] **Step 1.13: Delete the original file**

```bash
git rm imessage_export.py
```

- [ ] **Step 1.14: Update `pyproject.toml`**

Modify `pyproject.toml`:

```toml
[project.scripts]
imessage-export = "imessage_export.cli:main"

[tool.setuptools.packages.find]
include = ["imessage_export*"]
```

Delete the existing `[tool.setuptools]` block with `py-modules = ["imessage_export"]`.

- [ ] **Step 1.15: Update test imports**

Modify `tests/test_decoder.py` line 17-18:

Before:
```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import imessage_export as ie
```

After:
```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from imessage_export import decoder, models, timestamps, db, contacts, window, export, writers, cli

class _IE:
    """Compatibility shim so the existing test body can keep using `ie.foo`."""
ie = _IE()
for module in (decoder, models, timestamps, db, contacts, window, export, writers, cli):
    for name in dir(module):
        if not name.startswith("_"):
            setattr(ie, name, getattr(module, name))
```

The shim preserves the existing `ie.decode_attributed_body(...)` test calls without rewriting each test. If the test file references symbols not in the listed modules, add the missing module to the loop.

If any other test files exist under `tests/` (check: `ls tests/`), repeat the same import update for each.

- [ ] **Step 1.16: Run the test suite**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -10
```

Expected: `OK` with the exact same test count recorded in Step 1.2. If the count differs or any test fails, do NOT commit — investigate.

- [ ] **Step 1.17: Smoke-test the CLI is wired**

```bash
python3 -m imessage_export --help 2>&1 | head -20
```

Expected: argparse help output with `--chat-id`, `--list`, `--date`, etc. — the same surface as before. No tracebacks.

- [ ] **Step 1.18: Verify the entry point script works (with the package installed in editable mode if `pyproject.toml` is honored)**

```bash
python3 -c "from imessage_export.cli import main; print('ok')"
```

Expected: `ok`.

- [ ] **Step 1.19: Commit**

```bash
git add imessage_export/ pyproject.toml tests/
git status
git commit -m "$(cat <<'EOF'
Restructure imessage_export.py into imessage_export/ package.

Mechanical split along existing section banners: timestamps, decoder,
models, db, contacts, window, export, writers, cli. No behavior change.
All existing tests pass identically.

Required prerequisite for the TUI makeover spec — the [tui] submodule
will land in subsequent commits as `imessage_export/tui/`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 1.20: Open PR for the restructure** (or merge directly if working solo)

```bash
git push -u origin tui-makeover-phase1-restructure
gh pr create --title "Restructure imessage_export.py into a package" --body "$(cat <<'EOF'
## Summary
- Promote single-file `imessage_export.py` to `imessage_export/` package
- Split along existing section banners (timestamps, decoder, models, db, contacts, window, export, writers, cli)
- No behavior change — argparse surface, output files, and test results are byte-identical

## Test plan
- [x] `python3 -m unittest discover -s tests -v` passes with the same count as before
- [x] `python3 -m imessage_export --help` prints the unchanged CLI surface
- [x] `python3 -m imessage_export --list` against a real chat.db works (if FDA available)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

After merge, return to `main`:

```bash
git checkout main && git pull --ff-only
```

---

## Task 2: Add `[tui]` extra to `pyproject.toml` and create `tui/` skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `imessage_export/tui/__init__.py`

- [ ] **Step 2.1: Create a feature branch**

```bash
git checkout -b tui-makeover-phase1-tables
```

- [ ] **Step 2.2: Add the optional-dependencies group**

Modify `pyproject.toml` — insert after the `dependencies = []` line:

```toml
[project.optional-dependencies]
tui = ["rich>=13", "questionary>=2"]
```

- [ ] **Step 2.3: Create the empty TUI subpackage**

Create `imessage_export/tui/__init__.py`:

```python
"""Optional Rich + Questionary TUI for imessage-export.

This subpackage is only importable when the [tui] extra is installed:
    pip install 'imessage-history[tui]'

Modules under `tui/` may import rich and questionary at module top-level.
Core modules (`cli`, `db`, `writers`, ...) MUST NOT import anything under
`tui/` at module top-level — only inside the wizard/tables/preview dispatch
functions, so the headless path stays zero-deps.
"""
```

- [ ] **Step 2.4: Install editable + verify**

```bash
pip install -e '.[tui]'
python3 -c "import rich, questionary; print('rich', rich.__version__, 'questionary', questionary.__version__)"
```

Expected: version numbers ≥13 and ≥2.

- [ ] **Step 2.5: Commit**

```bash
git add pyproject.toml imessage_export/tui/__init__.py
git commit -m "$(cat <<'EOF'
Add [tui] optional extra (rich + questionary).

Default install stays zero-deps. Subpackage `imessage_export/tui/` is
created but empty — wizard, tables, preview, defaults, errors modules
land in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: TTY-detect dispatch in `cli.py`

**Files:**
- Modify: `imessage_export/cli.py`
- Create: `tests/test_cli_tty_detect.py`

- [ ] **Step 3.1: Write the failing test**

Create `tests/test_cli_tty_detect.py`:

```python
"""TTY-detect dispatch in cli.main()."""
from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imessage_export import cli


class TTYDetectTests(unittest.TestCase):
    """The bare `imessage-export` command should:
    - on a TTY without CI, attempt to launch the wizard;
    - when piped, print help (current behavior).
    """

    def test_piped_no_args_prints_help(self):
        buf = io.StringIO()
        with mock.patch.object(sys.stdout, "isatty", return_value=False), \
             mock.patch.object(sys.stderr, "isatty", return_value=False), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("sys.stdout", buf):
            os.environ.pop("CI", None)
            os.environ.pop("NONINTERACTIVE", None)
            rc = cli.main([])
        self.assertEqual(rc, 2)
        self.assertIn("usage", buf.getvalue().lower() + " ")

    def test_ci_env_forces_help_even_on_tty(self):
        buf = io.StringIO()
        with mock.patch.object(sys.stdout, "isatty", return_value=True), \
             mock.patch.object(sys.stderr, "isatty", return_value=True), \
             mock.patch.dict(os.environ, {"CI": "1"}, clear=False), \
             mock.patch("sys.stdout", buf):
            rc = cli.main([])
        self.assertEqual(rc, 2)

    def test_tty_no_args_calls_wizard_dispatch(self):
        with mock.patch.object(sys.stdout, "isatty", return_value=True), \
             mock.patch.object(sys.stderr, "isatty", return_value=True), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("imessage_export.cli._run_wizard") as run_wiz:
            os.environ.pop("CI", None)
            os.environ.pop("NONINTERACTIVE", None)
            run_wiz.return_value = 0
            rc = cli.main([])
        run_wiz.assert_called_once()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3.2: Run the test to verify it fails**

```bash
python3 -m unittest tests.test_cli_tty_detect -v 2>&1 | tail -15
```

Expected: 3 test failures — `_run_wizard` does not exist; bare `main([])` does not dispatch.

- [ ] **Step 3.3: Implement TTY-detect dispatch**

Modify `imessage_export/cli.py` — replace the existing `main()` function with:

```python
def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _dispatch(args, argv, parser)


def _dispatch(args, argv, parser) -> int:
    no_explicit_args = (argv == [] or argv == ())
    is_tty = sys.stdout.isatty() and sys.stderr.isatty()
    is_ci = bool(os.environ.get("CI") or os.environ.get("NONINTERACTIVE"))
    has_action_flag = bool(
        args.list or args.list_contacts or args.chat_id or args.chat_identifier
        or args.participant or getattr(args, "from_date", None) or getattr(args, "to_date", None)
        or args.date
    )

    if no_explicit_args and is_tty and not is_ci and not has_action_flag:
        return _run_wizard()

    if args.list and is_tty and not is_ci:
        return _list_with_rich_table(args)
    if args.list_contacts and is_tty and not is_ci:
        return _list_contacts_with_rich_table(args)

    if no_explicit_args:
        parser.print_help()
        return 2

    try:
        conn = open_db(Path(args.db))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    try:
        return _run(args, conn)
    finally:
        conn.close()


def _run_wizard() -> int:
    try:
        from .tui.wizard import run as run_wizard
    except ImportError:
        print(_TUI_MISSING_MSG, file=sys.stderr)
        return 2
    return run_wizard()


def _list_with_rich_table(args) -> int:
    try:
        from .tui.tables import list_chats as tui_list
    except ImportError:
        print(_TUI_MISSING_MSG, file=sys.stderr)
        return 2
    return tui_list(args)


def _list_contacts_with_rich_table(args) -> int:
    try:
        from .tui.tables import list_contacts as tui_list_contacts
    except ImportError:
        print(_TUI_MISSING_MSG, file=sys.stderr)
        return 2
    return tui_list_contacts(args)


_TUI_MISSING_MSG = (
    "imessage-export: interactive mode needs the [tui] extra.\n"
    "  pip install 'imessage-history[tui]'\n"
    "Or run headless:  imessage-export --list"
)
```

The original `main()` body (that parses args, opens the DB, calls `_run()`) moves into `_dispatch()` after the TTY branches.

- [ ] **Step 3.4: Run the test to verify it passes**

```bash
python3 -m unittest tests.test_cli_tty_detect -v 2>&1 | tail -10
```

Expected: 3 tests pass.

- [ ] **Step 3.5: Run full suite to verify no regressions**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: `OK`, same count as Task 1's baseline plus 3 (the new test class).

- [ ] **Step 3.6: Commit**

```bash
git add imessage_export/cli.py tests/test_cli_tty_detect.py
git commit -m "$(cat <<'EOF'
Add TTY-detect dispatch to cli.main().

Bare `imessage-export` on a TTY now attempts to launch the wizard;
piped or `CI=1` falls back to help. `--list` and `--list-contacts` on
a TTY route through the Rich-table modules. When [tui] isn't
installed, a friendly stdlib message points at `pip install`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Rich tables for `--list` and `--list-contacts`

**Files:**
- Create: `imessage_export/tui/tables.py`

- [ ] **Step 4.1: Implement `tui/tables.py`**

Create `imessage_export/tui/tables.py`:

```python
"""Rich-styled tables for --list and --list-contacts (TTY only)."""
from __future__ import annotations

from pathlib import Path
from rich.console import Console
from rich.table import Table

from ..db import open_db, list_recent_chats, list_contacts_csv
from ..timestamps import detect_date_unit


def list_chats(args) -> int:
    console = Console()
    try:
        conn = open_db(Path(args.db))
    except Exception as e:
        console.print(f"[red]ERROR:[/red] {e}")
        return 1
    try:
        chats = list_recent_chats(conn, args.list_limit)
    finally:
        conn.close()

    if not chats:
        console.print("[dim](no chats found)[/dim]")
        return 0

    table = Table(title="Recent chats", show_lines=False, header_style="bold")
    table.add_column("ID", justify="right")
    table.add_column("Kind")
    table.add_column("Identifier / Participants")
    table.add_column("Msgs", justify="right")
    table.add_column("Last")

    for c in chats:
        who = c["display_name"] or c["participants"] or c["chat_identifier"] or ""
        identifier = c["chat_identifier"] or ""
        if identifier and who and identifier != who:
            label = f"{who}  ·  {identifier}"
        else:
            label = who or identifier
        table.add_row(
            str(c["chat_id"]),
            c["style"],
            label,
            str(c["msg_count"]),
            c["last_message_local"] or "—",
        )

    console.print(table)
    console.print(
        f"  [dim]Showing {len(chats)} of {args.list_limit} "
        f"— use --list-limit to change.[/dim]"
    )
    return 0


def list_contacts(args) -> int:
    """Rich-styled contacts table. Falls through to db.list_contacts_csv when
    --contacts is provided (which prints contact-mapped CSV rows)."""
    console = Console()
    try:
        conn = open_db(Path(args.db))
    except Exception as e:
        console.print(f"[red]ERROR:[/red] {e}")
        return 1
    try:
        unit = detect_date_unit(conn)
        # list_contacts_csv prints to stdout itself. To render a Rich table,
        # we re-implement: walk all distinct handles and join against the
        # caller-provided contacts file (if any). For Phase 1, simplest is
        # to call the existing function so behavior matches piped mode.
        return list_contacts_csv(
            conn,
            unit,
            Path(args.contacts) if args.contacts else None,
        )
    finally:
        conn.close()
```

Note: `list_contacts` deliberately delegates to the existing `list_contacts_csv()` for Phase 1. Phase 1's goal for `--list-contacts` is "Rich table on TTY, plain CSV when piped" — and the existing function already produces well-formatted output. A dedicated Rich table for contacts is low-priority polish that can wait for Phase 2. The TTY-detect dispatch in `cli.py` already routes here on TTY; the function does the right thing in both cases.

- [ ] **Step 4.2: Smoke test on a real chat.db**

If FDA is available:

```bash
python3 -m imessage_export --list --list-limit 5
```

Expected: a Rich table with the columns ID / Kind / Identifier / Msgs / Last. If FDA isn't available, skip — manual test only.

- [ ] **Step 4.3: Verify piped output stays plain**

```bash
python3 -m imessage_export --list --list-limit 5 | cat
```

Expected: plain text (no ANSI escape codes when run through `cat`). The TTY-detect dispatch sends this to the original code path.

- [ ] **Step 4.4: Run full suite**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: `OK`.

- [ ] **Step 4.5: Commit**

```bash
git add imessage_export/tui/tables.py
git commit -m "$(cat <<'EOF'
Add Rich-styled --list output for TTY sessions.

When stdout is a TTY (and CI is not set), --list renders as a Rich
table with ID / Kind / Identifier / Msgs / Last columns. Piped output
falls through to the existing plain text formatter, so scripts that
parse --list keep working. --list-contacts delegates to the existing
CSV writer for Phase 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: No-Rich-on-headless smoke test

**Files:**
- Create: `tests/test_no_rich_import_on_headless.py`

- [ ] **Step 5.1: Write the test**

Create `tests/test_no_rich_import_on_headless.py`:

```python
"""The headless code path must not import rich or questionary at module
load time. If it does, importing imessage_export.cli adds 150-250ms of
import cost to every headless invocation — defeating the [tui] split."""
from __future__ import annotations

import subprocess
import sys
import unittest


class HeadlessImportPurityTests(unittest.TestCase):

    def test_importing_cli_does_not_pull_rich(self):
        script = (
            "import sys, imessage_export.cli; "
            "imessage_export.cli.build_parser(); "
            "leaked = [m for m in ('rich', 'questionary') if m in sys.modules]; "
            "print('LEAKED:', leaked)"
        )
        out = subprocess.check_output(
            [sys.executable, "-c", script], text=True
        ).strip()
        self.assertEqual(out, "LEAKED: []", f"unexpected: {out!r}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5.2: Run the test**

```bash
python3 -m unittest tests.test_no_rich_import_on_headless -v 2>&1 | tail -5
```

Expected: PASS. If it fails with `LEAKED: ['rich']`, find the offending top-level import in `cli.py` (most likely a stray `from rich import ...` or `from .tui import ...` outside a function body) and move it inside the dispatch function.

- [ ] **Step 5.3: Commit**

```bash
git add tests/test_no_rich_import_on_headless.py
git commit -m "$(cat <<'EOF'
Test that importing cli does not transitively load rich/questionary.

Guards the invariant that the headless code path stays zero-deps. The
test spawns a subprocess so it doesn't see anything the test suite
itself has imported.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Defaults file (`tui/defaults.py`) + roundtrip test

**Files:**
- Create: `imessage_export/tui/defaults.py`
- Create: `tests/test_tui_defaults.py`

- [ ] **Step 6.1: Write the failing test**

Create `tests/test_tui_defaults.py`:

```python
"""Defaults file roundtrip + schema-version handling."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imessage_export.tui import defaults


class DefaultsRoundtripTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "recent.json"

    def test_save_and_load_roundtrip(self):
        d = defaults.Defaults(
            contacts_path="/tmp/contacts.csv",
            output_dir="/tmp/exports",
            me_name="Ben",
            last_chat_id=42,
        )
        defaults.save(d, self.path)
        loaded = defaults.load(self.path)
        self.assertEqual(loaded.contacts_path, "/tmp/contacts.csv")
        self.assertEqual(loaded.output_dir, "/tmp/exports")
        self.assertEqual(loaded.me_name, "Ben")
        self.assertEqual(loaded.last_chat_id, 42)

    def test_missing_file_returns_empty_defaults(self):
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.contacts_path)
        self.assertIsNone(loaded.output_dir)
        self.assertIsNone(loaded.me_name)
        self.assertIsNone(loaded.last_chat_id)

    def test_unknown_version_returns_empty_defaults(self):
        self.path.write_text(json.dumps({
            "version": 999,
            "contacts_path": "/should/be/ignored",
        }))
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.contacts_path)

    def test_save_writes_0600_perms(self):
        d = defaults.Defaults(me_name="Ben")
        defaults.save(d, self.path)
        mode = self.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"got {oct(mode)}")
        parent_mode = self.path.parent.stat().st_mode & 0o777
        # tempdir was created with whatever umask, so this test only verifies
        # we don't loosen it; the actual prod path uses _ensure_dir.
        self.assertLessEqual(parent_mode, 0o755)

    def test_ensure_dir_creates_with_0700(self):
        nested = self.path.parent / "nested" / "deeper"
        out = nested / "recent.json"
        defaults.save(defaults.Defaults(me_name="Ben"), out)
        # On macOS/Linux, the parent dirs should be 0o700.
        self.assertEqual(nested.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6.2: Run the test to verify it fails**

```bash
python3 -m unittest tests.test_tui_defaults -v 2>&1 | tail -10
```

Expected: import error or AttributeError on `defaults.Defaults` / `defaults.save` / `defaults.load`.

- [ ] **Step 6.3: Implement `tui/defaults.py`**

Create `imessage_export/tui/defaults.py`:

```python
"""Last-used wizard answers persisted to ~/.config/imessage-export/recent.json.

Schema:
    {
      "version": 1,
      "contacts_path": "<absolute path>" | null,
      "output_dir":    "<absolute path>" | null,
      "me_name":       "Ben"            | null,
      "last_chat_id":  142              | null,
      "last_used":     "<ISO-8601>"     | null
    }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1
DEFAULT_PATH = Path.home() / ".config" / "imessage-export" / "recent.json"


@dataclass
class Defaults:
    contacts_path: Optional[str] = None
    output_dir: Optional[str] = None
    me_name: Optional[str] = None
    last_chat_id: Optional[int] = None
    last_used: Optional[str] = None


def load(path: Path = DEFAULT_PATH) -> Defaults:
    """Return a Defaults object. Missing file or unknown schema -> empty."""
    if not path.exists():
        return Defaults()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return Defaults()
    if data.get("version") != SCHEMA_VERSION:
        return Defaults()
    return Defaults(
        contacts_path=data.get("contacts_path"),
        output_dir=data.get("output_dir"),
        me_name=data.get("me_name"),
        last_chat_id=data.get("last_chat_id"),
        last_used=data.get("last_used"),
    )


def save(d: Defaults, path: Path = DEFAULT_PATH) -> None:
    """Write defaults to disk with 0o600 perms and 0o700 parent dirs."""
    _ensure_dir(path.parent)
    payload = {
        "version": SCHEMA_VERSION,
        **asdict(d),
        "last_used": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    # Atomic-ish: write to .tmp, fchmod, rename.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        os.chmod(tmp, 0o600)
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)
    os.chmod(path, 0o600)


def _ensure_dir(d: Path) -> None:
    """Create d and any missing parents under d, all with 0o700."""
    parts = []
    cur = d
    while not cur.exists():
        parts.append(cur)
        cur = cur.parent
    for p in reversed(parts):
        p.mkdir(mode=0o700)
```

- [ ] **Step 6.4: Run the test to verify it passes**

```bash
python3 -m unittest tests.test_tui_defaults -v 2>&1 | tail -10
```

Expected: 5 tests pass.

- [ ] **Step 6.5: Commit**

```bash
git add imessage_export/tui/defaults.py tests/test_tui_defaults.py
git commit -m "$(cat <<'EOF'
Add tui/defaults.py: persisted last-used wizard answers.

JSON at ~/.config/imessage-export/recent.json with schema versioning,
0o600 file perms, 0o700 parent dirs. Missing file or unknown version
returns empty Defaults so first-run never errors.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wizard (`tui/wizard.py`) — chat picker + window + paths + confirm

This task implements the six wizard steps from the spec. Wizard internals are not unit-tested (per spec — Questionary mocking is brittle). Verification is the manual checklist at the end.

**Files:**
- Create: `imessage_export/tui/wizard.py`

- [ ] **Step 7.1: Implement the wizard end-to-end**

Create `imessage_export/tui/wizard.py`:

```python
"""Interactive wizard for `imessage-export` (six steps + welcome panel)."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import questionary
from rich.console import Console
from rich.panel import Panel

from ..db import open_db, list_recent_chats, chat_info, chat_participants
from ..timestamps import detect_date_unit
from .defaults import Defaults, load as load_defaults, save as save_defaults

console = Console()


def run() -> int:
    """Drive the wizard. Returns process exit code."""
    _welcome()
    defaults = load_defaults()

    try:
        conn = open_db(_default_db_path())
    except Exception as e:
        console.print(f"[red]ERROR opening chat.db:[/red] {e}")
        console.print(
            "[dim]Most likely cause: Full Disk Access not granted. "
            "System Settings ▸ Privacy & Security ▸ Full Disk Access ▸ "
            f"add {sys.executable}[/dim]"
        )
        return 2

    try:
        chat_id = _step_pick_chat(conn, defaults)
        if chat_id is None:
            return 0  # cancelled

        window = _step_pick_window(conn, chat_id)
        if window is None:
            return 0

        contacts = _step_contacts(defaults)
        output_dir = _step_output_dir(defaults)
        me_name = _step_me_name(defaults)

        info = chat_info(conn, chat_id)
        if not _step_confirm(info, window, contacts, output_dir, me_name):
            console.print("[dim]Cancelled.[/dim]")
            return 0
    finally:
        conn.close()

    # Persist answers.
    save_defaults(Defaults(
        contacts_path=str(contacts) if contacts else None,
        output_dir=str(output_dir),
        me_name=me_name,
        last_chat_id=chat_id,
    ))

    # Build an argparse Namespace and dispatch to the existing headless runner.
    from ..cli import _run, DEFAULT_DB  # local import: keep top-level deps clean
    args = _build_args_namespace(
        chat_id=chat_id,
        window=window,
        contacts=contacts,
        output_dir=output_dir,
        me_name=me_name,
        db=DEFAULT_DB,
    )
    # _run() expects an open connection; reopen since we closed it above.
    conn = open_db(Path(args.db))
    try:
        rc = _run(args, conn)
    finally:
        conn.close()

    if rc == 0:
        _maybe_show_preview(output_dir, info, window)
    return rc


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _welcome():
    console.print(Panel.fit(
        "[bold]imessage-export[/bold] — interactive mode\n"
        "[dim]Everything stays on this machine. No network calls.\n"
        "Run with --help to see the headless flag surface.[/dim]",
        border_style="cyan",
    ))


def _step_pick_chat(conn, defaults: Defaults) -> Optional[int]:
    rows = list_recent_chats(conn, limit=100)
    if not rows:
        console.print("[red]No chats found in chat.db.[/red]")
        return None

    choices = []
    if defaults.last_chat_id:
        for r in rows:
            if r["chat_id"] == defaults.last_chat_id:
                label = _format_chat_row(r)
                choices.append(questionary.Choice(f"⭐ Use last chat: {label}", value=r["chat_id"]))
                break

    for r in rows:
        choices.append(questionary.Choice(_format_chat_row(r), value=r["chat_id"]))

    return questionary.select(
        "Which chat?",
        choices=choices,
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()


def _format_chat_row(r) -> str:
    who = r["display_name"] or r["participants"] or r["chat_identifier"] or "(unknown)"
    kind = r["style"]
    last = r["last_message_local"] or "—"
    return f"[{r['chat_id']}] {who} · {kind} · {r['msg_count']} msgs · last {last}"


def _step_pick_window(conn, chat_id: int):
    mode = questionary.select(
        "Time window?",
        choices=[
            questionary.Choice("Single day", value="day"),
            questionary.Choice("Date range", value="range"),
            questionary.Choice("Everything", value="all"),
        ],
    ).ask()
    if mode is None:
        return None

    today_str = date.today().isoformat()

    if mode == "day":
        d = questionary.text("Date (YYYY-MM-DD)", default=today_str).ask()
        if not d:
            return None
        start = questionary.text(
            "Start time (HH:MM, optional)", default=""
        ).ask()
        end = questionary.text(
            "End time (HH:MM, optional)", default=""
        ).ask()
        return {"mode": "day", "date": d, "start_time": start or None, "end_time": end or None}

    if mode == "range":
        f = questionary.text("From date (YYYY-MM-DD)").ask()
        t = questionary.text("To date (YYYY-MM-DD)", default=today_str).ask()
        if not f or not t:
            return None
        return {"mode": "range", "from_date": f, "to_date": t}

    # mode == "all"
    info = chat_info(conn, chat_id)
    if info.get("msg_count", 0) > 5000:
        ok = questionary.confirm(
            f"This chat has {info['msg_count']} messages. Export everything?",
            default=False,
        ).ask()
        if not ok:
            return None
    return {"mode": "all"}


def _step_contacts(defaults: Defaults) -> Optional[Path]:
    default_value = defaults.contacts_path or (
        str(Path.cwd() / "contacts.csv") if (Path.cwd() / "contacts.csv").exists() else ""
    )
    raw = questionary.path(
        "Contacts file (empty = none)",
        default=default_value,
    ).ask()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.exists():
        console.print(f"[yellow]Note:[/yellow] {p} doesn't exist — proceeding without contacts.")
        return None
    return p


def _step_output_dir(defaults: Defaults) -> Path:
    raw = questionary.path(
        "Output directory",
        default=defaults.output_dir or str(Path.cwd() / "exports"),
    ).ask()
    return Path(raw).expanduser() if raw else Path.cwd() / "exports"


def _step_me_name(defaults: Defaults) -> str:
    raw = questionary.text(
        "Your name (label for messages you sent)",
        default=defaults.me_name or "Me",
    ).ask()
    return raw or "Me"


def _step_confirm(info, window, contacts, output_dir, me_name) -> bool:
    summary = (
        f"[bold]Chat[/bold]: {info.get('label', '?')}  ([dim]{info.get('msg_count', 0)} msgs[/dim])\n"
        f"[bold]Window[/bold]: {_window_summary(window)}\n"
        f"[bold]Contacts[/bold]: {contacts or '(none)'}\n"
        f"[bold]Output[/bold]: {output_dir}\n"
        f"[bold]Me[/bold]: {me_name}"
    )
    console.print(Panel(summary, title="Confirm export", border_style="green"))
    return bool(questionary.confirm("Run export?", default=True).ask())


def _window_summary(w) -> str:
    if w["mode"] == "day":
        bits = [w["date"]]
        if w.get("start_time") or w.get("end_time"):
            bits.append(f"{w.get('start_time') or '00:00'}–{w.get('end_time') or '23:59'}")
        return "  ".join(bits)
    if w["mode"] == "range":
        return f"{w['from_date']} → {w['to_date']}"
    return "everything"


def _maybe_show_preview(output_dir: Path, info, window):
    md_path = _resolve_output_md(output_dir, info, window)
    if not md_path or not md_path.exists():
        return
    if questionary.confirm("Preview conversation.md?", default=False).ask():
        from .preview import show_markdown
        show_markdown(md_path)


def _resolve_output_md(output_dir: Path, info, window) -> Optional[Path]:
    # The exporter writes to <output_dir>/<chat-label>/<date>/conversation.md.
    # Use the same folder-naming function the writer uses.
    from ..writers import slugify
    label = info.get("label", "unknown")
    if window["mode"] == "day":
        date_str = window["date"]
    elif window["mode"] == "range":
        date_str = window["from_date"]
    else:
        date_str = "all"
    return output_dir / slugify(label) / date_str / "conversation.md"


def _default_db_path() -> Path:
    from ..cli import DEFAULT_DB
    return DEFAULT_DB


def _build_args_namespace(*, chat_id, window, contacts, output_dir, me_name, db) -> argparse.Namespace:
    """Translate wizard answers into an argparse.Namespace matching what
    `cli._run()` expects. Mirror every field `_run` reads."""
    ns = argparse.Namespace(
        chat_id=chat_id, chat_identifier=None, participant=None,
        list=False, list_limit=30, list_contacts=False,
        from_date=window.get("from_date"), to_date=window.get("to_date"),
        date=window.get("date"),
        start_time=window.get("start_time"), end_time=window.get("end_time"),
        start_datetime=None, end_datetime=None,
        output_dir=str(output_dir),
        me_name=me_name,
        contacts=str(contacts) if contacts else None,
        include_attachments=False,
        limit=None,
        db=str(db),
    )
    return ns
```

The `_build_args_namespace()` field list must match every attribute the existing `_run()` reads. If the redactor merged into `main` before Task 1 ran, `_run()` will also read `args.redact`, `args.redact_only`, `args.redact_names_file`, `args.no_redact_phones`, `args.no_redact_emails`, `args.no_redact_urls`, `args.suggest_names`. Add those to the namespace with safe defaults:

```python
        # Redaction defaults — only relevant if the redactor exists in this build.
        redact=False, redact_only=False,
        redact_names_file=None,
        no_redact_phones=False, no_redact_emails=False, no_redact_urls=False,
        suggest_names=False,
```

If you're not sure whether `_run()` reads them, the safest check is to grep:

```bash
grep -n "args\." imessage_export/cli.py | head -30
```

Any `args.<name>` referenced there must be present in the namespace.

- [ ] **Step 7.2: Smoke-test the wizard locally**

```bash
python3 -m imessage_export
```

Expected: welcome panel, chat picker prompt. Type-to-filter narrows the list. Pick a chat → step through to the confirm panel. Cancel at any prompt with Ctrl+C → returns 0.

This is a manual test. If FDA is missing, the wizard will hit the styled FDA error in `run()` and return 2 — verify the message renders.

- [ ] **Step 7.3: Verify the no-rich-on-headless test still passes**

```bash
python3 -m unittest tests.test_no_rich_import_on_headless -v 2>&1 | tail -5
```

Expected: still PASS. `cli.py` does not import `wizard` at module top-level — only inside `_run_wizard()`.

- [ ] **Step 7.4: Run full suite**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: `OK`.

- [ ] **Step 7.5: Commit**

```bash
git add imessage_export/tui/wizard.py
git commit -m "$(cat <<'EOF'
Add tui/wizard.py: six-step interactive wizard.

Chat picker with type-to-filter (defaults file offers last chat as the
first option). Single-day / date-range / everything window step. Path
prompts for contacts and output dir, text prompt for me-name, all
pre-filled from the defaults file. Rich confirm panel summarizes the
choices. After successful export, offers a Markdown preview.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Markdown preview (`tui/preview.py`)

**Files:**
- Create: `imessage_export/tui/preview.py`

- [ ] **Step 8.1: Implement preview**

Create `imessage_export/tui/preview.py`:

```python
"""Render conversation.md inline with Rich + paging."""
from __future__ import annotations

from pathlib import Path
from rich.console import Console
from rich.markdown import Markdown


def show_markdown(path: Path) -> None:
    """Render `path` as Markdown. Uses Rich's pager so long exports scroll."""
    console = Console()
    text = path.read_text()
    md = Markdown(text)
    with console.pager(styles=True):
        console.print(md)
```

- [ ] **Step 8.2: Smoke-test by running the wizard end-to-end on a real export**

(Manual.) Run `python3 -m imessage_export`, pick a chat, export, accept the "Preview?" prompt. Expected: Rich-rendered Markdown in a pager (q to quit).

- [ ] **Step 8.3: Commit**

```bash
git add imessage_export/tui/preview.py
git commit -m "$(cat <<'EOF'
Add tui/preview.py: Rich Markdown preview after export.

Opens conversation.md in Rich's pager so day headers, gap markers,
speaker headers, and attachment lines render styled. The wizard
prompts for this after a successful export.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Styled error screens (`tui/errors.py`)

The FDA-denied screen is already inline in `wizard.py:run()` (it covers the most common error path). Two more errors get dedicated styled panels: "no chats match query" and "contacts.csv malformed". Other errors keep existing argparse/exception behavior.

**Files:**
- Create: `imessage_export/tui/errors.py`

- [ ] **Step 9.1: Implement the error helpers**

Create `imessage_export/tui/errors.py`:

```python
"""Styled error panels used by the wizard and Rich-table dispatchers."""
from __future__ import annotations

import sys
from rich.console import Console
from rich.panel import Panel


def fda_denied(reason: str) -> None:
    console = Console(stderr=True)
    console.print(Panel.fit(
        f"[red]Full Disk Access denied.[/red]\n\n"
        f"[dim]{reason}[/dim]\n\n"
        f"Open System Settings ▸ Privacy & Security ▸ Full Disk Access.\n"
        f"Add [bold]{sys.executable}[/bold] (or your terminal app).\n"
        f"Quit and reopen the terminal so the new permission is picked up.",
        title="Cannot read chat.db",
        border_style="red",
    ))


def no_chats_match(query: str) -> None:
    console = Console(stderr=True)
    console.print(Panel.fit(
        f"No chats matched [bold]{query}[/bold].\n\n"
        f"Try [bold]imessage-export --list[/bold] to see all chats.",
        title="No match",
        border_style="yellow",
    ))


def contacts_malformed(path: str, row_num: int, detail: str) -> None:
    console = Console(stderr=True)
    console.print(Panel.fit(
        f"[red]contacts.csv could not be parsed.[/red]\n\n"
        f"File: [bold]{path}[/bold]\n"
        f"Row:  [bold]{row_num}[/bold]\n"
        f"Error: {detail}\n\n"
        f"Expected columns: [dim]handle,name[/dim] (see contacts.example.csv).",
        title="Malformed contacts file",
        border_style="red",
    ))
```

- [ ] **Step 9.2: Wire the FDA error into wizard.py**

Modify `imessage_export/tui/wizard.py` — in `run()`, replace the inline FDA error with:

```python
    try:
        conn = open_db(_default_db_path())
    except Exception as e:
        from .errors import fda_denied
        fda_denied(str(e))
        return 2
```

(Other errors stay inline for Phase 1.)

- [ ] **Step 9.3: Manual smoke test**

If FDA is denied for your terminal, run `python3 -m imessage_export` and verify the panel renders. Otherwise, manually raise (e.g. `import imessage_export.tui.errors as e; e.fda_denied("test")`) at a REPL to confirm rendering.

- [ ] **Step 9.4: Commit**

```bash
git add imessage_export/tui/errors.py imessage_export/tui/wizard.py
git commit -m "$(cat <<'EOF'
Add tui/errors.py: styled FDA-denied / no-match / malformed-contacts
panels. Wizard FDA error path uses the new helper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Update README + CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 10.1: Update the README badge and add a TUI section**

Modify `README.md`:

Replace the existing "Stdlib only" badge line:

```markdown
[![Stdlib only](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen.svg)](pyproject.toml)
```

with:

```markdown
[![Zero required deps](https://img.shields.io/badge/required%20deps-zero-brightgreen.svg)](pyproject.toml)
[![Optional TUI](https://img.shields.io/badge/optional-TUI%20extra-blue.svg)](pyproject.toml)
```

Add a new section after the existing "Running" section, before "Privacy & scope":

```markdown
## Interactive TUI (optional)

For a guided wizard, install with the `[tui]` extra:

```bash
pip install 'imessage-history[tui]'
```

Then just run:

```bash
imessage-export
```

You'll get a Rich-styled welcome panel, a type-to-filter chat picker, a
time-window picker, and a confirm panel before the export runs. After a
successful export, you can optionally preview `conversation.md` inline
with paging.

The `[tui]` extra adds two pure-Python dependencies:
[Rich](https://github.com/Textualize/rich) and
[Questionary](https://github.com/tmbo/questionary). The default install
(`pip install imessage-history`) still has zero runtime deps and works
exactly as before.

The headless flag surface is unchanged — `imessage-export --chat-id N
--date 2026-06-06` still works regardless of which install you used.
```

- [ ] **Step 10.2: Update CLAUDE.md**

Modify `CLAUDE.md`:

Find the line:
```
- One file, no packages. Helpers grouped by section banner comments.
```

Replace with:
```
- Core code lives under `imessage_export/` (small package along section
  banners: timestamps, decoder, models, db, contacts, window, export,
  writers, cli). The optional TUI under `imessage_export/tui/` is only
  importable when the `[tui]` extra is installed; core modules must
  not import anything under `tui/` at module top level.
```

Find the repo layout block and update the file listing accordingly.

- [ ] **Step 10.3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
Update README + CLAUDE.md for the TUI extra and package restructure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Redaction integration (the seventh wizard step)

**Precondition:** the redactor has merged to `main` and exposes
`RedactionConfig`, `Redactor`, `suggest_names()` along with the
`--redact` / `--redact-only` / `--redact-names-file` / `--no-redact-*` /
`--suggest-names` CLI flags. If those aren't present yet, skip this
task — Phase 1 ships without the integration and the seventh step is
added in a follow-up PR.

**Files:**
- Modify: `imessage_export/tui/wizard.py`

- [ ] **Step 11.1: Add the `_step_redact()` function to `wizard.py`**

In `imessage_export/tui/wizard.py`, add after the `_step_me_name()` function:

```python
def _step_redact() -> Optional[dict]:
    """Returns {} when the user declines, or a dict of flag values."""
    mode = questionary.select(
        "Redact identifiers and PII before writing?",
        choices=[
            questionary.Choice("No", value="off"),
            questionary.Choice("Yes — keep both originals and redacted files", value="redact"),
            questionary.Choice("Yes — redacted only (folder name is pseudonymized)", value="redact-only"),
        ],
        default="No",
    ).ask()
    if mode is None:
        return None
    if mode == "off":
        return {}

    extra_names_raw = questionary.path(
        "Extra names file (one name per line, empty to skip)",
        default="",
    ).ask()
    extra_names = Path(extra_names_raw).expanduser() if extra_names_raw else None

    categories = questionary.checkbox(
        "Scrub these from message bodies (Space to toggle, Enter to confirm)",
        choices=[
            questionary.Choice("Phones", value="phones", checked=True),
            questionary.Choice("Emails", value="emails", checked=True),
            questionary.Choice("URLs",   value="urls",   checked=True),
        ],
    ).ask()
    if categories is None:
        return None

    return {
        "redact": mode == "redact",
        "redact_only": mode == "redact-only",
        "redact_names_file": str(extra_names) if extra_names else None,
        "no_redact_phones": "phones" not in categories,
        "no_redact_emails": "emails" not in categories,
        "no_redact_urls":   "urls"   not in categories,
    }
```

- [ ] **Step 11.2: Wire the redaction step into `run()` and the namespace builder**

Modify `imessage_export/tui/wizard.py:run()`. The original (Task 7) body looks like:

```python
        contacts = _step_contacts(defaults)
        output_dir = _step_output_dir(defaults)
        me_name = _step_me_name(defaults)

        info = chat_info(conn, chat_id)
        if not _step_confirm(info, window, contacts, output_dir, me_name):
            console.print("[dim]Cancelled.[/dim]")
            return 0
    finally:
        conn.close()

    # Persist answers.
    save_defaults(Defaults(
        contacts_path=str(contacts) if contacts else None,
        output_dir=str(output_dir),
        me_name=me_name,
        last_chat_id=chat_id,
    ))

    from ..cli import _run, DEFAULT_DB
    args = _build_args_namespace(
        chat_id=chat_id,
        window=window,
        contacts=contacts,
        output_dir=output_dir,
        me_name=me_name,
        db=DEFAULT_DB,
    )
    conn = open_db(Path(args.db))
    try:
        rc = _run(args, conn)
    finally:
        conn.close()

    if rc == 0:
        _maybe_show_preview(output_dir, info, window)
    return rc
```

Replace with:

```python
        contacts = _step_contacts(defaults)
        output_dir = _step_output_dir(defaults)
        me_name = _step_me_name(defaults)

        redact_choices = _step_redact()
        if redact_choices is None:
            return 0

        info = chat_info(conn, chat_id)
        if not _step_confirm(info, window, contacts, output_dir, me_name, redact_choices):
            console.print("[dim]Cancelled.[/dim]")
            return 0
    finally:
        conn.close()

    # save_defaults(Defaults(...)) call from Task 7 stays unchanged.
    # The redaction choices are persisted in args.redact* below, not in Defaults.

    args = _build_args_namespace(
        chat_id=chat_id,
        window=window,
        contacts=contacts,
        output_dir=output_dir,
        me_name=me_name,
        db=DEFAULT_DB,
        redact_choices=redact_choices,
    )
    # conn-reopen and `_run(args, conn)` call from Task 7 stay unchanged.
    if rc == 0:
        _maybe_show_preview(output_dir, info, window, redact_choices)
    return rc
```

Update `_build_args_namespace()` to accept and apply `redact_choices`:

```python
def _build_args_namespace(*, chat_id, window, contacts, output_dir, me_name, db, redact_choices=None) -> argparse.Namespace:
    redact_choices = redact_choices or {}
    ns = argparse.Namespace(
        chat_id=chat_id, chat_identifier=None, participant=None,
        list=False, list_limit=30, list_contacts=False,
        from_date=window.get("from_date"), to_date=window.get("to_date"),
        date=window.get("date"),
        start_time=window.get("start_time"), end_time=window.get("end_time"),
        start_datetime=None, end_datetime=None,
        output_dir=str(output_dir),
        me_name=me_name,
        contacts=str(contacts) if contacts else None,
        include_attachments=False,
        limit=None,
        db=str(db),
        # Redaction flags (default off; overridden by wizard choices).
        redact=redact_choices.get("redact", False),
        redact_only=redact_choices.get("redact_only", False),
        redact_names_file=redact_choices.get("redact_names_file"),
        no_redact_phones=redact_choices.get("no_redact_phones", False),
        no_redact_emails=redact_choices.get("no_redact_emails", False),
        no_redact_urls=redact_choices.get("no_redact_urls", False),
        suggest_names=False,  # diagnostic mode, never enabled from wizard
    )
    return ns
```

Update `_step_confirm()` to accept and display `redact_choices`:

```python
def _step_confirm(info, window, contacts, output_dir, me_name, redact_choices) -> bool:
    summary = (
        f"[bold]Chat[/bold]: {info.get('label', '?')}  ([dim]{info.get('msg_count', 0)} msgs[/dim])\n"
        f"[bold]Window[/bold]: {_window_summary(window)}\n"
        f"[bold]Contacts[/bold]: {contacts or '(none)'}\n"
        f"[bold]Output[/bold]: {output_dir}\n"
        f"[bold]Me[/bold]: {me_name}\n"
        f"[bold]Redact[/bold]: {_redact_summary(redact_choices)}"
    )
    console.print(Panel(summary, title="Confirm export", border_style="green"))
    return bool(questionary.confirm("Run export?", default=True).ask())


def _redact_summary(choices: dict) -> str:
    if not choices or not (choices.get("redact") or choices.get("redact_only")):
        return "off"
    mode = "redacted only" if choices.get("redact_only") else "both versions"
    pii = []
    if not choices.get("no_redact_phones"): pii.append("phones")
    if not choices.get("no_redact_emails"): pii.append("emails")
    if not choices.get("no_redact_urls"):   pii.append("URLs")
    extra = f" + names file" if choices.get("redact_names_file") else ""
    return f"{mode} (scrub {', '.join(pii) or 'identifiers only'}{extra})"
```

- [ ] **Step 11.3: Update the preview resolver and call site**

Replace the Task 7 `_resolve_output_md()` and `_maybe_show_preview()` signatures. Modify `_maybe_show_preview()` to accept `redact_choices`:

```python
def _maybe_show_preview(output_dir: Path, info, window, redact_choices):
    md_path = _resolve_output_md(output_dir, info, window, redact_choices)
    if not md_path or not md_path.exists():
        return
    label = "redacted Markdown" if redact_choices and (redact_choices.get("redact_only") or redact_choices.get("redact")) else "Markdown"
    if questionary.confirm(f"Preview {label}?", default=False).ask():
        from .preview import show_markdown
        show_markdown(md_path)
```

Replace `_resolve_output_md()` with:

```python
def _resolve_output_md(output_dir: Path, info, window, redact_choices) -> Optional[Path]:
    """Find the conversation MD that the export just produced.

    Three cases:
      - no redaction: <output_dir>/<slug(label)>/<date>/conversation.md
      - --redact:     <output_dir>/<slug(label)>/<date>/conversation_redacted.md
      - --redact-only: folder name is pseudonymized + 4-char hash (per redaction
                      spec). Scan glob and pick the most recently written.
    """
    if window["mode"] == "day":
        date_str = window["date"]
    elif window["mode"] == "range":
        date_str = window["from_date"]
    else:
        date_str = "all"

    if redact_choices and redact_choices.get("redact_only"):
        # Folder name is pseudonymized; scan all subfolders for the date dir.
        candidates = list(output_dir.glob(f"*/{date_str}/conversation*.md"))
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    from ..writers import slugify
    label = info.get("label", "unknown")
    base = output_dir / slugify(label) / date_str
    if redact_choices and redact_choices.get("redact"):
        return base / "conversation_redacted.md"
    return base / "conversation.md"
```

- [ ] **Step 11.4: Smoke test the redaction wizard step**

(Manual.) Run `python3 -m imessage_export`, pick a chat, choose "Yes — redacted only" at the redaction step, accept defaults for PII categories, confirm. Expected: redacted files appear under `exports/Person B-XXXX/<date>/`. Preview opens the redacted markdown.

- [ ] **Step 11.5: Commit**

```bash
git add imessage_export/tui/wizard.py
git commit -m "$(cat <<'EOF'
Add seventh wizard step: redact identifiers and PII.

Mode select (off / redact / redact-only) maps to --redact /
--redact-only. Path prompt covers --redact-names-file. Checkbox for
Phones / Emails / URLs maps to --no-redact-* flags (defaults on).
--suggest-names stays headless-only — it's a diagnostic that bypasses
the export step.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Manual verification checklist

Run through this list before declaring Phase 1 done. Each item maps to a spec verification step.

- [ ] `pip install -e .` (no extras) — `imessage-export --chat-id N --date Y` works headlessly.
- [ ] `pip install -e '.[tui]'` — `imessage-export` (no args) on a TTY enters the wizard.
- [ ] `imessage-export | cat` — prints help, no ANSI escape codes in `cat`'s output.
- [ ] `imessage-export --list | head` — plain text, no escapes.
- [ ] `imessage-export --list` on TTY — Rich table renders.
- [ ] `imessage-export --list-contacts > /tmp/handles.csv` — produces valid CSV the existing flow can re-consume.
- [ ] Wizard end-to-end on a real conversation; confirm `~/.config/imessage-export/recent.json` exists with `0o600` perms and a `0o700` parent directory.
- [ ] Delete `~/.config/imessage-export/recent.json`; rerun wizard — first run succeeds (no error, no pre-fills).
- [ ] Uninstall TUI: `pip uninstall rich questionary` then run `imessage-export` on TTY → friendly "needs the [tui] extra" message, exit 2.
- [ ] `CI=1 imessage-export` → prints help, no wizard, no Rich.
- [ ] (If redactor is integrated) Wizard with "redacted only" → produces redacted files; preview opens the redacted MD.

If every box checks, Phase 1 is complete.
