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
        # Suppress Highlighted-driven auto-load until the app is past
        # its on_mount() bootstrap. Otherwise the initial _refresh_list
        # (which sets index=0) races with the explicit
        # select_chat_id(last_chat_id) and triggers two show_loading()
        # calls back-to-back, which collide on the placeholder widget id.
        self._suppress_highlight_load = True

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

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Auto-load the highlighted chat. Without this, arrow-key nav just
        # moved the highlight without ever firing ChatSelected, so the
        # preview pane sat on "Loading…" (or the previous chat) until the
        # user remembered to press Enter.
        if self._suppress_highlight_load:
            return
        item = event.item
        if item is None:
            return
        chat_id = getattr(item, "data", None)
        if chat_id is not None:
            self.post_message(self.ChatSelected(chat_id))

    def enable_highlight_autoload(self) -> None:
        """Called by the app after on_mount finishes wiring up the initial
        chat selection. Subsequent arrow-key highlights will auto-load."""
        self._suppress_highlight_load = False

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
    HistoryView > .history-placeholder {
        padding: 2 0;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._placeholder_visible = True
        # Progressive-load state: keep the full message list in memory but
        # only render the last `_shown_count` to keep Textual's Strip
        # cache small. Scroll-to-top reveals the next chunk.
        self._all_messages: list = []
        self._shown_count: int = 0
        self._loading_older: bool = False
        self._recent_widget: Static | None = None

    def show_placeholder(self, text: str = "Pick a chat from the left.") -> None:
        self.remove_children()
        # Use a class instead of an id so that calling show_placeholder
        # twice in quick succession (remove_children is async; the prior
        # widget may still be in the node tree) doesn't collide on a
        # duplicate id.
        ph = Static(text, classes="history-placeholder")
        self.mount(ph)
        self._placeholder_visible = True

    def show_loading(self) -> None:
        self.show_placeholder("Loading…")

    PREVIEW_CAP = 2000        # initial window size (most-recent messages)
    LOAD_MORE_CHUNK = 2000    # how many older messages to add per scroll-up
    LOAD_MORE_THRESHOLD = 3   # trigger when scroll_y <= this many lines

    def render_messages(self, messages: list) -> None:
        """Open the chat at its tail and lazy-load older messages on scroll-up.

        Mounts the most-recent chunk as a Static with id "recent-chunk".
        Older chunks are mounted ahead of it as separate Statics on each
        scroll-up. After each load, scroll_to_widget(recent-chunk, top=True)
        anchors the viewport at the same content the user was reading —
        Textual handles the height math (no race against virtual_size).

        Trade-off (carried from #22): no per-row widgets, so the
        click-a-row-to-mark-range feature doesn't work — use the Window
        modal for date-range selection.
        """
        self._all_messages = list(messages)
        self.remove_children()
        self._placeholder_visible = False
        if not self._all_messages:
            self.show_placeholder("No messages in this chat.")
            return

        self._shown_count = min(self.PREVIEW_CAP, len(self._all_messages))
        visible = self._all_messages[-self._shown_count:]
        hidden = len(self._all_messages) - self._shown_count

        blob = self._build_blob(visible, hidden_count=hidden)
        # Use classes (not id) — remove_children() is async, so a rapid
        # chat-switch can still have the previous "recent-chunk" in the
        # node tree when we mount the next one. Classes coexist; ids don't.
        self._recent_widget = Static(blob, classes="history-blob recent-chunk")
        self.mount(self._recent_widget)
        self.call_after_refresh(self.scroll_end, animate=False)

    def _build_blob(self, visible: list, *, hidden_count: int = 0) -> Text:
        blob = Text()
        if hidden_count:
            blob.append(
                f"── {hidden_count:,} older messages — scroll up to load more ──\n\n",
                style="dim italic",
            )

        last_date = None
        for m in visible:
            ts = m.timestamp  # "YYYY-MM-DD HH:MM:SS"
            day = ts[:10]
            if day != last_date:
                dt = datetime.strptime(day, "%Y-%m-%d")
                if last_date is not None:
                    blob.append("\n")
                blob.append(
                    f"── {dt.strftime('%A, %B %-d, %Y')} ──\n",
                    style="bold cyan",
                )
                last_date = day
            ts_str = ts[11:19]
            speaker = m.author_label or ""
            body = (m.text or "").replace("\n", "\n          ")
            blob.append(f"[{ts_str}] ", style="dim")
            blob.append(f"{speaker}: ", style="bold")
            blob.append(body)
            blob.append("\n")
        return blob

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        # Reactive watcher Textual calls when scroll position changes.
        # Load the next older chunk when the user scrolls to the top, but
        # only while we have more cached and aren't already loading.
        if self._loading_older:
            return
        if not self._all_messages:
            return
        if self._shown_count >= len(self._all_messages):
            return
        if new_value > self.LOAD_MORE_THRESHOLD:
            return
        self._load_more_older()

    def _load_more_older(self) -> None:
        self._loading_older = True
        prev_shown = self._shown_count
        new_shown = min(prev_shown + self.LOAD_MORE_CHUNK, len(self._all_messages))
        # The chunk of older messages this load reveals: everything between
        # what was shown before and what's shown now.
        if prev_shown > 0:
            older_slice = self._all_messages[-new_shown:-prev_shown]
        else:
            older_slice = self._all_messages[-new_shown:]
        remaining_hidden = len(self._all_messages) - new_shown
        older_blob = self._build_blob(older_slice, hidden_count=remaining_hidden)
        self._shown_count = new_shown

        recent = self._recent_widget
        older_widget = Static(older_blob, classes="history-blob older")
        if recent is not None and recent.is_mounted:
            self.mount(older_widget, before=recent)
        else:
            self.mount(older_widget)

        def _anchor_to_recent() -> None:
            # Let Textual compute heights — no virtual_size race.
            if recent is not None and recent.is_mounted:
                try:
                    self.scroll_to_widget(recent, top=True, animate=False)
                except Exception:
                    pass
            self._loading_older = False

        self.call_after_refresh(_anchor_to_recent)

    def _format_row(self, m) -> Text:
        ts = m.timestamp[11:19]  # HH:MM:SS
        speaker = m.author_label or ""
        body = (m.text or "").replace("\n", "\n          ")
        # Resolve theme palette to literal hex codes at render time. Rich's
        # style parser doesn't understand Textual's `$var` markup and
        # silently drops unknown style names — so we can't put `$muted` /
        # `$primary` directly in the style strings. Pull hex from the
        # active palette instead. The day-header / range-highlight Static
        # widgets still get their colors from App.CSS (theme variables),
        # because Textual interpolates `$var` at CSS parse time.
        from ..theme import PALETTES, DAWNFOX  # cheap; cached by Python import system
        # Fallback is static (no subprocess) so rendering never blocks on
        # `defaults read` even if theme registration regressed.
        try:
            pal = PALETTES[self.app.theme]
        except (KeyError, AttributeError):
            pal = DAWNFOX  # safe static fallback; never shells out
        is_me = bool(m.is_from_me)
        # `$primary` is bound to `accent` and `$accent` is bound to
        # `accent_alt` (see register_textual_themes), so use the same
        # mapping here for "me" vs "other".
        speaker_color = pal["accent_alt"] if is_me else pal["accent"]
        text = Text()
        text.append(f"[{ts}] ", style=pal["muted"])
        text.append(f"{speaker}: ", style=f"bold {speaker_color}")
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
