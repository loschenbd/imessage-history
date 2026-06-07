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
from .widgets import HistoryView, Sidebar, StatusLine


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
        ("e", "export", "Export"),
        ("z", "relaunch_wizard", "Wizard"),
        ("h", "help", "Help"),
        ("q", "quit", "Quit"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.conn = None
        self._defaults: Defaults | None = None

    def compose(self) -> ComposeResult:
        from .widgets import ActionBar, StatusLine
        yield Horizontal(
            Sidebar(chats=[], contacts={}, id="sidebar"),
            HistoryView(id="history"),
            id="main",
        )
        yield StatusLine(id="status")
        yield ActionBar(id="action-bar")

    def on_mount(self) -> None:
        self._defaults = load_defaults()
        self.state.contacts_path = (
            Path(self._defaults.contacts_path) if self._defaults.contacts_path else None
        )
        self.state.output_dir = (
            Path(self._defaults.output_dir) if self._defaults.output_dir else Path.cwd() / "exports"
        )
        self.state.me_name = self._defaults.me_name or "Me"

        try:
            self.conn = open_db(DEFAULT_DB)
        except BaseException as exc:
            from .modals import ErrorModal
            self.push_screen(ErrorModal(
                title="Cannot read chat.db",
                body=(
                    "Messages requires Full Disk Access for the process\n"
                    "running Python.\n\n"
                    "Open System Settings → Privacy & Security → Full Disk Access\n"
                    f"and add: {__import__('sys').executable}\n\n"
                    f"({exc})"
                ),
                quit_on_close=True,
            ))
            return
        self.state.chats = [dict(r) for r in list_recent_chats(self.conn, 100)]

        if not self.state.chats:
            from .modals import ErrorModal
            self.push_screen(ErrorModal(
                title="No chats in chat.db",
                body="Make sure Messages is set up on this Mac and that you've sent or received at least one message.",
                quit_on_close=True,
            ))
            return

        sidebar = self.query_one(Sidebar)
        sidebar._all_chats = self.state.chats
        sidebar._refresh_list("")

        last = self._defaults.last_chat_id
        if last and any(c.get("chat_id") == last for c in self.state.chats):
            sidebar.select_chat_id(last)

        # Offer to scan Contacts.app if we have no source.
        has_contacts_file = (
            (self.state.contacts_path and self.state.contacts_path.exists())
            or (Path.cwd() / "contacts.csv").exists()
        )
        if not has_contacts_file:
            self.call_later(self._offer_contacts_scan)
        else:
            self._load_contacts_into_state()

    def _load_contacts_into_state(self) -> None:
        from ...contacts import load_contacts
        path = self.state.contacts_path or (Path.cwd() / "contacts.csv")
        if path and Path(path).exists():
            try:
                self.state.contacts = load_contacts(Path(path))
            except Exception:
                self.state.contacts = {}

    async def _offer_contacts_scan(self) -> None:
        from .modals import ContactsScanModal
        result = await self.push_screen_wait(ContactsScanModal())
        if result is not None:
            self.state.contacts_path = result
            self._load_contacts_into_state()
            self._persist_defaults()

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
        self._refresh_status()
        self._persist_defaults()

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
        self.state.history_search_query = None
        history = self.query_one(HistoryView)
        for s in history.query("#history-search"):
            s.remove()
        history.render_messages(event.messages)
        self._refresh_status()
        # Move focus to the first message row so the user can arrow / press /.
        rows = list(history.query(".message-row"))
        if rows:
            rows[0].focus()

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
            self._refresh_status()
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
        self._refresh_status()

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
        self._refresh_status()

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
        self._refresh_status()

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
        self._refresh_status()

    # ------------------------------------------------------------------
    # Task 11: ExportConfirmModal + export worker
    # ------------------------------------------------------------------

    async def action_export(self) -> None:
        from .modals import ExportConfirmModal
        from .state import resolved_window, _format_window

        if self.state.selected_chat_id is None:
            return

        window = resolved_window(self.state)
        chat_row = next((c for c in self.state.chats if c.get("chat_id") == self.state.selected_chat_id), {})
        chat_label = (
            chat_row.get("display_name")
            or chat_row.get("participants")
            or chat_row.get("chat_identifier")
            or f"chat {self.state.selected_chat_id}"
        )
        n = self._count_messages_in_window(window)
        summary = [
            f"Chat:    {chat_label}",
            f"Window:  {_format_window(window)}",
            f"Count:   {n} messages",
            f"Output:  {self.state.output_dir}",
            f"Redact:  {'on' if self.state.redact else 'off'}",
        ]
        confirm = await self.push_screen_wait(ExportConfirmModal(summary_lines=summary))
        if not confirm:
            return

        self._run_export_worker()

    def _count_messages_in_window(self, window: dict) -> int:
        msgs = self.state.selected_chat_messages
        if not msgs or window.get("mode") == "all":
            return len(msgs)
        # Range/day mode: filter by date string prefix.
        if window["mode"] == "day":
            day = window["date"]
            return sum(1 for m in msgs if m["timestamp"].startswith(day))
        if window["mode"] == "range":
            f, t = window["from_date"], window["to_date"]
            return sum(1 for m in msgs if f <= m["timestamp"][:10] <= t)
        return len(msgs)

    @work(thread=True, exclusive=True)
    def _run_export_worker(self) -> None:
        import argparse
        from ...cli import _run, DEFAULT_DB
        from .state import resolved_window, reset_after_export

        try:
            window = resolved_window(self.state)
            ns = argparse.Namespace(
                chat_id=self.state.selected_chat_id,
                chat_identifier=None, participant=None,
                list=False, list_limit=30, list_contacts=False,
                from_date=window.get("from_date"), to_date=window.get("to_date"),
                date=window.get("date"),
                start_time=window.get("start_time"), end_time=window.get("end_time"),
                start_datetime=None, end_datetime=None,
                output_dir=str(self.state.output_dir),
                me_name=self.state.me_name,
                contacts=str(self.state.contacts_path) if self.state.contacts_path else None,
                include_attachments=False,
                limit=None,
                db=str(DEFAULT_DB),
                redact=self.state.redact.get("redact", False),
                redact_only=self.state.redact.get("redact_only", False),
                redact_names_file=self.state.redact.get("redact_names_file"),
                no_redact_phones=self.state.redact.get("no_redact_phones", False),
                no_redact_emails=self.state.redact.get("no_redact_emails", False),
                no_redact_urls=self.state.redact.get("no_redact_urls", False),
                suggest_names=False,
                build_contacts=False,
                wizard=False,
                app=False,
            )
            rc = _run(ns, self.conn)
            if rc == 0:
                n = self._count_messages_in_window(window)
                success_tag = f"✓ Exported {n} msgs → {self.state.output_dir}"
                self.call_from_thread(reset_after_export, self.state, success_tag=success_tag)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            self.call_from_thread(self._show_exception_modal, str(exc), tb)
            return
        self.call_from_thread(self._post_export_refresh)

    def _show_exception_modal(self, exc_str: str, tb: str) -> None:
        from .modals import ErrorModal
        self.push_screen(ErrorModal(
            title="Export failed",
            body=f"{exc_str}\n\n— traceback —\n{tb}",
        ))

    def _post_export_refresh(self) -> None:
        # Repaint marks (now cleared) and let the future status-line widget refresh.
        self._repaint_marks()
        self._refresh_status()

    # ------------------------------------------------------------------
    # Task 13: ActionBar routing + accelerators + HelpModal
    # ------------------------------------------------------------------

    def on_button_pressed(self, event) -> None:
        bid = event.button.id
        if bid == "btn-window":     self.run_action("open_window_modal")
        elif bid == "btn-settings": self.run_action("open_settings_modal")
        elif bid == "btn-redact":   self.run_action("open_redact_modal")
        elif bid == "btn-export":   self.run_action("export")
        elif bid == "btn-wizard":   self.action_relaunch_wizard()
        elif bid == "btn-help":     self.action_help()
        elif bid == "btn-quit":     self.exit(0)

    def action_relaunch_wizard(self) -> None:
        """Quit the app and re-launch as `imessage-export --wizard`."""
        import os, sys
        self.exit(0)
        os.execvp(sys.argv[0], [sys.argv[0], "--wizard"])

    def action_help(self) -> None:
        from .modals import HelpModal
        self.push_screen(HelpModal())

    def on_key(self, event) -> None:
        # When an Input has focus, never let single-letter accelerators fire.
        if self.focused and getattr(self.focused, "__class__", type(None)).__name__ == "Input":
            if event.character and event.character.isalpha():
                # Let the input keep the keystroke; don't run BINDINGS.
                event.prevent_default()

    def _refresh_status(self) -> None:
        try:
            self.query_one("#status", StatusLine).update_from_state(self.state)
        except Exception:
            pass  # status widget not mounted yet during early mount steps
