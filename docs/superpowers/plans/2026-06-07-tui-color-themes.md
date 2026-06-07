# TUI Color Themes (dawnfox / terafox) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a unified color theme system to the Rich wizard and the Textual app using the dawnfox (light) and terafox (dark) palettes from EdenEast/nightfox.nvim, with macOS auto-detect and env/CLI/persisted overrides.

**Architecture:** A new `imessage_export/tui/theme.py` module owns both palette dicts, the precedence-walked resolver (`cli → env → defaults → macOS detect → fallback`), a `make_console()` helper that returns a Rich `Console` wired to a semantic `Theme`, and a `register_textual_themes()` helper that registers both palettes with the Textual app. Rich consumers (`wizard.py`, `tables.py`, `errors.py`) drop raw color names (`cyan`/`green`/`yellow`/`red`) in favor of semantic style names (`accent`/`success`/`warning`/`error`). The Textual app calls `register_textual_themes(self)` and sets `self.theme = resolve_theme_name()` in `on_mount`, then references `$primary`/`$success`/etc. tokens in `App.CSS`.

**Tech Stack:** Python 3.10+, stdlib `subprocess`+`unittest`+`json`, Rich ≥13 (already pinned), Textual ≥0.86 (pin bumped from `>=0.79`). No new runtime deps; everything lives under the existing `[tui]` extra.

---

## File Structure

**Create:**
- `imessage_export/tui/theme.py` — palette dicts, `detect_appearance()`, `resolve_theme_name()`, `resolve_palette()`, `make_console()`, `get_console()`, `register_textual_themes()`.
- `tests/test_theme.py` — palette completeness, console-style mapping, resolver precedence, `detect_appearance` mocked branches, idempotent textual-theme registration.

**Modify:**
- `imessage_export/tui/defaults.py` — add `Defaults.theme_override` field (load/save).
- `imessage_export/tui/wizard.py` — replace `console = Console()` with `console = get_console()`; replace raw color names with semantic styles.
- `imessage_export/tui/tables.py` — same: themed console + semantic styles.
- `imessage_export/tui/errors.py` — same.
- `imessage_export/tui/app/app.py` — register themes + set `self.theme` in `on_mount`; expand `App.CSS`.
- `imessage_export/tui/app/widgets.py` — replace inline `style="dim"`/`"bold"` in history rendering with CSS-class-based markup.
- `imessage_export/tui/app/modals.py` — add Theme row to `SettingsModal`.
- `imessage_export/cli.py` — add `--theme {dawnfox,terafox,auto}` flag.
- `pyproject.toml` — bump `textual>=0.79,<1.0` → `textual>=0.86,<1.0`.
- `tests/test_tui_defaults.py` — three new tests: `theme_override` roundtrip, back-compat, bad-value coercion.
- `tests/test_cli_tty_detect.py` — two new tests: flag parse, unknown-value rejection.
- `README.md` — short "Theming" section.
- `CLAUDE.md` — one convention bullet.

---

## Task 0: Branch + capture baseline

Not a code task. Pure setup so subsequent tasks are reproducible.

- [ ] **Step 0.1: Make sure `main` is clean and up to date**

```bash
git checkout main
git pull --ff-only origin main
git status --short
```

Expected: clean working tree, `main` up to date with origin. If anything's dirty, sort it out before continuing — the task does deletes/moves that will collide.

- [ ] **Step 0.2: Create the feature branch**

```bash
git checkout -b tui-color-themes
```

- [ ] **Step 0.3: Capture the pre-change test baseline**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -5
```

Expected: `OK` and a test count (e.g. `Ran 169 tests in 0.4s`). **Record the count.** Every task afterward must finish with the new count ≥ baseline (no skipped or removed tests).

---

## Task 1: Create `tui/theme.py` — palettes + resolver + Rich Console builder

This is the foundational module. Every other task imports from here.

**Files:**
- Create: `imessage_export/tui/theme.py`
- Create: `tests/test_theme.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_theme.py`:

```python
"""Unit tests for the theme resolver + Rich Console builder."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from imessage_export.tui import theme


class PaletteCompletenessTests(unittest.TestCase):
    REQUIRED_KEYS = {
        "bg", "bg_alt", "bg_3", "fg",
        "accent", "accent_alt",
        "success", "warning", "error",
        "day_header", "muted", "border_soft",
    }

    def test_dawnfox_has_all_keys(self):
        self.assertEqual(set(theme.DAWNFOX.keys()), self.REQUIRED_KEYS)

    def test_terafox_has_all_keys(self):
        self.assertEqual(set(theme.TERAFOX.keys()), self.REQUIRED_KEYS)

    def test_palettes_dict_lookup(self):
        self.assertIs(theme.PALETTES["dawnfox"], theme.DAWNFOX)
        self.assertIs(theme.PALETTES["terafox"], theme.TERAFOX)

    def test_all_values_are_hex_strings(self):
        for name, pal in theme.PALETTES.items():
            for key, val in pal.items():
                self.assertTrue(
                    val.startswith("#") and len(val) == 7,
                    f"{name}.{key}={val!r} is not a 7-char #rrggbb string",
                )


class MakeConsoleTests(unittest.TestCase):
    def test_console_registers_accent_style(self):
        console = theme.make_console(theme.DAWNFOX)
        # rich.style.Style stringifies to its color
        self.assertEqual(str(console.get_style("accent")), theme.DAWNFOX["accent"])

    def test_console_registers_success_style(self):
        console = theme.make_console(theme.TERAFOX)
        self.assertEqual(str(console.get_style("success")), theme.TERAFOX["success"])

    def test_console_registers_all_semantic_styles(self):
        console = theme.make_console(theme.DAWNFOX)
        for name in ("accent", "accent_alt", "success", "warning",
                     "error", "muted", "day_header"):
            # Will raise MissingStyle if not registered.
            console.get_style(name)


class DetectAppearanceTests(unittest.TestCase):
    def _completed(self, returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["defaults"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_dark_mode(self):
        with mock.patch.object(theme.subprocess, "run",
                               return_value=self._completed(0, "Dark\n")):
            self.assertEqual(theme.detect_appearance(), "dark")

    def test_light_mode_returncode_1(self):
        with mock.patch.object(theme.subprocess, "run",
                               return_value=self._completed(1, "")):
            self.assertEqual(theme.detect_appearance(), "light")

    def test_missing_binary(self):
        with mock.patch.object(theme.subprocess, "run",
                               side_effect=FileNotFoundError()):
            self.assertEqual(theme.detect_appearance(), "light")

    def test_timeout(self):
        with mock.patch.object(
            theme.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="defaults", timeout=2.0),
        ):
            self.assertEqual(theme.detect_appearance(), "light")


class ResolveThemeNameTests(unittest.TestCase):
    def test_cli_wins(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli="terafox", env="dawnfox", persisted="dawnfox",
            )
        self.assertEqual(name, "terafox")

    def test_env_beats_persisted(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli=None, env="terafox", persisted="dawnfox",
            )
        self.assertEqual(name, "terafox")

    def test_persisted_beats_detect(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli=None, env=None, persisted="terafox",
            )
        self.assertEqual(name, "terafox")

    def test_auto_falls_through_to_detect(self):
        with mock.patch.object(theme, "detect_appearance", return_value="dark"):
            name = theme.resolve_theme_name(
                cli=None, env="auto", persisted=None,
            )
        self.assertEqual(name, "terafox")

    def test_detect_light_means_dawnfox(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(cli=None, env=None, persisted=None)
        self.assertEqual(name, "dawnfox")

    def test_detect_dark_means_terafox(self):
        with mock.patch.object(theme, "detect_appearance", return_value="dark"):
            name = theme.resolve_theme_name(cli=None, env=None, persisted=None)
        self.assertEqual(name, "terafox")

    def test_unknown_env_treated_as_auto(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli=None, env="catppuccin", persisted=None,
            )
        self.assertEqual(name, "dawnfox")

    def test_unknown_persisted_treated_as_auto(self):
        with mock.patch.object(theme, "detect_appearance", return_value="dark"):
            name = theme.resolve_theme_name(
                cli=None, env=None, persisted="catppuccin",
            )
        self.assertEqual(name, "terafox")


class ResolvePaletteTests(unittest.TestCase):
    def test_returns_dict_matching_resolved_name(self):
        with mock.patch.object(theme, "resolve_theme_name", return_value="dawnfox"):
            self.assertIs(theme.resolve_palette(), theme.DAWNFOX)
        with mock.patch.object(theme, "resolve_theme_name", return_value="terafox"):
            self.assertIs(theme.resolve_palette(), theme.TERAFOX)


class GetConsoleTests(unittest.TestCase):
    def test_get_console_is_singleton_per_palette(self):
        # First call initializes; subsequent calls return the same instance.
        theme._reset_console_for_tests()
        c1 = theme.get_console()
        c2 = theme.get_console()
        self.assertIs(c1, c2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 1.2: Run the test to verify it fails**

```bash
python3 -m unittest tests.test_theme -v 2>&1 | tail -15
```

Expected: `ModuleNotFoundError: No module named 'imessage_export.tui.theme'`.

- [ ] **Step 1.3: Create `imessage_export/tui/theme.py`**

Create the new file:

```python
"""Color theme system: dawnfox (light) / terafox (dark) palettes.

