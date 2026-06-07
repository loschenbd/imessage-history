"""Sidebar (chat list with filter), HistoryView, StatusLine, ActionBar.

HistoryView, StatusLine, ActionBar are filled in by Tasks 6 / 7 / 13.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Label, Static


class Sidebar(Vertical):
    """Filter input + scrollable chat list."""

    DEFAULT_CSS = """
    Sidebar {
        width: 32;
        border-right: solid $accent;
    }
    Sidebar > #sidebar-filter {
        margin: 0 1;
    }
    Sidebar > #sidebar-list {
        height: 1fr;
    }
    """

    class ChatSelected(TextualMessage):
        """Emitted when the user picks a chat (Enter or click)."""
        def __init__(self, chat_id: int) -> None:
            super().__init__()
            self.chat_id = chat_id

    def __init__(self, chats: list[dict], contacts: dict, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._all_chats = list(chats)
        self._contacts = contacts

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter…", id="sidebar-filter")
        yield ListView(id="sidebar-list")

    def on_mount(self) -> None:
        self._refresh_list("")

    def _format_row(self, row: dict) -> str:
        # Reuse the same display approach the wizard uses.
        from ..wizard import _format_chat_row
        return _format_chat_row(row, self._contacts)

    def _refresh_list(self, query: str) -> None:
        list_view = self.query_one("#sidebar-list", ListView)
        list_view.clear()
        q = query.strip().lower()
        for row in self._all_chats:
            label = self._format_row(row)
            if q and q not in label.lower():
                continue
            item = ListItem(Label(label))
            item.data = row.get("chat_id") if isinstance(row, dict) else row["chat_id"]  # type: ignore[attr-defined]
            list_view.append(item)
        # Highlight the first item so arrow keys feel right immediately.
        list_view.index = 0 if list_view.children else None

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sidebar-filter":
            self._refresh_list(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        chat_id = getattr(event.item, "data", None)
        if chat_id is not None:
            self.post_message(self.ChatSelected(chat_id))

    def select_chat_id(self, chat_id: int) -> None:
        """Highlight the row whose chat_id matches (used for first-run pre-select)."""
        list_view = self.query_one("#sidebar-list", ListView)
        for idx, item in enumerate(list_view.children):
            if getattr(item, "data", None) == chat_id:
                list_view.index = idx
                self.post_message(self.ChatSelected(chat_id))
                break


class HistoryView(VerticalScroll):
    """Scrollable rendered chat history.

    Renders messages with the same day-header convention used by the
    Markdown writer: `── Saturday, June 6, 2026 ──` before the first
    message of each calendar day. Speaker headers are bold.
    """

    DEFAULT_CSS = """
    HistoryView {
        padding: 0 2;
    }
    HistoryView > .day-header {
        color: $accent;
        text-style: bold;
        padding: 1 0 0 0;
    }
    HistoryView > .message-row {
        padding: 0;
    }
    HistoryView > .message-row.is-selected-endpoint {
        background: $accent 30%;
    }
    HistoryView > .message-row.is-in-range {
        background: $accent 15%;
    }
    HistoryView > #history-placeholder {
        padding: 2 0;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._placeholder_visible = True

    def show_placeholder(self, text: str = "Pick a chat from the left.") -> None:
        self.remove_children()
        ph = Static(text, id="history-placeholder")
        self.mount(ph)
        self._placeholder_visible = True

    def show_loading(self) -> None:
        self.show_placeholder("Loading…")

    def render_messages(self, messages: list) -> None:
        """Render `messages` (list[Message]) into the pane."""
        self.remove_children()
        self._placeholder_visible = False
        if not messages:
            self.show_placeholder("No messages in this chat.")
            return

        last_date = None
        for m in messages:
            ts = m.timestamp  # "YYYY-MM-DD HH:MM:SS"
            day = ts[:10]
            if day != last_date:
                dt = datetime.strptime(day, "%Y-%m-%d")
                header = f"── {dt.strftime('%A, %B %-d, %Y')} ──"
                self.mount(Static(header, classes="day-header"))
                last_date = day
            row = Static(self._format_row(m), classes="message-row")
            row.data_msg_id = m.message_id  # type: ignore[attr-defined]
            self.mount(row)

    def _format_row(self, m) -> Text:
        ts = m.timestamp[11:19]  # HH:MM:SS
        speaker = m.author_label or ""
        body = (m.text or "").replace("\n", "\n          ")
        text = Text()
        text.append(f"[{ts}] ", style="dim")
        text.append(f"{speaker}: ", style="bold")
        text.append(body)
        return text

    # ------------------------------------------------------------------
    # Task 7: Range marks
    # ------------------------------------------------------------------

    class RangeMarkRequested(TextualMessage):
        """User clicked or Enter'd a message row — mark it as a range endpoint."""
        def __init__(self, msg_id: int) -> None:
            super().__init__()
            self.msg_id = msg_id

    BINDINGS = [
        ("enter", "mark_row", "Mark range endpoint"),
        ("space", "mark_row", "Mark range endpoint"),
        ("escape", "clear_marks", "Clear marks"),
    ]

    def on_click(self, event) -> None:
        target = event.widget
        msg_id = getattr(target, "data_msg_id", None)
        if msg_id is not None:
            self.post_message(self.RangeMarkRequested(msg_id))

    def action_mark_row(self) -> None:
        focused = self.app.focused
        msg_id = getattr(focused, "data_msg_id", None)
        if msg_id is not None:
            self.post_message(self.RangeMarkRequested(msg_id))

    def action_clear_marks(self) -> None:
        self.post_message(self.RangeMarkRequested(msg_id=-1))  # sentinel: clear

    def apply_marks(self, start_id: int | None, end_id: int | None, messages: list[dict]) -> None:
        """Repaint range highlight CSS classes based on current marks.

        `messages` is the same `selected_chat_messages` list (each {msg_id, timestamp})
        so the in-range span is contiguous in render order.
        """
        if start_id is None and end_id is None:
            for row in self.query(".message-row"):
                row.remove_class("is-in-range")
                row.remove_class("is-selected-endpoint")
            return

        ids_in_order = [m["message_id"] for m in messages]
        endpoints = {start_id, end_id} - {None}
        if start_id and end_id:
            lo, hi = sorted([ids_in_order.index(start_id), ids_in_order.index(end_id)])
            in_range_ids = set(ids_in_order[lo:hi+1])
        else:
            in_range_ids = endpoints

        for row in self.query(".message-row"):
            msg_id = getattr(row, "data_msg_id", None)
            row.set_class(msg_id in endpoints, "is-selected-endpoint")
            row.set_class(msg_id in in_range_ids and msg_id not in endpoints, "is-in-range")


