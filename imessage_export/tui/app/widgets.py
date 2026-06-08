"""Sidebar (chat list with filter), HistoryView, StatusLine, ActionBar.

HistoryView, StatusLine, ActionBar are filled in by Tasks 6 / 7 / 13.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Button, Input, ListItem, ListView, Label, Static


def _format_time_12h(ts: str) -> str:
    """Convert "YYYY-MM-DD HH:MM:SS" (or "HH:MM:SS") to Messages-style "h:mm AM/PM"."""
    hh = ts[11:13] if len(ts) >= 19 else ts[:2]
    mm = ts[14:16] if len(ts) >= 19 else ts[3:5]
    h = int(hh)
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{mm} {period}"


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
        # Reuse the wizard's formatter but drop the `[chat_id]` prefix —
        # the id is opaque to the user and just adds visual noise in the
        # narrow sidebar column.
        from ..wizard import _format_chat_row
        return _format_chat_row(row, self._contacts, include_id=False)

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
        """Type-to-filter + arrow-key bridge between filter input and chat list.

        - When the list has focus and the user types a printable single character,
          focus the filter input and forward the character via insert_text_at_cursor.
        - When the list has focus at the top row and Up is pressed, focus the
          filter input. Lets the user reach the filter from the list using the
          same arrow keys they're already using to scroll the list — no Tab,
          no mouse, no separate keybinding to remember.
        - When the filter has focus and Down is pressed, focus the list. Mirrors
          the list→filter Up bridge so the pair feels symmetric.
        - When the filter has focus and Esc is pressed, clear the filter and refocus
          the list. Esc events from widgets outside the sidebar pass through (the
          `focused is filter_input` guard ensures we don't swallow them).
        - Other arrow-key presses (Down in list, Up in filter, Up/Down mid-list)
          fall through to the focused widget's default behavior.
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

        if focused is list_view and event.key == "up":
            # Only bridge when the highlight is at the top (or the list is
            # empty after a filter narrowed it to nothing). Otherwise let
            # ListView's own cursor_up handle the press.
            if list_view.index is None or list_view.index == 0:
                filter_input.focus()
                event.prevent_default()
                event.stop()
                return

        if focused is filter_input and event.key == "down":
            list_view.focus()
            event.prevent_default()
            event.stop()
            return

        if focused is filter_input and event.key == "escape":
            filter_input.value = ""
            list_view.focus()
            event.prevent_default()
            event.stop()
            return


class ChatHeader(Static):
    """One-line summary of the currently rendered chat.

    Sits directly above the HistoryView and gets refreshed every time the
    user picks a different chat (or when contacts load late and resolve
    a previously-bare handle into a real name). Deliberately discreet —
    muted color, single line, no controls — because the meaningful
    interaction surface is the history below it.
    """

    DEFAULT_CSS = """
    ChatHeader {
        height: 1;
        padding: 0 2;
        color: $foreground;
        background: $panel;
    }
    """

    def show_empty(self) -> None:
        self.update("")

    def update_from_chat(self, chat_row: dict, contacts: dict) -> None:
        # Reuse the wizard's handle→name resolver so the header reads as
        # the same person the sidebar row points at, minus the "last X"
        # timestamp (which is redundant once messages are rendered).
        from ..wizard import _resolve_names
        raw_who = (
            chat_row.get("display_name")
            or chat_row.get("participants")
            or chat_row.get("chat_identifier")
            or "(unknown)"
        )
        who = _resolve_names(raw_who, contacts) if contacts else raw_who
        kind = chat_row.get("style", "")
        msgs = chat_row.get("msg_count")
        last = chat_row.get("last_message_local")

        # Title in the active accent so the user can spot the header at a
        # glance; metadata in dim so it stays in the background. The
        # accent hex comes from the theme palette (Rich can't parse
        # Textual's `$accent` markup in style strings — same constraint
        # HistoryView._format_row works around).
        from ..theme import PALETTES, DAWNFOX
        try:
            pal = PALETTES[self.app.theme]
        except (KeyError, AttributeError):
            pal = DAWNFOX
        accent_hex = pal.get("accent") or pal.get("accent_alt") or ""

        text = Text()
        title_style = f"bold {accent_hex}" if accent_hex else "bold"
        text.append(who, style=title_style)
        if kind:
            text.append("  ·  ", style="dim")
            text.append(kind, style="dim")
        if isinstance(msgs, int):
            text.append("  ·  ", style="dim")
            text.append(f"{msgs:,} messages", style="dim")
        if last:
            text.append("  ·  ", style="dim")
            text.append(f"last {last}", style="dim")
        self.update(text)


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
    HistoryView > .load-more-affordance {
        color: $accent;
        background: $accent 12%;
        text-style: bold;
        padding: 1 2;
        margin: 0 0 1 0;
        text-align: center;
    }
    HistoryView > .load-more-affordance:hover {
        background: $accent 25%;
    }
    HistoryView > .beginning-marker {
        color: $accent;
        background: $accent 8%;
        text-style: italic;
        padding: 1 2;
        margin: 0 0 1 0;
        text-align: center;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._placeholder_visible = True
        # Progressive-load state: keep the full message list in memory but
        # only render the last `_shown_count` to keep Textual's Strip cache
        # small. The "o" binding (or clicking the affordance) reveals the
        # next chunk.
        self._all_messages: list = []
        self._shown_count: int = 0
        # The topmost mounted CHUNK widget (not the affordance). Each
        # successful action_load_older advances this pointer to the freshly
        # mounted older chunk so the NEXT load mounts before the new
        # topmost (keeping chunks in chronological order).
        self._topmost_widget: Static | None = None
        # Persistent clickable affordance pinned above every chunk. Lives
        # for as long as there are unshown older messages; removed once
        # _shown_count catches up to len(_all_messages).
        self._load_more_widget: Static | None = None
        # Informational marker that replaces the affordance once every
        # older message has been loaded — gives the user a clear "you've
        # reached the start" signal instead of nothing.
        self._beginning_widget: Static | None = None

    def show_placeholder(self, text: str = "Pick a chat from the left.") -> None:
        self.remove_children()
        # Reset progressive-load state. The old topmost chunk + affordance
        # are being pruned by remove_children() — keep no dangling
        # references to them, and don't let `_shown_count` from a prior
        # chat survive (otherwise a fresh render_messages would compute
        # its hidden_count from the wrong base).
        self._all_messages = []
        self._shown_count = 0
        self._topmost_widget = None
        self._load_more_widget = None
        self._beginning_widget = None
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
    LOAD_MORE_CHUNK = 2000    # how many older messages to add per "o" press

    def render_messages(self, messages: list) -> None:
        """Open the chat at its tail. Older messages stay hidden until the
        user explicitly asks for them with the "o" binding.

        Mounts the most-recent chunk as a Static and records it as the
        topmost mounted chunk. Each subsequent action_load_older mounts
        the next older chunk above the current topmost.

        Why explicit instead of auto-load on scroll-up: the auto-load
        version (PRs #24/#25 + three rounds of follow-up fixes) raced
        against Textual's layout in ways that produced MountError crashes,
        wrong chronological order on multi-load, and a runaway-load loop
        that left the user stuck at scroll_y=0 with every chunk mounted.
        Explicit triggering eliminates the whole class of races by removing
        the implicit trigger; there's no watcher to re-enter, no anchor
        callback to race against `virtual_size`, no stale-state path.

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

        blob = self._build_blob(visible)
        # Use classes (not id) — remove_children() is async, so a rapid
        # chat-switch can still have the previous "recent-chunk" in the
        # node tree when we mount the next one. Classes coexist; ids don't.
        self._topmost_widget = Static(blob, classes="history-blob recent-chunk")
        self.mount(self._topmost_widget)
        # Mount whichever top indicator matches state: clickable affordance
        # if there are still older messages to fetch, otherwise a "reached
        # the start" marker so the top of the scroll is never empty (the
        # user always gets feedback).
        self._refresh_top_indicator(hidden)
        self.call_after_refresh(self.scroll_end, animate=False)

    def _refresh_top_indicator(self, remaining: int) -> None:
        """Reconcile the two top widgets with the current load state.

        - remaining > 0: clickable "Load X older messages" affordance above
          the topmost chunk; "beginning" marker is removed.
        - remaining == 0: italic "Beginning of conversation" marker above
          the topmost chunk; affordance is removed.

        Each widget is a single long-lived Static updated in place when
        already mounted (avoids mount/remove churn on every load).
        """
        topmost = self._topmost_widget
        if remaining > 0:
            if self._beginning_widget is not None and self._beginning_widget.parent is self:
                self._beginning_widget.remove()
            self._beginning_widget = None
            if self._load_more_widget is not None and self._load_more_widget.parent is self:
                self._load_more_widget.update(self._load_more_text(remaining))
            else:
                self._load_more_widget = Static(
                    self._load_more_text(remaining),
                    classes="history-blob load-more-affordance",
                )
                if topmost is not None and topmost.parent is self:
                    self.mount(self._load_more_widget, before=topmost)
                else:
                    self.mount(self._load_more_widget)
        else:
            if self._load_more_widget is not None and self._load_more_widget.parent is self:
                self._load_more_widget.remove()
            self._load_more_widget = None
            total = len(self._all_messages)
            if total == 0:
                if self._beginning_widget is not None and self._beginning_widget.parent is self:
                    self._beginning_widget.remove()
                self._beginning_widget = None
                return
            label = self._beginning_text(total)
            if self._beginning_widget is not None and self._beginning_widget.parent is self:
                self._beginning_widget.update(label)
            else:
                self._beginning_widget = Static(
                    label,
                    classes="history-blob beginning-marker",
                )
                if topmost is not None and topmost.parent is self:
                    self.mount(self._beginning_widget, before=topmost)
                else:
                    self.mount(self._beginning_widget)

    def _beginning_text(self, total: int) -> Text:
        text = Text()
        text.append("── ", style="dim")
        text.append(f"Beginning of conversation  •  {total:,} total messages", style="italic")
        text.append(" ──", style="dim")
        return text

    def _load_more_text(self, remaining: int) -> Text:
        """Affordance label: chunk size + remaining count + how to trigger."""
        chunk = min(self.LOAD_MORE_CHUNK, remaining)
        text = Text()
        text.append("⬆  ", style="bold")
        text.append(f"Load {chunk:,} older messages", style="bold")
        text.append(f"  •  {remaining:,} remaining  •  ", style="dim")
        text.append("click here or press [o]", style="italic")
        text.append("  ⬆", style="bold")
        return text

    def _build_blob(self, visible: list) -> Text:
        """Render a chunk of messages as a single Rich Text blob.

        Every message line is tagged with `meta={"msg_id": m.message_id}`
        on its style spans. Textual surfaces that meta dict via
        `event.style.meta` when the user clicks the line, which is how
        click-to-mark-a-range survives the single-Static blob model
        (we don't have per-row widgets to attach `data_msg_id` to).
        """
        blob = Text()
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
            ts_str = _format_time_12h(ts)
            speaker = m.author_label or ""
            body = (m.text or "").replace("\n", "\n          ")
            # Meta-tag every span on this line with the message id. A click
            # anywhere along the line will report this id back via
            # `event.style.meta["msg_id"]` so on_click can mark it as a
            # range endpoint without needing per-row widgets.
            line_meta = Style(meta={"msg_id": m.message_id})
            blob.append(f"[{ts_str}] ", style=line_meta + Style.parse("dim"))
            blob.append(f"{speaker}: ", style=line_meta + Style.parse("bold"))
            blob.append(body, style=line_meta)
            blob.append("\n")
        return blob

    # Lines of newly-loaded chunk to peek into the viewport after a load so
    # the user can see fresh content appeared above without losing their
    # reading position.
    LOAD_PEEK_LINES = 3

    def action_load_older(self) -> None:
        """Reveal the next chunk of older messages above the topmost one.

        Bound to "o" via BINDINGS, and also triggered when the user clicks
        the "Load older messages" affordance pinned at the top of the
        scroll. Preserves the user's relative scroll position across the
        load (so they keep reading where they were), then nudges the
        viewport up by `LOAD_PEEK_LINES` lines so a sliver of the freshly
        mounted chunk peeks into view — visible confirmation that new
        content was added.
        """
        if not self._all_messages:
            return
        if self._shown_count >= len(self._all_messages):
            return

        prev_shown = self._shown_count
        new_shown = min(prev_shown + self.LOAD_MORE_CHUNK, len(self._all_messages))
        # The chunk of older messages this load reveals: everything between
        # what was shown before and what's shown now.
        if prev_shown > 0:
            older_slice = self._all_messages[-new_shown:-prev_shown]
        else:
            older_slice = self._all_messages[-new_shown:]
        older_blob = self._build_blob(older_slice)
        self._shown_count = new_shown
        remaining_hidden = len(self._all_messages) - new_shown

        # Capture the user's current viewing reference BEFORE the mount so
        # we can restore their position after layout settles. The previous
        # topmost chunk's virtual_region.y is our anchor: whatever shifts
        # above it (a new older chunk added, the affordance possibly
        # removed) is reflected as a delta in its virtual_region.y after
        # the layout pass — and that's exactly the offset we need to apply
        # to scroll_y to keep the same content under the user's cursor.
        prev_top = self._topmost_widget
        old_scroll_y = self.scroll_y
        try:
            old_top_y = prev_top.virtual_region.y if prev_top is not None else 0
        except Exception:
            old_top_y = 0

        # Mount the new older chunk ABOVE whatever's currently topmost so
        # chunks stay in chronological order: load N is older than load N-1,
        # so it has to go above load N-1's widget — not above the recent
        # chunk (which would interleave them wrong on load 2+).
        # `parent is self`, not `is_mounted`: Textual's `_is_mounted` is
        # sticky-True after first mount even when the widget has since been
        # detached (e.g. by a prior `remove_children()`). `parent is self`
        # is the truthful "still a child of mine" check, and it also
        # returns True synchronously after `self.mount(...)` queues, so it
        # works during the mid-mount window too.
        older_widget = Static(older_blob, classes="history-blob older")
        if prev_top is not None and prev_top.parent is self:
            self.mount(older_widget, before=prev_top)
        else:
            self.mount(older_widget)
        self._topmost_widget = older_widget

        # Reconcile the top indicator: still hidden → keep/update the
        # clickable affordance; nothing left → swap in the "beginning of
        # conversation" marker so the user sees a clear endpoint.
        self._refresh_top_indicator(remaining_hidden)

        # Defer the scroll adjustment until layout settles so
        # prev_top.virtual_region.y reflects the post-mount position.
        def _preserve_position_with_peek() -> None:
            if prev_top is None or prev_top.parent is not self:
                return
            try:
                new_top_y = prev_top.virtual_region.y
                delta = new_top_y - old_top_y
                target_y = max(0, old_scroll_y + delta - self.LOAD_PEEK_LINES)
                self.scroll_to(y=target_y, animate=False)
            except Exception:
                pass

        self.call_after_refresh(_preserve_position_with_peek)

    def _format_row(self, m) -> Text:
        ts = _format_time_12h(m.timestamp)
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
        ("o", "load_older", "Load 2,000 older messages"),
    ]

    def on_click(self, event) -> None:
        target = event.widget
        if target is self._load_more_widget:
            self.action_load_older()
            event.stop()
            return
        # Range-mark routing on the single-Static blob model: every
        # message line is rendered with `meta={"msg_id": ...}` on its
        # Rich spans, and Textual surfaces that under the click position
        # as `event.style.meta`. If we got a msg_id back, treat the
        # click as a "mark this message as a range endpoint" request.
        style = getattr(event, "style", None)
        if style is not None:
            meta = getattr(style, "meta", None) or {}
            msg_id = meta.get("msg_id")
            if msg_id is not None:
                self.post_message(self.RangeMarkRequested(int(msg_id)))
                event.stop()
                return
        # Fallback for any per-row widgets that still carry data_msg_id
        # (kept for forward-compat — currently unused after the blob
        # rewrite, but cheap to leave).
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

        Defensive: if either marked id is missing from `messages` (e.g. the
        user switched to a chat where the previous marks don't apply, and
        the app-level cleanup didn't catch it), bail out silently rather
        than crash on `list.index(x)`. The visual update is a no-op
        anyway because the per-row widgets the old design targeted no
        longer exist under the single-Static blob model — Phase 3 of
        the redesign will rebuild this with per-line styling.
        """
        if start_id is None and end_id is None:
            for row in self.query(".message-row"):
                row.remove_class("is-in-range")
                row.remove_class("is-selected-endpoint")
            return

        ids_in_order = [m["message_id"] for m in messages]
        if start_id is not None and start_id not in ids_in_order:
            return
        if end_id is not None and end_id not in ids_in_order:
            return
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
        # Partial-selection hint: when the user has clicked one message
        # but not the second yet, `resolved_window` falls through to
        # "everything" — which would otherwise render as "everything
        # (from selection)" and read like a contradiction. Show the
        # start anchor + the next action the user should take.
        if (state.window_source == "selection"
                and state.range_start_msg_id
                and not state.range_end_msg_id):
            msg_by_id = {m["message_id"]: m for m in state.selected_chat_messages}
            anchor = msg_by_id.get(state.range_start_msg_id)
            if anchor is not None:
                ts = anchor["timestamp"]
                window_str = (
                    f"start: {ts[:10]} {ts[11:16]} — click another message to set end"
                )
            else:
                window_str = "1 mark set — click another message to set end"
            source = "from selection"
        else:
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
        height: 4;
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
