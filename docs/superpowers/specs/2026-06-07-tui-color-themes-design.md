# TUI color themes (dawnfox / terafox) — design

**Date:** 2026-06-07
**Scope:** Add a unified color theme system to the Rich wizard and the
Textual app using the dawnfox (light) and terafox (dark) palettes from
EdenEast/nightfox.nvim. Auto-detect appearance from macOS; allow
override via env var, CLI flag, defaults file, and a Textual settings
modal.

## Goals

1. Both interactive surfaces — the Rich-based wizard and the Textual
   app — render in a coherent, deliberately chosen palette instead of
   the current mix of generic Rich color names (`cyan`/`green`/`yellow`/
   `red`) and unstyled Textual chrome.
2. Light/dark mirrors the user's macOS appearance with zero
   configuration on first run.
3. The override path is explicit and predictable: env var, CLI flag,
   persisted preference, system, fallback — first hit wins.
4. Adding a new colored element is a one-line semantic mapping
   (`border_style="success"`), never a hex-code lookup at the call site.

## Non-goals

- No support for additional palettes beyond dawnfox/terafox. Adding one
  later is a small change but out of scope here.
- No live re-detect when the user toggles macOS appearance mid-session.
  Restart the app, same as iTerm/Ghostty themselves.
- No theme support outside the TUI. CSV/JSON/TXT/MD/AI-ready writers
  emit plain text and stay theme-unaware.
- No first-class Linux/Windows appearance detection. Those platforms
  fall back to terafox unless overridden — they already lack a stable
  `defaults read`-style API, and adding `COLORFGBG` parsing was rejected
  during brainstorm as too fragile.
- No theme step added to the Rich wizard. The wizard is the secondary
  surface now; first-run heaviness isn't worth it. Users still get the
  env var / CLI flag and inherit what the Textual app persisted.

## Palettes

Canonical values lifted from `EdenEast/nightfox.nvim` source
(`lua/nightfox/palette/{dawnfox,terafox}.lua`). Both palettes provide
the same set of semantic role keys so consumers never branch on which
palette is active.

### Dawnfox (light, warm)

| Role          | Hex      | Source                |
|---------------|----------|-----------------------|
| `bg`          | `#faf4ed`| palette.bg1           |
| `bg_alt`      | `#f2e9e1`| palette.bg2           |
| `bg_3`        | `#e1d9cd`| palette.bg3           |
| `fg`          | `#575279`| palette.fg1           |
| `accent`      | `#286983`| palette.blue.base     |
| `accent_alt`  | `#38836a`| palette.cyan.base     |
| `success`     | `#6e8e3d`| palette.green.base    |
| `warning`     | `#ad8000`| palette.yellow.base   |
| `error`       | `#c2453a`| palette.red.base      |
| `day_header`  | `#907aa9`| palette.magenta.base  |
| `muted`       | `#a59689`| palette.comment       |
| `border_soft` | `#e1d9cd`| palette.bg3           |

### Terafox (dark, earthy teal)

| Role          | Hex      | Source                |
|---------------|----------|-----------------------|
| `bg`          | `#152528`| palette.bg1           |
| `bg_alt`      | `#1d3337`| palette.bg2           |
| `bg_3`        | `#254147`| palette.bg3           |
| `fg`          | `#e6eaea`| palette.fg1           |
| `accent`      | `#5a93aa`| palette.blue.base     |
| `accent_alt`  | `#a1cdd8`| palette.cyan.base     |
| `success`     | `#7aa4a1`| palette.green.base    |
| `warning`     | `#fda47f`| palette.yellow.base   |
| `error`       | `#e85c51`| palette.red.base      |
| `day_header`  | `#ad5c7c`| palette.magenta.base  |
| `muted`       | `#6b7e83`| palette.comment       |
| `border_soft` | `#254147`| palette.bg3           |

## Architecture

One new module owns every color decision. Consumers never reference
hex codes or raw color names.

