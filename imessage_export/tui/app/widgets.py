"""Sidebar (chat list with filter), HistoryView, StatusLine, ActionBar.

HistoryView, StatusLine, ActionBar are filled in by Tasks 6 / 7 / 13.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.widgets import Button, Input, ListItem, ListView, Label, Static


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

    Day boundaries render as a full-width rule (`──── Saturday, June 6,
    2026 ────`) at chunk-render width. Within a day, consecutive
    messages from the same speaker (no internal ≥30-min gap) collapse
    under a single `Speaker  ·  9:00 AM` header; continuation lines
    carry inline padded times. All speaker/footnote/selection styling
    is baked into the Rich Text at build time using palette hex from
    `theme.PALETTES` — so this widget has no `.speaker-*`/`.day-header`
    CSS classes to wire up.
    """

    DEFAULT_CSS = """
    HistoryView {
        padding: 0 2;
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
        # for the duration of a render so `history_render.paint` can
        # paint the endpoints and in-range lines without re-querying
        # app state for every message. Updated by `apply_marks` and
        # consumed by every `_repaint_for_ids` call.
        self._mark_start_id: int | None = None
        self._mark_end_id: int | None = None
        self._in_range_ids: set[int] = set()
        # Set of every msg_id present in the loaded chat — used by
        # on_click to drop stale-meta clicks after a filter or chat
        # switch. O(1) membership lookup, rebuilt in render_messages.
        self._loaded_ids: set[int] = set()
        # Contacts mapping forwarded to history_render.format_row. Empty
        # by default — currently unused by the formatter (forward-compat
        # for handle→display-name swap-in), kept so _ChunkRender.build
        # has a stable dict reference to consume.
        self._contacts: dict = {}
        # Re-entrancy guard for watch_scroll_y. action_load_older calls
        # scroll_to() to preserve reading position, which would re-fire
        # watch_scroll_y. The guard prevents that recursive invocation
        # from triggering a second autoload before the first completes.
        self._autoload_in_flight: bool = False
        # Set by action_scroll_top so that watch_scroll_y skips the
        # autoload check for the Home scroll. Home is "jump to the top
        # of loaded content" — the affordance stays visible so the user
        # can explicitly request more; auto-triggering a load would
        # override the user's intended position via _preserve_position_with_peek.
        self._home_scroll_in_flight: bool = False

    def show_placeholder(self, text: str = "Pick a chat from the left.") -> None:
        self.remove_children()
        # Reset progressive-load state. The old topmost chunk + affordance
        # are being pruned by remove_children() — keep no dangling
        # references to them, and don't let `_shown_count` from a prior
        # chat survive (otherwise a fresh render_messages would compute
        # its hidden_count from the wrong base).
        self._all_messages = []
        self._loaded_ids = set()
        self._unfiltered_messages = []
        self._shown_count = 0
        self._topmost_widget = None
        self._load_more_widget = None
        self._beginning_widget = None
        self._mark_start_id = None
        self._mark_end_id = None
        self._in_range_ids = set()
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
        self._loaded_ids = {m.message_id for m in self._all_messages}
        self.remove_children()
        self._placeholder_visible = False
        if not self._all_messages:
            # Filtered to nothing vs an actually-empty chat read
            # different to the user; pick the message accordingly.
            self.show_placeholder(
                "No messages match this filter."
                if _from_filter else "No messages in this chat."
            )
            return

        self._shown_count = min(self.PREVIEW_CAP, len(self._all_messages))
        visible = self._all_messages[-self._shown_count:]
        hidden = len(self._all_messages) - self._shown_count

        from . import history_render

        # Build the cached _ChunkRender for the visible slice. The
        # base Text is unstyled — paint() layers the current cursor +
        # selection state on the clone we hand to Static.update().
        # `width` makes row_line_counts wrap-aware so the cursor's
        # screen-y math doesn't drift on long-message chats.
        palette = self._palette()
        chunk = history_render._ChunkRender.build(
            visible, self._contacts,
            palette=palette, width=self._render_width(),
        )
        marks = history_render.MarkState(
            self._mark_start_id, self._mark_end_id, frozenset(self._in_range_ids))
        decorated = history_render.paint(chunk, marks, palette)
        # Use classes (not id) — remove_children() is async, so a rapid
        # chat-switch can still have the previous "recent-chunk" in the
        # node tree when we mount the next one. Classes coexist; ids don't.
        self._topmost_widget = Static(decorated, classes="history-blob recent-chunk")
        chunk.widget = self._topmost_widget
        # Stash the full _ChunkRender on the widget so _repaint_for_ids
        # can find it later. `chunk.msg_ids` is the single source of
        # truth for which messages this chunk owns.
        self._topmost_widget._chunk_render = chunk  # type: ignore[attr-defined]
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

    def _palette(self) -> dict:
        """Return the active theme's palette dict (DAWNFOX/TERAFOX),
        defaulting to DAWNFOX if the app's theme isn't one we know."""
        from ..theme import PALETTES, DAWNFOX
        try:
            return PALETTES[self.app.theme]
        except (KeyError, AttributeError):
            return DAWNFOX

    def _render_width(self) -> int:
        """The width chunk Statics actually render at — used by
        `_ChunkRender.build` so per-row line counts include word-wrap.

        Empirically this is `size.width - 2` for our padding/scrollbar
        layout (verified against `chunk.virtual_region.width` in
        manual repro). Returns a positive int even before first
        layout — a 1-cell fallback keeps `build` from crashing while
        downstream scroll math harmlessly disagrees by a few cells
        until the next render after we have a real width.
        """
        w = self.size.width - 2
        return w if w > 0 else 80

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
        # Build the older slice's _ChunkRender and decorate with the
        # current cursor + mark state so any selection that spans into
        # the older slice paints on first render.
        from . import history_render
        palette = self._palette()
        chunk = history_render._ChunkRender.build(
            older_slice, self._contacts,
            palette=palette, width=self._render_width(),
        )
        marks = history_render.MarkState(
            self._mark_start_id, self._mark_end_id, frozenset(self._in_range_ids))
        older_decorated = history_render.paint(
            chunk, marks, palette)
        self._shown_count = new_shown
        # _loaded_ids is already complete (every loaded message lives
        # in _all_messages from chat-load time — load-older only widens
        # _shown_count). No rebuild needed here.
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
        older_widget = Static(older_decorated, classes="history-blob older")
        chunk.widget = older_widget
        older_widget._chunk_render = chunk  # type: ignore[attr-defined]
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

    # ------------------------------------------------------------------
    # Task 7: Range marks
    # ------------------------------------------------------------------

    class RangeMarkRequested(TextualMessage):
        """User clicked or Enter'd a message row — mark it as a range endpoint."""
        def __init__(self, msg_id: int) -> None:
            super().__init__()
            self.msg_id = msg_id

    AUTOLOAD_TOP_MARGIN = 5  # rows from top of loaded content that
                             # trigger an auto-load of the next older chunk

    BINDINGS = [
        ("up", "scroll_up", "Scroll up"),
        ("down", "scroll_down", "Scroll down"),
        ("pageup", "page_up", "Page up"),
        ("pagedown", "page_down", "Page down"),
        ("home", "scroll_top", "Top of loaded"),
        ("end", "scroll_bottom", "Latest message"),
        ("o", "load_older", "Load older messages"),
        ("escape", "clear_marks", "Clear range marks"),
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
                # Drop clicks whose msg_id was filtered out / unloaded
                # mid-prune. The post-handler can't recover from a stale
                # id cleanly, and silently dropping matches the prior
                # stale-id recovery pattern.
                msg_id = int(msg_id)
                if msg_id not in self._loaded_ids:
                    event.stop()
                    return
                self.post_message(self.RangeMarkRequested(msg_id))
                event.stop()
                return

    def action_scroll_up(self) -> None:
        """Scroll viewport up by 1 row; auto-load older chunk if near top."""
        self.scroll_relative(y=-1, animate=False)
        self._check_autoload_threshold()

    def action_scroll_down(self) -> None:
        """Scroll viewport down by 1 row."""
        self.scroll_relative(y=1, animate=False)

    def action_page_up(self) -> None:
        """Scroll viewport up by one viewport height; auto-load if near top."""
        self.scroll_relative(y=-self._viewport_height_lines(), animate=False)
        self._check_autoload_threshold()

    def action_page_down(self) -> None:
        """Scroll viewport down by one viewport height."""
        self.scroll_relative(y=self._viewport_height_lines(), animate=False)

    def action_scroll_top(self) -> None:
        """Scroll to the top of currently-loaded content.

        The "Load older messages" affordance remains visible so the user
        can request more content explicitly. We deliberately do NOT
        auto-load here — firing action_load_older would override y=0
        via its position-restore callback and snap the viewport away
        from where Home was supposed to land. watch_scroll_y still
        handles all other programmatic and mouse-driven scrolls.
        """
        # Early return when already at top: scroll_to(y=0) would no-op
        # without firing watch_scroll_y, leaving _home_scroll_in_flight
        # stuck True and suppressing the next organic autoload.
        if self.scroll_y == 0:
            return
        self._home_scroll_in_flight = True
        self.scroll_to(y=0, animate=False)

    def action_scroll_bottom(self) -> None:
        """Scroll to the bottom (latest message)."""
        self.scroll_end(animate=False)

    def _check_autoload_threshold(self) -> None:
        """If the viewport is within AUTOLOAD_TOP_MARGIN of the top and
        older messages are still hidden, fire one action_load_older.

        Bounded by the chat size: action_load_older itself short-circuits
        when _shown_count >= len(_all_messages). The existing scroll-
        restore logic in action_load_older keeps the user's reading
        position stable across the mount, so from the user's perspective
        it feels like infinite scroll.
        """
        if self._shown_count >= len(self._all_messages):
            return
        if self.scroll_y < self.AUTOLOAD_TOP_MARGIN:
            self.action_load_older()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Fires on every scroll_y change (keyboard, mouse-wheel, drag,
        programmatic). When the user scrolls into the top margin, auto-
        load the next older chunk so this feels like infinite scroll
        regardless of which input caused the scroll. Guarded against
        re-entry — action_load_older itself causes a scroll_y change via
        scroll_to() to preserve the user's reading position. Skipped
        when _home_scroll_in_flight is set — action_scroll_top owns the
        explicit scroll to y=0 and must not have its landing position
        overridden by the position-restore inside action_load_older.

        Widget.watch_scroll_y updates the scrollbar position and triggers
        a refresh, so we call super() first to preserve that behaviour.
        """
        super().watch_scroll_y(old_value, new_value)
        if self._autoload_in_flight:
            return
        # If Home fired this scroll (action_scroll_top set the flag), skip
        # the autoload so _preserve_position_with_peek doesn't override y=0.
        # Clear the flag here so future organic scrolls are unrestricted.
        if self._home_scroll_in_flight:
            self._home_scroll_in_flight = False
            return
        self._autoload_in_flight = True
        try:
            self._check_autoload_threshold()
        finally:
            self._autoload_in_flight = False

    def _viewport_height_lines(self) -> int:
        """Best-effort viewport height in terminal rows. Used by
        page up/down to compute a sensible jump distance. Falls back
        to 20 if the size isn't known yet (e.g. during early mount).
        """
        try:
            h = int(self.size.height)
        except Exception:
            h = 0
        return h if h > 0 else 20

    def action_clear_marks(self) -> None:
        self.post_message(self.RangeMarkRequested(msg_id=-1))  # sentinel: clear

    def apply_marks(self, start_id: int | None, end_id: int | None, messages: list[dict]) -> None:
        """Update the visual range-highlight on every mounted chunk.

        Stores `start_id` / `end_id` and the computed in-range id set on
        the view, then asks each affected chunk Static to repaint via
        `_repaint_for_ids` (which reads those instance attrs and paints
        the endpoint / in-range styles through `history_render.paint`).

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
            self._repaint_for_ids(old_highlighted)

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
        self._repaint_for_ids(old_highlighted | new_highlighted)

    def _repaint_for_ids(self, affected_ids: set[int] | None = None) -> None:
        """Repaint chunks whose ids intersect `affected_ids`.

        When `affected_ids` is None, every chunk is repainted —
        used by the cold-load path. With a set, chunks whose
        `_chunk_render.msg_ids` don't intersect are skipped (the perf
        invariant from PR #248eaec — pinned by the repaint-only-affected
        test).
        """
        from . import history_render
        palette = self._palette()
        marks = history_render.MarkState(
            self._mark_start_id, self._mark_end_id, frozenset(self._in_range_ids))
        for child in list(self.children):
            chunk: history_render._ChunkRender | None = getattr(
                child, "_chunk_render", None)
            if chunk is None:
                continue
            if affected_ids is not None and not (set(chunk.msg_ids) & affected_ids):
                continue
            decorated = history_render.paint(
                chunk, marks, palette)
            child.update(decorated)


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
