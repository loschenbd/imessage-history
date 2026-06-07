# Sample output schema

## conversation.json

```json
{
  "metadata": {
    "exported_at": "2026-06-06T14:30:22-04:00",
    "me_name": "Ben",
    "chat_ids": [42],
    "participants": [
      {"handle": "+15551234567", "service": "iMessage", "resolved_name": "Alice"}
    ],
    "message_count": 87,
    "actual_first_local": "2025-05-01 14:02:11",
    "actual_last_local":  "2025-05-01 17:58:44",
    "window": {
      "apple_start": 738921600000000000,
      "apple_end":   738936000000000000,
      "local_start": "2025-05-01 14:00:00",
      "local_end":   "2025-05-01 18:00:00",
      "utc_start":   "2025-05-01T18:00:00+00:00",
      "utc_end":     "2025-05-01T22:00:00+00:00",
      "tz":          "America/New_York",
      "input": {
        "from_date": null, "to_date": null,
        "date": "2025-05-01",
        "start_time": "14:00", "end_time": "18:00",
        "start_datetime": null, "end_datetime": null
      }
    },
    "timestamp_unit_detected": "ns",
    "attribution_note": "..."
  },
  "messages": [
    {
      "message_id": 91234,
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
  ]
}
```

## conversation.csv columns

```
message_id, timestamp, timestamp_utc, chat_id,
sender_handle, is_from_me, author_label,
text, has_attachment, attachment_filenames
```

`attachment_filenames` is `|`-joined.

## conversation.txt format

```
[2025-05-01 14:32:10] Alice: Hey, are you free later?
[2025-05-01 14:33:02] Ben: Yes, after 6 works.
[2025-05-01 14:33:40] Ben:   [Attachments: photo1.jpg]
```

## conversation_ai_ready.txt

Header → conversation lines → attribution footer.

## Redacted exports

When `--redact` or `--redact-only` is set, a parallel set of files appears:

```
exports/<label>/<YYYY-MM-DD>/
├── conversation.csv                       (only without --redact-only)
├── conversation.json                      (only without --redact-only)
├── conversation.txt                       (only without --redact-only)
├── conversation.md                        (only without --redact-only)
├── conversation_ai_ready.txt              (only without --redact-only)
├── conversation_redacted.csv              (always when --redact / --redact-only)
├── conversation_redacted.json
├── conversation_redacted.txt
├── conversation_redacted.md
├── conversation_redacted_ai_ready.txt
├── pseudonym_map.json
└── analysis_prompt.txt
```

In `--redact-only` mode the folder name becomes
`<pseudonymized-label>-<4hex>` where the hash is a stable `sha1(chat_ids)`
truncation. Same chat always hashes the same way; different chats land in
different folders.

### `pseudonym_map.json`

```json
{
  "aliases_to_pseudonym": {
    "Ben":           "Person A",
    "Mallory":       "Person B",
    "+15551234567":  "Person B"
  },
  "people": [
    { "pseudonym": "Person A", "aliases": ["Ben"] },
    { "pseudonym": "Person B", "aliases": ["Mallory", "+15551234567"] }
  ]
}
```

`aliases_to_pseudonym` is the flat lookup tools should use. `people` is the
grouped human-audit view (same data, easier to scan).