```
imessage_export/tui/theme.py     ← new
  DAWNFOX: dict[str, str]
  TERAFOX: dict[str, str]
  PALETTES = {"dawnfox": DAWNFOX, "terafox": TERAFOX}

  def detect_appearance() -> str          # 'light' | 'dark'
  def resolve_theme_name(*, cli=None, env=None, persisted=None) -> str
  def resolve_palette() -> dict[str, str]
  def make_console(palette) -> rich.console.Console
  def register_textual_themes(app) -> None
```

### Resolution order

`resolve_theme_name()` walks these inputs first-hit-wins:

1. **CLI flag** — `--theme dawnfox|terafox` parsed by `build_parser()`,
   passed in by `cli.main()`. One-shot; never written to defaults.
2. **Env var** — `IMESSAGE_EXPORT_THEME=dawnfox|terafox|auto`. `auto`
   means "skip me, fall through."
3. **Defaults file** — `Defaults.theme_override`. Set by the Textual
   Settings modal. Same `auto` semantics.
4. **macOS auto-detect** — `subprocess.run(["defaults", "read", "-g",
   "AppleInterfaceStyle"], capture_output=True)`. Exit 0 + stdout
   `"Dark\n"` → terafox. Anything else → dawnfox.
5. **Fallback** — terafox. Matches the "TUIs ship dark" convention.

`detect_appearance()` wraps the shell-out in `try/except
(FileNotFoundError, subprocess.SubprocessError)` so non-macOS hosts
silently fall through to the fallback.

### Rich integration (wizard / tables / errors)

`make_console(palette)` returns a `Console` initialized with a Rich
`Theme` mapping the semantic role names to the palette's hexes:

```python
Theme({
    "accent":     palette["accent"],
    "accent_alt": palette["accent_alt"],
    "success":    palette["success"],
    "warning":    palette["warning"],
    "error":      palette["error"],
    "muted":      palette["muted"],
    "day_header": palette["day_header"],
})
```

A single module-level `_console: Console | None = None` is initialized
on first call to `get_console()`. The three Rich-using modules
(`wizard.py`, `tables.py`, `errors.py`) drop their `console = Console()`
top-level statements and call `get_console()` inside their functions.

Concrete rewrites in those three files:

| Today                                  | After                                            |
|----------------------------------------|--------------------------------------------------|
| `border_style="cyan"` (welcome panel)  | `border_style="accent"`                          |
| `border_style="green"` (confirm)       | `border_style="success"`                         |
| `border_style="yellow"` (info)         | `border_style="warning"`                         |
| `border_style="red"` (errors)          | `border_style="error"`                           |
| `[red]ERROR:[/red]`                    | `[error]ERROR:[/error]`                          |
| `[yellow]Note:[/yellow]`               | `[warning]Note:[/warning]`                       |
| `[bold green]✓[/bold green]`           | `[bold success]✓[/bold success]`                 |
| `style="bold cyan"` (Last column)      | `style="bold accent"`                            |
| `style="dim"` (table secondary cols)   | `style="muted"`                                  |

Inline `[dim]` modifiers stay — Rich's `dim` is a render attribute, not
a color, and still useful for de-emphasis on top of the themed `fg`.

### Textual integration

`register_textual_themes(app)` calls `app.register_theme(...)` once for
each palette using Textual 0.86+'s `textual.theme.Theme`. Mapping:

| Textual theme field | Palette role  |
|---------------------|---------------|
| `primary`           | `accent`      |
| `secondary`         | `accent_alt`  |
| `accent`            | `accent_alt`  |
| `success`           | `success`     |
| `warning`           | `warning`     |
| `error`             | `error`       |
| `foreground`        | `fg`          |
| `background`        | `bg`          |
| `surface`           | `bg_alt`      |
| `panel`             | `bg_3`        |
| `dark`              | `True` for terafox, `False` for dawnfox |
| `variables`         | `{"day-header": day_header, "muted": muted, "border-soft": border_soft}` |