Owns every color decision in the TUI. Consumers reference semantic
style names (`accent`, `success`, `warning`, `error`, `muted`, `day_header`)
and never hex codes or raw Rich color names.

Resolution order (first hit wins):
  1. CLI flag                    (cli="dawnfox" | "terafox")
  2. Env var                     (IMESSAGE_EXPORT_THEME=...)
  3. Persisted defaults          (Defaults.theme_override)
  4. macOS auto-detect           (defaults read -g AppleInterfaceStyle)
  5. Fallback                    ("terafox" — TUIs ship dark)

`auto`, `None`, and unknown strings all mean "skip me, fall through."
"""
from __future__ import annotations

import subprocess
from typing import Optional

from rich.console import Console
from rich.theme import Theme

# Palette role keys — both palettes expose this exact set.
DAWNFOX: dict[str, str] = {
    "bg":          "#faf4ed",
    "bg_alt":      "#f2e9e1",
    "bg_3":        "#e1d9cd",
    "fg":          "#575279",
    "accent":      "#286983",
    "accent_alt":  "#38836a",
    "success":     "#6e8e3d",
    "warning":     "#ad8000",
    "error":       "#c2453a",
    "day_header":  "#907aa9",
    "muted":       "#a59689",
    "border_soft": "#e1d9cd",
}

TERAFOX: dict[str, str] = {
    "bg":          "#152528",
    "bg_alt":      "#1d3337",
    "bg_3":        "#254147",
    "fg":          "#e6eaea",
    "accent":      "#5a93aa",
    "accent_alt":  "#a1cdd8",
    "success":     "#7aa4a1",
    "warning":     "#fda47f",
    "error":       "#e85c51",
    "day_header":  "#ad5c7c",
    "muted":       "#6b7e83",
    "border_soft": "#254147",
}

PALETTES: dict[str, dict[str, str]] = {
    "dawnfox": DAWNFOX,
    "terafox": TERAFOX,
}

_FALLBACK_THEME = "terafox"


def detect_appearance() -> str:
    """Return 'light' or 'dark' from macOS system appearance.

    Falls back to 'light' on non-macOS hosts, missing `defaults` binary,
    or any subprocess error — the resolver then picks dawnfox, which is
    fine for unknown environments (CI, headless containers).
    """
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "light"
    if result.returncode == 0 and result.stdout.strip() == "Dark":
        return "dark"
    return "light"


def resolve_theme_name(
    *,
    cli: Optional[str] = None,
    env: Optional[str] = None,
    persisted: Optional[str] = None,
) -> str:
    """Walk the precedence chain and return 'dawnfox' or 'terafox'."""
    for candidate in (cli, env, persisted):
        if candidate in PALETTES:
            return candidate
    # All inputs were None / "auto" / unknown -> fall back to detect.
    return "terafox" if detect_appearance() == "dark" else "dawnfox"


def resolve_palette(
    *,
    cli: Optional[str] = None,
    env: Optional[str] = None,
    persisted: Optional[str] = None,
) -> dict[str, str]:
    """Convenience: resolve to a palette dict instead of a name."""
    return PALETTES[resolve_theme_name(cli=cli, env=env, persisted=persisted)]


def make_console(palette: dict[str, str]) -> Console:
    """Build a Rich Console with semantic style names bound to the palette."""
    return Console(theme=Theme({
        "accent":     palette["accent"],
        "accent_alt": palette["accent_alt"],
        "success":    palette["success"],
        "warning":    palette["warning"],
        "error":      palette["error"],
        "muted":      palette["muted"],
        "day_header": palette["day_header"],
    }))


# Singleton Rich Console — initialized lazily so headless invocations
# never pay the import-time cost of Rich.
_console: Optional[Console] = None


def get_console() -> Console:
    """Return the shared Rich Console, initialized on first call."""
    global _console
    if _console is None:
        _console = make_console(resolve_palette())
    return _console


def _reset_console_for_tests() -> None:
    """Test-only hook to clear the singleton between tests."""
    global _console
    _console = None


def register_textual_themes(app) -> None:
    """Register dawnfox and terafox with a Textual App.

    Idempotent: re-registering a name is a no-op (catches the exception
    so on_mount can run more than once safely in tests).
    """
    from textual.theme import Theme as TextualTheme  # local import: keep CLI startup cheap

    for name, pal in PALETTES.items():
        ttheme = TextualTheme(
            name=name,
            primary=pal["accent"],
            secondary=pal["accent_alt"],
            accent=pal["accent_alt"],
            warning=pal["warning"],
            error=pal["error"],
            success=pal["success"],
            foreground=pal["fg"],
            background=pal["bg"],
            surface=pal["bg_alt"],
            panel=pal["bg_3"],
            dark=(name == "terafox"),
            variables={
                "day-header":  pal["day_header"],
                "muted":       pal["muted"],
                "border-soft": pal["border_soft"],
            },
        )
        try:
            app.register_theme(ttheme)
        except Exception:
            # Textual raises if name already registered; tolerate it.
            pass
```

- [ ] **Step 1.4: Run the test to verify it passes**

```bash
python3 -m unittest tests.test_theme -v 2>&1 | tail -25
```

Expected: all tests pass (~24 tests). The `test_register_textual_themes_idempotent` test is added in Task 5; ignore that name for now.

- [ ] **Step 1.5: Run the full suite to confirm no regression**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`, count = baseline + the new tests from Task 1.

- [ ] **Step 1.6: Commit**

```bash
git add imessage_export/tui/theme.py tests/test_theme.py
git commit -m "$(cat <<'EOF'
TUI theme module: dawnfox/terafox palettes + resolver

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extend `Defaults` with `theme_override`

Persist the user's explicit theme choice across runs. Older `recent.json` files must still load.

**Files:**
- Modify: `imessage_export/tui/defaults.py`
- Modify: `tests/test_tui_defaults.py`

- [ ] **Step 2.1: Add the failing tests**

Open `tests/test_tui_defaults.py` and append the new test class at the bottom (before any `if __name__ == "__main__":` line — if that line exists, insert before it):

```python


class ThemeOverrideTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "recent.json"

    def test_theme_override_roundtrip(self):
        from imessage_export.tui import defaults
        d = defaults.Defaults(me_name="Ben", theme_override="dawnfox")
        defaults.save(d, self.path)
        loaded = defaults.load(self.path)
        self.assertEqual(loaded.theme_override, "dawnfox")

    def test_old_file_without_theme_override_loads_cleanly(self):
        # Simulate a recent.json written before this change: same schema
        # version, no theme_override field.
        import json as jsonmod
        self.path.write_text(jsonmod.dumps({
            "version": 1,
            "contacts_path": "/tmp/contacts.csv",
            "output_dir":    "/tmp/exports",
            "me_name":       "Ben",
            "last_chat_id":  42,
            "last_used":     "2026-06-06T12:00:00-04:00",
        }))
        from imessage_export.tui import defaults
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.theme_override)
        self.assertEqual(loaded.me_name, "Ben")

    def test_bad_theme_override_value_coerced_to_none(self):
        import json as jsonmod
        self.path.write_text(jsonmod.dumps({
            "version": 1,
            "theme_override": "catppuccin-mocha",
        }))
        from imessage_export.tui import defaults
        loaded = defaults.load(self.path)
        self.assertIsNone(loaded.theme_override)
```

If `tests/test_tui_defaults.py` doesn't already import `tempfile` and `Path` at the top, those imports already exist — confirm by reading the file's first ~10 lines and append the new class after the last existing class.

- [ ] **Step 2.2: Run the new tests to confirm they fail**

```bash
python3 -m unittest tests.test_tui_defaults.ThemeOverrideTests -v 2>&1 | tail -10
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'theme_override'`. The bad-value and back-compat tests may pass by accident — that's fine, the roundtrip test is the gating one.

- [ ] **Step 2.3: Extend `Defaults` and the load/save paths**

Open `imessage_export/tui/defaults.py` and apply three edits.

Edit 1 — the dataclass:

```python
@dataclass
class Defaults:
    contacts_path: Optional[str] = None
    output_dir: Optional[str] = None
    me_name: Optional[str] = None
    last_chat_id: Optional[int] = None
    last_used: Optional[str] = None
    theme_override: Optional[str] = None
```

Edit 2 — the `load()` function. Replace the existing return:

```python
    raw_theme = data.get("theme_override")
    if raw_theme not in ("dawnfox", "terafox", None):
        raw_theme = None
    return Defaults(
        contacts_path=data.get("contacts_path"),
        output_dir=data.get("output_dir"),
        me_name=data.get("me_name"),
        last_chat_id=data.get("last_chat_id"),
        last_used=data.get("last_used"),
        theme_override=raw_theme,
    )
```

Edit 3 — also update the docstring at the top of the file:

```python
"""Last-used wizard answers persisted to ~/.config/imessage-export/recent.json.

