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


# Per-run cache for Style.parse — called 3× per message line in
# `_build_blob`, so for a 2000-message chunk that's 6000 parses per
# repaint. Parsed Style objects are immutable and safe to share across
# every blob and every chunk; the cache trims that to ~5 parses total
# (one per distinct style string we use).
_STYLE_CACHE: dict[str, Style] = {}


def _parse_style(spec: str) -> Style:
    """Return a memoized `Style.parse(spec)` (empty Style for empty spec)."""
    cached = _STYLE_CACHE.get(spec)
    if cached is None:
        cached = Style.parse(spec) if spec else Style()
        _STYLE_CACHE[spec] = cached
    return cached


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
        # narrow sidebar column. `terse_names=True` also drops the
        # "(+1234567890)" suffix when the contact resolved to a name, so
        # the column reads as "Mom · message · 412 msgs · last …" rather
        # than "Mom (+13087089787) · …". _refresh_list searches against
        # the raw handles separately, so filter-by-number still works.
        from ..wizard import _format_chat_row
        return _format_chat_row(
            row, self._contacts, include_id=False, terse_names=True,
        )

    def _refresh_list(self, query: str) -> None:
        list_view = self.query_one("#sidebar-list", ListView)
        list_view.clear()
        q = query.strip().lower()
        for row in self._all_chats:
            label = self._format_row(row)
            if q:
                # Search against the display label AND the raw participants
                # / chat_identifier. The display dropped handles for matched
                # contacts; without this widening, typing a phone number
                # would hide its (named) chat — a silent regression.
                haystack = " ".join((
                    label,
                    str(row.get("participants") or ""),
                    str(row.get("chat_identifier") or ""),
                )).lower()
                if q not in haystack:
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

        if focused is list_view and event.key == "right":
            # Right arrow on the chat list jumps into the history pane.
            # Pairs with HistoryView's Left handler so the two panes feel
            # like a single bidirectional spatial nav.
            try:
                history = self.app.query_one(HistoryView)
            except Exception:
                return
            history.focus()
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
        # `terse_when_resolved=True` drops the "(+1234567890)" suffix when
        # a contact name resolved — the header reads as "Mom" instead of
        # "Mom (+13087089787)". When no contact matched, the bare handle
        # still shows.
        who = (
            _resolve_names(raw_who, contacts, terse_when_resolved=True)
            if contacts else raw_who
        )
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