`ImessageExportApp.on_mount` calls `register_textual_themes(self)`
before any other UI setup, then sets `self.theme = resolve_theme_name()`.

The currently empty-ish `App.CSS` grows to reference the auto-bound
theme tokens. Approximate target:

```css
Screen { background: $background; color: $foreground; }
Sidebar { background: $surface; border-right: solid $panel; }
Sidebar > .selected { background: $panel; color: $primary; text-style: bold; }
HistoryView { background: $background; }
HistoryView .day-header { color: $day-header; text-style: bold; }
HistoryView .gap-marker { color: $muted; text-style: italic; }
HistoryView .speaker-other { color: $primary; text-style: bold; }
HistoryView .speaker-me    { color: $accent;  text-style: bold; }
HistoryView .timestamp     { color: $muted; }
StatusLine { background: $surface; color: $foreground; }
ActionBar  { background: $panel;   color: $foreground; }
ActionBar .key { color: $primary; text-style: bold; }
ErrorModal { border: thick $error; }
```

`widgets.py`'s history renderer (currently builds Rich `Text` with
inline `style="dim"`/`"bold"`) is reworked to attach CSS classes
(`.timestamp`, `.speaker-me`, `.speaker-other`, `.day-header`,
`.gap-marker`) on the rendered elements so the colors come from the
active theme, not from hardcoded inline styles.

### Pyproject pin

`textual>=0.79,<1.0` → `textual>=0.86,<1.0`. Themes API arrived in
Textual 0.86; the local install is already 0.89.1, but the lower bound
needs to reflect the API requirement.

## Persistence + in-app override

`Defaults` gains one optional field:

```python
@dataclass
class Defaults:
    # existing fields ...
    theme_override: Optional[str] = None   # 'dawnfox' | 'terafox' | None
```

`load()` ignores unknown string values (`theme_override="catppuccin"`
silently becomes `None`) — same defensive pattern already used for the
version field. `save()` round-trips the new field. Schema version stays
at `1`: optional fields with a `None` default are backward compatible,
older recent.json files load cleanly.

The Textual app's existing Settings modal adds one row labeled
**Theme** with three radio options: *Auto (system)*, *Dawnfox (light)*,
*Terafox (dark)*. Selecting one writes `theme_override` to the
defaults file and immediately swaps `self.theme` on the running app.
Selecting "Auto" writes `null` (the JSON spelling of `None`); the
existing roundtrip code already treats `null` the same as the field
being absent.

CLI: `--theme {dawnfox,terafox,auto}` added to `build_parser()` in the
"Output" / general flags group. `auto` is one-shot: it means "ignore
any persisted override for this run," not "clear the persisted
override."

## Data flow

```
cli.main()
  ├─ parse args
  ├─ if --list / --list-contacts / wizard / app:
  │     palette = resolve_palette(cli=args.theme,
  │                               env=os.environ.get("IMESSAGE_EXPORT_THEME"),
  │                               persisted=Defaults.load().theme_override)
  │     console = make_console(palette)   # singletoned inside theme.py
  │
  └─ Textual app path additionally:
        on_mount:
          register_textual_themes(self)
          self.theme = resolve_theme_name(cli=..., env=..., persisted=...)
```

`resolve_palette()` is the high-level helper for Rich consumers (gives
back a dict). `resolve_theme_name()` returns the name string for
Textual. Both delegate to the same private precedence walker.

## Error handling

- **`defaults` binary missing** (Linux/Windows CI) — caught at
  `detect_appearance()`; falls through to the fallback theme.
- **`subprocess` timeout** — wrap call in `timeout=2.0`. On timeout,
  fall through. (`defaults read` returns in <100ms in practice; a
  2-second budget is a backstop for stuck CI environments.)
- **Bad value in env var** — `IMESSAGE_EXPORT_THEME=nope` is treated
  the same as `auto`. No noisy stderr; the value is a preference, not
  a typo worth shouting about.
