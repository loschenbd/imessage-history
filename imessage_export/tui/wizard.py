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
from rich.panel import Panel

from ..db import chat_info, list_recent_chats, open_db
from .defaults import Defaults, load as load_defaults, save as save_defaults

# Themed Rich console; resolved lazily so headless paths never pay for Rich.
from .theme import get_console as _get_console

def _console():
    return _get_console()

TOTAL_STEPS = 7


def _step_banner(n: int, label: str):
    """Print a subtle step indicator above each prompt."""
    _console().print(
        f"\n[dim]──[/dim] [bold accent]Step {n}/{TOTAL_STEPS}[/bold accent]  "
        f"{label}  [dim]──[/dim]"
    )


def run() -> int:
    """Drive the wizard. Returns process exit code."""
    defaults = load_defaults()
    _welcome()
    contacts_for_picker = _load_contacts_for_picker(defaults)
    if not contacts_for_picker:
        scanned_path = _step_offer_build_contacts()
        if scanned_path is not None:
            try:
                from ..contacts import load_contacts
                contacts_for_picker = load_contacts(scanned_path)
                defaults.contacts_path = str(scanned_path)
            except Exception:
                contacts_for_picker = {}

    try:
        conn = open_db(_default_db_path())
    except Exception as e:
        from .errors import fda_denied
        fda_denied(str(e))
        return 2

    try:
        chat_id, picker_row = _step_pick_chat(conn, defaults, contacts_for_picker)
        if chat_id is None:
            return 0

        # Pull a richer info bundle that includes msg_count + a folder-label
        # for the confirm panel. Re-use the picker's already-aggregated row
        # instead of re-scanning every chat.
        info = _enriched_chat_info(conn, chat_id, picker_row)

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
            _console().print("[dim]Cancelled.[/dim]")
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
        # cli._run prints per-phase progress lines (load / redact / per-file
        # writer ticks) so the user can see big exports moving. Wrapping that
        # in console.status would hide the ticks behind a spinner.
        rc = _run(args, conn)
    finally:
        conn.close()

    if rc == 0:
        _console().print("\n[bold success]✓[/bold success] Export complete.")
        _maybe_show_preview(output_dir, info, window, redact_choices)
    return rc


# ──────────────────────────────────────────────────────────────────────────
# steps
# ──────────────────────────────────────────────────────────────────────────


def _welcome():
    _console().print()
    _console().print(Panel(
        "[bold accent]imessage-export[/bold accent]  [dim]·[/dim]  interactive mode\n\n"
        "Export a single conversation from your local Messages database\n"
        "into AI-ready files. [bold]Everything stays on this machine.[/bold]\n\n"
        "[dim]Press Ctrl+C at any prompt to cancel.\n"
        "Run with --help to see the headless flag surface.[/dim]",
        title="[bold]Welcome[/bold]",
        title_align="left",
        border_style="accent",
        padding=(1, 2),
    ))


def _step_offer_build_contacts() -> Optional[Path]:
    """Offer to scan macOS Contacts.app and write contacts.csv. Returns the
    written path on success, None if the user declines or the scan fails."""
    _console().print()
    _console().print(Panel(
        "[warning]No contacts file found.[/warning]\n\n"
        "You can populate one in seconds by scanning macOS Contacts.\n"
        "First scan triggers a one-time Contacts permission prompt.\n\n"
        "[dim]This writes [bold]contacts.csv[/bold] in the current directory.[/dim]",
        title="[bold]Set up contacts[/bold]",
        title_align="left",
        border_style="warning",
        padding=(1, 2),
    ))
    answer = questionary.confirm(
        "Scan macOS Contacts now? (recommended)",
        default=True,
    ).ask()
    if not answer:
        return None

    target = Path.cwd() / "contacts.csv"
    from ..contacts_macos import fetch_contacts, write_csv
    try:
        with _console().status("[bold accent]Reading Contacts.app… (up to 5 min on large books)[/bold accent]", spinner="dots"):
            rows = fetch_contacts()
    except RuntimeError as e:
        _console().print(f"[error]Could not read Contacts:[/error] {e}")
        return None

    if not rows:
        _console().print("[warning]No contacts found in Contacts.app — proceeding without.[/warning]")
        return None

    count = write_csv(rows, target)
    _console().print(f"[bold success]✓[/bold success] Wrote {count} contacts → [bold]{target}[/bold]")
    return target


