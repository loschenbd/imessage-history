"""Interactive wizard for `imessage-export`.

Steps:
    welcome panel
    1. chat picker (type-to-filter)
    2. time window
    3. contacts file
    4. output directory
    5. me-name
    6. redaction (off / both / redacted-only + per-category PII toggles)
    7. confirm
    run → optional Markdown preview

Wizard internals are intentionally not unit-tested — Questionary mocking is
brittle. Verification is the manual smoke-test checklist in the plan.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Optional

import questionary
from rich.console import Console
from rich.panel import Panel

from ..db import chat_info, list_recent_chats, open_db
from .defaults import Defaults, load as load_defaults, save as save_defaults

console = Console()


def run() -> int:
    """Drive the wizard. Returns process exit code."""
    _welcome()
    defaults = load_defaults()

    try:
        conn = open_db(_default_db_path())
    except Exception as e:
        from .errors import fda_denied
        fda_denied(str(e))
        return 2

    try:
        chat_id = _step_pick_chat(conn, defaults)
        if chat_id is None:
            return 0

        # Pull a richer info bundle that includes msg_count + a folder-label
        # for the confirm panel. list_recent_chats already aggregates msg_count
        # so we re-use that row when we can find it.
        info = _enriched_chat_info(conn, chat_id)

        window = _step_pick_window(info)
        if window is None:
            return 0

        contacts = _step_contacts(defaults)
        output_dir = _step_output_dir(defaults)
        me_name = _step_me_name(defaults)

        redact_choices = _step_redact()
        if redact_choices is None:
            return 0

        if not _step_confirm(info, window, contacts, output_dir, me_name, redact_choices):
            console.print("[dim]Cancelled.[/dim]")
            return 0
    finally:
        conn.close()

    save_defaults(Defaults(
        contacts_path=str(contacts) if contacts else None,
        output_dir=str(output_dir),
        me_name=me_name,
        last_chat_id=chat_id,
    ))

    from ..cli import _run, DEFAULT_DB
    args = _build_args_namespace(
        chat_id=chat_id, window=window, contacts=contacts,
        output_dir=output_dir, me_name=me_name, db=DEFAULT_DB,
        redact_choices=redact_choices,
    )
    conn = open_db(Path(args.db))
    try:
        rc = _run(args, conn)
    finally:
        conn.close()

    if rc == 0:
        _maybe_show_preview(output_dir, info, window, redact_choices)
    return rc


# ──────────────────────────────────────────────────────────────────────────
# steps
# ──────────────────────────────────────────────────────────────────────────


def _welcome():
    console.print(Panel.fit(
        "[bold]imessage-export[/bold] — interactive mode\n"
        "[dim]Everything stays on this machine. No network calls.\n"
        "Run with --help to see the headless flag surface.[/dim]",
        border_style="cyan",
    ))


def _step_pick_chat(conn, defaults: Defaults) -> Optional[int]:
    """Type-to-filter chat picker. No pre-highlighted default."""
    rows = list_recent_chats(conn, 100)
    if not rows:
        console.print("[red]No chats found in chat.db.[/red]")
        return None

    choices = [
        questionary.Choice(_format_chat_row(r), value=r["chat_id"])
        for r in rows
    ]

    return questionary.select(
        "Which chat? (type to filter)",
        choices=choices,
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()


def _format_chat_row(r: dict) -> str:
    """Format one chat row for the picker."""
    who = (
        r.get("display_name")
        or r.get("participants")
        or r.get("chat_identifier")
        or "(unknown)"
    )
    kind = r.get("style", "")
    msgs = r.get("msg_count", "?")
    last = r.get("last_message_local") or "—"
    rid = r.get("chat_id", "?")
    return f"[{rid}] {who} · {kind} · {msgs} msgs · last {last}"


def _step_pick_window(info: dict):
    """Type-to-filter time window picker. No pre-highlighted default."""
    mode = questionary.select(
        "Time window? (type to filter)",
        choices=[
            questionary.Choice("Single day", value="day"),
            questionary.Choice("Date range", value="range"),
            questionary.Choice("Everything", value="all"),
        ],
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()
    if mode is None:
        return None

    today_str = date.today().isoformat()

    if mode == "day":
        d = questionary.text("Date (YYYY-MM-DD)", default=today_str).ask()
        if not d:
            return None
        start = questionary.text("Start time (HH:MM, optional)", default="").ask()
        end = questionary.text("End time (HH:MM, optional)", default="").ask()
        return {"mode": "day", "date": d, "start_time": start or None, "end_time": end or None}

    if mode == "range":
        f = questionary.text("From date (YYYY-MM-DD)").ask()
        t = questionary.text("To date (YYYY-MM-DD)", default=today_str).ask()
        if not f or not t:
            return None
        return {"mode": "range", "from_date": f, "to_date": t}

    msg_count = info.get("msg_count", 0)
    if msg_count and msg_count > 5000:
        ok = questionary.confirm(
            f"This chat has {msg_count} messages. Export everything?",
            default=False,
        ).ask()
        if not ok:
            return None
    return {"mode": "all"}


def _step_contacts(defaults: Defaults) -> Optional[Path]:
    default_value = defaults.contacts_path or (
        str(Path.cwd() / "contacts.csv") if (Path.cwd() / "contacts.csv").exists() else ""
    )
    raw = questionary.path(
        "Contacts file (empty = none)",
        default=default_value,
    ).ask()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.exists():
        console.print(f"[yellow]Note:[/yellow] {p} doesn't exist — proceeding without contacts.")
        return None
    return p


def _step_output_dir(defaults: Defaults) -> Path:
    raw = questionary.path(
        "Output directory",
        default=defaults.output_dir or str(Path.cwd() / "exports"),
    ).ask()
    return Path(raw).expanduser() if raw else Path.cwd() / "exports"


def _step_me_name(defaults: Defaults) -> str:
    raw = questionary.text(
        "Your name (label for messages you sent)",
        default=defaults.me_name or "Me",
    ).ask()
    return raw or "Me"


def _step_redact() -> Optional[dict]:
    """Seventh wizard step: redaction. None = cancel; {} = decline."""
    mode = questionary.select(
        "Redact identifiers and PII before writing? (type to filter)",
        choices=[
            questionary.Choice("No", value="off"),
            questionary.Choice("Yes — keep both originals and redacted files", value="redact"),
            questionary.Choice("Yes — redacted only (folder name is pseudonymized)", value="redact-only"),
        ],
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()
    if mode is None:
        return None
    if mode == "off":
        return {}

    extra_names_raw = questionary.path(
        "Extra names file (one name per line, empty to skip)",
        default="",
    ).ask()
    extra_names = Path(extra_names_raw).expanduser() if extra_names_raw else None

    categories = questionary.checkbox(
        "Scrub these from message bodies (Space to toggle, Enter to confirm)",
        choices=[
            questionary.Choice("Phones", value="phones", checked=True),
            questionary.Choice("Emails", value="emails", checked=True),
            questionary.Choice("URLs",   value="urls",   checked=True),
        ],
    ).ask()
    if categories is None:
        return None

    return {
        "redact": mode == "redact",
        "redact_only": mode == "redact-only",
        "redact_names_file": str(extra_names) if extra_names else None,
        "no_redact_phones": "phones" not in categories,
        "no_redact_emails": "emails" not in categories,
        "no_redact_urls":   "urls"   not in categories,
    }


def _step_confirm(info: dict, window, contacts, output_dir, me_name, redact_choices) -> bool:
    chat_label = info.get("label") or info.get("display_name") or info.get("chat_identifier") or "?"
    msg_count = info.get("msg_count", "?")
    summary = (
        f"[bold]Chat[/bold]: {chat_label}  ([dim]{msg_count} msgs[/dim])\n"
        f"[bold]Window[/bold]: {_window_summary(window)}\n"
        f"[bold]Contacts[/bold]: {contacts or '(none)'}\n"
        f"[bold]Output[/bold]: {output_dir}\n"
        f"[bold]Me[/bold]: {me_name}\n"
        f"[bold]Redact[/bold]: {_redact_summary(redact_choices)}"
    )
    console.print(Panel(summary, title="Confirm export", border_style="green"))
    return bool(questionary.confirm("Run export?", default=True).ask())


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _enriched_chat_info(conn, chat_id: int) -> dict:
    """Combine chat_info() with the matching list_recent_chats() row.

    `chat_info()` returns display_name/style/chat_identifier/is_group but no
    msg_count or last-seen. We look up the picker row by id (best-effort —
    chats past the 100-row cap fall back to bare chat_info) so the confirm
    panel can show "(N msgs)" and the "everything" branch can warn on huge
    chats.
    """
    base = dict(chat_info(conn, chat_id))
    base["chat_id"] = chat_id
    base["label"] = (
        base.get("display_name") or base.get("chat_identifier") or f"chat {chat_id}"
    )
    base["msg_count"] = 0
    for r in list_recent_chats(conn, 200):
        if r.get("chat_id") == chat_id:
            base["msg_count"] = r.get("msg_count", 0)
            base["participants"] = r.get("participants", "")
            label_from_row = (
                r.get("display_name") or r.get("participants") or r.get("chat_identifier")
            )
            if label_from_row:
                base["label"] = label_from_row
            break
    return base


def _redact_summary(choices: dict) -> str:
    if not choices or not (choices.get("redact") or choices.get("redact_only")):
        return "off"
    mode = "redacted only" if choices.get("redact_only") else "both versions"
    pii = []
    if not choices.get("no_redact_phones"): pii.append("phones")
    if not choices.get("no_redact_emails"): pii.append("emails")
    if not choices.get("no_redact_urls"):   pii.append("URLs")
    extra = " + names file" if choices.get("redact_names_file") else ""
    return f"{mode} (scrub {', '.join(pii) or 'identifiers only'}{extra})"


def _window_summary(w) -> str:
    if w["mode"] == "day":
        bits = [w["date"]]
        if w.get("start_time") or w.get("end_time"):
            bits.append(f"{w.get('start_time') or '00:00'}–{w.get('end_time') or '23:59'}")
        return "  ".join(bits)
    if w["mode"] == "range":
        return f"{w['from_date']} → {w['to_date']}"
    return "everything"


def _maybe_show_preview(output_dir: Path, info: dict, window, redact_choices):
    md_path = _resolve_output_md(output_dir, info, window, redact_choices)
    if not md_path or not md_path.exists():
        return
    label = "redacted Markdown" if redact_choices and (redact_choices.get("redact_only") or redact_choices.get("redact")) else "Markdown"
    if questionary.confirm(f"Preview {label}?", default=False).ask():
        from .preview import show_markdown
        show_markdown(md_path)


def _resolve_output_md(output_dir: Path, info: dict, window, redact_choices) -> Optional[Path]:
    if window["mode"] == "day":
        date_str = window["date"]
    elif window["mode"] == "range":
        date_str = window["from_date"]
    else:
        date_str = "all"

    if redact_choices and redact_choices.get("redact_only"):
        # Folder name is pseudonymized + hash-suffixed; we can't reconstruct
        # it without re-running redaction, so glob by date and take the newest.
        candidates = list(output_dir.glob(f"*/{date_str}/conversation*.md"))
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    from ..writers import slugify
    label = info.get("label") or info.get("display_name") or info.get("chat_identifier") or "unknown"
    base = output_dir / slugify(label) / date_str
    if redact_choices and redact_choices.get("redact"):
        return base / "conversation_redacted.md"
    return base / "conversation.md"


def _default_db_path() -> Path:
    from ..cli import DEFAULT_DB
    return DEFAULT_DB


def _build_args_namespace(*, chat_id, window, contacts, output_dir, me_name, db, redact_choices=None) -> argparse.Namespace:
    """Build an argparse.Namespace matching every attr cli._run() reads."""
    redact_choices = redact_choices or {}
    ns = argparse.Namespace(
        chat_id=chat_id, chat_identifier=None, participant=None,
        list=False, list_limit=30, list_contacts=False,
        from_date=window.get("from_date"), to_date=window.get("to_date"),
        date=window.get("date"),
        start_time=window.get("start_time"), end_time=window.get("end_time"),
        start_datetime=None, end_datetime=None,
        output_dir=str(output_dir),
        me_name=me_name,
        contacts=str(contacts) if contacts else None,
        include_attachments=False,
        limit=None,
        db=str(db),
        # Redaction.
        redact=redact_choices.get("redact", False),
        redact_only=redact_choices.get("redact_only", False),
        redact_names_file=redact_choices.get("redact_names_file"),
        no_redact_phones=redact_choices.get("no_redact_phones", False),
        no_redact_emails=redact_choices.get("no_redact_emails", False),
        no_redact_urls=redact_choices.get("no_redact_urls", False),
        suggest_names=False,
    )
    return ns
