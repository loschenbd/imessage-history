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

    BINDINGS = [
        ("w", "open_window_modal", "Window"),
        ("s", "open_settings_modal", "Settings"),
        ("r", "open_redact_modal", "Redact"),
    ]

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

    # ------------------------------------------------------------------
    # Task 7: Range marks
    # ------------------------------------------------------------------

    def on_history_view_range_mark_requested(self, event: HistoryView.RangeMarkRequested) -> None:
        if event.msg_id == -1:
            # Sentinel: clear all marks (Esc).
            self.state.range_start_msg_id = None
            self.state.range_end_msg_id = None
            if self.state.typed_window:
                self.state.window_source = "typed"
            else:
                self.state.window_source = "all"
        else:
            self._mark_message(event.msg_id)
        self._repaint_marks()

    def _mark_message(self, msg_id: int) -> None:
        s = self.state
        if s.range_start_msg_id is None:
            s.range_start_msg_id = msg_id
        elif s.range_end_msg_id is None and msg_id != s.range_start_msg_id:
            s.range_end_msg_id = msg_id
        else:
            # Third click / re-click: clear and start over.
            s.range_start_msg_id = msg_id
            s.range_end_msg_id = None
        s.window_source = "selection"
        s.last_export_status = None

    def _repaint_marks(self) -> None:
        history = self.query_one(HistoryView)
        history.apply_marks(
            self.state.range_start_msg_id,
            self.state.range_end_msg_id,
            self.state.selected_chat_messages,
        )

    # ------------------------------------------------------------------
    # Task 8: WindowModal
    # ------------------------------------------------------------------

    async def action_open_window_modal(self) -> None:
        from .modals import WindowModal
        result = await self.push_screen_wait(WindowModal())
        if result is None:
            return
        self.state.typed_window = result
        self.state.window_source = "typed"
        self.state.last_export_status = None

    # ------------------------------------------------------------------
    # Task 9: SettingsModal
    # ------------------------------------------------------------------

    async def action_open_settings_modal(self) -> None:
        from .modals import SettingsModal
        result = await self.push_screen_wait(SettingsModal(
            contacts_path=str(self.state.contacts_path) if self.state.contacts_path else None,
            output_dir=str(self.state.output_dir),
            me_name=self.state.me_name,
        ))
        if result is None:
            return

        # Validate the contacts path if one was given.
        if result["contacts_path"]:
            from ...contacts import load_contacts
            try:
                self.state.contacts = load_contacts(Path(result["contacts_path"]))
                self.state.contacts_path = Path(result["contacts_path"])
            except Exception as exc:
                from .modals import ErrorModal
                await self.push_screen_wait(ErrorModal(
                    title="Contacts file error",
                    body=f"{result['contacts_path']}\n\n{exc}",
                ))
                return  # don't persist a bad path
        else:
            self.state.contacts_path = None
            self.state.contacts = {}

        self.state.output_dir = Path(result["output_dir"]).expanduser()
        self.state.me_name = result["me_name"]
        self._persist_defaults()

    def _persist_defaults(self) -> None:
        from ..defaults import Defaults, save as save_defaults
        save_defaults(Defaults(
            contacts_path=str(self.state.contacts_path) if self.state.contacts_path else None,
            output_dir=str(self.state.output_dir),
            me_name=self.state.me_name,
            last_chat_id=self.state.selected_chat_id,
        ))

    # ------------------------------------------------------------------
    # Task 10: RedactModal
    # ------------------------------------------------------------------

    async def action_open_redact_modal(self) -> None:
        from .modals import RedactModal
        result = await self.push_screen_wait(RedactModal(current=self.state.redact))
        if result is None:
            return
        self.state.redact = result
