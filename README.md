# imessage-history

[![tests](https://img.shields.io/github/actions/workflow/status/loschenbd/imessage-history/test.yml?branch=main&label=tests)](https://github.com/loschenbd/imessage-history/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Stdlib only](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen.svg)](pyproject.toml)

Export a single iMessage conversation from the local macOS Messages database into
AI-ready files with explicit speaker attribution on every line.

- Reads `~/Library/Messages/chat.db` (SQLite) directly, **read-only**.
- One Python script, **stdlib only** (Python 3.10+).
- Produces CSV, JSON, TXT, Markdown, an AI-optimized TXT, and a prompt template.
- Supports exact intra-day time windows (`--date + --start-time/--end-time` and
  `--start-datetime/--end-datetime`) in **local time**, converted to Apple's
  2001-epoch units. The resolved window is included in `conversation.json`'s
  `metadata` block and in the AI-ready header.

## Privacy & scope

This tool reads your private message history. Before running it, know exactly
what it does and doesn't do:

- **Everything stays on your machine.** The script makes **zero network calls**.
  It uses only Python's standard library. There is no telemetry, no analytics,
  no auto-update.
- **It cannot write to `chat.db`.** The database is opened with SQLite's
  `mode=ro&immutable=1` URI flags **and** `PRAGMA query_only = ON`, with a
  pre-flight assertion that the read-only PRAGMA actually took. A regression
  test under `tests/` proves every write statement (DELETE/UPDATE/INSERT/
  CREATE/DROP/ALTER/REPLACE) raises and the DB file is byte-for-byte
  unchanged after close. If any of these guards regress, the script refuses
  to run.
- **Exports contain private message bodies.** Treat anything under `exports/`
  as sensitive — the same as a `.env` file. The default `.gitignore` keeps it
  out of git; the script's `umask(0o077)` keeps it `600`/`700` on disk.
- **`contacts.csv` is your private mapping.** Phone numbers and names. Also
  gitignored. Use `contacts.example.csv` as the shape reference.
- **Pasting outputs into a hosted LLM sends that conversation to a third
  party.** If you do this, you've moved the data off your machine. For
  sensitive threads, run a local model (Ollama, LM Studio) instead.
- **Reporting bugs?** Don't attach real exports to GitHub issues. Reproduce
  with a synthetic / heavily redacted fixture, or describe the failure mode.

If any of the above is a problem for your use case, this is the wrong tool.

## 1. Grant Full Disk Access (one-time)

macOS protects `~/Library/Messages/chat.db`. The process that runs Python needs
Full Disk Access:

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Click **+** and add the terminal you'll run from (e.g. **Terminal.app**,
   **iTerm**, **Ghostty**, **Warp**). Add `python3` from `/opt/homebrew/bin/`
   or `/usr/bin/` if you'd rather grant it to the interpreter directly.
3. Quit and reopen the terminal so the new permission is picked up.

You'll know it's missing if you see `authorization denied` on first run.

## 2. List recent chats to find the one you want

```bash
python3 imessage_export.py --list
```

Output columns: chat ROWID, kind (1:1 vs group), last message (local), message
count, the chat's `chat_identifier`, display name / participants. Pick the
**ID** in column 1 for the next step.

## 3. Export

```bash
# By chat ROWID
python3 imessage_export.py --chat-id 42 --me-name "Ben"

# By chat_identifier (a phone, email, or group GUID)
python3 imessage_export.py --chat-identifier "+15551234567" --me-name "Ben"

# By substring match on a participant (handle / display name / chat_identifier)
python3 imessage_export.py --participant "alice@" --me-name "Ben"
```

### Time windows (local time)

```bash
# Whole day
python3 imessage_export.py --chat-id 42 --date 2025-05-01

# Intra-day window on a single day
python3 imessage_export.py --chat-id 42 \
  --date 2025-05-01 --start-time 14:00 --end-time 18:30

# Arbitrary local datetime range (crosses days)
python3 imessage_export.py --chat-id 42 \
  --start-datetime "2025-05-01 14:00" \
  --end-datetime   "2025-05-02 09:30:00"

# Day-granularity range (legacy)
python3 imessage_export.py --chat-id 42 \
  --from-date 2025-05-01 --to-date 2025-05-07
```

All bounds are interpreted in the **system's local timezone**, then converted
to Apple's 2001-epoch nanoseconds (or seconds on pre-macOS-10.13 databases —
the script auto-detects). Upper bounds are **exclusive**, so
`--end-time 18:30` includes everything up to but not including 18:30:00 local.

The resolved window (local string, UTC ISO, Apple-ns, detected unit) is written
to `conversation.json → metadata.window` and printed at the top of
`conversation_ai_ready.txt`.

### Useful extras

```bash
--contacts contacts.csv       # map handles → human names (see below)
--me-name "Ben"               # label for is_from_me=1 lines
--include-attachments         # resolve attachment filenames per message
--limit 1000                  # cap message count
--output-dir ./exports        # default is ./exports
--db /path/to/chat.db         # default ~/Library/Messages/chat.db
```

## 4. Contacts mapping (handle → name)

The macOS AddressBook is also FDA-protected and its schema varies. Provide a
simple CSV instead:

```csv
handle,name
+15551234567,Alice
alice@example.com,Alice
+15557654321,Bob
```