# ---------------------------------------------------------------------------
# Task 13: StatusLine + ActionBar
# ---------------------------------------------------------------------------

from textual.widgets import Button as TextualButton


class StatusLine(Static):
    """One-line summary of resolved state."""

    DEFAULT_CSS = """
    StatusLine {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def update_from_state(self, state) -> None:
        if state.last_export_status:
            self.update(state.last_export_status)
            return
        from .state import resolved_window
        w = resolved_window(state)
        if w["mode"] == "day":
            bits = [w["date"]]
            if w.get("start_time") or w.get("end_time"):
                bits.append(f"{w.get('start_time') or '00:00'}–{w.get('end_time') or '23:59'}")
            window_str = " ".join(bits)
        elif w["mode"] == "range":
            window_str = f"{w['from_date']}..{w['to_date']}"
        else:
            window_str = "everything"

        source = {
            "selection": "from selection",
            "typed":     "from Window modal",
            "all":       "everything",
        }[state.window_source]
        contacts_str = f"contacts: {state.contacts_path.name}" if state.contacts_path else "contacts: none"
        redact_str = "redact: on" if state.redact else "redact: off"
        self.update(
            f"window: {window_str} ({source}) · output: {state.output_dir} · {contacts_str} · {redact_str}"
        )


class ActionBar(Horizontal):
    """Row of visible buttons. Each button's first letter is the accelerator."""

    DEFAULT_CSS = """
    ActionBar {
        height: 3;
        padding: 0 1;
        border-top: solid $accent;
    }
    ActionBar > Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield TextualButton("[u]W[/u]indow…",   id="btn-window")
        yield TextualButton("[u]S[/u]ettings…", id="btn-settings")
        yield TextualButton("[u]R[/u]edact…",   id="btn-redact")
        yield TextualButton("[u]E[/u]xport",    id="btn-export", variant="primary")
        yield TextualButton("Wi[u]z[/u]ard",    id="btn-wizard")
        yield TextualButton("[u]H[/u]elp",      id="btn-help")
        yield TextualButton("[u]Q[/u]uit",      id="btn-quit")