- **Bad value in CLI flag** — argparse `choices=["dawnfox", "terafox",
  "auto"]` rejects it at parse time with the standard usage message.
- **Bad value in defaults file** — `load()` already coerces unknown
  string fields to `None`; nothing extra to do.
- **Textual theme registration race** — both themes must be registered
  before `self.theme = ...` is set, otherwise Textual raises. Order
  enforced by the single `register_textual_themes()` helper which
  registers both before returning.

## Testing

### `tests/test_theme.py` (new)

| Test                                                | What it asserts                                                     |
|-----------------------------------------------------|---------------------------------------------------------------------|
| `test_both_palettes_have_all_semantic_keys`         | DAWNFOX and TERAFOX have identical key sets                         |
| `test_make_console_registers_named_styles`          | Console.get_style("accent") returns the dawnfox blue                |
| `test_resolve_theme_name_cli_wins`                  | cli="terafox", env="dawnfox", persisted="dawnfox" → terafox          |
| `test_resolve_theme_name_env_beats_persisted`       | cli=None, env="terafox", persisted="dawnfox" → terafox              |
| `test_resolve_theme_name_persisted_beats_detect`    | cli=None, env=None, persisted="terafox", detect→light → terafox    |
| `test_resolve_theme_name_auto_falls_through`        | env="auto", persisted="terafox" → terafox                          |
| `test_resolve_theme_name_unknown_env_treated_as_auto` | env="catppuccin", persisted=None, detect→light → dawnfox          |
| `test_detect_appearance_dark`                       | mock subprocess returns "Dark\n", returncode=0 → "dark"            |
| `test_detect_appearance_light`                      | mock subprocess returncode=1 → "light"                              |
| `test_detect_appearance_missing_binary`             | mock raises FileNotFoundError → "light"                             |
| `test_detect_appearance_timeout`                    | mock raises subprocess.TimeoutExpired → "light"                     |
| `test_register_textual_themes_idempotent`           | calling twice doesn't raise (app guards already registered names)   |

### `tests/test_tui_defaults.py` (extend)

| Test                                                | What it asserts                                                     |
|-----------------------------------------------------|---------------------------------------------------------------------|
| `test_theme_override_roundtrip`                     | save(Defaults(theme_override="dawnfox")) → load() returns same     |
| `test_old_file_without_theme_override_loads_cleanly`| file written before this change loads with theme_override=None     |
| `test_bad_theme_override_value_coerced_to_none`     | file with theme_override="xyz" → load() returns None for that field |

### `tests/test_cli_tty_detect.py` (extend)

| Test                                                | What it asserts                                                     |
|-----------------------------------------------------|---------------------------------------------------------------------|
| `test_theme_flag_parsed`                            | `--theme terafox` puts "terafox" on args.theme                      |
| `test_theme_flag_rejects_unknown`                   | `--theme foo` raises SystemExit (argparse)                          |

### Not tested (deliberate YAGNI)

- Textual rendering snapshots — terminal width + Unicode width + color
  quantization make these brittle.
- Rich rendering byte-comparisons — same reason.
- macOS auto-detect actually shelling out — mocked in unit tests; no
  need for real-system detection in CI.
- Settings-modal interaction in the Textual app — covered by manual
  verification.

## Manual verification

Run through before merging the implementation PR:

1. `imessage-export` on a TTY with macOS in Dark mode → terafox.
2. Toggle macOS to Light → relaunch → dawnfox.
3. `IMESSAGE_EXPORT_THEME=dawnfox imessage-export` in Dark mode → dawnfox.
4. `imessage-export --theme terafox` in Light mode → terafox.
5. In the Textual app, open Settings → set Theme to Dawnfox → confirm
   the app swaps immediately and `~/.config/imessage-export/recent.json`
   gets `"theme_override": "dawnfox"`.
6. Set Theme back to Auto → recent.json shows `"theme_override": null`.
7. `imessage-export --list` on a TTY — Rich table renders with themed
   header / muted columns.
