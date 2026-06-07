"""Styled error panels used by the wizard and Rich-table dispatchers."""
from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel


def fda_denied(reason: str) -> None:
    console = Console(stderr=True)
    console.print(Panel.fit(
        f"[red]Full Disk Access denied.[/red]\n\n"
        f"[dim]{reason}[/dim]\n\n"
        f"Open System Settings ▸ Privacy & Security ▸ Full Disk Access.\n"
        f"Add [bold]{sys.executable}[/bold] (or your terminal app).\n"
        f"Quit and reopen the terminal so the new permission is picked up.",
        title="Cannot read chat.db",
        border_style="red",
    ))


def no_chats_match(query: str) -> None:
    console = Console(stderr=True)
    console.print(Panel.fit(
        f"No chats matched [bold]{query}[/bold].\n\n"
        f"Try [bold]imessage-export --list[/bold] to see all chats.",
        title="No match",
        border_style="yellow",
    ))


def contacts_malformed(path: str, row_num: int, detail: str) -> None:
    console = Console(stderr=True)
    console.print(Panel.fit(
        f"[red]contacts.csv could not be parsed.[/red]\n\n"
        f"File: [bold]{path}[/bold]\n"
        f"Row:  [bold]{row_num}[/bold]\n"
        f"Error: {detail}\n\n"
        f"Expected columns: [dim]handle,name[/dim] (see contacts.example.csv).",
        title="Malformed contacts file",
        border_style="red",
    ))