def _load_contacts_for_picker(defaults) -> dict:
    """Try to load contacts from defaults.contacts_path or ./contacts.csv.
    Returns {} on any failure — picker degrades to raw handles gracefully."""
    from pathlib import Path as _P
    from ..contacts import load_contacts
    candidate = None
    if defaults.contacts_path and _P(defaults.contacts_path).exists():
        candidate = _P(defaults.contacts_path)
    elif (_P.cwd() / "contacts.csv").exists():
        candidate = _P.cwd() / "contacts.csv"
    if candidate is None:
        return {}
    try:
        return load_contacts(candidate)
    except Exception:
        return {}


def _step_pick_chat(conn, defaults: Defaults, contacts: dict) -> tuple[Optional[int], Optional[dict]]:
    """Type-to-filter chat picker. No pre-highlighted default.

    Returns (chat_id, picker_row). The row is the already-aggregated record
    the picker saw, so the caller can build the confirm panel without a
    second full scan of `chat`+`message`.
    """
    _step_banner(1, "Pick a chat")
    # Fetch every chat so the user can type-to-filter across the full
    # history, not just the most recent few hundred.
    rows = list_recent_chats(conn, None)
    if not rows:
        _console().print("[error]No chats found in chat.db.[/error]")
        return None, None

    choices = [
        questionary.Choice(_format_chat_row(r, contacts), value=r["chat_id"])
        for r in rows
    ]

    chat_id = questionary.select(
        "Which chat? (type to filter)",
        choices=choices,
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()
    if chat_id is None:
        return None, None
    row = next((r for r in rows if r.get("chat_id") == chat_id), None)
    return chat_id, row


def _format_chat_row(r: dict, contacts: dict, *, include_id: bool = True) -> str:
    """Format one chat row for the picker.

    `include_id=False` drops the leading `[chat_id]` token — the TUI sidebar
    uses this since the id is a debug crutch the user doesn't filter or read
    by. The wizard's questionary picker keeps it so curious users can still
    cross-reference against `--list` output.
    """
    raw_who = (
        r.get("display_name")
        or r.get("participants")
        or r.get("chat_identifier")
        or "(unknown)"
    )
    # Resolve handles via contacts. `raw_who` may be a single handle or a
    # comma-joined list of handles (group chat).
    who = _resolve_names(raw_who, contacts) if contacts else raw_who
    kind = r.get("style", "")
    msgs = r.get("msg_count", "?")
    last = r.get("last_message_local") or "—"
    body = f"{who} · {kind} · {msgs} msgs · last {last}"
    if not include_id:
        return body
    rid = r.get("chat_id", "?")
    return f"[{rid}] {body}"


def _resolve_names(raw: str, contacts: dict) -> str:
    """Render a handle (or comma-joined handle list) as "Name (handle)" pairs.

    Falls back to bare handle when no contact match. Both the name AND the
    handle stay in the visible string so questionary's type-to-filter works
    against either. Dedupes by name when multiple handles resolve to the
    same person (e.g. email + phone for the same contact)."""
    from ..contacts import normalize_handle

    pairs: list[tuple[str, list[str]]] = []  # [(name_or_handle, [handles])]
    seen_keys = set()

    for piece in raw.split(","):
        h = piece.strip()
        if not h:
            continue
        key = normalize_handle(h)
        name = contacts.get(key) or contacts.get(h)
        if name:
            dedupe_key = ("name", name)
            if dedupe_key in seen_keys:
                for entry in pairs:
                    if entry[0] == name:
                        if h not in entry[1]:
                            entry[1].append(h)
                        break
            else:
                seen_keys.add(dedupe_key)
                pairs.append((name, [h]))
        else:
            dedupe_key = ("handle", key or h)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            pairs.append((h, []))

    parts = []
    for label, handles in pairs:
        if handles:
            parts.append(f"{label} ({', '.join(handles)})")
        else:
            parts.append(label)
    return ", ".join(parts)


def _step_pick_window(info: dict):
    """Type-to-filter time window picker. No pre-highlighted default."""
    _step_banner(2, "Choose a time window")
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

        preset = questionary.select(
            "Time of day? (type to filter)",
            choices=[
                questionary.Choice("All day (no time filter)",            value="all"),
                questionary.Choice("Morning   (12:00 AM – 12:00 PM)",     value="morning"),
                questionary.Choice("Afternoon (12:00 PM – 6:00 PM)",      value="afternoon"),
                questionary.Choice("Evening   (6:00 PM – 12:00 AM)",      value="evening"),
                questionary.Choice("Custom range",                        value="custom"),
            ],
            use_search_filter=True,
            use_jk_keys=False,
        ).ask()
        if preset is None:
            return None

        if preset == "all":
            start_time = None
            end_time   = None
        elif preset == "morning":
            start_time, end_time = "00:00", "12:00"
        elif preset == "afternoon":
            start_time, end_time = "12:00", "18:00"
        elif preset == "evening":
            start_time, end_time = "18:00", "23:59"
        else:  # custom
            start_time = _ask_time("Start time", "9am")
            if start_time is None:
                return None
            end_time = _ask_time("End time", "5pm")
            if end_time is None:
                return None

        return {"mode": "day", "date": d, "start_time": start_time, "end_time": end_time}

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


def _ask_time(label: str, example: str) -> Optional[str]:
    """Ask for a time. Accepts 9am, 12pm, 14:30, noon, midnight. Loops on parse error."""
    from ..window import parse_time_12h
    while True:
        raw = questionary.text(f"{label} (e.g. {example})").ask()
        if raw is None:
            return None
        if not raw.strip():
            return None  # treat empty as no bound
        try:
            return parse_time_12h(raw)
        except ValueError:
            _console().print(f"[warning]Didn't understand {raw!r} — try '9am' or '14:30'.[/warning]")


def _step_contacts(defaults: Defaults) -> Optional[Path]:
    _step_banner(3, "Contacts file")
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
        _console().print(f"[warning]Note:[/warning] {p} doesn't exist — proceeding without contacts.")
        return None
    return p


def _step_output_dir(defaults: Defaults) -> Path:
    _step_banner(4, "Output directory")
    raw = questionary.path(
        "Output directory",
        default=defaults.output_dir or str(Path.cwd() / "exports"),
    ).ask()
    return Path(raw).expanduser() if raw else Path.cwd() / "exports"


def _step_me_name(defaults: Defaults) -> str:
    _step_banner(5, "Your name")
    raw = questionary.text(
        "Your name (label for messages you sent)",
        default=defaults.me_name or "Me",
    ).ask()
    return raw or "Me"


def _step_redact() -> Optional[dict]:
    """Seventh wizard step: redaction. None = cancel; {} = decline."""
    _step_banner(6, "Redaction")
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
    _step_banner(7, "Confirm and run")
    from rich.table import Table

    chat_label = info.get("label") or info.get("display_name") or info.get("chat_identifier") or "?"
    msg_count = info.get("msg_count", 0)

    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold accent", justify="right")
    t.add_column()
    t.add_row("Chat",     f"{chat_label}  [dim]({msg_count} msgs)[/dim]")
    t.add_row("Window",   _window_summary(window))
    t.add_row("Contacts", str(contacts) if contacts else "[dim](none)[/dim]")
    t.add_row("Output",   f"[underline]{output_dir}[/underline]")
    t.add_row("Me",       me_name)
    t.add_row("Redact",   _redact_summary(redact_choices))

    _console().print(Panel(
        t,
        title="[bold]Confirm export[/bold]",
        title_align="left",
        border_style="success",
        padding=(1, 2),
    ))
    return bool(questionary.confirm("Run export?", default=True).ask())


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _enriched_chat_info(conn, chat_id: int, picker_row: Optional[dict] = None) -> dict:
    """Combine chat_info() with the picker's already-aggregated row.

    `chat_info()` returns display_name/style/chat_identifier/is_group but no
    msg_count or last-seen. `picker_row` (when provided) carries the
    pre-aggregated msg_count + participants the picker already computed, so
    we avoid scanning the full `chat`+`message` join a second time.
    """
    base = dict(chat_info(conn, chat_id))
    base["chat_id"] = chat_id
    base["label"] = (
        base.get("display_name") or base.get("chat_identifier") or f"chat {chat_id}"
    )
    base["msg_count"] = 0
    if picker_row:
        base["msg_count"] = picker_row.get("msg_count", 0)
        base["participants"] = picker_row.get("participants", "")
        label_from_row = (
            picker_row.get("display_name")
            or picker_row.get("participants")
            or picker_row.get("chat_identifier")
        )
        if label_from_row:
            base["label"] = label_from_row
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
