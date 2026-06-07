"""Top-level Textual app: ImessageExportApp.

Composes the layout (Sidebar | HistoryView | ActionBar) and owns the
shared `AppState`. Per-widget interactions are wired through Textual
messages.
"""
from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.message import Message as TextualMessage

from ...cli import DEFAULT_DB
from ...db import list_recent_chats, open_db
from ..defaults import Defaults, load as load_defaults
from .state import AppState
from .widgets import HistoryView, Sidebar


class HistoryLoaded(TextualMessage):
    def __init__(self, chat_id: int, messages: list) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.messages = messages


class ImessageExportApp(App):
    """Default interactive surface for `imessage-export` on a TTY."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        height: 1fr;
    }
    """

    TITLE = "imessage-export"
    SUB_TITLE = "interactive mode"

    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.conn = None
        self._defaults: Defaults | None = None

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Sidebar(chats=[], contacts={}, id="sidebar"),
            HistoryView(id="history"),
            id="main",
        )

    def on_mount(self) -> None:
        self._defaults = load_defaults()
        self.state.contacts_path = (
            Path(self._defaults.contacts_path) if self._defaults.contacts_path else None
        )
        self.state.output_dir = (
            Path(self._defaults.output_dir) if self._defaults.output_dir else Path.cwd() / "exports"
        )
        self.state.me_name = self._defaults.me_name or "Me"

        self.conn = open_db(DEFAULT_DB)
        self.state.chats = [dict(r) for r in list_recent_chats(self.conn, 100)]

        sidebar = self.query_one(Sidebar)
        sidebar._all_chats = self.state.chats
        sidebar._refresh_list("")

        last = self._defaults.last_chat_id
        if last and any(c.get("chat_id") == last for c in self.state.chats):
            sidebar.select_chat_id(last)

    def on_unmount(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def on_sidebar_chat_selected(self, event: Sidebar.ChatSelected) -> None:
        self.state.selected_chat_id = event.chat_id
        self.state.history_loading = True
        history = self.query_one(HistoryView)
        history.show_loading()
        self._load_history_worker(event.chat_id)

    @work(thread=True, exclusive=True)
    def _load_history_worker(self, chat_id: int) -> None:
        from .workers import load_chat_messages
        messages = load_chat_messages(
            self.conn,
            chat_id=chat_id,
            contacts=self.state.contacts,
            me_name=self.state.me_name,
        )
        self.post_message(HistoryLoaded(chat_id, messages))

    def on_history_loaded(self, event: HistoryLoaded) -> None:
        if event.chat_id != self.state.selected_chat_id:
            return
        self.state.selected_chat_messages = [
            {"message_id": m.message_id, "timestamp": m.timestamp} for m in event.messages
        ]
        self.state.history_loading = False
        history = self.query_one(HistoryView)
        history.render_messages(event.messages)
