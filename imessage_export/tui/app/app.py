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
from .widgets import ChatHeader, HistoryView, Sidebar, StatusLine


class HistoryLoaded(TextualMessage):
    def __init__(self, chat_id: int, messages: list) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.messages = messages


class HistoryLoadFailed(TextualMessage):
    """Fired when the history worker raised. Surfaces the error in-app
    instead of leaving the user stranded on the "Loading…" placeholder."""
    def __init__(self, chat_id: int, summary: str, detail: str) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.summary = summary
        self.detail = detail


class ImessageExportApp(App):
    """Default interactive surface for `imessage-export` on a TTY."""

    CSS = """
    Screen { layout: vertical; background: $background; color: $foreground; }
    #main { height: 1fr; }
    #history-pane { width: 1fr; height: 1fr; }

    Sidebar { background: $surface; border-right: solid $panel; }
    Sidebar > .selected { background: $panel; color: $primary; text-style: bold; }
    Sidebar.region-active { border-right: thick $accent; }

    ChatHeader { background: $surface; border-bottom: solid $panel; height: 2; }

    HistoryView { background: $background; color: $foreground; }
    HistoryView.region-active { border-left: thick $accent; }
    HistoryView .day-header { color: $day-header; text-style: bold; }
    HistoryView .gap-marker { color: $muted; text-style: italic; }
    HistoryView .speaker-other { color: $primary; text-style: bold; }
    HistoryView .speaker-me    { color: $accent;  text-style: bold; }
    HistoryView .timestamp     { color: $muted; }

    StatusLine { background: $surface; color: $foreground; }
    ActionBar  { background: $panel;   color: $foreground; }
    ActionBar.region-active { border-top: thick $accent; }
    ActionBar .key { color: $primary; text-style: bold; }
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
        # Register themes AND set the active theme early: App.CSS references
        # custom variables ($day-header etc.) at stylesheet-parse time, which
        # happens before on_mount runs. We re-resolve the theme in on_mount
        # so persisted/env/CLI sources can override the __init__ guess.
        import os
        from ..theme import register_textual_themes, resolve_theme_name
        register_textual_themes(self)
        self.theme = resolve_theme_name(
            cli=getattr(self, "_cli_theme", None),
            env=os.environ.get("IMESSAGE_EXPORT_THEME"),
            persisted=None,  # defaults aren't loaded yet; on_mount re-resolves
        )

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        from .widgets import ActionBar, StatusLine, WindowStrip
        yield Horizontal(
            Sidebar(chats=[], contacts={}, id="sidebar"),
            Vertical(
                ChatHeader(id="chat-header"),
                WindowStrip(id="window-strip"),
                HistoryView(id="history"),
                id="history-pane",
            ),
            id="main",
        )
        yield StatusLine(id="status")
        yield ActionBar(id="action-bar")

    def on_mount(self) -> None:
        import os
        from ..theme import register_textual_themes, resolve_theme_name

        self._defaults = load_defaults()
        register_textual_themes(self)
        self.theme = resolve_theme_name(
            cli=getattr(self, "_cli_theme", None),
            env=os.environ.get("IMESSAGE_EXPORT_THEME"),
            persisted=self._defaults.theme_override,
        )
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

        # Now that initial selection is wired, let arrow-nav auto-load.
        sidebar.enable_highlight_autoload()

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
        # Wire the contacts into the Sidebar so the chat list renders
        # "Name (handle)" pairs instead of bare phone numbers, matching
        # the wizard. The Sidebar was constructed with contacts={} before
        # this callback ran.
        try:
            sidebar = self.query_one(Sidebar)
        except Exception:
            return
        sidebar._contacts = self.state.contacts
        current_filter = ""
        try:
            from textual.widgets import Input
            current_filter = sidebar.query_one("#sidebar-filter", Input).value
        except Exception:
            pass
        sidebar._refresh_list(current_filter)
        # Header may have rendered the raw handle before contacts arrived;
        # re-render now that the resolver has names to work with.
        self._refresh_chat_header()

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
        self._refresh_chat_header()
        self._load_history_worker(event.chat_id)
        self._refresh_status()
        self._persist_defaults()

    def _refresh_chat_header(self) -> None:
        """Repaint the chat header from current state.

        Called on chat selection AND when contacts load late, so a header
        that initially rendered "+15551234567" gets upgraded to
        "Beautiful Wife (+1555…)" the moment the contacts file resolves.
        """
        try:
            header = self.query_one(ChatHeader)
        except Exception:
            return
        if self.state.selected_chat_id is None:
            header.show_empty()
            return
        chat_row = next(
            (c for c in self.state.chats if c.get("chat_id") == self.state.selected_chat_id),
            None,
        )
        if chat_row is None:
            header.show_empty()
            return
        header.update_from_chat(chat_row, self.state.contacts)

    @work(thread=True, exclusive=True)
    def _load_history_worker(self, chat_id: int) -> None:
        from ...db import open_db
        from .workers import load_chat_messages
        # Open a per-call read-only connection so a slow load (e.g. a 70k
        # chat) doesn't hold the shared self.conn lock and stall the next
        # chat click. exclusive=True still discards the previous worker's
        # result via the chat_id check in on_history_loaded.
        try:
            worker_conn = open_db(DEFAULT_DB)
            try:
                messages = load_chat_messages(
                    worker_conn,
                    chat_id=chat_id,
                    contacts=self.state.contacts,
                    me_name=self.state.me_name,
                )
            finally:
                worker_conn.close()
        except Exception as e:
            # Without this catch, a worker exception is swallowed by
            # Textual's @work and the user is stranded on "Loading…"
            # forever. Surface it via HistoryLoadFailed instead.
            import traceback
            self.post_message(HistoryLoadFailed(
                chat_id, f"{type(e).__name__}: {e}", traceback.format_exc()
            ))
            return
        self.post_message(HistoryLoaded(chat_id, messages))

    def on_history_loaded(self, event: HistoryLoaded) -> None:
        if event.chat_id != self.state.selected_chat_id:
            return
        self.state.selected_chat_messages = [
            {"message_id": m.message_id, "timestamp": m.timestamp} for m in event.messages
        ]
        # Range marks from the previous chat don't apply to this one —
        # their message_ids belong to a different chat. Clear them
        # before any code path (StatusLine refresh, apply_marks, the
        # next click) tries to resolve them against the new
        # `selected_chat_messages` and crashes on list.index(...).
        if self.state.range_start_msg_id is not None or self.state.range_end_msg_id is not None:
            self.state.range_start_msg_id = None
            self.state.range_end_msg_id = None
            if self.state.window_source == "selection":
                self.state.window_source = "typed" if self.state.typed_window else "all"
        self.state.history_loading = False
        history = self.query_one(HistoryView)
        history.render_messages(event.messages)
        self._refresh_status()

    def on_history_load_failed(self, event: HistoryLoadFailed) -> None:
        # Only surface the latest failure; ignore stale workers' errors.
        if event.chat_id != self.state.selected_chat_id:
            return
        self.state.history_loading = False
        history = self.query_one(HistoryView)
        history.show_placeholder(
            f"Couldn't load chat {event.chat_id}: {event.summary}\n\n"
            "Try a different chat, or restart the app."
        )
        # Detail goes to the Textual log (visible via `textual console`).
        self.log.error(event.detail)
        self._refresh_status()

    # ------------------------------------------------------------------
    # Task 7: Range marks
    # ------------------------------------------------------------------

    def on_window_strip_window_changed(self, event) -> None:
        """User applied / cleared the inline date strip.

        - window=None  → clear typed_window; restore the full chat.
        - window=dict  → store as typed_window, demote any selection
          marks (they're chat-relative, not window-relative), and
          re-render the preview filtered to the new window.
        """
        from .widgets import WindowStrip
        history = self.query_one(HistoryView)
        if event.window is None:
            self.state.typed_window = None
            # Drop range marks too — they may have referenced messages
            # outside the previous filter; keeping them in "selection"
            # would silently re-filter the preview on next click.
            self.state.range_start_msg_id = None
            self.state.range_end_msg_id = None
            self.state.window_source = "all"
            history.filter_messages(None)
        else:
            self.state.typed_window = event.window
            self.state.window_source = "typed"
            self.state.range_start_msg_id = None
            self.state.range_end_msg_id = None
            history.filter_messages(event.window)
        self.state.last_export_status = None
        self._refresh_status()

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
        """Apply a click on `msg_id` to the range marks. Delegates to
        the pure helper `state.apply_click_mark`; this method only
        handles the UI side-effects (window_source, status refresh)
        and skips them on no-op clicks so a duplicate click doesn't
        flip the user out of, say, the typed-window source."""
        from .state import apply_click_mark
        if not apply_click_mark(self.state, msg_id):
            return
        self.state.window_source = "selection"
        self.state.last_export_status = None
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
            theme_override=self._defaults.theme_override if self._defaults else None,
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
        # Apply the picked theme live + remember it for persistence.
        if self._defaults is not None:
            self._defaults.theme_override = result["theme_override"]
        import os
        from ..theme import resolve_theme_name
        self.theme = resolve_theme_name(
            cli=getattr(self, "_cli_theme", None),
            env=os.environ.get("IMESSAGE_EXPORT_THEME"),
            persisted=result["theme_override"],
        )
        self._persist_defaults()
        self._refresh_status()

    def _persist_defaults(self) -> None:
        from ..defaults import Defaults, save as save_defaults
        save_defaults(Defaults(
            contacts_path=str(self.state.contacts_path) if self.state.contacts_path else None,
            output_dir=str(self.state.output_dir),
            me_name=self.state.me_name,
            last_chat_id=self.state.selected_chat_id,
            theme_override=self._defaults.theme_override if self._defaults else None,
        ))

    def on_descendant_focus(self, event) -> None:
        """Whenever focus changes, mark exactly one region as active."""
        from .widgets import Sidebar, HistoryView, ActionBar
        active_region = None
        w = event.widget
        while w is not None:
            if isinstance(w, (Sidebar, HistoryView, ActionBar)):
                active_region = w
                break
            w = w.parent
        for region_cls in (Sidebar, HistoryView, ActionBar):
            try:
                region = self.query_one(region_cls)
            except Exception:
                continue
            region.set_class(region is active_region, "region-active")

        # Update the status chip too.
        from .widgets import StatusLine
        tag = "sidebar"
        if active_region is not None:
            from .widgets import HistoryView, ActionBar
            if isinstance(active_region, HistoryView):
                tag = "history"
            elif isinstance(active_region, ActionBar):
                tag = "actions"
            else:
                tag = "sidebar"
        try:
            self.query_one(StatusLine).set_focus_region(tag)
        except Exception:
            pass

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
        # Prepend a warning when the user is about to export the entire
        # chat without any constraint. Gives them a chance to bail and
        # either type a window (`W`) or click two messages to define
        # an inline range — the two paths the redesign added — before
        # committing to a possibly massive export.
        if self.state.window_source == "all":
            summary = [
                "⚠  No window or selection set — this will export EVERY message.",
                "   Cancel to type a window (W) or click two messages to mark a range.",
                "",
            ] + summary
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