class WindowStrip(Horizontal):
    """Inline date/time picker + relative-window presets pinned above
    HistoryView.

    On Apply / Clear / preset-button press it posts `WindowChanged`
    with either a window dict (mode="range" + date/time fields) or
    None (meaning "clear the filter, show everything"). The app
    handler updates `state.typed_window` and re-renders the preview
    filtered to the new window via `HistoryView.filter_messages`.

    The presets (7d / 30d / Month / Year) just populate the From/To
    fields and post the same dict shape — so the user can tweak the
    times after picking a preset, or save out a custom range.
    """

    DEFAULT_CSS = """
    WindowStrip {
        height: 3;
        padding: 0 1;
        border-bottom: solid $accent;
    }
    WindowStrip Label { margin: 1 0 0 1; }
    WindowStrip Input { width: 12; margin: 0 1; }
    WindowStrip Button { margin: 0 0; min-width: 8; }
    """

    class WindowChanged(TextualMessage):
        def __init__(self, window: Optional[dict]) -> None:
            super().__init__()
            self.window = window

    def compose(self) -> ComposeResult:
        yield Label("From")
        yield Input(placeholder="YYYY-MM-DD", id="ws-from")
        yield Label("To")
        yield Input(placeholder="YYYY-MM-DD", id="ws-to")
        yield Label("Times")
        yield Input(placeholder="9am", id="ws-start")
        yield Input(placeholder="5pm", id="ws-end")
        yield Button("Apply", id="ws-apply", variant="primary")
        yield Button("Clear", id="ws-clear")
        yield Button("7d", id="ws-preset-7d")
        yield Button("30d", id="ws-preset-30d")
        yield Button("Month", id="ws-preset-month")
        yield Button("Year", id="ws-preset-year")

    def _val(self, fid: str) -> str:
        return self.query_one(f"#{fid}", Input).value.strip()

    def _set_dates(self, fd: str, td: str) -> None:
        self.query_one("#ws-from", Input).value = fd
        self.query_one("#ws-to", Input).value = td

    def _parse_time(self, fid: str) -> Optional[str]:
        from ...window import parse_time_12h
        v = self._val(fid)
        if not v:
            return None
        try:
            return parse_time_12h(v)
        except ValueError:
            return None  # silently drop bad times; better UX than refusing

    @staticmethod
    def preset_range(preset_id: str, today: date) -> Optional[tuple[str, str]]:
        """Map a preset button id to a (from_date, to_date) ISO pair.

        Pure helper exposed for unit tests so the date math doesn't
        require a running Textual app to verify.
        """
        if preset_id == "ws-preset-7d":
            return ((today - timedelta(days=7)).isoformat(), today.isoformat())
        if preset_id == "ws-preset-30d":
            return ((today - timedelta(days=30)).isoformat(), today.isoformat())
        if preset_id == "ws-preset-month":
            return (today.replace(day=1).isoformat(), today.isoformat())
        if preset_id == "ws-preset-year":
            return (today.replace(month=1, day=1).isoformat(), today.isoformat())
        return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid == "ws-clear":
            for fid in ("ws-from", "ws-to", "ws-start", "ws-end"):
                self.query_one(f"#{fid}", Input).value = ""
            self.post_message(self.WindowChanged(None))
            return

        if bid.startswith("ws-preset-"):
            preset = self.preset_range(bid, date.today())
            if preset is None:
                return
            fd, td = preset
            self._set_dates(fd, td)
            self.post_message(self.WindowChanged({
                "mode": "range",
                "from_date": fd,
                "to_date": td,
            }))
            return

        if bid == "ws-apply":
            fd = self._val("ws-from")
            td = self._val("ws-to")
            st = self._parse_time("ws-start")
            et = self._parse_time("ws-end")
            if not (fd or td or st or et):
                # Nothing typed — treat Apply as Clear so an empty form
                # doesn't lock the user into a meaningless window.
                self.post_message(self.WindowChanged(None))
                return
            window: dict = {"mode": "range", "from_date": fd, "to_date": td}
            if st:
                window["start_time"] = st
            if et:
                window["end_time"] = et
            self.post_message(self.WindowChanged(window))


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
        # Original unfiltered chat — saved from the FIRST render_messages
        # call so filter_messages(window) can restore the full list when
        # the user clears the WindowStrip. Kept in sync with the worker
        # output, NOT mutated by progressive load (that only shrinks the
        # `_shown_count` window into `_all_messages`).
        self._unfiltered_messages: list = []
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
        # Range-mark visual state. Mirrors `app.state.range_*_msg_id`
        # for the duration of a render so `_build_blob` can paint the
        # endpoints and in-range lines without re-querying app state
        # for every message. Updated by `apply_marks` and consumed on
        # every `_build_blob` call.
        self._mark_start_id: int | None = None
        self._mark_end_id: int | None = None
        self._in_range_ids: set[int] = set()
        # Keyboard cursor. Independent of the mark endpoints — the user
        # moves the cursor row-by-row with up/down arrows, and then
        # presses space/enter to drop a range endpoint at the cursor
        # position. _build_blob paints a `▸ ` gutter marker on the
        # cursored row so the user can see where their keyboard focus
        # sits. None means "no cursor" (e.g. before any chat is loaded).
        self._cursor_msg_id: int | None = None

    def show_placeholder(self, text: str = "Pick a chat from the left.") -> None:
        self.remove_children()
        # Reset progressive-load state. The old topmost chunk + affordance
        # are being pruned by remove_children() — keep no dangling
        # references to them, and don't let `_shown_count` from a prior
        # chat survive (otherwise a fresh render_messages would compute
        # its hidden_count from the wrong base).
        self._all_messages = []
        self._unfiltered_messages = []
        self._shown_count = 0
        self._topmost_widget = None
        self._load_more_widget = None
        self._beginning_widget = None
        self._mark_start_id = None
        self._mark_end_id = None
        self._in_range_ids = set()
        self._cursor_msg_id = None
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

    def render_messages(self, messages: list, *, _from_filter: bool = False) -> None:
        """Open the chat at its tail. Older messages stay hidden until the
        user explicitly asks for them with the "o" binding.

        Mounts the most-recent chunk as a Static and records it as the
        topmost mounted chunk. Each subsequent action_load_older mounts
        the next older chunk above the current topmost.

        `_from_filter=True` means the call came from `filter_messages`
        and `messages` is already a filtered subset — we should NOT
        overwrite `_unfiltered_messages`, so the user can clear the
        filter and restore the full chat. Every other caller is
        external (the chat-load worker) and that IS the new baseline.
        """
        if not _from_filter:
            self._unfiltered_messages = list(messages)
        self._all_messages = list(messages)
        self.remove_children()
        self._placeholder_visible = False
        if not self._all_messages:
            self.show_placeholder("No messages in this chat.")
            return

        self._shown_count = min(self.PREVIEW_CAP, len(self._all_messages))
        visible = self._all_messages[-self._shown_count:]
        hidden = len(self._all_messages) - self._shown_count

        # Seed the keyboard cursor on the most-recent message — that's
        # where the user is reading from, so up-arrow walks backward
        # through history in the direction they're scanning. Preserve
        # a still-valid cursor across re-renders (e.g. filter toggles)
        # so the user doesn't lose their place.
        loaded_ids = {m.message_id for m in self._all_messages}
        if self._cursor_msg_id not in loaded_ids:
            self._cursor_msg_id = self._all_messages[-1].message_id

        blob = self._build_blob(visible)
        # Use classes (not id) — remove_children() is async, so a rapid
        # chat-switch can still have the previous "recent-chunk" in the
        # node tree when we mount the next one. Classes coexist; ids don't.
        self._topmost_widget = Static(blob, classes="history-blob recent-chunk")
        # Stash the slice on the widget itself so apply_marks can find
        # all chunks (and only chunks — not the affordance / beginning
        # marker / WindowStrip) when it needs to re-render with new
        # highlight info.
        self._topmost_widget._chunk_messages = visible  # type: ignore[attr-defined]
        # Cache the chunk's message_id set so apply_marks can do an O(1)
        # intersection test against the changed-ids set instead of walking
        # `_chunk_messages` to rebuild the set on every click.
        self._topmost_widget._chunk_ids = {m.message_id for m in visible}  # type: ignore[attr-defined]
        self.mount(self._topmost_widget)
        # Mount whichever top indicator matches state: clickable affordance
        # if there are still older messages to fetch, otherwise a "reached
        # the start" marker so the top of the scroll is never empty (the
        # user always gets feedback).
        self._refresh_top_indicator(hidden)
        self.call_after_refresh(self.scroll_end, animate=False)

    def filter_messages(self, window: Optional[dict]) -> None:
        """Apply (or clear) the WindowStrip filter and re-render.

        `window=None` restores the full chat from `_unfiltered_messages`.
        Otherwise the helper in `state.filter_by_window` filters the
        full list down to messages inside the window, and we re-render
        with `_from_filter=True` so `_unfiltered_messages` survives the
        round-trip.
        """
        from .state import filter_by_window
        if window is None:
            filtered = list(self._unfiltered_messages)
        else:
            filtered = filter_by_window(self._unfiltered_messages, window)
        self.render_messages(filtered, _from_filter=True)

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

    # Every row carries a 2-col gutter — `▸ ` for the keyboard cursor,
    # two spaces otherwise — so all rows stay column-aligned and the
    # cursor marker doesn't shift the timestamp/body left and right as
    # the user navigates. Wrapped continuation lines indent under the
    # message body (2 gutter + 10 cols ≈ "[12:34 pm] "). On a row with
    # a selection background, the leading 12 cols of the wrap still
    # carry the row style so the colored block stays contiguous.
    _CURSOR_MARKER = "▸ "
    _GUTTER_BLANK = "  "
    _WRAP_INDENT = " " * 12

    def _selection_colors(self) -> tuple[str, str, str]:
        """Return (endpoint_bg, range_bg, contrast_fg) hex codes for the
        active theme.

        Endpoint and in-range backgrounds are pulled from the palette's
        two accent slots so they're visually related but distinct — the
        eye can pick out the anchors from the band at a glance without
        the colors clashing.

        Foreground is the theme's `bg` — the inverse-contrast partner of
        `fg`. Painting body text in `bg` over a saturated `accent` background
        keeps the result readable on both light and dark themes without
        per-theme tuning.

        Returns "" for any missing palette key so callers can short-circuit
        and skip background styling on themes that didn't expose accents.
        """
        from ..theme import PALETTES, DAWNFOX
        try:
            pal = PALETTES[self.app.theme]
        except (KeyError, AttributeError):
            pal = DAWNFOX
        return (
            pal.get("accent_alt", ""),  # endpoint bg — the distinct anchor color
            pal.get("accent", ""),       # in-range bg — the related band color
            pal.get("bg", ""),           # text color that stays readable on either
        )

    def _build_blob(self, visible: list) -> Text:
        """Render a chunk of messages as a single Rich Text blob.

        Every message line is tagged with `meta={"msg_id": m.message_id}`
        on its style spans. Textual surfaces that meta dict via
        `event.style.meta` when the user clicks the line, which is how
        click-to-mark-a-range survives the single-Static blob model
        (we don't have per-row widgets to attach `data_msg_id` to).

        Selection highlight paints the full message line:
          - endpoints render with the `accent_alt` background (the distinct
            anchor color) in bold, so the two range edges pop;
          - in-range lines render with the `accent` background — the same
            color family as the endpoints so the band reads as one
            selection, but a different hue so endpoints stay legible
            against it.
        On a selected row, the per-span `dim` timestamp style is dropped —
        dim text on a saturated background reads as washed-out. The
        speaker stays bold because bold layers cleanly on a colored bg.
        """
        blob = Text()
        last_date = None
        endpoints = {self._mark_start_id, self._mark_end_id} - {None}
        in_range = self._in_range_ids
        cursor_id = self._cursor_msg_id
        endpoint_bg, range_bg, contrast_fg = self._selection_colors()
        for m in visible:
            ts = m.timestamp  # "YYYY-MM-DD HH:MM:SS"
            day = ts[:10]
            if day != last_date:
                dt = datetime.strptime(day, "%Y-%m-%d")
                if last_date is not None:
                    blob.append("\n")
                # Day-header isn't a selectable row; render it flush-left
                # with no row-level selection styling.
                blob.append(
                    f"── {dt.strftime('%A, %B %-d, %Y')} ──\n",
                    style="bold cyan",
                )
                last_date = day

            is_endpoint = m.message_id in endpoints
            is_in_range = (not is_endpoint) and m.message_id in in_range
            if is_endpoint and endpoint_bg and contrast_fg:
                row_style = f"bold {contrast_fg} on {endpoint_bg}"
                ts_style = ""        # drop dim so the timestamp reads on the bg
                speaker_style = "bold"
            elif is_in_range and range_bg and contrast_fg:
                row_style = f"{contrast_fg} on {range_bg}"
                ts_style = ""        # ditto — dim washes out on a colored row
                speaker_style = "bold"
            else:
                row_style = ""
                ts_style = "dim"
                speaker_style = "bold"

            ts_str = _format_time_12h(ts)
            speaker = m.author_label or ""
            body = (m.text or "").replace("\n", "\n" + self._WRAP_INDENT)

            is_cursor = m.message_id == cursor_id
            gutter_str = self._CURSOR_MARKER if is_cursor else self._GUTTER_BLANK
            # `bold` so the marker pops without needing a fg color —
            # on selected rows it inherits `contrast_fg` from row_style.
            gutter_style = "bold" if is_cursor else ""

            # Meta-tag every span on this line with the message id. A click
            # anywhere along the line — gutter, timestamp, speaker, body —
            # reports this id back via `event.style.meta["msg_id"]` so
            # on_click can mark it as a range endpoint without per-row
            # widgets.
            line_meta = Style(meta={"msg_id": m.message_id})

            def _meta_style(extra: str) -> Style:
                # Module-level `_parse_style` memoizes the parse; per-line
                # `+ line_meta` is cheap (just a dict merge for the meta).
                return line_meta + _parse_style(extra)

            line = Text()
            line.append(gutter_str, style=_meta_style(gutter_style))
            line.append(f"[{ts_str}] ", style=_meta_style(ts_style))
            line.append(f"{speaker}: ", style=_meta_style(speaker_style))
            line.append(body, style=_meta_style(""))
            line.append("\n")
            if row_style:
                # Apply the row-level background + foreground as the base
                # style. Per-span styles (the `bold` speaker) layer on top
                # without losing the background, because Rich treats bold
                # as a modifier rather than a fg/bg override.
                line.stylize(row_style)
            blob.append(line)
        return blob

    def _accent_hex(self) -> str:
        """Return the active theme's accent hex, or "" if unavailable."""
        from ..theme import PALETTES, DAWNFOX
        try:
            pal = PALETTES[self.app.theme]
        except (KeyError, AttributeError):
            pal = DAWNFOX
        return pal.get("accent") or pal.get("accent_alt") or ""

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
        older_widget._chunk_messages = older_slice  # type: ignore[attr-defined]
        older_widget._chunk_ids = {m.message_id for m in older_slice}  # type: ignore[attr-defined]
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
        ("up", "cursor_up", "Previous message"),
        ("down", "cursor_down", "Next message"),
        ("enter", "mark_row", "Mark range endpoint"),
        ("space", "mark_row", "Mark range endpoint"),
        ("escape", "clear_marks", "Clear marks"),
        ("o", "load_older", "Load 2,000 older messages"),
    ]

    def on_key(self, event) -> None:
        """Left arrow jumps back to the sidebar chat list.

        Pairs with Sidebar's Right handler so ←/→ feel like a single
        spatial bridge between the two main panes. HistoryView doesn't
        scroll horizontally, so consuming Left here doesn't break any
        real interaction.
        """
        if event.key == "left":
            try:
                sidebar = self.app.query_one(Sidebar)
                list_view = sidebar.query_one("#sidebar-list", ListView)
            except Exception:
                return
            list_view.focus()
            event.prevent_default()
            event.stop()

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
        """Drop a range endpoint at the keyboard cursor's current row.

        Posts the same RangeMarkRequested message a mouse click would,
        so the app-level mark logic (nearest-endpoint adjustment, range
        computation, repaint) doesn't need to know whether the request
        came from a click or a key. Silent no-op when no cursor is set
        (e.g. empty chat).
        """
        if self._cursor_msg_id is not None:
            self.post_message(self.RangeMarkRequested(self._cursor_msg_id))

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def action_cursor_down(self) -> None:
        self._move_cursor(+1)

    def _move_cursor(self, delta: int) -> None:
        """Advance the keyboard cursor by `delta` messages and repaint.

        Clamps to the loaded-message bounds (no wrap) — the user always
        knows where the ends are. Repaints only the chunk(s) holding the
        old and new cursor rows so the response stays fast even on long
        chats. After moving, asks the parent VerticalScroll to bring the
        cursor's chunk into view if it scrolled off-screen.
        """
        if not self._all_messages or self._cursor_msg_id is None:
            return
        ids = [m.message_id for m in self._all_messages]
        try:
            i = ids.index(self._cursor_msg_id)
        except ValueError:
            # Cursor refers to an id no longer in the loaded list
            # (chat-switch race). Reset to the latest message instead
            # of crashing — same recovery pattern apply_marks uses.
            self._cursor_msg_id = self._all_messages[-1].message_id
            self._rerender_chunks({self._cursor_msg_id})
            self._scroll_cursor_into_view()
            return
        new_i = max(0, min(len(ids) - 1, i + delta))
        if new_i == i:
            return  # already at a bound; nothing to repaint
        old_id = self._cursor_msg_id
        new_id = ids[new_i]
        self._cursor_msg_id = new_id
        # Only the two rows that changed need a repaint — old loses its
        # marker, new gains one. _rerender_chunks intersects this against
        # each chunk's `_chunk_ids`, so chunks that contain neither are
        # skipped (the perf invariant from the previous commit).
        self._rerender_chunks({old_id, new_id})
        self._scroll_cursor_into_view()

    def _scroll_cursor_into_view(self) -> None:
        """Best-effort: scroll the chunk holding the cursor into view.

        Single-Static-per-chunk means we can't address an individual
        message line as a widget — we don't know its y inside the
        rendered Text without re-measuring layout. Scrolling the
        containing chunk into view is a coarse approximation, but it
        keeps the cursor reachable within a chunk and triggers an
        actual scroll when the cursor walks past a chunk boundary.
        Fails silently if Textual can't honor the scroll request (e.g.
        the chunk isn't mounted yet during a fast nav burst).
        """
        cursor = self._cursor_msg_id
        if cursor is None:
            return
        for child in self.children:
            chunk_ids = getattr(child, "_chunk_ids", None)
            if chunk_ids and cursor in chunk_ids:
                try:
                    self.scroll_to_widget(child, animate=False)
                except Exception:
                    pass
                return

    def action_clear_marks(self) -> None:
        self.post_message(self.RangeMarkRequested(msg_id=-1))  # sentinel: clear

    def apply_marks(self, start_id: int | None, end_id: int | None, messages: list[dict]) -> None:
        """Update the visual range-highlight on every mounted chunk.

        Stores `start_id` / `end_id` and the computed in-range id set on
        the view, then asks each chunk Static to rebuild its blob via
        `_build_blob` (which reads those instance attrs and paints the
        endpoint / in-range styles).

        Defensive: if either marked id is missing from `messages` (e.g.
        the user switched to a chat where the previous marks don't
        apply, and the app-level cleanup didn't catch it), clear the
        visual marks rather than crash on `list.index(x)`.

        Performance: builds an id→index dict (O(N) once, O(1) per
        lookup) instead of repeated `list.index()` scans, and only
        repaints chunks whose ids intersect either the old or new
        highlight set. For a long chat with several chunks loaded,
        that turns "rebuild every chunk on every click" into "rebuild
        the one chunk you clicked in" — the click-to-mark feedback
        loop drops from sluggish to instant.
        """
        # Snapshot the OLD highlight set so we can repaint any chunks
        # whose rows need to *un*-highlight after the marks change.
        old_highlighted: set[int] = set(self._in_range_ids)
        if self._mark_start_id is not None:
            old_highlighted.add(self._mark_start_id)
        if self._mark_end_id is not None:
            old_highlighted.add(self._mark_end_id)

        def _commit_clear() -> None:
            self._mark_start_id = None
            self._mark_end_id = None
            self._in_range_ids = set()
            # Only chunks that *had* highlighted rows need a repaint;
            # everything else is already in its unhighlighted state.
            self._rerender_chunks(old_highlighted)

        if start_id is None and end_id is None:
            if not old_highlighted:
                return  # nothing was highlighted; nothing to repaint
            _commit_clear()
            return

        # O(N) one-shot build, O(1) lookups — replaces two O(N)
        # `ids_in_order.index(...)` calls.
        id_to_index = {m["message_id"]: i for i, m in enumerate(messages)}
        if (start_id is not None and start_id not in id_to_index) or (
            end_id is not None and end_id not in id_to_index
        ):
            # Mark refers to a message not in the loaded list — wipe
            # the visual state and let the caller catch up.
            _commit_clear()
            return

        new_highlighted: set[int]
        if start_id is not None and end_id is not None:
            lo = id_to_index[start_id]
            hi = id_to_index[end_id]
            if lo > hi:
                lo, hi = hi, lo
            new_in_range = {messages[i]["message_id"] for i in range(lo, hi + 1)}
            new_highlighted = new_in_range
        else:
            new_in_range = {start_id, end_id} - {None}
            new_highlighted = set(new_in_range)

        # Bail early when nothing changed — repeated clicks on the same
        # endpoint, or an idempotent app-level callback, should not
        # re-render thousands of message lines.
        if (
            self._mark_start_id == start_id
            and self._mark_end_id == end_id
            and self._in_range_ids == new_in_range
        ):
            return

        self._mark_start_id = start_id
        self._mark_end_id = end_id
        self._in_range_ids = new_in_range
        # Repaint any chunk that either *was* showing a highlight (needs
        # to clear it) or *now* needs to show one. Chunks outside both
        # sets are pixel-identical before and after — skipping them is
        # what makes click feedback feel instant.
        self._rerender_chunks(old_highlighted | new_highlighted)

    def _rerender_chunks(self, affected_ids: set[int] | None = None) -> None:
        """Rebuild chunk Statics whose ids intersect `affected_ids`.

        Iterates `self.children` and only touches widgets that carry a
        `_chunk_messages` attribute — that's how we distinguish actual
        message chunks from the WindowStrip / ChatHeader siblings, the
        load-more affordance, and the beginning marker (none of which
        should be repainted on mark changes).

        When `affected_ids` is None, every chunk is rebuilt — the
        original full-redraw behavior, kept for callers (and tests)
        that don't have a precise "what changed" set. When it's a set,
        chunks whose `_chunk_ids` don't intersect it are skipped.
        """
        for child in list(self.children):
            slice_msgs = getattr(child, "_chunk_messages", None)
            if slice_msgs is None:
                continue
            if affected_ids is not None:
                chunk_ids = getattr(child, "_chunk_ids", None)
                if chunk_ids is not None and not (chunk_ids & affected_ids):
                    continue
            child.update(self._build_blob(slice_msgs))


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
