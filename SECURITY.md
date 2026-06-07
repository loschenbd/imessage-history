# Security policy

This tool reads private message content. A bug here can leak data or worse —
so security reports get priority.

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.**

Instead, email **<ben@benjaminloschen.com>** with:

- A description of the issue
- Reproduction steps (using synthetic data — see CONTRIBUTING.md)
- Affected version / commit SHA
- Your assessment of impact

I aim to acknowledge reports within 7 days and fix or document a
non-issue within 30. Coordinated disclosure: hold off on public discussion
until a patched release is available.

## What counts as a security issue

Treat any of these as critical:

- **Read-only guard regression.** The `chat.db` connection must remain
  read-only. If you can produce a code path where `open_db()` returns a
  connection that successfully executes a write statement, or where the file
  bytes change after `close()`, that's critical.
- **Network egress.** This tool is stdlib-only and makes zero network calls.
  Anything that introduces an outbound request — even via a hidden DNS lookup
  or a stdlib quirk — is a vulnerability.
- **Permission downgrade.** New files / directories must be created with
  `0600` / `0700` modes (the `umask(0o077)` invariant). Anything that
  produces world-readable exports is a vulnerability.
- **`exports/` or `contacts.csv` leaking into git.** The `.gitignore` rules
  must continue to exclude all of: `exports/`, `contacts.csv`,
  `__pycache__/`, `.claude/projects/`. A misconfigured ignore that allows
  any of these to be staged is a vulnerability.
- **Path traversal or template injection** in the resolved chat label,
  contact names, or any user-controlled string that ends up in a file path.

## What doesn't count

- "The exporter can read my chat.db." That's the entire point of the tool.
  Anyone with Full Disk Access on a Mac can read chat.db; this script just
  makes it tabular.
- "I committed `contacts.csv` by accident." The `.gitignore` is in place;
  using `git add -f` to override it is on you. Recover with
  `git filter-repo` (or the BFG) on your own fork.
- "Pasting `conversation_ai_ready.txt` into ChatGPT sent my conversation to
  OpenAI." Correct. The README warns about this. Run a local model
  (Ollama, LM Studio) for sensitive threads.

## Threat model

This tool assumes:

- The macOS user account running it is the legitimate owner of `chat.db`.
- The local filesystem is trusted (FileVault recommended, but out of scope).
- Other processes running as the same Unix user can read the exports — POSIX
  permissions don't sandbox same-user reads. If that's not OK, run inside an
  encrypted DMG or use per-file encryption (`age`, `gpg`).

This tool does **not** protect against:

- Malware running as your Unix user (it has the same FDA you granted).
- Backups uploaded to cloud storage (Time Machine, iCloud Drive, third-party
  sync) that include `exports/` — check your backup tool's exclusion list.
- Shoulder surfing or screen recordings.

If your threat model includes any of those, this tool alone is not enough.