Schema:
    {
      "version": 1,
      "contacts_path":   "<absolute path>"   | null,
      "output_dir":      "<absolute path>"   | null,
      "me_name":         "Ben"              | null,
      "last_chat_id":    142                | null,
      "last_used":       "<ISO-8601>"       | null,
      "theme_override":  "dawnfox"|"terafox"| null
    }
"""
```

`save()` does not need changes — it uses `asdict(d)` which automatically picks up the new field.

- [ ] **Step 2.4: Run the new tests to confirm they pass**

```bash
python3 -m unittest tests.test_tui_defaults.ThemeOverrideTests -v 2>&1 | tail -10
```

Expected: all three pass.

- [ ] **Step 2.5: Run the full suite**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 2.6: Commit**

```bash
git add imessage_export/tui/defaults.py tests/test_tui_defaults.py
git commit -m "$(cat <<'EOF'
defaults: persist theme_override (dawnfox|terafox|null)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `--theme` flag to argparse

**Files:**
- Modify: `imessage_export/cli.py`
- Modify: `tests/test_cli_tty_detect.py`

- [ ] **Step 3.1: Add the failing tests**

Open `tests/test_cli_tty_detect.py` and append before any `if __name__ == "__main__":` line:

```python


class ThemeFlagTests(unittest.TestCase):
    def test_theme_flag_parsed(self):
        from imessage_export import cli
        args = cli.build_parser().parse_args(["--theme", "terafox", "--chat-id", "42"])
        self.assertEqual(args.theme, "terafox")

    def test_theme_flag_accepts_auto(self):
        from imessage_export import cli
        args = cli.build_parser().parse_args(["--theme", "auto", "--chat-id", "42"])
        self.assertEqual(args.theme, "auto")

    def test_theme_flag_rejects_unknown(self):
        from imessage_export import cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--theme", "foo", "--chat-id", "42"])

    def test_theme_flag_default_is_none(self):
        from imessage_export import cli
        args = cli.build_parser().parse_args(["--chat-id", "42"])
        self.assertIsNone(args.theme)
```

- [ ] **Step 3.2: Run to verify failure**

```bash
python3 -m unittest tests.test_cli_tty_detect.ThemeFlagTests -v 2>&1 | tail -10
```

Expected: `AttributeError: 'Namespace' object has no attribute 'theme'` on all four.

- [ ] **Step 3.3: Wire the flag in `cli.py`**

Open `imessage_export/cli.py`. Find the `tui = p.add_argument_group("interactive mode")` block (around line 92) and append a third argument inside it:

```python
    tui.add_argument(
        "--theme",
        choices=("dawnfox", "terafox", "auto"),
        default=None,
        help="Override the active color theme. 'auto' means ignore "
             "any persisted preference and re-detect from macOS. "
             "Default: persisted preference or macOS auto-detect.",
    )
```

- [ ] **Step 3.4: Run to verify pass**

```bash
python3 -m unittest tests.test_cli_tty_detect.ThemeFlagTests -v 2>&1 | tail -10
```

Expected: all four pass.

- [ ] **Step 3.5: Run full suite**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 3.6: Commit**

```bash
git add imessage_export/cli.py tests/test_cli_tty_detect.py
git commit -m "$(cat <<'EOF'
cli: add --theme {dawnfox,terafox,auto} flag

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire the themed console into Rich consumers

This swap is mechanical but touches three files. Each file gets `get_console()` instead of `Console()` and each raw color name becomes a semantic style.

**Files:**
- Modify: `imessage_export/tui/wizard.py`
- Modify: `imessage_export/tui/tables.py`
- Modify: `imessage_export/tui/errors.py`

- [ ] **Step 4.1: Patch `tables.py`**

Open `imessage_export/tui/tables.py` and apply three edits.

Edit 1 — imports. Replace:

```python
from rich.console import Console
from rich.table import Table

from ..db import list_contacts_csv, list_recent_chats, open_db
from ..timestamps import detect_date_unit
```

with:

```python
from rich.table import Table

from ..db import list_contacts_csv, list_recent_chats, open_db
from ..timestamps import detect_date_unit
from .theme import get_console
```

Edit 2 — in `list_chats()`, replace:

```python
    console = Console()
```

with:

```python
    console = get_console()
```

Same edit in `list_contacts()`.

Edit 3 — semantic style names. In `list_chats()`:

```python
    table = Table(title="Recent chats", show_lines=False, header_style="bold")
    table.add_column("ID", justify="right", style="muted")
    table.add_column("Kind", style="muted")
    table.add_column("Participants", style="bold")
    table.add_column("Msgs", justify="right", style="muted")
    table.add_column("Last", style="bold accent")
```

And in both `list_chats()` and `list_contacts()` replace `[red]ERROR:[/red]` with `[error]ERROR:[/error]`. Replace `[dim](no chats found)[/dim]` with `[muted](no chats found)[/muted]`. Replace the trailing `[dim]Showing ... [/dim]` line with `[muted]Showing ... [/muted]`.

- [ ] **Step 4.2: Patch `errors.py`**

Open `imessage_export/tui/errors.py`. Replace the whole file body with:

```python
"""Styled error panels used by the wizard and Rich-table dispatchers."""
from __future__ import annotations

import sys

from rich.panel import Panel

from .theme import get_console


def fda_denied(reason: str) -> None:
    console = get_console()
    console.print(Panel.fit(
        f"[error]Full Disk Access denied.[/error]\n\n"
        f"[muted]{reason}[/muted]\n\n"
        f"Open System Settings ▸ Privacy & Security ▸ Full Disk Access.\n"
        f"Add [bold]{sys.executable}[/bold] (or your terminal app).\n"
        f"Quit and reopen the terminal so the new permission is picked up.",
        title="Cannot read chat.db",
        border_style="error",
    ), file=sys.stderr)


def no_chats_match(query: str) -> None:
    console = get_console()
    console.print(Panel.fit(
        f"No chats matched [bold]{query}[/bold].\n\n"
        f"Try [bold]imessage-export --list[/bold] to see all chats.",
        title="No match",
        border_style="warning",
    ), file=sys.stderr)


def contacts_malformed(path: str, row_num: int, detail: str) -> None:
    console = get_console()
    console.print(Panel.fit(
        f"[error]contacts.csv could not be parsed.[/error]\n\n"
        f"File: [bold]{path}[/bold]\n"
        f"Row:  [bold]{row_num}[/bold]\n"
        f"Error: {detail}\n\n"
        f"Expected columns: [muted]handle,name[/muted] (see contacts.example.csv).",
        title="Malformed contacts file",
        border_style="error",
    ), file=sys.stderr)
```

Note the explicit `file=sys.stderr` on each `console.print` — `get_console()` is shared and not stderr-bound by default, so we redirect per-call.

- [ ] **Step 4.3: Patch `wizard.py`**

Open `imessage_export/tui/wizard.py`. Apply five edits.

Edit 1 — replace the top-level `console = Console()` (around line 31) with the lazy accessor pattern:

```python
# Themed Rich console; resolved lazily so headless paths never pay for Rich.
from .theme import get_console as _get_console

def _console():
    return _get_console()
```

Then update the existing `from rich.console import Console` import — if it's only used for the now-removed `Console()` call, remove the line entirely. If it's used elsewhere (e.g. as a type hint), leave it.

Edit 2 — every `console.print(...)`, `console.status(...)`, and `console.input(...)` call in the file becomes `_console().print(...)`, etc. Do this with a global find/replace inside the file:

- `console.print(`  →  `_console().print(`
- `console.status(`  →  `_console().status(`
- `console.input(`   →  `_console().input(`

Edit 3 — semantic color tokens. Replace the following exact substrings (case-sensitive, all should be unique in their lines):

| Find                                              | Replace with                                       |
|---------------------------------------------------|----------------------------------------------------|
| `[bold cyan]Step {n}/{TOTAL_STEPS}[/bold cyan]`   | `[bold accent]Step {n}/{TOTAL_STEPS}[/bold accent]` |
| `[bold cyan]imessage-export[/bold cyan]`          | `[bold accent]imessage-export[/bold accent]`        |
| `border_style="cyan"`                             | `border_style="accent"`                            |
| `[yellow]No contacts file found.[/yellow]`        | `[warning]No contacts file found.[/warning]`        |
| `border_style="yellow"`                           | `border_style="warning"`                            |
| `[bold cyan]Reading Contacts.app… (up to 5 min on large books)[/bold cyan]` | `[bold accent]Reading Contacts.app… (up to 5 min on large books)[/bold accent]` |
| `[red]Could not read Contacts:[/red]`             | `[error]Could not read Contacts:[/error]`           |
| `[yellow]No contacts found in Contacts.app — proceeding without.[/yellow]` | `[warning]No contacts found in Contacts.app — proceeding without.[/warning]` |
| `[red]No chats found in chat.db.[/red]`           | `[error]No chats found in chat.db.[/error]`         |
| `[yellow]Didn't understand`                       | `[warning]Didn't understand`                        |
| `[/yellow].` (only in the "didn't understand" line — leave intact) | `[/warning].`                          |
| `[yellow]Note:[/yellow]`                          | `[warning]Note:[/warning]`                         |
| `style="bold cyan", justify="right")`             | `style="bold accent", justify="right")`            |
| `border_style="green",`                           | `border_style="success",`                          |
| `[bold green]✓[/bold green]`                      | `[bold success]✓[/bold success]`                    |

If the find/replace tool can't make a substring unique, anchor with one more line of context.

Edit 4 — verify nothing else in `wizard.py` uses `cyan`/`yellow`/`green`/`red` raw color names. Grep:

```bash
grep -nE "\\[(red|yellow|green|cyan)\\]|border_style=\"(red|yellow|green|cyan)\"|style=\"[^\"]*\\b(red|yellow|green|cyan)\\b" imessage_export/tui/wizard.py
```

Expected: zero hits. If anything remains, map it semantically (`cyan` → `accent`, `green` → `success`, `yellow` → `warning`, `red` → `error`) and rerun.

- [ ] **Step 4.4: Smoke-test that imports still work**

```bash
python3 -c "from imessage_export.tui import wizard, tables, errors; print('ok')"
```

Expected: `ok`. If you see an `ImportError`, the most likely culprit is a stray `from rich.console import Console` removal that broke another reference; restore the import.

- [ ] **Step 4.5: Run the full suite**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`. No new tests in this task — the regression net is the existing `test_no_rich_import_on_headless` smoke test (must still pass — `get_console` is wrapped behind a function so headless `import imessage_export.cli` still doesn't transitively import `tui.theme`).

- [ ] **Step 4.6: Commit**

```bash
git add imessage_export/tui/wizard.py imessage_export/tui/tables.py imessage_export/tui/errors.py
git commit -m "$(cat <<'EOF'
wizard/tables/errors: route through themed Rich console

Replace hardcoded cyan/green/yellow/red with semantic style names
(accent/success/warning/error/muted) backed by the dawnfox / terafox
palette resolver.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Register themes in the Textual app + expand CSS

**Files:**
- Modify: `imessage_export/tui/app/app.py`
- Modify: `tests/test_theme.py` (add the idempotent-registration test)

- [ ] **Step 5.1: Add the idempotent-registration test**

Open `tests/test_theme.py` and append before any `if __name__ == "__main__":` line:

```python


class RegisterTextualThemesTests(unittest.TestCase):
    def test_register_textual_themes_idempotent(self):
        """Re-registering must not raise — on_mount can run twice in tests."""
        from imessage_export.tui import theme

        class _FakeApp:
            def __init__(self):
                self._registered: list[str] = []

            def register_theme(self, t):
                # Mimic Textual's "already registered" error on duplicates.
                if t.name in self._registered:
                    raise ValueError(f"theme {t.name!r} already registered")
                self._registered.append(t.name)

        app = _FakeApp()
        theme.register_textual_themes(app)
        # Second call must not raise.
        theme.register_textual_themes(app)
        # Both themes registered (at least once).
        self.assertIn("dawnfox", app._registered)
        self.assertIn("terafox", app._registered)
```

- [ ] **Step 5.2: Confirm the test passes (it should already — Task 1 implemented the helper)**

```bash
python3 -m unittest tests.test_theme.RegisterTextualThemesTests -v 2>&1 | tail -5
```

Expected: PASS. If FAIL with `ImportError: textual`, install: `pip install -e '.[tui]'`.

- [ ] **Step 5.3: Open `imessage_export/tui/app/app.py` and locate `on_mount()`**

You're looking for the method definition around line 72:

```python
    def on_mount(self) -> None:
        self._defaults = load_defaults()
```

- [ ] **Step 5.4: Insert theme registration at the top of `on_mount()`**

Replace the first two lines of `on_mount` with:

```python
    def on_mount(self) -> None:
        import os
        from ..theme import register_textual_themes, resolve_theme_name

        self._defaults = load_defaults()
        register_textual_themes(self)
        self.theme = resolve_theme_name(
            cli=getattr(self, "_cli_theme", None),
            env=os.environ.get("IMESSAGE_EXPORT_THEME"),
            persisted=self._defaults.theme_override,
        )
```

The `getattr(self, "_cli_theme", None)` is a slot the app's caller (`cli.py`) populates before mounting. That wiring lands in Task 7 — for now the slot will always be absent, so `cli=None` and the env/persisted/detect path drives the theme.

- [ ] **Step 5.5: Expand `App.CSS`**

Replace the existing `CSS = """ ... """` block on `ImessageExportApp` with:

```python
    CSS = """
    Screen { background: $background; color: $foreground; }
    #main { height: 1fr; }

    Sidebar { background: $surface; border-right: solid $panel; }
    Sidebar > .selected { background: $panel; color: $primary; text-style: bold; }

    HistoryView { background: $background; color: $foreground; }
    HistoryView .day-header { color: $day-header; text-style: bold; }
    HistoryView .gap-marker { color: $muted; text-style: italic; }
    HistoryView .speaker-other { color: $primary; text-style: bold; }
    HistoryView .speaker-me    { color: $accent;  text-style: bold; }
    HistoryView .timestamp     { color: $muted; }

    StatusLine { background: $surface; color: $foreground; }
    ActionBar  { background: $panel;   color: $foreground; }
    ActionBar .key { color: $primary; text-style: bold; }
    """
```

The new tokens (`$day-header`, `$muted`) come from the Textual theme's `variables` dict that `register_textual_themes` populated.

- [ ] **Step 5.6: Smoke-test the app imports and mounts (offline check)**

```bash
python3 -c "
from imessage_export.tui.app.app import ImessageExportApp
app = ImessageExportApp()
print('ok')
"
```

Expected: `ok`. If you see `AttributeError: 'ImessageExportApp' object has no attribute 'register_theme'`, the installed Textual is too old — `pip install -U 'textual>=0.86,<1.0'`. (Task 8 bumps the pin.)

- [ ] **Step 5.7: Run the full suite**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5.8: Commit**

```bash
git add imessage_export/tui/app/app.py tests/test_theme.py
git commit -m "$(cat <<'EOF'
textual app: register dawnfox/terafox themes; expand App.CSS

Sets self.theme in on_mount via the theme resolver. Adds CSS rules so
sidebar, history view, status line, and action bar pick up theme
tokens ($background, $primary, $day-header, $muted, …).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Migrate history-view inline styles to CSS classes

The history view currently renders Rich `Text` with `style="dim"` / `"bold"` inline. To pick up theme colors we attach Textual CSS classes instead.

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`

- [ ] **Step 6.1: Read the current `HistoryView` render code**

Open `imessage_export/tui/app/widgets.py` and find the method that renders a single message — there's a `text = Text()` block around line 164:

```python
        text = Text()
        text.append(f"[{ts}] ", style="dim")
        text.append(f"{speaker}: ", style="bold")
        text.append(body)
```

There may also be code that renders the day-header / gap-marker lines. Find those by searching:

```bash
grep -nE "day|gap|speaker|timestamp" imessage_export/tui/app/widgets.py | head -30
```

- [ ] **Step 6.2: Replace inline styles with Rich markup keyed to CSS classes**

Textual maps Rich markup like `[@click=...]` and CSS classes set on widget instances. For inline `Text`, the right swap is to use Rich markup that refers to theme variables.

Replace the four-line block above with:

```python
        text = Text()
        text.append(f"[{ts}] ", style="$muted")
        is_me = m.is_from_me  # adjust to actual field on the message object
        speaker_style = "$accent bold" if is_me else "$primary bold"
        text.append(f"{speaker}: ", style=speaker_style)
        text.append(body)
```

If `m.is_from_me` is not the attribute (read your message dataclass — it might be `m.author_label == self.app.state.me_name`), use whatever distinguishes "me" from "other" in this view. The variable name `is_me` is the only thing you need.

For day-headers — if there's a renderer that writes `── Saturday, June 6, 2026 ──` as a rich Text, change its `style="bold"` (or similar) to `style="$day-header bold"`.

For gap markers — change `style="italic dim"` (or similar) to `style="$muted italic"`.

If the file's rendering path uses `Static` widgets per message (instead of one big Text), the equivalent change is to add Textual classes on the `Static`:

```python
        widget = Static(f"[{ts}] {speaker}: {body}", classes="message")
        widget.add_class("speaker-me" if is_me else "speaker-other")
```

Decide which path the file uses and apply the appropriate change.

- [ ] **Step 6.3: Smoke-test by running the app**

```bash
python3 -m imessage_export --app
```

Expected (if Full Disk Access is granted): the Textual app launches with the new colors. Quit with `q`. If FDA isn't granted you'll hit the existing FDA-denied modal — that's fine, it confirms imports work.

If you see `ColorParseError: unable to parse '$muted'` then Rich (not Textual) is rendering the text and doesn't understand Textual's `$var` syntax. In that case keep the literal hex by resolving the palette at render time:

```python
        from ..theme import resolve_palette
        pal = resolve_palette()  # cache on the widget if hot-path
        text.append(f"[{ts}] ", style=pal["muted"])
        text.append(f"{speaker}: ", style=f"bold {pal['accent' if is_me else 'accent_alt']}")
```

This is a pragmatic fallback: Rich Text inside a Textual widget doesn't always pick up `$var` substitution. If the smoke test renders correctly with `$muted`, prefer that — but the hex fallback is the safe option if not.

- [ ] **Step 6.4: Run the full suite**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`. No new tests; visual verification is manual.

- [ ] **Step 6.5: Commit**

```bash
git add imessage_export/tui/app/widgets.py
git commit -m "$(cat <<'EOF'
history view: theme-aware timestamps, speakers, day-headers, gaps

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Settings modal — Theme picker + CLI override plumbing

**Files:**
- Modify: `imessage_export/tui/app/modals.py`
- Modify: `imessage_export/tui/app/app.py`
- Modify: `imessage_export/cli.py`

- [ ] **Step 7.1: Add the Theme row to `SettingsModal`**

Open `imessage_export/tui/app/modals.py` and locate `SettingsModal` (around line 109).

Step 7.1a — extend `__init__`:

```python
    def __init__(
        self, *, contacts_path: Optional[str], output_dir: str, me_name: str,
        theme_override: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._contacts_path = contacts_path or ""
        self._output_dir = output_dir
        self._me_name = me_name
        self._theme_override = theme_override   # 'dawnfox' | 'terafox' | None
```

Step 7.1b — extend `compose()` to add the theme row. Find the existing rows and append a fourth before the button row. You'll need a `RadioSet` (Textual standard widget) with three buttons:

```python
    def compose(self) -> ComposeResult:
        from textual.widgets import RadioButton, RadioSet
        with Vertical():
            yield Static("Settings", classes="modal-title")
            with Horizontal():
                yield Label("Contacts file: ")
                yield Input(value=self._contacts_path, id="contacts-path")
            with Horizontal():
                yield Label("Output dir:    ")
                yield Input(value=self._output_dir, id="output-dir")
            with Horizontal():
                yield Label("Your label:    ")
                yield Input(value=self._me_name, id="me-name")
            with Horizontal():
                yield Label("Theme:         ")
                with RadioSet(id="theme-set"):
                    yield RadioButton("Auto (system)", id="theme-auto",
                                      value=(self._theme_override is None))
                    yield RadioButton("Dawnfox (light)", id="theme-dawnfox",
                                      value=(self._theme_override == "dawnfox"))
                    yield RadioButton("Terafox (dark)", id="theme-terafox",
                                      value=(self._theme_override == "terafox"))
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")
```

Step 7.1c — extend `on_button_pressed` to return the picked theme:

```python
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        theme_set = self.query_one("#theme-set")
        pressed = theme_set.pressed_button  # the active RadioButton
        if pressed is None or pressed.id == "theme-auto":
            picked_theme = None
        elif pressed.id == "theme-dawnfox":
            picked_theme = "dawnfox"
        else:
            picked_theme = "terafox"
        self.dismiss({
            "contacts_path": self.query_one("#contacts-path", Input).value.strip() or None,
            "output_dir":    self.query_one("#output-dir", Input).value.strip() or "./exports",
            "me_name":       self.query_one("#me-name", Input).value.strip() or "Me",
            "theme_override": picked_theme,
        })
```

- [ ] **Step 7.2: Apply the new theme in the app when the modal returns**

Open `imessage_export/tui/app/app.py` and find `action_open_settings_modal` (around line 281).

The current handler likely passes contacts/output/me to the modal and persists the dict to defaults. Update it to also pass `theme_override` and act on the returned value.

```python
    async def action_open_settings_modal(self) -> None:
        from .modals import SettingsModal
        result = await self.push_screen_wait(SettingsModal(
            contacts_path=str(self.state.contacts_path) if self.state.contacts_path else None,
            output_dir=str(self.state.output_dir),
            me_name=self.state.me_name,
            theme_override=self._defaults.theme_override,
        ))
        if result is None:
            return
        # Existing fields:
        self.state.contacts_path = Path(result["contacts_path"]) if result["contacts_path"] else None
        self.state.output_dir = Path(result["output_dir"])
        self.state.me_name = result["me_name"]
        self._defaults.contacts_path = result["contacts_path"]
        self._defaults.output_dir = result["output_dir"]
        self._defaults.me_name = result["me_name"]
        # New: theme override + live swap
        self._defaults.theme_override = result["theme_override"]
        import os
        from ..theme import resolve_theme_name
        self.theme = resolve_theme_name(
            cli=getattr(self, "_cli_theme", None),
            env=os.environ.get("IMESSAGE_EXPORT_THEME"),
            persisted=self._defaults.theme_override,
        )
        self._persist_defaults()
```

The existing implementation may differ in detail (e.g. it might call `self._load_contacts_into_state()` after saving). Preserve everything that's already there; only the three new lines marked `# New:` and the dict-key additions are required.

- [ ] **Step 7.3: Wire the CLI `--theme` flag down to the app**

Open `imessage_export/cli.py`. Find the function that launches the Textual app — search for `ImessageExportApp` or `from .tui.app.app import`:

```bash
grep -n "ImessageExportApp" imessage_export/cli.py
```

In the function that builds + runs the app, set the slot before `.run()`:

```python
    app = ImessageExportApp()
    app._cli_theme = getattr(args, "theme", None)
    app.run()
```

Use `getattr` with a `None` default so older callers (tests that build a Namespace without `theme`) keep working.

Also wire the same value into the wizard path. Find `_run_wizard` (or the function that launches the Questionary wizard):

```bash
grep -nE "def _run_wizard|run_wizard" imessage_export/cli.py
```

Before any console.print / questionary calls, set the Rich theme based on the CLI flag:

```python
    import os
    from .tui.theme import get_console, resolve_palette, make_console, _reset_console_for_tests
    # Force a re-resolve so --theme overrides whatever singletoned earlier.
    _reset_console_for_tests()
    palette = resolve_palette(
        cli=getattr(args, "theme", None),
        env=os.environ.get("IMESSAGE_EXPORT_THEME"),
        persisted=__import__("imessage_export.tui.defaults",
                             fromlist=["load"]).load().theme_override,
    )
    # Prime the singleton so wizard/tables/errors see the same console.
    import imessage_export.tui.theme as _theme_mod
    _theme_mod._console = make_console(palette)
```

(The chained `__import__` is ugly but avoids a top-level `from .tui.defaults import load` that would defeat the no-rich-on-headless invariant. Inline it once here.)

- [ ] **Step 7.4: Smoke-test**

```bash
# Verify CLI parse still works:
python3 -m imessage_export --help 2>&1 | grep -A1 '\\-\\-theme'
```

Expected: the `--theme` line in the help output.

```bash
# Verify the Textual app accepts the new field on __init__:
python3 -c "
from imessage_export.tui.app.app import ImessageExportApp
app = ImessageExportApp()
app._cli_theme = 'dawnfox'
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 7.5: Run full suite**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 7.6: Commit**

```bash
git add imessage_export/tui/app/modals.py imessage_export/tui/app/app.py imessage_export/cli.py
git commit -m "$(cat <<'EOF'
Settings modal: Theme picker + CLI --theme plumbing

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Bump textual pin in pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 8.1: Update the pin**

Open `pyproject.toml` and find the `[project.optional-dependencies]` block (or wherever `textual>=0.79,<1.0` lives — grep to confirm):

```bash
grep -n textual pyproject.toml
```

Replace `textual>=0.79,<1.0` with `textual>=0.86,<1.0`.

- [ ] **Step 8.2: Confirm the installed version satisfies the new pin**

```bash
python3 -c "import textual; v=textual.__version__; parts=tuple(int(x) for x in v.split('.')[:2]); assert parts>=(0,86), v; print('ok', v)"
```

Expected: `ok 0.89.1` (or whatever the current is).

- [ ] **Step 8.3: Confirm the editable install still resolves**

```bash
pip install -e '.[tui]' 2>&1 | tail -5
```

Expected: `Successfully installed ...` or `Requirement already satisfied`. No error about textual constraints.

- [ ] **Step 8.4: Run full suite**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 8.5: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
pyproject: bump textual>=0.79 -> >=0.86 for themes API

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: README + CLAUDE.md updates

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 9.1: Add a "Theming" section to README.md**

Open `README.md`. Pick a spot after the "Interactive TUI" section (or wherever the TUI is first introduced) and add:

```markdown
## Theming

The TUI ships with two palettes from
[EdenEast/nightfox.nvim](https://github.com/EdenEast/nightfox.nvim):

- **dawnfox** — warm light theme
- **terafox** — earthy dark theme

By default the active palette follows your macOS appearance
(`System Settings → Appearance`). To override:

```bash
# One-shot CLI flag (highest precedence)
imessage-export --theme dawnfox
imessage-export --theme terafox
imessage-export --theme auto   # ignore persisted preference for this run

# Env var (next-highest)
IMESSAGE_EXPORT_THEME=terafox imessage-export
```

Or use the Textual app's Settings modal (press `s`) to pick a theme
and persist it to `~/.config/imessage-export/recent.json`.

Auto-detect only runs on macOS. On other platforms the TUI falls back
to terafox unless one of the overrides above is set.
```

- [ ] **Step 9.2: Add a Code-conventions bullet to CLAUDE.md**

Open `CLAUDE.md`. Find the "Code conventions" section (around line 59 — has bullets about package layout, time math, writers, tests, redactor). Add a new bullet at the end of the bulleted list:

```markdown
- TUI color decisions live in `imessage_export/tui/theme.py`. Never
  hardcode color names (`red`, `cyan`, `green`, `yellow`) or hex codes
  in `wizard.py`, `tables.py`, `errors.py`, or the Textual app's CSS.
  Use semantic styles (`accent`, `success`, `warning`, `error`,
  `muted`, `day_header`) or Textual theme tokens (`$primary`,
  `$success`, `$day-header`, …).
```

- [ ] **Step 9.3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: theming section + convention bullet

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Manual verification + open PR

- [ ] **Step 10.1: Run the manual verification checklist from the spec**

If Full Disk Access is granted to this Python, walk through:

1. `imessage-export --app` in macOS Dark mode → terafox.
2. Switch macOS to Light → relaunch → dawnfox.
3. `IMESSAGE_EXPORT_THEME=dawnfox imessage-export --app` in Dark mode → dawnfox.
4. `imessage-export --app --theme terafox` in Light mode → terafox.
5. Inside the Textual app: `s` to open Settings → set Theme to Dawnfox → confirm it swaps live and `~/.config/imessage-export/recent.json` now has `"theme_override": "dawnfox"`.
6. Set back to Auto → `recent.json` shows `"theme_override": null`.
7. `imessage-export --list` on TTY — Rich table renders with new colors.
8. `imessage-export --list | cat` — plain output, zero ANSI escapes (cat shows escape codes if any). Verify with `imessage-export --list | cat -v | head` if unsure.
9. `imessage-export --wizard` — wizard panels use new palette.

If FDA isn't granted, skip the chat-touching steps; the rest (help, list, wizard up to FDA error, theme-flag parse) still verify.

- [ ] **Step 10.2: Final pass over the full test suite**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -10
```

Expected: `OK`. Test count = baseline + (Task 1 tests ~17) + (Task 2 tests 3) + (Task 3 tests 4) + (Task 5 test 1) = baseline + ~25.

- [ ] **Step 10.3: Push and open the PR**

```bash
git push -u origin tui-color-themes
gh pr create --title "TUI color themes: dawnfox (light) / terafox (dark)" --body "$(cat <<'EOF'
## Summary
- New `imessage_export/tui/theme.py` owns palette dicts + resolver + Rich Console builder + Textual theme registration.
- macOS auto-detect (`defaults read -g AppleInterfaceStyle`) with override precedence: CLI flag → env var → persisted defaults → detect → fallback (terafox).
- Rich consumers (wizard / tables / errors) swap raw color names for semantic styles (`accent`/`success`/`warning`/`error`/`muted`).
- Textual app registers both themes, sets `self.theme` in `on_mount`, expands `App.CSS` with theme tokens.
- Settings modal gains a Theme row (Auto / Dawnfox / Terafox); CLI gains `--theme {dawnfox,terafox,auto}`.
- `textual` pin bumped `>=0.79` → `>=0.86` for the themes API.

## Test plan
- [x] Unit: palette completeness, semantic-style registration, resolver precedence, `detect_appearance` mocked branches, idempotent textual registration, defaults roundtrip, CLI flag parse.
- [x] Headless purity preserved (`tests/test_no_rich_import_on_headless.py` still green).
- [ ] Manual: `imessage-export --app` in Dark + Light macOS modes.
- [ ] Manual: env / CLI / persisted overrides exercise each precedence rung.
- [ ] Manual: Settings modal live-swap + persist.
- [ ] Manual: `imessage-export --list | cat` stays escape-free.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 10.4: Clean up**

If you used the brainstorming visual companion server, stop it:

```bash
scripts/stop-server.sh /Users/benjaminloschen/Projects/imessage-history/.superpowers/brainstorm/<session>
```

(Optional — the server auto-exits after 30 minutes of inactivity anyway.)

---

## Self-Review

**Spec coverage:**
- Spec §"Palettes" — Task 1 (dict literals + completeness test).
- Spec §"Architecture" — Task 1 (theme.py).
- Spec §"Resolution order" — Task 1 (`resolve_theme_name`) + Task 5 (`on_mount`) + Task 7 (CLI plumbing).
- Spec §"Rich integration" — Task 4.
- Spec §"Textual integration" — Tasks 5 + 6.
- Spec §"Pyproject pin" — Task 8.
- Spec §"Persistence + in-app override" — Tasks 2 + 7.
- Spec §"Data flow" — Tasks 5 + 7 (both ends of the wiring).
- Spec §"Error handling" — Task 1 (`detect_appearance` try/except, env-var defensive coerce in `resolve_theme_name`, defaults defensive coerce in Task 2).
- Spec §"Testing" — Tasks 1 / 2 / 3 / 5 (per the test tables in those tasks).
- Spec §"Manual verification" — Task 10.
- Spec §"Migration" — Task 2 (back-compat test) + Task 8 (pip bump).
- Spec §"Risks" — addressed in the spec; no separate task needed.
- Spec §"File-level impact" — table at top of this plan.

All spec sections mapped.

**Placeholder scan:** no TBD / TODO / "implement later" / "appropriate error handling" / "similar to Task N" anywhere. Each step includes the actual code or command.

**Type consistency:**
- `DAWNFOX` / `TERAFOX` are `dict[str, str]` everywhere they're referenced.
- `detect_appearance() -> str` returns the literal `"light"` or `"dark"` in every code path that references it.
- `resolve_theme_name(*, cli, env, persisted) -> str` keyword signature is consistent across Tasks 1, 5, 7.
- `Defaults.theme_override: Optional[str]` matches across Tasks 2, 5, 7.
- `--theme` choices `("dawnfox", "terafox", "auto")` are identical in argparse (Task 3) and used consistently in the resolver (Task 1) and the wizard wiring (Task 7).
- `get_console()` vs `_console()` — `wizard.py` uses the local `_console()` wrapper for the late-binding pattern; `tables.py` and `errors.py` use `get_console()` directly; both delegate to the module's singleton. Consistent.
- `_reset_console_for_tests` is used in Task 1 (test fixture) and Task 7 (CLI re-resolve). Same signature both places.

Plan complete.
