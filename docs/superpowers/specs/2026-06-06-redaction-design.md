# Redaction / pseudonymization — design

**Date:** 2026-06-06
**Status:** approved by user, ready for implementation plan
**Scope:** add an opt-in redaction layer to `imessage_export.py` so users
can paste conversations into hosted LLMs without leaking identifiers or
in-body PII.

## Goal

Today, the only safe way to feed an export to a hosted LLM is to run a
local model (per `README.md` § Risk mitigation) or hand-redact each
file. Add a CLI affordance that produces paste-ready redacted versions
of every export format, with deterministic pseudonyms so the LLM can
still follow who's talking to whom.

## Non-goals

- No NER / ML / external model. Stdlib-only invariant stands.
- No automatic detection of proper nouns that aren't in `contacts.csv`
  or `--redact-names-file`. The `--suggest-names` flag exists exactly
  because we can't do this automatically.
- No attempt to redact attachment file contents (we don't read them).
  Attachment filenames in `attachment_filenames` stay verbatim.
- No support for re-redacting an existing export folder. The redactor
  runs at export time only. A post-process subcommand is feasible
  future work but out of scope here.

## User decisions captured during brainstorm

| Decision              | Value                                                                                                |
|-----------------------|------------------------------------------------------------------------------------------------------|
| Redaction scope       | Identifiers + body PII (handles, names, phone numbers, emails, URLs)                                 |
| Replacement strategy  | Consistent pseudonyms, deterministic across runs                                                     |
| Pseudonym format      | `Person A` / `Person B` / … `Person Z` / `Person AA` / `Person AB` / …                              |
| Case sensitivity      | Insensitive — `mallory` matches `Mallory` in contacts                                                |
| CLI shape             | Two flags: `--redact` (parallel files) and `--redact-only` (redacted files only)                     |
| Bootstrap tooling     | `--suggest-names` to scan a window for proper-noun candidates user can review and add to a names file |

## Architecture

One new section in `imessage_export.py`, roughly 120 lines. No new
files, no new dependencies.

### Public surface

```python
@dataclass(frozen=True)
class RedactionConfig:
    me_name: str
    extra_names: list[str] = field(default_factory=list)
    redact_phones: bool = True
    redact_emails: bool = True
    redact_urls: bool = True
    case_sensitive: bool = False


class Redactor:
    def __init__(self,
                 messages: list[Message],
                 metadata: dict,
                 contacts: dict[str, str],
                 config: RedactionConfig): ...

    # The four outputs consumed by main():
    def redact_messages(self) -> list[Message]:
        """Deep-copy messages, apply all substitutions, return."""
    def redact_metadata(self) -> dict:
        """Deep-copy metadata, redact participants + window strings, return."""
    def pseudonym_map(self) -> dict:
        """Serializable dict with both flat and grouped views."""
    def chat_label(self) -> str:
        """Pseudonymized folder label (e.g. 'Person B' for a 1:1 chat)."""
```

### Internals (one line each)

- `_build_pseudonym_map()` — walks messages in timeline order, assigns
  `Person A` to the device owner (`config.me_name`) first, then
  next-new-speaker → `Person B`, etc. Also maps every value in
  `contacts.csv` (even ones not in this chat) and every entry in
  `config.extra_names`.
- `_substitution_table()` — orders substitutions longest-first so
  `Mallory Smith` runs before `Mallory`.
- `_redact_text(s)` — runs literal-string substitutions (case-folded
  when `not config.case_sensitive`), then PII regexes
  (`\b\+?\d[\d\-\s().]{7,}\b` for phones, standard email regex,
  `https?://\S+` for URLs).
- `_excel_letters(n)` — `0 → A`, `25 → Z`, `26 → AA`, `27 → AB`, …

### CLI flags added to `build_parser()`

| Flag                       | Type   | Effect                                                                                              |
|----------------------------|--------|-----------------------------------------------------------------------------------------------------|
| `--redact`                 | bool   | Write the originals AND `conversation_redacted.*` + `pseudonym_map.json`.                           |
| `--redact-only`            | bool   | Write only the redacted files + `pseudonym_map.json`. Folder name uses the pseudonymized label.    |
| `--redact-names-file PATH` | path   | Flat text file, one extra name per line. All pseudonymized into the same `Person X` namespace.     |
| `--no-redact-phones`       | bool   | Disable phone-number scrubbing in body text. By default phones ARE scrubbed; pass this flag to disable. |
| `--no-redact-emails`       | bool   | Same shape for emails (default: scrubbed; pass flag to disable).                                       |
| `--no-redact-urls`         | bool   | Same shape for URLs.                                                                                 |
| `--suggest-names`          | bool   | Diagnostic mode: skip export entirely, scan the selected window, print proper-noun candidates to stdout. |

`--redact` and `--redact-only` are not mutually exclusive. Either flag
on its own enables the redactor. If both are set, `--redact-only` wins
(redacted files only, no originals). `--suggest-names` is its own mode
and is mutually exclusive with `--redact` / `--redact-only`: if you
pass `--suggest-names` together with either, the script errors out
with `--suggest-names cannot be combined with --redact / --redact-only`.
`--suggest-names` reuses the existing source-selection flags
(`--chat-id` / `--chat-identifier` / `--participant`) and time-window
flags so the same selectors work in both modes.

## Data flow

```
1. parse_args
2. open_db
3. if args.suggest_names:
       run suggest_names(conn, ...); return 0
4. export(conn, ...) → messages, metadata          # unchanged from today
5. if args.redact or args.redact_only:
       config   = RedactionConfig(me_name=args.me_name,
                                  extra_names=read_lines(args.redact_names_file),
                                  redact_phones=not args.no_redact_phones,
                                  ...)
       redactor    = Redactor(messages, metadata, contacts, config)
       red_msgs    = redactor.redact_messages()
       red_meta    = redactor.redact_metadata()
       pmap        = redactor.pseudonym_map()
       red_label   = redactor.chat_label()
6. label_for_folder = red_label if args.redact_only else chat_label(metadata)
   out_dir = output_dir / slugify(label_for_folder) / date_str
   out_dir.mkdir(parents=True, exist_ok=True)
7. if not args.redact_only:
       write_csv      (out_dir / "conversation.csv",            messages)
       write_json     (out_dir / "conversation.json",           messages, metadata)
       write_txt      (out_dir / "conversation.txt",            messages)
       write_markdown (out_dir / "conversation.md",             messages, metadata)
       write_ai_ready (out_dir / "conversation_ai_ready.txt",   messages, metadata)
8. if args.redact or args.redact_only:
       suffix = "" if args.redact_only else "_redacted"
       write_csv      (out_dir / f"conversation{suffix}.csv",          red_msgs)
       write_json     (out_dir / f"conversation{suffix}.json",         red_msgs, red_meta)
       write_txt      (out_dir / f"conversation{suffix}.txt",          red_msgs)
       write_markdown (out_dir / f"conversation{suffix}.md",           red_msgs, red_meta)
       write_ai_ready (out_dir / f"conversation{suffix}_ai_ready.txt", red_msgs, red_meta)
       write_json     (out_dir / "pseudonym_map.json",                 pmap)
9. write_prompt(out_dir / "analysis_prompt.txt")                       # static, unredacted
```

### Key invariants

- Writers (`write_csv`, `write_markdown`, etc.) receive already-redacted
  objects. They have zero awareness of redaction. Schema changes don't
  require touching the redactor.
- Pseudonym map is built once, applied to messages + metadata in the
  same pass — guaranteed consistent across all five files.
- Deep copy happens inside `Redactor.redact_messages()`; the caller's
  `messages` list is untouched, so step 7 still writes the un-redacted
  originals correctly.
- In `--redact-only` mode the folder name itself uses the pseudonymized
  label. In `--redact` mode (both versions on disk) the folder uses the
  real contact name because you've already opted to keep the un-redacted
  files locally.
- **Collision-avoidance in `--redact-only`.** Every 1:1 chat would
  otherwise land in `exports/Person B/<date>/` (since the other party
  is always `Person B` in a 1:1) and overwrite each other across
  conversations. The redacted folder name appends a 4-char stable hash
  of `chat_id`: `exports/Person B-a3f9/2026-06-06/`. Same chat always
  hashes the same way, so re-running the same export still overwrites
  in place; different chats land in different folders.

### `pseudonym_map.json` shape

```json
{
  "aliases_to_pseudonym": {
    "Ben":           "Person A",
    "Mallory":       "Person B",
    "+15551234567":  "Person B",
    "shannon":       "Person C"
  },
  "people": [
    { "pseudonym": "Person A", "aliases": ["Ben"] },
    { "pseudonym": "Person B", "aliases": ["Mallory", "+15551234567"] },
    { "pseudonym": "Person C", "aliases": ["shannon"] }
  ]
}
```

`aliases_to_pseudonym` is the flat lookup the substitution loop uses.
`people` is the grouped human-audit view (same data, easier to scan).

### `--suggest-names` algorithm

1. Run the same SQL as `export()` for the selected window.
2. Concatenate every message body text.
3. Tokenize on word boundaries; keep tokens matching
   `\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b` (1–3 capitalized words in a
   row).
4. Drop:
   - A static stopword set: months, weekdays, days-of-week-abbrevs,
     common sentence-leading words (`The`, `This`, `That`, `What`,
     `I`, `My`, `But`, `And`, `So`, `OK`, `Okay`).
   - Any token already mapped in `contacts.csv` (case-folded compare).
   - Tokens occurring only once.
5. Sort by frequency descending.
6. Print:

   ```
   # Run with `--redact-names-file <this-file>` to pseudonymize these.
   # Review and remove false positives before using.

   # 12× — "Person B was upset that Shannon left early"
   Shannon
   # 8× — "Rachelle said she'd be over after dinner"
   Rachelle
   # 3× — "we went to Trader Joe's"
   Trader Joe's
   ```

User redirects to a file, deletes false positives, passes via
`--redact-names-file`.

## Error handling

- **`--redact-names-file` missing or unreadable** → fail before opening
  the DB with the same error shape as the existing `--contacts` failure.
- **Lines in `--redact-names-file`** are stripped, empties dropped,
  duplicates deduped. No warning — it's user-curated input.
- **Alias contains regex metacharacters** (`O'Brien`, `(work)`) — we
  use `str.replace`, never `re.sub`, on user-provided strings.
- **Empty conversation** → empty pseudonym map, identity transforms,
  valid (almost-empty) output files.
- **Group chat with > 26 speakers** → `Person AA`, `Person AB`, …
  (`_excel_letters` handles up to `ZZ` = 701 speakers without
  overflow).
- **Phone number that's actually the same person from another number**
  (Mallory texting from a backup) — we can't know. Pseudonymizes to
  `[PHONE]`. Documented in README.
- **`pseudonym_map.json` is the de-redaction key.** Written with the
  existing `umask(0o077)` so the file is `0o600`. README and CLI help
  will say "treat this like a password — don't share it next to the
  redacted export."
- **`--redact-only` + zero resolved participants** → folder falls back
  to `Person ?`. Warning printed to stderr.

## Testing

### `tests/test_redactor.py` — unit tests on `Redactor`

- `test_me_is_always_person_a`
- `test_pseudonym_assignment_is_timeline_ordered`
- `test_phone_in_body_text_substituted`
- `test_email_in_body_text_substituted`
- `test_url_in_body_text_substituted`
- `test_contact_name_in_body_text_pseudonymizes_consistently`
- `test_longest_alias_wins`
- `test_regex_metacharacters_in_alias_are_safe`
- `test_pseudonym_map_includes_both_views`
- `test_deterministic_across_runs`
- `test_disable_phone_redaction`
- `test_thirty_speaker_group_uses_double_letter_pseudonyms`
- `test_extra_names_file_is_pseudonymized`
- `test_case_insensitive_match_by_default`
- `test_case_sensitive_mode_respected`

### `tests/test_end_to_end.py` — integration through the export pipeline

- `test_redact_flag_produces_both_versions`
- `test_redact_only_skips_originals`
- `test_redact_only_folder_name_is_pseudonymized`
- `test_redact_only_appends_chat_hash_for_collision_avoidance`
- `test_two_different_chats_in_redact_only_land_in_distinct_folders`
- `test_redacted_csv_has_no_real_handles`
- `test_redacted_ai_ready_has_no_real_name`
- `test_pseudonym_map_perms_are_600`
- `test_unredacted_files_unchanged_by_redact_flag`
- `test_redact_and_redact_only_mutually_exclusive`

### Adversarial sweep (the "did I leak anything?" gate)

- `test_no_unmapped_handle_appears_in_redacted_output` — collect every
  handle from `chat.handle` in the fixture, assert none appear verbatim
  in any redacted file.
- `test_no_contact_name_appears_in_redacted_output` — same shape for
  every name in the fixture's `contacts.csv`.

These run on every full export of the synthetic fixture and would
catch a regression where a new writer is added but forgets to receive
the redacted message list.

### `--suggest-names` test

- `test_suggest_names_finds_proper_nouns_not_in_contacts` — fixture
  has body text mentioning "Shannon" who isn't in contacts; output
  must include `Shannon` with a count and a context snippet.
- `test_suggest_names_excludes_existing_contacts` — names already in
  `contacts.csv` are filtered out.
- `test_suggest_names_excludes_stopwords` — `Monday`, `January`, `The`,
  `I` are filtered out.

### CI gate

All of the above run on the existing matrix
(ubuntu-latest + macos-latest × Python 3.10–3.13). The existing 37
tests must continue to pass.

## Known limitations (documented in `--help` and README)

1. Third-party names not in `contacts.csv` or `--redact-names-file`
   won't be caught. `--suggest-names` is the workaround.
2. Common-word names break literal substitution. `Will` in contacts
   will redact "will you?" to "Person X you?". Don't add common-word
   names; use `--redact-names-file` for one-off mentions instead.
3. International phone formats are best-effort.
4. URL detection is greedy (stops at whitespace) — trailing punctuation
   may sneak in but the URL still gets `[URL]`-replaced.
5. The `pseudonym_map.json` IS the de-redaction key. Mode `0o600`,
   never share alongside the redacted export.
6. Attachment filenames stay verbatim.

## Defaults summary

| Setting                    | Default     |
|----------------------------|-------------|
| Pseudonym format           | `Person A`  |
| Case sensitivity           | Insensitive |
| Phone redaction            | On          |
| Email redaction            | On          |
| URL redaction              | On          |
| Redaction itself           | Off (opt-in via `--redact` or `--redact-only`) |

## File-level impact

- `imessage_export.py` — add `RedactionConfig`, `Redactor`,
  `suggest_names()`, six new argparse flags, redaction branching in
  `_run()`.
- `tests/test_redactor.py` — new file, ~15 unit tests.
- `tests/test_end_to_end.py` — append ~10 redaction integration tests.
- `tests/fixtures/build_sample_db.py` — extend to seed an extra
  speaker mentioned only in body text, plus a row whose body contains
  a phone number, an email, and a URL.
- `README.md` — new section: "Redacting before pasting to a hosted
  LLM."
- `CLAUDE.md` — add the redactor to the "code conventions" / "schema
  gotchas" sections so future Claude sessions know how it plugs in.
- `sample_output_schema.md` — document the redacted file naming and
  `pseudonym_map.json` shape.

## Open follow-ups (out of scope for this spec)

- Post-process subcommand to re-redact an existing export folder.
- Importing names from macOS AddressBook (FDA-protected, schema
  varies — would warrant its own design).
- Per-conversation custom pseudonyms ("call Mallory `Partner`,
  not `Person B`").
