"""Modal screens for the Textual app.

Each modal is a Textual ModalScreen subclass that dismisses with a result
dict (or None if cancelled).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RadioButton, RadioSet, Static


class WindowModal(ModalScreen[Optional[dict]]):
    """Pick a typed export window. Dismisses with the window dict or None."""

    DEFAULT_CSS = """
    WindowModal {
        align: center middle;
    }
    WindowModal > Vertical {
        width: 60;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    WindowModal Horizontal {
        height: 3;
    }
    WindowModal Input {
        width: 16;
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Set window", classes="modal-title")
            yield RadioSet(
                RadioButton("Single day", value=False, id="mode-day"),
                RadioButton("Date range", value=True, id="mode-range"),
                RadioButton("Everything", value=False, id="mode-all"),
                id="mode-set",
            )
            with Horizontal():
                yield Label("From:")
                yield Input(value="", placeholder="YYYY-MM-DD", id="from-date")
                yield Label("To:")
                yield Input(value=date.today().isoformat(), placeholder="YYYY-MM-DD", id="to-date")
            with Horizontal():
                yield Label("Start:")
                yield Input(value="", placeholder="9am", id="start-time")
                yield Label("End:")
                yield Input(value="", placeholder="5pm", id="end-time")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        # Save: read mode + fields, build the wizard-shape dict.
        mode_set = self.query_one(RadioSet)
        pressed = mode_set.pressed_button
        mode_id = pressed.id if pressed else "mode-range"

        from ...window import parse_time_12h

        def _t(field_id: str) -> Optional[str]:
            v = self.query_one(f"#{field_id}", Input).value.strip()
            if not v:
                return None
            try:
                return parse_time_12h(v)
            except ValueError:
                return None  # silently drop bad times; better UX than refusing to save

        if mode_id == "mode-day":
            d = self.query_one("#to-date", Input).value.strip() or date.today().isoformat()
            self.dismiss({
                "mode": "day",
                "date": d,
                "start_time": _t("start-time"),
                "end_time":   _t("end-time"),
            })
        elif mode_id == "mode-range":
            self.dismiss({
                "mode": "range",
                "from_date": self.query_one("#from-date", Input).value.strip(),
                "to_date":   self.query_one("#to-date", Input).value.strip(),
                "start_time": _t("start-time"),
                "end_time":   _t("end-time"),
            })
        else:
            self.dismiss({"mode": "all"})


class SettingsModal(ModalScreen[Optional[dict]]):
    """Edit persistent contacts/output/me. Dismisses with a dict or None."""

    DEFAULT_CSS = """
    SettingsModal {
        align: center middle;
    }
    SettingsModal > Vertical {
        width: 70;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    def __init__(
        self, *, contacts_path: Optional[str], output_dir: str, me_name: str,
        theme_override: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._contacts_path = contacts_path or ""
        self._output_dir = output_dir
        self._me_name = me_name
        self._theme_override = theme_override   # 'dawnfox' | 'terafox' | None

    def compose(self) -> ComposeResult:
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

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        theme_set = self.query_one("#theme-set", RadioSet)
        pressed = theme_set.pressed_button
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


class RedactModal(ModalScreen[Optional[dict]]):
    """Configure redaction for the next export. Dismisses with the choices dict or None."""

    DEFAULT_CSS = """
    RedactModal {
        align: center middle;
    }
    RedactModal > Vertical {
        width: 60;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    def __init__(self, *, current: dict) -> None:
        super().__init__()
        self._current = current or {}

    def compose(self) -> ComposeResult:
        c = self._current
        with Vertical():
            yield Static("Redact", classes="modal-title")
            yield RadioSet(
                RadioButton("Off", value=not (c.get("redact") or c.get("redact_only")), id="r-off"),
                RadioButton("Keep both versions", value=bool(c.get("redact")), id="r-both"),
                RadioButton("Redacted only", value=bool(c.get("redact_only")), id="r-only"),
                id="redact-mode",
            )
            with Horizontal():
                yield Label("Extra names file:")
                yield Input(value=c.get("redact_names_file") or "", id="names-file")
            yield Static("Scrub from message bodies:")
            yield Checkbox("Phones", value=not c.get("no_redact_phones", False), id="cb-phones")
            yield Checkbox("Emails", value=not c.get("no_redact_emails", False), id="cb-emails")
            yield Checkbox("URLs",   value=not c.get("no_redact_urls",   False), id="cb-urls")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return

        mode_pressed = self.query_one("#redact-mode", RadioSet).pressed_button
        mode_id = mode_pressed.id if mode_pressed else "r-off"

        if mode_id == "r-off":
            self.dismiss({})
            return

        names = self.query_one("#names-file", Input).value.strip() or None
        self.dismiss({
            "redact":      mode_id == "r-both",
            "redact_only": mode_id == "r-only",
            "redact_names_file": names,
            "no_redact_phones": not self.query_one("#cb-phones", Checkbox).value,
            "no_redact_emails": not self.query_one("#cb-emails", Checkbox).value,
            "no_redact_urls":   not self.query_one("#cb-urls",   Checkbox).value,
        })


class ExportConfirmModal(ModalScreen[bool]):
    """Confirm the user wants to run the export with the resolved settings."""

    DEFAULT_CSS = """
    ExportConfirmModal {
        align: center middle;
    }
    ExportConfirmModal > Vertical {
        width: 70;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_no", "Cancel"),
        ("y", "dismiss_yes", "Yes"),
        ("n", "dismiss_no", "No"),
    ]

    def __init__(self, *, summary_lines: list[str]) -> None:
        super().__init__()
        self._lines = summary_lines

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Export?", classes="modal-title")
            for line in self._lines:
                yield Static(line)
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Run", id="run", variant="primary")

    def action_dismiss_yes(self) -> None: self.dismiss(True)
    def action_dismiss_no(self)  -> None: self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "run")


class ContactsScanModal(ModalScreen[Optional[Path]]):
    """First-run scan offer. Dismisses with the written CSV path or None."""

    DEFAULT_CSS = """
    ContactsScanModal {
        align: center middle;
    }
    ContactsScanModal > Vertical {
        width: 64;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss_none", "Skip")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Set up contacts", classes="modal-title")
            yield Static(
                "No contacts file found. You can populate one in seconds\n"
                "by scanning macOS Contacts. First scan triggers a one-time\n"
                "Contacts permission prompt."
            )
            with Horizontal():
                yield Button("Skip", id="skip")
                yield Button("Scan now", id="scan", variant="primary")

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "skip":
            self.dismiss(None)
            return
        target = Path.cwd() / "contacts.csv"
        self._run_scan_worker(target)

    @work(thread=True)
    def _run_scan_worker(self, target: Path) -> None:
        from ...contacts_macos import fetch_contacts, write_csv
        try:
            rows = fetch_contacts()
        except RuntimeError as exc:
            self.app.call_from_thread(
                self.app.notify, f"Could not read Contacts: {exc}", severity="warning"
            )
            self.app.call_from_thread(self.dismiss, None)
            return
        if not rows:
            self.app.call_from_thread(
                self.app.notify, "No contacts found in Contacts.app.", severity="warning"
            )
            self.app.call_from_thread(self.dismiss, None)
            return
        write_csv(rows, target)
        self.app.call_from_thread(self.dismiss, target)


class HelpModal(ModalScreen[None]):
    """Display the binding cheatsheet."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > Vertical {
        width: 76;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Help", classes="modal-title")
            yield Static(
                "Navigation\n"
                "  Tab / Shift+Tab       Cycle focus: Sidebar → History → Actions\n"
                "  ← →                    Jump between sidebar and history\n"
                "  ↑ ↓                    Move within the focused region\n"
                "  Home / End            Jump to top / bottom\n"
                "  PageUp / PageDown     Page within the focused region\n"
                "  Esc                   Context-aware: clear filter / clear marks / close search\n"
                "\n"
                "Sidebar\n"
                "  (type letters)        Filter the chat list\n"
                "  ↑ at top row          Jump up into the filter\n"
                "  ↓ in filter           Jump down into the chat list\n"
                "  Enter                 Open the highlighted chat\n"
                "\n"
                "History\n"
                "  ↑ ↓                    Move keyboard cursor (▸) one message up / down\n"
                "  Space / Enter         Mark cursor row as range endpoint (1st = start, 2nd = end)\n"
                "  click a message       Same as Space, but with the mouse\n"
                "  click after both set  Move nearest endpoint to the click (extend / shrink)\n"
                "  o / click banner      Load 2,000 older messages\n"
                "  /                     Search within this chat\n"
                "  Esc                   Clear marks / clear search\n"
                "\n"
                "Window strip (above the chat)\n"
                "  From / To             Date inputs (YYYY-MM-DD)\n"
                "  Times                 Start / end time (e.g. 9am, 5pm)\n"
                "  7d / 30d / Month / Year   Quick relative-window presets\n"
                "  Apply                 Filter preview to the typed window\n"
                "  Clear                 Drop the filter, restore full chat\n"
                "\n"
                "Actions (work globally except while typing in an input)\n"
                "  W  Window…   S  Settings…   R  Redact…   E  Export\n"
                "  Z  Wizard    H/?  Help       Q  Quit\n"
            )
            yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, event) -> None:
        self.dismiss(None)


class ErrorModal(ModalScreen[None]):
    """Generic error modal — used for FDA denial, malformed contacts, no chats, exceptions."""

    DEFAULT_CSS = """
    ErrorModal {
        align: center middle;
    }
    ErrorModal > Vertical {
        width: 76;
        padding: 1 2;
        border: thick $error;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close")]

    def __init__(self, *, title: str, body: str, quit_on_close: bool = False) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._quit_on_close = quit_on_close

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="modal-title")
            yield Static(self._body)
            yield Button("Quit" if self._quit_on_close else "OK", id="ok", variant="primary")

    def on_button_pressed(self, event) -> None:
        if self._quit_on_close:
            self.app.exit(2)
        else:
            self.dismiss(None)

    def action_dismiss(self) -> None:
        if self._quit_on_close:
            self.app.exit(2)
        else:
            self.dismiss(None)
