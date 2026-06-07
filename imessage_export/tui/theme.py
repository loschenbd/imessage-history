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
    or any subprocess error.
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


_console: Optional[Console] = None


def get_console() -> Console:
    """Return the shared Rich Console, initialized on first call."""
    global _console
    if _console is None:
        _console = make_console(resolve_palette())
    return _console


_stderr_console: Optional[Console] = None


def get_stderr_console() -> Console:
    """Return the shared stderr-bound Rich Console, initialized on first call.

    Used by error-panel renderers in `errors.py` so error UX lands on fd 2
    and stays out of pipe-grep'd stdout.
    """
    global _stderr_console
    if _stderr_console is None:
        _stderr_console = make_console(resolve_palette())
        _stderr_console.file = __import__("sys").stderr
    return _stderr_console


def _reset_console_for_tests() -> None:
    """Test-only hook to clear the singletons between tests."""
    global _console, _stderr_console
    _console = None
    _stderr_console = None


def register_textual_themes(app) -> None:
    """Register dawnfox and terafox with a Textual App.

    Idempotent: re-registering a name is a no-op.
    """
    from textual.theme import Theme as TextualTheme

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
            pass
