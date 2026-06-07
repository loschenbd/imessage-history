# Working on this repo with Claude Code

This project exports a single iMessage conversation from the local macOS
`~/Library/Messages/chat.db` into AI-ready files (CSV / JSON / TXT / Markdown).
It is a **personal-data tool** — exports contain private message bodies.

## What you can assume about a fresh checkout

- **Pure Python 3.10+**, stdlib only. No `pip install` step.
- **Read-only DB access**: `chat.db` is opened with `mode=ro&immutable=1` plus
  `PRAGMA query_only=ON`. Never write to or modify `chat.db`.
- **FDA-protected source**: the macOS Messages DB requires Full Disk Access for
  the process running Python. If you see `authorization denied`, that's the
  cause; tell the user, don't try to chmod or copy around it.
- **No network**: nothing in this repo makes outbound calls. Keep it that way.

## Repo layout

```
imessage_export.py          # the one-file exporter (CLI + library)
tests/test_decoder.py       # stdlib unittest regression suite
contacts.example.csv        # example handle→name mapping (safe to share)
contacts.csv                # REAL handle→name mapping — gitignored, PRIVATE
README.md                   # user-facing usage docs
sample_output_schema.md     # schema of the generated JSON / CSV
exports/                    # generated conversation folders — gitignored, PRIVATE
.claude/settings.json       # shared Claude Code permissions / config
```

## Running locally

```bash
# List recent chats
python3 imessage_export.py --list

# Dump every handle as a starter contacts.csv
python3 imessage_export.py --list-contacts > /tmp/handles.csv

# Export one conversation for one date window
python3 imessage_export.py --chat-id <ID> \
  --me-name "<your-label>" \
  --date YYYY-MM-DD \
  --start-time HH:MM --end-time HH:MM \
  --contacts contacts.csv

# Run the test suite
python3 -m unittest discover -s tests -v
```

Output lands in `exports/<contact-or-group>/<YYYY-MM-DD>/` with:

- `conversation.csv`
- `conversation.json`
- `conversation.txt`
- `conversation.md` (Notion / Obsidian friendly)
- `conversation_ai_ready.txt` (header + speaker-attributed body + footer)
- `analysis_prompt.txt`

## Code conventions

- One file, no packages. Helpers grouped by section banner comments.
- All time math is in the system's local timezone, converted to Apple's
  2001-epoch nanoseconds for SQL. Upper bounds are exclusive.
- Writers (`write_csv`, `write_json`, `write_txt`, `write_markdown`,
  `write_ai_ready`) take `(path, messages, metadata)` and don't share state.
- Tests live under `tests/` and use stdlib `unittest` so they run with no
  external dependencies.

## Schema gotchas to remember

Apple's `chat.db` has several quirks that silently corrupt naive output:

1. **`message.handle_id` is the OTHER party, not the sender.** On outgoing rows
   (`is_from_me = 1`) it points to the recipient. The exporter nulls
   `sender_handle` for outgoing rows — `author_label` is the source of truth
   for who sent a message.
2. **`message.text` can be NULL** even for plain-text messages — the body is in
   `message.attributedBody`, an `NSAttributedString` typedstream blob. Use
   `decode_attributed_body` for the fallback.
3. **`attributedBody` length-prefix `0x81`** is followed by a **2-byte
   little-endian** length, not 1 byte. Getting this wrong truncates every long
   message at exactly 255 chars.
4. **Tapbacks / reactions** are rows where `associated_message_type` is in
   2000–2006 (added) or 3000–3006 (removed). `associated_message_guid` has the
   form `p:N/<GUID>` or `bp:<GUID>` — strip the prefix before joining against
   `message.guid` to find the target message.
5. **Edited / unsent / app-payload rows** can have NULL `text` AND NULL
   `attributedBody`. Check `date_edited`, `date_retracted`, and
   `balloon_bundle_id` before rendering as a blank speaker line.

## Privacy expectations

- **Don't paste export contents into the conversation** unless the user has
  explicitly opened them. Treat anything under `exports/` as private even when
  asked to summarize.
- **Don't commit private files.** `.gitignore` already excludes `exports/`,
  `contacts.csv`, `__pycache__/`, and most of `.claude/`. If you add new
  scripts that produce derived data, gitignore the outputs.
- **No cloud uploads.** Don't suggest piping exports into hosted LLMs without
  flagging that it sends private messages to a third party. For sensitive
  threads, recommend a local model (Ollama, LM Studio).
- **Permissions.** The exporter sets `os.umask(0o077)` so new files are `600`
  and new dirs `700`. If you add I/O, keep that constraint.

## Useful one-shot diagnostics

```bash
# Confirm Python can see chat.db at all (will fail without FDA)
python3 -c "import sqlite3; sqlite3.connect('file:'+__import__('os').path.expanduser('~/Library/Messages/chat.db')+'?mode=ro', uri=True).execute('select count(*) from message').fetchone()"

# See what permission your shell currently has
ls -la ~/Library/Messages/chat.db
```

## Out of scope

- Live syncing or watching `chat.db` for new messages.
- Writing back to Messages, sending iMessages, or modifying the DB.
- Anything that needs network access or third-party packages.

If a request strays into these, propose a smaller alternative or surface the
constraint to the user rather than working around it.
