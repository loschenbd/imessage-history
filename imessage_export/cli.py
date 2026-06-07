"""Command-line interface.

`build_parser()` wires up argparse; `validate_args()` enforces the
cross-flag invariants argparse can't (e.g. --start-time requires
--date). `main()` is the entry point used by `python -m
imessage_export` and the `imessage-export` console script; `_run()`
is the workhorse that runs after `chat.db` is open.

Most of the heavy lifting lives in the other modules — `cli.py` is the
glue that wires argparse onto `open_db`, `resolve_chat_ids`,
`load_contacts`, `resolve_window`, `export`, the writers, and
(optionally) the `Redactor`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from .contacts import load_contacts
from .db import chat_label, list_recent_chats, list_contacts_csv, open_db, resolve_chat_ids
from .export import export
from .models import Message
from .redactor import RedactionConfig, Redactor, suggest_names
from .timestamps import detect_date_unit
from .window import resolve_window
from .writers import (
    slugify,
    write_ai_ready,
    write_csv,
    write_json,
    write_markdown,
    write_prompt,
    write_txt,
)


DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export one iMessage conversation to AI-ready files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_argument_group("source selection (choose one)")
    src.add_argument("--chat-id", type=int, help="Numeric chat.ROWID")
    src.add_argument("--chat-identifier", help="chat.chat_identifier or chat.guid")
    src.add_argument("--participant", help="Substring match against handle.id, "
                                           "chat.display_name, or chat.chat_identifier")
    src.add_argument("--list", action="store_true", help="List recent chats and exit")
    src.add_argument("--list-limit", type=int, default=30)
    src.add_argument("--list-contacts", action="store_true",
                     help="Print every distinct handle in chat.db as a starter "
                          "contacts.csv (handle,name) and exit. Use to bootstrap "
                          "your contacts.csv.")

    win = p.add_argument_group("time window (local time; all bounds optional)")
    win.add_argument("--from-date", help="YYYY-MM-DD (inclusive)")
    win.add_argument("--to-date", help="YYYY-MM-DD (inclusive)")
    win.add_argument("--date", help="YYYY-MM-DD — single day; combine with --start-time/--end-time")
    win.add_argument("--start-time", help="HH:MM or HH:MM:SS (requires --date)")
    win.add_argument("--end-time", help="HH:MM or HH:MM:SS (requires --date)")
    win.add_argument("--start-datetime", help="YYYY-MM-DD HH:MM[:SS]")
    win.add_argument("--end-datetime", help="YYYY-MM-DD HH:MM[:SS]")

    out = p.add_argument_group("output / formatting")
    out.add_argument("--output-dir", default="./exports", help="Default: ./exports")
    out.add_argument("--me-name", default="Me", help="Label for messages where is_from_me=1")
    out.add_argument("--contacts", help="CSV with columns: handle,name "
                                        "(maps handle.id → human name)")
    out.add_argument("--include-attachments", action="store_true",
                     help="Resolve attachment filenames per message")
    out.add_argument("--limit", type=int, help="Cap number of messages")

    macos = p.add_argument_group("macOS Contacts integration")
    macos.add_argument(
        "--build-contacts",
        nargs="?",
        const="contacts.csv",
        default=None,
        metavar="PATH",
        help="Read macOS Contacts.app via AppleScript and write a "
             "handle,name CSV (default: contacts.csv). Skips export.",
    )

    tui = p.add_argument_group("interactive mode")
    tui.add_argument(
        "--wizard",
        action="store_true",
        help="Use the linear Questionary wizard instead of the Textual app.",
    )
    tui.add_argument(
        "--app",
        action="store_true",
        help="Force the Textual app even when other flags are present.",
    )

    red = p.add_argument_group("redaction / pseudonymization")
    red.add_argument("--redact", action="store_true",
                     help="Also write a parallel set of redacted files "
                          "(conversation_redacted.* + pseudonym_map.json).")
    red.add_argument("--redact-only", action="store_true",
                     help="Write ONLY the redacted set. Folder name uses the "
                          "pseudonymized label + a stable 4-char chat-id hash.")
    red.add_argument("--redact-names-file", default=None,
                     help="Flat text file, one extra name per line. All "
                          "pseudonymized into the same Person X namespace.")
    red.add_argument("--no-redact-phones", action="store_true",
                     help="Disable phone-number scrubbing in body text.")
    red.add_argument("--no-redact-emails", action="store_true",
                     help="Disable email-address scrubbing in body text.")
    red.add_argument("--no-redact-urls", action="store_true",
                     help="Disable URL scrubbing in body text.")
    red.add_argument("--suggest-names", action="store_true",
                     help="Scan the selected window for proper-noun "
                          "candidates and print them. Skips export.")

    p.add_argument("--db", default=str(DEFAULT_DB),
                   help=f"Path to chat.db (default: {DEFAULT_DB})")
    return p


def validate_args(args):
    if args.start_time or args.end_time:
        if not args.date and not (args.start_datetime or args.end_datetime):
            raise SystemExit("--start-time / --end-time require --date")
    if args.date and (args.start_datetime or args.end_datetime):
        raise SystemExit("Use either --date+--start-time/--end-time OR "
                         "--start-datetime/--end-datetime, not both.")
    if args.suggest_names and (args.redact or args.redact_only):
        raise SystemExit("--suggest-names cannot be combined with --redact / --redact-only")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args)
    # Exported message bodies are private. Make new dirs 700 and new files 600
    # by default — protects against group/other read on any filesystem that
    # honors POSIX modes (most local disks; some network shares ignore it).
    os.umask(0o077)
    return _dispatch(args, argv if argv is not None else sys.argv[1:], parser)


def _dispatch(args, argv, parser) -> int:
    no_explicit_args = (argv == [] or argv == ())
    is_tty = sys.stdout.isatty() and sys.stderr.isatty()
    is_ci = bool(os.environ.get("CI") or os.environ.get("NONINTERACTIVE"))
    has_action_flag = bool(
        args.list or args.list_contacts or args.chat_id or args.chat_identifier
        or args.participant or getattr(args, "from_date", None) or getattr(args, "to_date", None)
        or args.date or args.build_contacts
    )

    if args.app:
        return _run_app()

    if args.wizard and not has_action_flag:
        return _run_wizard()

    if no_explicit_args and is_tty and not is_ci and not has_action_flag:
        return _run_app()

    if args.build_contacts is not None:
        from .contacts_macos import build_contacts_csv
        return build_contacts_csv(Path(args.build_contacts))

    if args.list and is_tty and not is_ci:
        return _list_with_rich_table(args)
    if args.list_contacts and is_tty and not is_ci:
        return _list_contacts_with_rich_table(args)

    if no_explicit_args:
        parser.print_help()
        return 2

    try:
        conn = open_db(Path(args.db))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    try:
        return _run(args, conn)
    finally:
        conn.close()


def _run_app() -> int:
    """Enter the Textual app. Requires the [tui] extra."""
    try:
        from .tui.app import run as run_app
    except ImportError:
        print(
            "imessage-export: interactive mode needs the [tui] extra.\n"
            "  pip install 'imessage-history[tui]'\n"
            "Or run headless:  imessage-export --list",
            file=sys.stderr,
        )
        return 2
    return run_app()


def _run_wizard() -> int:
    try:
        from .tui.wizard import run as run_wizard
    except ImportError:
        print(_TUI_MISSING_MSG, file=sys.stderr)
        return 2
    return run_wizard()


def _list_with_rich_table(args) -> int:
    try:
        from .tui.tables import list_chats as tui_list
    except ImportError:
        print(_TUI_MISSING_MSG, file=sys.stderr)
        return 2
    return tui_list(args)


def _list_contacts_with_rich_table(args) -> int:
    try:
        from .tui.tables import list_contacts as tui_list_contacts
    except ImportError:
        print(_TUI_MISSING_MSG, file=sys.stderr)
        return 2
    return tui_list_contacts(args)


_TUI_MISSING_MSG = (
    "imessage-export: interactive mode needs the [tui] extra.\n"
    "  pip install 'imessage-history[tui]'\n"
    "Or run headless:  imessage-export --list"
)


def _run(args, conn) -> int:
    unit = detect_date_unit(conn)

    if args.list:
        chats = list_recent_chats(conn, args.list_limit)
        if not chats:
            print("(no chats found)")
            return 0
        print(f"{'ID':>5}  {'KIND':<5}  {'LAST':<16}  {'MSGS':>5}  IDENTIFIER / PARTICIPANTS")
        for c in chats:
            who = c["display_name"] or c["participants"] or c["chat_identifier"] or ""
            print(f"{c['chat_id']:>5}  {c['style']:<5}  {c['last_message_local']:<16}  "
                  f"{c['msg_count']:>5}  {c['chat_identifier'] or ''}  |  {who}")
        return 0

    if args.list_contacts:
        return list_contacts_csv(conn, unit, Path(args.contacts) if args.contacts else None)

    if args.suggest_names:
        chat_ids = resolve_chat_ids(
            conn,
            chat_id=args.chat_id,
            chat_identifier=args.chat_identifier,
            participant=args.participant,
        )
        if not chat_ids:
            print("ERROR: no matching chat found.", file=sys.stderr)
            return 1
        contacts = load_contacts(Path(args.contacts)) if args.contacts else {}
        window = resolve_window(args, unit)
        messages, _ = export(
            conn,
            chat_ids=chat_ids,
            contacts=contacts,
            me_name=args.me_name,
            window=window,
            limit=args.limit,
            include_attachments=False,
            unit=unit,
        )
        return suggest_names(messages, contacts)

    if not (args.chat_id or args.chat_identifier or args.participant):
        print("ERROR: choose --chat-id, --chat-identifier, --participant, --list, "
              "or --list-contacts",
              file=sys.stderr)
        return 2

    chat_ids = resolve_chat_ids(
        conn,
        chat_id=args.chat_id,
        chat_identifier=args.chat_identifier,
        participant=args.participant,
    )
    if not chat_ids:
        print("ERROR: no matching chat found.", file=sys.stderr)
        return 1
    if len(chat_ids) > 1 and not args.participant:
        print(f"NOTE: identifier matched {len(chat_ids)} chat rows — exporting all.",
              file=sys.stderr)

    contacts = load_contacts(Path(args.contacts)) if args.contacts else {}
    window = resolve_window(args, unit)

    messages, metadata = export(
        conn,
        chat_ids=chat_ids,
        contacts=contacts,
        me_name=args.me_name,
        window=window,
        limit=args.limit,
        include_attachments=args.include_attachments,
        unit=unit,
    )

    # Build the redactor if asked. Both --redact and --redact-only enable it.
    redactor = None
    red_messages: list[Message] | None = None
    red_metadata: dict | None = None
    if args.redact or args.redact_only:
        extra_names = []
        if args.redact_names_file:
            try:
                with open(args.redact_names_file) as f:
                    extra_names = [ln.strip() for ln in f
                                   if ln.strip() and not ln.lstrip().startswith("#")]
            except OSError as e:
                raise SystemExit(f"Cannot read --redact-names-file {args.redact_names_file}: {e}")
        rcfg = RedactionConfig(
            me_name=args.me_name,
            extra_names=extra_names,
            redact_phones=not args.no_redact_phones,
            redact_emails=not args.no_redact_emails,
            redact_urls=not args.no_redact_urls,
        )
        redactor     = Redactor(messages, metadata, contacts, rcfg)
        red_messages = redactor.redact_messages()
        red_metadata = redactor.redact_metadata()

    # Output directory: exports/<label>/<YYYY-MM-DD>/.
    # Date = window start if a window was given, else the actual first message's
    # date, else today. Re-exporting the same (label, date) overwrites.
    # In --redact-only mode, folder uses the pseudonymized label + a stable
    # 4-char hash of chat_ids so distinct chats don't collide on "Person B".
    # slugify() would mash the space into a dash; for the redacted folder name
    # we bypass it so "Person B-a3f9" stays readable.
    win = metadata["window"]
    date_str = (
        (win.get("local_start") or "")[:10]
        or (metadata.get("actual_first_local") or "")[:10]
        or datetime.now().strftime("%Y-%m-%d")
    )
    if args.redact_only and redactor is not None:
        chat_ids_str = ",".join(str(c) for c in metadata["chat_ids"])
        chash = hashlib.sha1(chat_ids_str.encode()).hexdigest()[:4]
        base = redactor.chat_label()
        # Permit "Person X" verbatim (1:1 chat with one pseudonym). Permit
        # "Person A+Person B+..." (group with joined pseudonyms). Otherwise
        # — e.g., a raw group display_name slipped through — slugify for
        # filesystem safety, and strip leading dots so traversal-style
        # prefixes ("../") can't survive as a leading "..-" segment.
        if not re.match(r"^Person [A-Z]+(\+Person [A-Z]+)*$", base):
            base = slugify(base).lstrip(".")
            if not base:
                base = "chat"
        folder_name = f"{base}-{chash}"
    else:
        folder_name = slugify(chat_label(metadata))
    out_dir = Path(args.output_dir) / folder_name / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write the originals UNLESS in --redact-only mode.
    if not args.redact_only:
        write_csv      (out_dir / "conversation.csv",          messages)
        write_json     (out_dir / "conversation.json",         messages, metadata)
        write_txt      (out_dir / "conversation.txt",          messages)
        write_markdown (out_dir / "conversation.md",           messages, metadata)
        write_ai_ready (out_dir / "conversation_ai_ready.txt", messages, metadata)

    # Write the redacted set if we built a redactor. We keep the "_redacted"
    # suffix even in --redact-only mode so the file names always signal that
    # the contents have been pseudonymized.
    if redactor is not None:
        write_csv      (out_dir / "conversation_redacted.csv",          red_messages)
        write_json     (out_dir / "conversation_redacted.json",         red_messages, red_metadata)
        write_txt      (out_dir / "conversation_redacted.txt",          red_messages)
        write_markdown (out_dir / "conversation_redacted.md",           red_messages, red_metadata)
        write_ai_ready (out_dir / "conversation_redacted_ai_ready.txt", red_messages, red_metadata)
        with open(out_dir / "pseudonym_map.json", "w") as f:
            json.dump(redactor.pseudonym_map(), f, indent=2, ensure_ascii=False)

    write_prompt(out_dir / "analysis_prompt.txt")

    print(f"Exported {len(messages)} messages → {out_dir}")
    if win["local_start"] or win["local_end"]:
        print(f"Window (local, {win['tz']}): {win['local_start']} → {win['local_end']}")
        print(f"Window (UTC):                 {win['utc_start']} → {win['utc_end']}")
    return 0
