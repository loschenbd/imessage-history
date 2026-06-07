---
name: Bug report
about: Something is wrong with the exporter
title: ""
labels: bug
assignees: ""
---

> **⚠️ Privacy reminder.** Do NOT paste real message content, phone numbers,
> emails, or screenshots showing other people's messages. Redact to
> `<phone>` / `<name>` / `<chat-id>` and describe the failure in prose.
> If you can't reproduce with synthetic data, that's fine — describe the
> shape of the broken row instead of attaching it.

## Environment

- macOS version:
- Python version (`python3 --version`):
- Commit / release version of `imessage-history`:

## What I ran

```bash
# Replace any private values with placeholders.
python3 imessage_export.py --chat-id <id> --me-name "<label>" --date YYYY-MM-DD
```

## What I expected

<!-- One or two sentences. -->

## What happened

<!-- Describe the output in prose. Counts, column values, line numbers in
     the script if you traced it. NO pasted message bodies. -->

## Minimal reproduction (optional)

If you can produce the bug with a synthetic `chat.db` row, paste the
SQL you used to build it here. The `tests/` directory has examples of
constructing fixtures from scratch.

```python
# e.g.
conn.execute("INSERT INTO message (...) VALUES (...)")
```
