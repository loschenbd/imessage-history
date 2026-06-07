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

from .state import filter_messages_by_query


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

    def on_key(self, event) -> None:
        """Type-to-filter: redirect printable keystrokes from the list to the filter.

        - When the list has focus and the user types a printable single character,
          focus the filter input and forward the character via insert_text_at_cursor.
        - When the filter has focus and Esc is pressed, clear the filter and refocus
          the list. Esc events from widgets outside the sidebar pass through (the
          `focused is filter_input` guard ensures we don't swallow them).
        - Arrow keys are non-printable (`event.character is None`), so they always
          flow through to whatever widget currently has focus.
        """
        list_view = self.query_one("#sidebar-list", ListView)
        filter_input = self.query_one("#sidebar-filter", Input)
        focused = self.app.focused

        if (
            focused is list_view
            and event.character
            and len(event.character) == 1
            and event.character.isprintable()
        ):
            filter_input.focus()
            filter_input.insert_text_at_cursor(event.character)
            event.prevent_default()
            event.stop()
            return

        if focused is filter_input and event.key == "escape":
            filter_input.value = ""
            list_view.focus()
            event.prevent_default()
            event.stop()
            return


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
    HistoryView > .message-row:focus {
        background: $accent 50%;
    }
    HistoryView > #history-placeholder {
        padding: 2 0;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._placeholder_visible = True
        self._all_messages: list = []

    def show_placeholder(self, text: str = "Pick a chat from the left.") -> None:
        # Textual's remove_children() is async — by the time mount() runs the
        # old placeholder is still in the children list, which would trip
        # DuplicateIds. If one already exists, update its text in place; in
        # either case strip stale rows/headers/search-bar so the placeholder
        # is what the user sees.
        existing = self.query("#history-placeholder")
        self.remove_children(".message-row, .day-header, #history-search")
        if existing:
            existing.first(Static).update(text)
        else:
            self.mount(Static(text, id="history-placeholder"))
        self._placeholder_visible = True

    def show_loading(self) -> None:
        self.show_placeholder("Loading…")

    def render_messages(self, messages: list) -> None:
        """Render `messages` (list[Message]) into the pane.

        Updates the cache, then routes to `_render_rows` which sweeps stale
        rows/headers/search-bar via a scoped (synchronous-enough) selector.
        Avoid `self.remove_children()` (full, async) here — it lets a stale
        placeholder linger past the next `show_placeholder` mount and trips
        DuplicateIds when the next chat is empty too.
        """
        self._all_messages = list(messages)
        self._render_rows(messages)

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
        ("home", "jump_home", "First message"),
        ("end", "jump_end", "Last message"),
        ("pageup", "jump_pageup", "Page up"),
        ("pagedown", "jump_pagedown", "Page down"),
        ("slash", "open_search", "Search"),
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

    def action_jump_home(self) -> None:
        rows = list(self.query(".message-row"))
        if rows:
            rows[0].focus()

    def action_jump_end(self) -> None:
        rows = list(self.query(".message-row"))
        if rows:
            rows[-1].focus()

    def _page_step(self) -> int:
        # Leave one line of overlap top + bottom for orientation (vim-style).
        return max(1, self.size.height - 2)

    def action_jump_pageup(self) -> None:
        rows = list(self.query(".message-row"))
        if not rows:
            return
        try:
            idx = rows.index(self.app.focused)
        except ValueError:
            rows[0].focus()
            return
        rows[max(0, idx - self._page_step())].focus()

    def action_jump_pagedown(self) -> None:
        rows = list(self.query(".message-row"))
        if not rows:
            return
        try:
            idx = rows.index(self.app.focused)
        except ValueError:
            rows[-1].focus()
            return
        rows[min(len(rows) - 1, idx + self._page_step())].focus()

    def action_open_search(self) -> None:
        self.open_search()

    def open_search(self) -> None:
        """Mount the search input at the top of the pane and focus it."""
        if self.query("#history-search"):
            self.query_one("#history-search", Input).focus()
            return
        search = Input(placeholder="Search this chat… (Esc to close)", id="history-search")
        # Mount BEFORE the first existing child so the search bar sits at the top.
        self.mount(search, before=0 if self.children else None)
        search.focus()

    def apply_search(self, query: str) -> None:
        """Re-render the pane filtered to messages matching `query`."""
        self.app.state.history_search_query = query or None
        self._render_rows(filter_messages_by_query(self._all_messages, query))

    def close_search(self) -> None:
        """Remove the search input and restore the unfiltered view."""
        for s in self.query("#history-search"):
            s.remove()
        self.app.state.history_search_query = None
        self._render_rows(self._all_messages)

    def _render_rows(self, messages: list) -> None:
        """Re-render only the row/header children, preserving #history-search.

        Caller-managed: does NOT touch `self._all_messages`. Use `render_messages`
        when you want a full reset that also drops the search bar and refreshes
        the cache.
        """
        if not messages:
            placeholder = "No matches." if self.app.state.history_search_query else "No messages in this chat."
            self.show_placeholder(placeholder)
            return
        self.remove_children(".message-row, .day-header, #history-placeholder")
        self._placeholder_visible = False
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
            row.can_focus = True
            row.data_msg_id = m.message_id  # type: ignore[attr-defined]
            self.mount(row)

    def on_input_changed(self, event) -> None:
        """As the user types in the search box, re-filter incrementally."""
        if event.input.id == "history-search":
            self.apply_search(event.value)

    def on_input_submitted(self, event) -> None:
        """Enter inside the search box focuses the first matching row."""
        if event.input.id == "history-search":
            for row in self.query(".message-row"):
                row.focus()
                return

    def on_key(self, event) -> None:
        focused = self.app.focused
        if event.key == "escape" and focused is not None and getattr(focused, "id", None) == "history-search":
            self.close_search()
            event.prevent_default()
            event.stop()

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

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._focus_region: str = "sidebar"

    def set_focus_region(self, region: str) -> None:
        """Set the focus chip tag and refresh the rendered line."""
        self._focus_region = region
        try:
            self.update_from_state(self.app.state)  # type: ignore[attr-defined]
        except Exception:
            pass

    def update_from_state(self, state) -> None:
        chip = f"[{self._focus_region}]"
        if state.last_export_status:
            self.update(f"{chip}  {state.last_export_status}")
            return
        from .state import resolved_window, _format_window
        w = resolved_window(state)
        window_str = _format_window(w)
        source = {
            "selection": "from selection",
            "typed":     "from Window modal",
            "all":       "everything",
        }[state.window_source]
        contacts_str = f"contacts: {state.contacts_path.name}" if state.contacts_path else "contacts: none"
        redact_str = "redact: on" if state.redact else "redact: off"
        self.update(
            f"{chip}  window: {window_str} ({source}) · output: {state.output_dir} · {contacts_str} · {redact_str}"
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
