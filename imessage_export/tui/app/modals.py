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
