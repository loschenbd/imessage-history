"""Modal screens for the Textual app.

Each modal is a Textual ModalScreen subclass that dismisses with a result
dict (or None if cancelled).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

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

    def __init__(self, *, contacts_path: Optional[str], output_dir: str, me_name: str) -> None:
        super().__init__()
        self._contacts_path = contacts_path or ""
        self._output_dir = output_dir
        self._me_name = me_name

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
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.dismiss({
            "contacts_path": self.query_one("#contacts-path", Input).value.strip() or None,
            "output_dir":    self.query_one("#output-dir", Input).value.strip() or "./exports",
            "me_name":       self.query_one("#me-name", Input).value.strip() or "Me",
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