Phone numbers are normalized to digits with optional leading `+`. Emails are
lowercased. Unmapped handles fall back to the raw phone/email. The string
`Unknown` only appears when the underlying row has **no** handle at all (rare;
some system-generated messages).

See `contacts.example.csv`.

## 5. Output layout

Each export drops a folder under `--output-dir`, grouped by contact then date:

```
exports/
└── Alice/
    └── 2026-06-06/
        ├── conversation.csv
        ├── conversation.json
        ├── conversation.txt
        ├── conversation_ai_ready.txt
        └── analysis_prompt.txt
```

The date is the window start when one is provided (`--date`, `--start-datetime`,
or `--from-date`); otherwise it's the date of the first actual message in the
conversation. Re-running an export for the same `(contact, date)` **overwrites**
the previous one. New folders are created `700` and files `600` (umask `0o077`)
so other users on the machine can't read your conversations.

### TXT format

```
[2025-05-01 14:32:10] Alice: Hey, are you free later?
[2025-05-01 14:33:02] Ben: Yes, after 6 works.
[2025-05-01 14:33:40] Ben:   [Attachments: photo1.jpg]
```

### AI-ready TXT

`conversation_ai_ready.txt` includes a header (participants, date range,
message count, resolved time window in both local + UTC) and an attribution
footer, then the formatted lines. Paste it directly into a chat with an AI
model.

### JSON

Each message:

```json
{
  "message_id": 123,
  "timestamp": "2025-05-01 14:32:10",
  "timestamp_utc": "2025-05-01T18:32:10+00:00",
  "chat_id": 42,
  "sender_handle": "+15551234567",
  "is_from_me": 0,
  "author_label": "Alice",
  "text": "Hey, are you free later?",
  "has_attachment": 0,
  "attachment_filenames": []
}
```

Plus a top-level `metadata` object: participants list, resolved window
(`local_start`/`local_end`/`utc_start`/`utc_end`/`apple_ns_start`/
`apple_ns_end`/`tz`), detected timestamp unit, and an attribution note.

## 6. How authorship is reconstructed

- `message.is_from_me = 1` → `author_label = --me-name` (default `"Me"`).
- `message.is_from_me = 0` → join `message.handle_id → handle.id`, resolve via
  the contacts CSV; otherwise emit the raw `handle.id`.
- Chat membership is reconstructed via `chat_message_join` and
  `chat_handle_join` so group chats keep per-message authorship.
- The handle resolution uses a normalized key (lowercased email; digits-only
  phone with optional `+`) so `+1 (555) 123-4567` and `+15551234567` map to the
  same contact.

## 7. Schema notes & gotchas

- **Timestamp unit.** macOS 10.13+ stores `message.date` as **nanoseconds**
  since 2001-01-01 UTC; older versions used **seconds**. The script samples a
  row and switches accordingly. The detected unit is written to
  `metadata.timestamp_unit_detected`.
- **Empty `message.text`.** On modern macOS, plain-text messages can store the
  text in `message.attributedBody` (an `NSAttributedString` typedstream blob)
  instead of `message.text`. The script extracts the string with a best-effort
  parser. Edge cases (mentions, rich attachments) may yield a partial string.
- **The `￼` placeholder** (U+FFFC) used for inline attachments is stripped from
  text; resolve real filenames with `--include-attachments`.
- **`chat.style`** is `43` for group, `45` for 1:1.
- **Read-only.** Opened with `mode=ro&immutable=1` plus `PRAGMA query_only`.
  Closing Messages.app before exporting avoids an `SQLITE_BUSY` race when the
  WAL is being checkpointed.

## 8. Run it

```bash
chmod +x imessage_export.py
python3 imessage_export.py --list
python3 imessage_export.py --chat-id <ID> --me-name "Ben" \
  --date 2025-05-01 --start-time 14:00 --end-time 18:00 \
  --contacts contacts.csv --include-attachments
```

Then paste `conversation_ai_ready.txt` into your AI of choice together with
`analysis_prompt.txt`.

## Acknowledgments

This project owes a debt to several pieces of prior art. If you want a more
batteries-included exporter (HTML output, attachment handling, edit history
extraction, Diagnostic Reports support), reach for one of these instead:

- [`imessage-exporter`](https://github.com/ReagentX/imessage-exporter) (Rust)
  — the de facto reference implementation for parsing `chat.db`. Handles
  `attributedBody` typedstreams, edit history, and tapbacks correctly across
  multiple macOS versions. Several quirks documented in this codebase
  (length-prefix encoding, `associated_message_guid` prefix format, tapback
  type codes) were cross-checked against its source.
- Apple's published [Messages
  documentation](https://developer.apple.com/documentation/usernotifications/messages_filter)
  — sparse but authoritative on the public-facing schema; everything else is
  reverse-engineered from observed DBs.

This repo's goals are deliberately narrower: a single Python file, stdlib
only, focused on producing AI-prompt-friendly text from one conversation at
a time.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: don't paste real
message content into issues / PRs, run the test suite, and keep new
dependencies out.

## Security

For privacy- or read-only-guard-related issues, see [SECURITY.md](SECURITY.md).
Do not open public issues for security reports.

## License

MIT. See [LICENSE](LICENSE).
