"""Rich-styled tables for --list and --list-contacts (TTY only)."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..db import list_contacts_csv, list_recent_chats, open_db
from ..timestamps import detect_date_unit


def list_chats(args) -> int:
    """Render `--list` output as a Rich table.

    Emphasizes Participants (bold) and Last (cyan) per UX spec — those are the
    columns the user scans first when picking a chat.
    """
    console = Console()
    try:
        conn = open_db(Path(args.db))
    except Exception as e:
        console.print(f"[red]ERROR:[/red] {e}")
        return 1
    try:
        limit = getattr(args, "list_limit", 30) or 30
        chats = list_recent_chats(conn, limit)
    finally:
        conn.close()

    if not chats:
        console.print("[dim](no chats found)[/dim]")
        return 0

    table = Table(title="Recent chats", show_lines=False, header_style="bold")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Kind", style="dim")
    table.add_column("Participants", style="bold")  # emphasized
    table.add_column("Msgs", justify="right", style="dim")
    table.add_column("Last", style="cyan")  # emphasized

    for c in chats:
        who = (
            c.get("display_name")
            or c.get("participants")
            or c.get("chat_identifier")
            or ""
        )
        identifier = c.get("chat_identifier") or ""
        if identifier and who and identifier != who:
            label = f"{who}  ·  {identifier}"
        else:
            label = who or identifier
        table.add_row(
            str(c.get("chat_id", "?")),
            str(c.get("style", "")),
            label,
            str(c.get("msg_count", "?")),
            c.get("last_message_local") or "—",
        )

    console.print(table)
    console.print(
        f"  [dim]Showing {len(chats)} of {limit} "
        f"— use --list-limit to change.[/dim]"
    )
    return 0


def list_contacts(args) -> int:
    """Delegate to the existing stdlib list_contacts_csv for Phase 1.

    A dedicated Rich-styled contacts table is Phase 2 polish; for now we just
    surface the same handle/name dump as the headless path so the user can
    still bootstrap their contacts.csv from the wizard's --list-contacts.
    """
    console = Console()
    try:
        conn = open_db(Path(args.db))
    except Exception as e:
        console.print(f"[red]ERROR:[/red] {e}")
        return 1
    try:
        unit = detect_date_unit(conn)
        return list_contacts_csv(
            conn,
            unit,
            Path(args.contacts) if getattr(args, "contacts", None) else None,
        )
    finally:
        conn.close()
