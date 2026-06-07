"""Styled error panels used by the wizard and Rich-table dispatchers."""
from __future__ import annotations

import sys

from rich.panel import Panel

from .theme import get_console


def fda_denied(reason: str) -> None:
    console = get_console()
    console.print(Panel.fit(
        f"[error]Full Disk Access denied.[/error]\n\n"
        f"[muted]{reason}[/muted]\n\n"
        f"Open System Settings ▸ Privacy & Security ▸ Full Disk Access.\n"
        f"Add [bold]{sys.executable}[/bold] (or your terminal app).\n"
        f"Quit and reopen the terminal so the new permission is picked up.",
        title="Cannot read chat.db",
        border_style="error",
    ), file=sys.stderr)


def no_chats_match(query: str) -> None:
    console = get_console()
    console.print(Panel.fit(
        f"No chats matched [bold]{query}[/bold].\n\n"
        f"Try [bold]imessage-export --list[/bold] to see all chats.",
        title="No match",
        border_style="warning",
    ), file=sys.stderr)


def contacts_malformed(path: str, row_num: int, detail: str) -> None:
    console = get_console()
    console.print(Panel.fit(
        f"[error]contacts.csv could not be parsed.[/error]\n\n"
        f"File: [bold]{path}[/bold]\n"
        f"Row:  [bold]{row_num}[/bold]\n"
        f"Error: {detail}\n\n"
        f"Expected columns: [muted]handle,name[/muted] (see contacts.example.csv).",
        title="Malformed contacts file",
        border_style="error",
    ), file=sys.stderr)