8. `imessage-export --list | cat` — piped output stays plain (no
   escapes), proves the theme only activates on TTY.
9. Linux CI / GitHub Actions runs the test suite green (auto-detect
   shells out OK because subprocess is mocked).

## Migration

- `~/.config/imessage-export/recent.json` files written by previous
  versions load unchanged; `theme_override` defaults to `None` =
  auto-detect. No data migration step needed.
- The argparse surface gains one optional flag; existing scripts that
  call `imessage-export` headlessly are unaffected.
- The `[tui]` extra still installs `rich + questionary + textual`.
  Bumping textual's lower bound to `>=0.86` may force a `pip install
  --upgrade` for users on an old pin, but the install is opt-in
  already.

## Risks

- **Color contrast on the muted role.** Dawnfox `muted` (`#a59689`) on
  `bg` (`#faf4ed`) measures around WCAG-AA-pass for body text but
  borderline for very small UI labels. Mitigation: muted is only used
  for de-emphasis (timestamps, gap markers, table secondary columns) —
  primary content uses `fg`.
- **Textual theme API churn.** Themes are still flagged as "new" in
  the Textual changelog. Mitigation: pinning `<1.0` keeps us in the
  API-stable-with-warnings range; the lower bound (`>=0.86`) is the
  minimum that actually has the API.
- **`defaults read` shell-out cost.** Adds ~30–80ms to startup on
  macOS. Acceptable: it runs once per session, the session is
  long-lived, and the user already pays the import-time cost of Rich.

## File-level impact

| Path                              | Change                                                                          |
|-----------------------------------|----------------------------------------------------------------------------------|
| `imessage_export/tui/theme.py`    | New file; palettes, resolver, `make_console`, `register_textual_themes`.        |
| `imessage_export/tui/wizard.py`   | Drop top-level `Console()`; use `get_console()`; rewrite hardcoded color names. |
| `imessage_export/tui/tables.py`   | Same: themed console + semantic style names.                                    |
| `imessage_export/tui/errors.py`   | Same: themed console + semantic style names.                                    |
| `imessage_export/tui/defaults.py` | Add `theme_override` field; defensive coercion on load.                         |
| `imessage_export/tui/app/app.py`  | Register themes in `on_mount`; set `self.theme`; populate `App.CSS`.            |
| `imessage_export/tui/app/widgets.py` | Replace inline `style="bold"` etc. on history elements with CSS classes.       |
| `imessage_export/tui/app/modals.py` | Add Theme row to Settings modal; wire to `theme_override`.                     |
| `imessage_export/cli.py`          | Add `--theme` flag; thread CLI/env/persisted into `resolve_theme_name`.         |
| `pyproject.toml`                  | Bump `textual>=0.79,<1.0` → `textual>=0.86,<1.0`.                              |
| `tests/test_theme.py`             | New: 12 unit tests per the table above.                                         |
| `tests/test_tui_defaults.py`      | Extend: 3 new tests for theme_override roundtrip + back-compat.                 |
| `tests/test_cli_tty_detect.py`    | Extend: 2 new tests for `--theme` flag parsing.                                 |
| `README.md`                       | New short section: "Theming" — how the default works, how to override.          |
| `CLAUDE.md`                       | One bullet under "Code conventions": never hardcode color names in `tui/`.      |

## Out of scope (future)

- Additional palettes (`nightfox`, `dayfox`, `nordfox`, `carbonfox`,
  `duskfox`). Mechanically trivial once the resolver is in; just more
  palette dicts.
- Per-element overrides ("make the day header `accent_alt` instead of
  `day_header`"). Would need a richer user-config schema.
- Linux `gsettings get org.gnome.desktop.interface color-scheme` auto-
  detect. Possible; not asked for.
- Auto-rerender when macOS appearance changes mid-session. Textual
  could subscribe to a watcher, but it's a niche flow.
