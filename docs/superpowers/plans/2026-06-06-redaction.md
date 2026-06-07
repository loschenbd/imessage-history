# Redaction / Pseudonymization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in redaction layer to `imessage_export.py` so users can produce paste-ready, pseudonymized versions of their exports for hosted LLMs — without breaking the stdlib-only, read-only invariants.

**Architecture:** A single `Redactor` component runs as a second pass over the messages + metadata that `export()` already builds. Existing writers stay completely pure — they don't know redaction exists. Two CLI surfaces: `--redact` (writes both versions side-by-side) and `--redact-only` (writes only the redacted set, folder name pseudonymized with a 4-char hash for collision avoidance). Plus a diagnostic `--suggest-names` mode that scans message bodies for proper-noun candidates the user may want to add to a names file.

**Tech Stack:** Python 3.10+, stdlib only (sqlite3, dataclasses, re, json, hashlib, copy, unittest). No new runtime dependencies. All work on the `redaction` branch in worktree `/Users/benjaminloschen/.config/superpowers/worktrees/imessage-history/redaction`.

**Source spec:** [`docs/superpowers/specs/2026-06-06-redaction-design.md`](../specs/2026-06-06-redaction-design.md)

---

## File map

| File                                | Action  | Responsibility                                              |
|-------------------------------------|---------|-------------------------------------------------------------|
| `imessage_export.py`                | Modify  | Add `RedactionConfig`, `Redactor`, `suggest_names`, CLI flags, `_run()` branching, `_excel_letters` helper |
| `tests/fixtures/build_sample_db.py` | Modify  | Add a body-text-only third-party speaker; add a message containing a phone, email, and URL                  |
| `tests/test_redactor.py`            | Create  | Unit tests on `Redactor` internals + `_excel_letters` + `suggest_names`                                     |
| `tests/test_end_to_end.py`          | Modify  | Append redaction integration tests + adversarial sweeps                                                     |
| `README.md`                         | Modify  | New section: "Redacting before pasting to a hosted LLM"                                                     |
| `CLAUDE.md`                         | Modify  | Add redactor to code conventions / privacy expectations                                                     |
| `sample_output_schema.md`           | Modify  | Document redacted file naming + `pseudonym_map.json` shape                                                  |

All work happens in worktree: `/Users/benjaminloschen/.config/superpowers/worktrees/imessage-history/redaction` (branch `redaction`).

---

## Task 1: Extend the synthetic fixture with redaction-relevant data

**Files:**
- Modify: `tests/fixtures/build_sample_db.py`

We need new fixture data so end-to-end and unit tests can exercise: a third-party speaker mentioned in body text only (no handle), and a message containing a phone number, email, and URL.

- [ ] **Step 1: Open `tests/fixtures/build_sample_db.py` and find the `build()` function's message-insertion section** (the comments `# rid 1 — Alice's incoming...` through `# rid 5 — Me, edited message`).

- [ ] **Step 2: Append two new messages to `build()` right before `conn.commit()`**

Add these calls after the existing `insert_message(rid=5, ...)` and before `conn.commit()`:

```python
    # rid 6 — Alice incoming, mentions a third party ("Carol") by name only.
    #         Carol is not in any handle row. Used by --suggest-names tests
    #         and by tests that verify body-text names get redacted only when
    #         supplied via --redact-names-file.
    insert_message(
        rid=6, offset_sec=150,
        text="Carol said she'd be here by 7. Carol is bringing dessert.",
        body=None,
        is_from_me=0,
    )
    # rid 7 — Me outgoing, body contains a phone, an email, and a URL.
    #         Used by tests that verify PII regex redaction.
    insert_message(
        rid=7, offset_sec=180,
        text="Hit me at +15557654321 or alice@example.com — see https://example.com/page?x=1",
        body=None,
        is_from_me=1,
    )
```

- [ ] **Step 3: Add a smoke test in `tests/test_end_to_end.py` to confirm the fixture grew**

In `EndToEndExportTests.test_metadata_has_resolved_window_and_participants`, change the assertion `self.assertEqual(md["message_count"], 5)` to `self.assertEqual(md["message_count"], 7)`.

Also update `test_csv_has_expected_columns` if it asserts a row count (it doesn't currently — verify by reading the file).

- [ ] **Step 4: Run the full test suite**

```bash
cd /Users/benjaminloschen/.config/superpowers/worktrees/imessage-history/redaction
python3 -m unittest discover -s tests -v
```

Expected: all 37 tests pass except `test_metadata_has_resolved_window_and_participants` which now confirms count = 7. (If you forgot to update it, it'll fail — fix and re-run.)

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/build_sample_db.py tests/test_end_to_end.py
git commit -m "test(fixture): add third-party name + PII row for redaction tests"
```

---

## Task 2: Add `RedactionConfig` and `_excel_letters` helper

**Files:**
- Modify: `imessage_export.py` (add new section)
- Create: `tests/test_redactor.py`

Foundational building blocks: a config dataclass and the pseudonym-letter sequence helper.

- [ ] **Step 1: Create `tests/test_redactor.py` with failing tests**

```python
"""Unit tests for the redaction component.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the script importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import imessage_export as ie


class ExcelLettersTests(unittest.TestCase):
    def test_zero_is_a(self):
        self.assertEqual(ie._excel_letters(0), "A")

    def test_twenty_five_is_z(self):
        self.assertEqual(ie._excel_letters(25), "Z")

    def test_twenty_six_is_aa(self):
        self.assertEqual(ie._excel_letters(26), "AA")

    def test_twenty_seven_is_ab(self):
        self.assertEqual(ie._excel_letters(27), "AB")

    def test_701_is_zz(self):
        self.assertEqual(ie._excel_letters(701), "ZZ")


class RedactionConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = ie.RedactionConfig(me_name="Ben")
        self.assertEqual(cfg.me_name, "Ben")
        self.assertEqual(cfg.extra_names, [])
        self.assertTrue(cfg.redact_phones)
        self.assertTrue(cfg.redact_emails)
        self.assertTrue(cfg.redact_urls)
        self.assertFalse(cfg.case_sensitive)

    def test_disable_phones(self):
        cfg = ie.RedactionConfig(me_name="Ben", redact_phones=False)
        self.assertFalse(cfg.redact_phones)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new file and verify failure**

```bash
python3 -m unittest tests.test_redactor -v
```

Expected: `AttributeError: module 'imessage_export' has no attribute '_excel_letters'` (or `RedactionConfig`).

- [ ] **Step 3: Add `_excel_letters` and `RedactionConfig` to `imessage_export.py`**

Add a new section in `imessage_export.py` right after the closing `]` of the `TAPBACK_GLYPHS` dict, BEFORE the `classify_tapback` function:

```python
# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RedactionConfig:
    me_name: str
    extra_names: list[str] = field(default_factory=list)
    redact_phones: bool = True
    redact_emails: bool = True
    redact_urls: bool = True
    case_sensitive: bool = False


def _excel_letters(n: int) -> str:
    """Spreadsheet-column-style letters: 0→A, 25→Z, 26→AA, 27→AB, …, 701→ZZ."""
    if n < 0:
        raise ValueError("_excel_letters requires n >= 0")
    s = ""
    n += 1  # shift to 1-indexed so the math works cleanly
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(ord("A") + rem) + s
    return s
```

- [ ] **Step 4: Run the test file and verify pass**

```bash
python3 -m unittest tests.test_redactor -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Run the full suite to confirm no regressions**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 37 (existing) + 7 (new) = 44 tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export.py tests/test_redactor.py
git commit -m "feat(redactor): RedactionConfig dataclass + _excel_letters helper"
```

---

## Task 3: Implement `_build_pseudonym_map` (the core of `Redactor.__init__`)

**Files:**
- Modify: `imessage_export.py` (add `Redactor.__init__` + `_build_pseudonym_map`)
- Modify: `tests/test_redactor.py`

`Redactor` walks the message timeline and assigns pseudonyms. Me=A, then each new speaker in chronological order gets B, C, D…

- [ ] **Step 1: Add failing tests to `tests/test_redactor.py`**

Append this class to `tests/test_redactor.py` BEFORE the `if __name__ == "__main__":` line:

```python
def _msg(message_id, is_from_me, author_label, timestamp="2025-05-01 14:00:00",
         sender_handle=None, text="", chat_id=1):
    """Build a Message with sensible defaults for redactor tests."""
    return ie.Message(
        message_id=message_id,
        timestamp=timestamp,
        timestamp_utc=timestamp.replace(" ", "T") + "+00:00",
        chat_id=chat_id,
        sender_handle=sender_handle,
        is_from_me=is_from_me,
        author_label=author_label,
        text=text,
        has_attachment=0,
        attachment_filenames=[],
    )


def _metadata(participants):
    return {
        "me_name": "Ben",
        "participants": [
            {"handle": h, "service": "iMessage", "resolved_name": name}
            for h, name in participants
        ],
        "window": {"local_start": "", "local_end": "", "utc_start": "", "utc_end": "",
                   "tz": "UTC", "apple_ns_start": None, "apple_ns_end": None,
                   "input": {}},
        "actual_first_local": "2025-05-01 14:00:00",
        "actual_last_local":  "2025-05-01 14:05:00",
        "chats": [{"display_name": "", "style": 45, "chat_identifier": "+15551234567",
                   "is_group": False}],
        "message_count": 1,
        "chat_ids": [1],
        "exported_at": "2025-05-01T14:05:00+00:00",
        "timestamp_unit_detected": "ns",
        "attribution_note": "",
    }


class PseudonymMapTests(unittest.TestCase):
    def test_me_is_always_person_a_even_if_speaks_second(self):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567"),
            _msg(2, is_from_me=1, author_label="Ben",   sender_handle=None),
        ]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()
        self.assertEqual(pmap["aliases_to_pseudonym"]["Ben"], "Person A")
        self.assertEqual(pmap["aliases_to_pseudonym"]["Alice"], "Person B")

    def test_timeline_ordered_b_then_c(self):
        # Alice speaks first, then Bob — group chat
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567"),
            _msg(2, is_from_me=0, author_label="Bob",   sender_handle="+15557654321"),
            _msg(3, is_from_me=1, author_label="Ben",   sender_handle=None),
        ]
        md = _metadata([("+15551234567", "Alice"), ("+15557654321", "Bob")])
        r = ie.Redactor(messages, md, contacts={
                            "+15551234567": "Alice", "+15557654321": "Bob"},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()["aliases_to_pseudonym"]
        self.assertEqual(pmap["Ben"],   "Person A")
        self.assertEqual(pmap["Alice"], "Person B")
        self.assertEqual(pmap["Bob"],   "Person C")

    def test_handles_share_pseudonym_with_name(self):
        # The handle and the contact name both map to the same Person
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()["aliases_to_pseudonym"]
        self.assertEqual(pmap["Alice"],         pmap["+15551234567"])

    def test_extra_names_get_their_own_pseudonym(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben", extra_names=["Carol"]))
        pmap = r.pseudonym_map()["aliases_to_pseudonym"]
        self.assertEqual(pmap["Carol"], "Person C")  # after Ben(A) and Alice(B)

    def test_grouped_view_present(self):
        messages = [_msg(1, is_from_me=1, author_label="Ben")]
        md = _metadata([])
        r = ie.Redactor(messages, md, contacts={},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()
        self.assertIn("people", pmap)
        self.assertIn("aliases_to_pseudonym", pmap)
        person_a = next(p for p in pmap["people"] if p["pseudonym"] == "Person A")
        self.assertIn("Ben", person_a["aliases"])
```

- [ ] **Step 2: Run and verify failure**

```bash
python3 -m unittest tests.test_redactor.PseudonymMapTests -v
```

Expected: `AttributeError: module 'imessage_export' has no attribute 'Redactor'`.

- [ ] **Step 3: Implement `Redactor` class with `__init__` and `pseudonym_map`**

Append the following to the `# Redaction` section in `imessage_export.py` (right after `_excel_letters`):

```python
class Redactor:
    """Build a deterministic alias→pseudonym map for one conversation.

    Inputs:
      messages : list[Message]    (timeline order, as produced by export())
      metadata : dict             (the metadata dict produced by export())
      contacts : dict[str, str]   (handle → name, as loaded from contacts.csv)
      config   : RedactionConfig

    The map is built once at __init__ time and re-used by the redact_* methods.
    """

    def __init__(self, messages, metadata, contacts, config):
        self._messages  = messages
        self._metadata  = metadata
        self._contacts  = contacts or {}
        self._config    = config
        self._alias_to_pseudonym: dict[str, str] = {}
        self._pseudonym_to_aliases: dict[str, list[str]] = {}
        self._build_pseudonym_map()

    def _assign_pseudonym(self, alias: str, pseudonym: str) -> None:
        """Map alias → pseudonym. No-op if alias already mapped."""
        if not alias or alias in self._alias_to_pseudonym:
            return
        self._alias_to_pseudonym[alias] = pseudonym
        self._pseudonym_to_aliases.setdefault(pseudonym, []).append(alias)

    def _new_pseudonym(self) -> str:
        n = len(self._pseudonym_to_aliases)
        return f"Person {_excel_letters(n)}"

    def _ensure_person(self, primary_alias: str, *aliases: str) -> str:
        """Get (or create) the pseudonym for primary_alias, registering aliases under it."""
        existing = self._alias_to_pseudonym.get(primary_alias)
        if existing is None:
            existing = self._new_pseudonym()
        self._assign_pseudonym(primary_alias, existing)
        for a in aliases:
            self._assign_pseudonym(a, existing)
        return existing

    def _build_pseudonym_map(self) -> None:
        # 1. Device owner is always Person A.
        self._ensure_person(self._config.me_name)

        # 2. Walk the message timeline assigning new speakers.
        for m in self._messages:
            if m.is_from_me:
                # Outgoing — author is me; nothing new to register.
                continue
            label  = m.author_label
            handle = m.sender_handle
            # Both label and handle (when present) belong to the same person.
            if label:
                self._ensure_person(label, *([handle] if handle else []))
            elif handle:
                self._ensure_person(handle)

        # 3. Register all contact names (even ones not in this conversation —
        #    they may be mentioned in body text from third-party speakers).
        for handle, name in self._contacts.items():
            if name and name not in self._alias_to_pseudonym:
                # Brand-new person from contacts.csv — new pseudonym.
                self._ensure_person(name, handle)
            elif name:
                # Name already mapped (it's a participant) — link the handle.
                self._ensure_person(name, handle)

        # 4. Register --redact-names-file extras.
        for extra in self._config.extra_names:
            if extra:
                self._ensure_person(extra)

    def pseudonym_map(self) -> dict:
        people = [
            {"pseudonym": p, "aliases": list(aliases)}
            for p, aliases in sorted(self._pseudonym_to_aliases.items())
        ]
        return {
            "aliases_to_pseudonym": dict(self._alias_to_pseudonym),
            "people": people,
        }
```

- [ ] **Step 4: Run and verify pass**

```bash
python3 -m unittest tests.test_redactor.PseudonymMapTests -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Run the full suite for no regressions**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 44 + 5 = 49 tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export.py tests/test_redactor.py
git commit -m "feat(redactor): pseudonym map (timeline-ordered, Me always Person A)"
```

---

## Task 4: Implement `_redact_text` — substitution + PII regex

**Files:**
- Modify: `imessage_export.py`
- Modify: `tests/test_redactor.py`

Substitution logic: replace every alias with its pseudonym, then scrub phones/emails/URLs.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_redactor.py`:

```python
class RedactTextTests(unittest.TestCase):
    def _make(self, **cfg):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567"),
            _msg(2, is_from_me=1, author_label="Ben"),
        ]
        md = _metadata([("+15551234567", "Alice")])
        return ie.Redactor(messages, md,
                           contacts={"+15551234567": "Alice"},
                           config=ie.RedactionConfig(me_name="Ben", **cfg))

    def test_alias_substituted_in_text(self):
        r = self._make()
        self.assertEqual(r._redact_text("Alice said hi"), "Person B said hi")

    def test_case_insensitive_by_default(self):
        r = self._make()
        self.assertEqual(r._redact_text("alice said hi"), "Person B said hi")

    def test_case_sensitive_mode_respected(self):
        r = self._make(case_sensitive=True)
        self.assertEqual(r._redact_text("alice said hi"), "alice said hi")
        self.assertEqual(r._redact_text("Alice said hi"), "Person B said hi")

    def test_longest_alias_wins(self):
        # If both "Alice" and "Alice Smith" map to same person, the longer match should
        # win (no leftover " Smith" suffix).
        messages = [_msg(1, is_from_me=0, author_label="Alice")]
        md = _metadata([])
        r = ie.Redactor(messages, md,
                        contacts={"+15551234567": "Alice Smith", "+15557654321": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        # Both should map to the same Person B (since the names key together via
        # _build_pseudonym_map only if they share a handle — verify behavior is at
        # least that the longer name fully consumes).
        self.assertNotIn("Smith", r._redact_text("Alice Smith said hi"))

    def test_phone_redacted(self):
        r = self._make()
        self.assertEqual(r._redact_text("call me at +15557654321"), "call me at [PHONE]")

    def test_phone_redaction_can_be_disabled(self):
        r = self._make(redact_phones=False)
        self.assertEqual(r._redact_text("call me at +15557654321"), "call me at +15557654321")

    def test_email_redacted(self):
        r = self._make()
        self.assertEqual(r._redact_text("write alice@example.com"), "write [EMAIL]")

    def test_url_redacted(self):
        r = self._make()
        self.assertEqual(r._redact_text("see https://example.com/x"), "see [URL]")

    def test_regex_metacharacters_in_alias_safe(self):
        # "O'Brien" and "(work)" as contact names must not crash or match unintended substrings.
        messages = [_msg(1, is_from_me=0, author_label="O'Brien")]
        md = _metadata([])
        r = ie.Redactor(messages, md,
                        contacts={"+15551234567": "O'Brien", "+15557654321": "(work)"},
                        config=ie.RedactionConfig(me_name="Ben"))
        out = r._redact_text("Met O'Brien at (work) yesterday")
        self.assertNotIn("O'Brien", out)
        self.assertNotIn("(work)",  out)
```

- [ ] **Step 2: Run, verify failure**

```bash
python3 -m unittest tests.test_redactor.RedactTextTests -v
```

Expected: `AttributeError: 'Redactor' object has no attribute '_redact_text'`.

- [ ] **Step 3: Implement `_redact_text` and supporting methods**

Append to the `Redactor` class in `imessage_export.py`:

```python
    # PII regexes. Conservative; documented as best-effort in README.
    _PHONE_RE = re.compile(r"\b\+?\d[\d\-\s().]{7,}\b")
    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _URL_RE   = re.compile(r"https?://\S+")

    def _ordered_aliases(self) -> list[str]:
        """Aliases ordered longest-first so 'Alice Smith' wins over 'Alice'."""
        return sorted(self._alias_to_pseudonym.keys(), key=len, reverse=True)

    def _redact_text(self, s: str) -> str:
        if not s:
            return s
        out = s
        case_sensitive = self._config.case_sensitive
        for alias in self._ordered_aliases():
            pseudonym = self._alias_to_pseudonym[alias]
            if case_sensitive:
                out = out.replace(alias, pseudonym)
            else:
                # Case-insensitive literal replace. Loop so all occurrences fire.
                # We rebuild lowercased indexes each pass since `out` shrinks/grows.
                lower_alias = alias.lower()
                start = 0
                while True:
                    idx = out.lower().find(lower_alias, start)
                    if idx == -1:
                        break
                    out = out[:idx] + pseudonym + out[idx + len(alias):]
                    start = idx + len(pseudonym)
        if self._config.redact_phones:
            out = self._PHONE_RE.sub("[PHONE]", out)
        if self._config.redact_emails:
            out = self._EMAIL_RE.sub("[EMAIL]", out)
        if self._config.redact_urls:
            out = self._URL_RE.sub("[URL]", out)
        return out
```

- [ ] **Step 4: Run and verify pass**

```bash
python3 -m unittest tests.test_redactor.RedactTextTests -v
```

Expected: 9 tests pass.

- [ ] **Step 5: Full suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 49 + 9 = 58 tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export.py tests/test_redactor.py
git commit -m "feat(redactor): _redact_text with alias substitution + phone/email/URL scrub"
```

---

## Task 5: Implement `redact_messages`, `redact_metadata`, and `chat_label`

**Files:**
- Modify: `imessage_export.py`
- Modify: `tests/test_redactor.py`

The public methods main() will call. Each returns a deep-copied, redacted version of its input.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_redactor.py`:

```python
class RedactMessagesTests(unittest.TestCase):
    def _make(self):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567",
                 text="Hey Ben"),
            _msg(2, is_from_me=1, author_label="Ben",   sender_handle=None,
                 text="Hi Alice — email me at b@x.com"),
        ]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        return r, messages, md

    def test_author_label_pseudonymized(self):
        r, _, _ = self._make()
        redacted = r.redact_messages()
        self.assertEqual(redacted[0].author_label, "Person B")
        self.assertEqual(redacted[1].author_label, "Person A")

    def test_text_pseudonymized(self):
        r, _, _ = self._make()
        redacted = r.redact_messages()
        self.assertEqual(redacted[0].text, "Hey Person A")
        self.assertIn("Person B", redacted[1].text)
        self.assertIn("[EMAIL]",  redacted[1].text)

    def test_original_messages_untouched(self):
        r, originals, _ = self._make()
        _ = r.redact_messages()
        self.assertEqual(originals[0].author_label, "Alice")
        self.assertEqual(originals[0].text,         "Hey Ben")

    def test_sender_handle_replaced_when_present(self):
        r, _, _ = self._make()
        redacted = r.redact_messages()
        self.assertEqual(redacted[0].sender_handle, "Person B")
        # Outgoing rows have no sender_handle to redact.
        self.assertIsNone(redacted[1].sender_handle)


class RedactMetadataTests(unittest.TestCase):
    def test_participants_pseudonymized(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        red_md = r.redact_metadata()
        p = red_md["participants"][0]
        self.assertEqual(p["resolved_name"], "Person B")
        self.assertEqual(p["handle"],        "Person B")

    def test_original_metadata_untouched(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        _ = r.redact_metadata()
        self.assertEqual(md["participants"][0]["resolved_name"], "Alice")


class ChatLabelTests(unittest.TestCase):
    def test_one_on_one_uses_other_party_pseudonym(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        self.assertEqual(r.chat_label(), "Person B")
```

- [ ] **Step 2: Run, verify failure**

```bash
python3 -m unittest tests.test_redactor.RedactMessagesTests tests.test_redactor.RedactMetadataTests tests.test_redactor.ChatLabelTests -v
```

Expected: `AttributeError: 'Redactor' object has no attribute 'redact_messages'`.

- [ ] **Step 3: Implement the three methods**

Append to the `Redactor` class. You'll also need `import copy` at the top of `imessage_export.py` if it isn't already imported — verify with `grep '^import copy' imessage_export.py`; if absent, add it after `import csv`.

```python
    def redact_messages(self) -> list[Message]:
        out = []
        for m in self._messages:
            new = copy.deepcopy(m)
            # Replace author_label by looking it up in the alias map.
            if new.author_label in self._alias_to_pseudonym:
                new.author_label = self._alias_to_pseudonym[new.author_label]
            # Replace sender_handle the same way.
            if new.sender_handle and new.sender_handle in self._alias_to_pseudonym:
                new.sender_handle = self._alias_to_pseudonym[new.sender_handle]
            # Scrub message body.
            new.text = self._redact_text(new.text)
            # Reaction target text (when present) also gets scrubbed.
            if new.reaction:
                rdict = dict(new.reaction)
                if rdict.get("target_text"):
                    rdict["target_text"] = self._redact_text(rdict["target_text"])
                if rdict.get("target_author") in self._alias_to_pseudonym:
                    rdict["target_author"] = self._alias_to_pseudonym[rdict["target_author"]]
                new.reaction = rdict
            out.append(new)
        return out

    def redact_metadata(self) -> dict:
        out = copy.deepcopy(self._metadata)
        for p in out.get("participants", []):
            for key in ("handle", "resolved_name"):
                v = p.get(key)
                if v and v in self._alias_to_pseudonym:
                    p[key] = self._alias_to_pseudonym[v]
        # me_name in metadata stays as the original label so the AI-ready header
        # accurately describes who "Person A" is in the redacted view.
        if out.get("me_name") in self._alias_to_pseudonym:
            out["me_name"] = self._alias_to_pseudonym[out["me_name"]]
        return out

    def chat_label(self) -> str:
        # 1:1 → the other participant's pseudonym.
        # Group → fall back to the existing chat_label() applied to redacted metadata.
        red_md = self.redact_metadata()
        return chat_label(red_md)
```

- [ ] **Step 4: Run and verify pass**

```bash
python3 -m unittest tests.test_redactor -v
```

Expected: all redactor tests pass.

- [ ] **Step 5: Full suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 58 + 7 = 65 tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export.py tests/test_redactor.py
git commit -m "feat(redactor): redact_messages / redact_metadata / chat_label"
```

---

## Task 6: Add CLI flags + validation

**Files:**
- Modify: `imessage_export.py` (`build_parser` + a new `validate_redaction_args` helper)
- Modify: `tests/test_redactor.py`

Wire `--redact`, `--redact-only`, `--redact-names-file`, `--no-redact-phones`, `--no-redact-emails`, `--no-redact-urls`, `--suggest-names` into argparse. Add mutual-exclusion check.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_redactor.py`:

```python
class CliArgsTests(unittest.TestCase):
    def test_flags_parse(self):
        parser = ie.build_parser()
        args = parser.parse_args([
            "--chat-id", "1",
            "--redact",
            "--redact-names-file", "/tmp/x.txt",
            "--no-redact-phones",
        ])
        self.assertTrue(args.redact)
        self.assertFalse(args.redact_only)
        self.assertEqual(args.redact_names_file, "/tmp/x.txt")
        self.assertTrue(args.no_redact_phones)

    def test_suggest_names_with_redact_is_rejected(self):
        with self.assertRaises(SystemExit):
            ie.main([
                "--db", "/tmp/non-existent.db",
                "--chat-id", "1",
                "--suggest-names",
                "--redact",
            ])
```

- [ ] **Step 2: Find `build_parser()` and the existing `out = p.add_argument_group("output / formatting")` block**

Locate the line `out.add_argument("--include-attachments", action="store_true",` and the surrounding context.

- [ ] **Step 3: Add a new argument group AFTER the `out` group's last argument, before `return p`**

In `build_parser()`:

```python
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
```

- [ ] **Step 4: Add validation in `validate_args`**

Find `validate_args(args)` and append before the function's last `return`:

```python
    if args.suggest_names and (args.redact or args.redact_only):
        raise SystemExit("--suggest-names cannot be combined with --redact / --redact-only")
```

- [ ] **Step 5: Run new tests**

```bash
python3 -m unittest tests.test_redactor.CliArgsTests -v
```

Expected: pass.

- [ ] **Step 6: Run the full suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 65 + 2 = 67 pass.

- [ ] **Step 7: Commit**

```bash
git add imessage_export.py tests/test_redactor.py
git commit -m "feat(cli): --redact / --redact-only / --redact-names-file / --suggest-names flags"
```

---

## Task 7: Wire redaction into `_run()` (the export branch)

**Files:**
- Modify: `imessage_export.py` (the `_run()` function)
- Modify: `tests/test_end_to_end.py`

After `export()` builds `messages + metadata`, if `args.redact` or `args.redact_only`, construct a `Redactor`, apply it, and write a parallel set of files. Include the collision-avoidance hash for `--redact-only` folder naming.

- [ ] **Step 1: Add failing end-to-end tests**

Append to `tests/test_end_to_end.py`:

```python
class RedactionEndToEndTests(EndToEndExportTests):
    """Run the existing fixture export with --redact / --redact-only."""

    def _redacted_run(self, extra=()):
        argv = [
            "--db", str(self.db_path),
            "--chat-id", "1",
            "--me-name", "Tester",
            "--output-dir", str(self.out_dir),
            *extra,
        ]
        rc = ie.main(argv)
        self.assertEqual(rc, 0)

    def _resolve_first_export_dir(self) -> Path:
        contacts = list(self.out_dir.iterdir())
        self.assertGreaterEqual(len(contacts), 1)
        dates = list(contacts[0].iterdir())
        return dates[0]

    def test_redact_flag_produces_both_versions(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        for name in (
            "conversation.txt", "conversation_redacted.txt",
            "conversation.json", "conversation_redacted.json",
            "conversation.csv", "conversation_redacted.csv",
            "conversation.md", "conversation_redacted.md",
            "conversation_ai_ready.txt", "conversation_redacted_ai_ready.txt",
            "pseudonym_map.json",
        ):
            self.assertTrue((out / name).exists(), f"{name} missing")

    def test_redact_only_skips_originals(self):
        self._redacted_run(extra=["--redact-only"])
        out = self._resolve_first_export_dir()
        # Originals must NOT exist.
        for name in ("conversation.txt", "conversation.json", "conversation.csv",
                     "conversation.md", "conversation_ai_ready.txt"):
            self.assertFalse((out / name).exists(), f"{name} should not exist in redact-only mode")
        # Redacted set must.
        for name in ("conversation_redacted.txt", "pseudonym_map.json"):
            self.assertTrue((out / name).exists(), f"{name} missing")

    def test_redact_only_folder_includes_hash(self):
        self._redacted_run(extra=["--redact-only"])
        contacts = list(self.out_dir.iterdir())
        # Folder looks like exports/Person B-<4hex>/<date>/
        self.assertEqual(len(contacts), 1)
        name = contacts[0].name
        self.assertTrue(name.startswith("Person"), f"unexpected folder name: {name}")
        self.assertRegex(name, r"^Person [A-Z]+-[0-9a-f]{4}$")

    def test_redacted_csv_has_no_real_handle(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        red = (out / "conversation_redacted.csv").read_text()
        self.assertNotIn("+15551234567", red)
        self.assertIn("Person",          red)

    def test_pseudonym_map_perms_are_600(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        pmap = out / "pseudonym_map.json"
        self.assertEqual(pmap.stat().st_mode & 0o777, 0o600)
```

- [ ] **Step 2: Run, verify failure**

```bash
python3 -m unittest tests.test_end_to_end.RedactionEndToEndTests -v
```

Expected: failures (`AttributeError: module 'imessage_export' has no attribute 'main'` won't fail; the redacted files won't exist).

- [ ] **Step 3: Find `_run()` in `imessage_export.py`**

Locate the block:
```python
    # Output directory: exports/<contact-or-group>/<YYYY-MM-DD>/
    ...
    out_dir = Path(args.output_dir) / slugify(label) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    write_csv(out_dir / "conversation.csv", messages)
    ...
```

- [ ] **Step 4: Replace the block with redaction-aware logic**

Replace from `# Output directory:` through the last `write_prompt(...)` line with:

```python
    # Build the redactor if asked. Both --redact and --redact-only enable it.
    redactor = None
    red_messages: list[Message] | None = None
    red_metadata: dict | None = None
    if args.redact or args.redact_only:
        extra_names = []
        if args.redact_names_file:
            try:
                with open(args.redact_names_file) as f:
                    extra_names = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
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
    # In --redact-only mode, folder uses the pseudonymized label + a stable
    # 4-char hash of chat_id so distinct chats don't collide on "Person B".
    win = metadata["window"]
    date_str = (
        (win.get("local_start") or "")[:10]
        or (metadata.get("actual_first_local") or "")[:10]
        or datetime.now().strftime("%Y-%m-%d")
    )
    if args.redact_only and redactor is not None:
        chat_ids_str = ",".join(str(c) for c in metadata["chat_ids"])
        chash = hashlib.sha1(chat_ids_str.encode()).hexdigest()[:4]
        label_for_folder = f"{redactor.chat_label()}-{chash}"
    else:
        label_for_folder = chat_label(metadata)
    out_dir = Path(args.output_dir) / slugify(label_for_folder) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write the originals UNLESS in --redact-only mode.
    if not args.redact_only:
        write_csv      (out_dir / "conversation.csv",          messages)
        write_json     (out_dir / "conversation.json",         messages, metadata)
        write_txt      (out_dir / "conversation.txt",          messages)
        write_markdown (out_dir / "conversation.md",           messages, metadata)
        write_ai_ready (out_dir / "conversation_ai_ready.txt", messages, metadata)

    # Write the redacted set if we built a redactor.
    if redactor is not None:
        suffix = "" if args.redact_only else "_redacted"
        write_csv      (out_dir / f"conversation{suffix}.csv",          red_messages)
        write_json     (out_dir / f"conversation{suffix}.json",         red_messages, red_metadata)
        write_txt      (out_dir / f"conversation{suffix}.txt",          red_messages)
        write_markdown (out_dir / f"conversation{suffix}.md",           red_messages, red_metadata)
        write_ai_ready (out_dir / f"conversation{suffix}_ai_ready.txt", red_messages, red_metadata)
        with open(out_dir / "pseudonym_map.json", "w") as f:
            json.dump(redactor.pseudonym_map(), f, indent=2, ensure_ascii=False)

    write_prompt(out_dir / "analysis_prompt.txt")
```

Add `import hashlib` near the top of `imessage_export.py` if it isn't already (`grep '^import hashlib' imessage_export.py`).

- [ ] **Step 5: Run the new tests**

```bash
python3 -m unittest tests.test_end_to_end.RedactionEndToEndTests -v
```

Expected: 5 pass.

- [ ] **Step 6: Run full suite for no regressions**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 67 + 5 = 72 pass.

- [ ] **Step 7: Commit**

```bash
git add imessage_export.py tests/test_end_to_end.py
git commit -m "feat(export): wire --redact / --redact-only into _run()"
```

---

## Task 8: Adversarial sweep tests — "did I leak anything?"

**Files:**
- Modify: `tests/test_end_to_end.py`

Catch the regression where a new writer is added and forgets to receive the redacted list.

- [ ] **Step 1: Add the sweep tests**

Append to `RedactionEndToEndTests` in `tests/test_end_to_end.py`:

```python
    def test_no_real_handle_appears_in_any_redacted_file(self):
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        for name in ("conversation_redacted.txt", "conversation_redacted.csv",
                     "conversation_redacted.json", "conversation_redacted.md",
                     "conversation_redacted_ai_ready.txt"):
            text = (out / name).read_text()
            self.assertNotIn("+15551234567", text, f"{name} leaked the real handle")

    def test_no_real_contact_name_appears_in_any_redacted_file(self):
        # contacts.csv fixture maps +15551234567 → Alice — and the test fixture
        # build_sample_db.py uses Alice as the author label too. None of the
        # redacted files should contain the string "Alice".
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        for name in ("conversation_redacted.txt", "conversation_redacted.csv",
                     "conversation_redacted.json", "conversation_redacted.md",
                     "conversation_redacted_ai_ready.txt"):
            text = (out / name).read_text()
            self.assertNotIn("Alice", text, f"{name} leaked the real name")

    def test_redacted_body_pii_scrubbed(self):
        # Fixture rid 7 contains a phone, email, and URL.
        self._redacted_run(extra=["--redact"])
        out = self._resolve_first_export_dir()
        red_text = (out / "conversation_redacted.txt").read_text()
        self.assertNotIn("+15557654321",        red_text)
        self.assertNotIn("alice@example.com",   red_text)
        self.assertNotIn("https://example.com", red_text)
        self.assertIn("[PHONE]", red_text)
        self.assertIn("[EMAIL]", red_text)
        self.assertIn("[URL]",   red_text)
```

- [ ] **Step 2: Run new tests**

```bash
python3 -m unittest tests.test_end_to_end.RedactionEndToEndTests -v
```

Expected: 8 pass.

If `test_no_real_contact_name_appears_in_any_redacted_file` fails: the fixture's `contacts.csv` setup is implicit. The `EndToEndExportTests` base class doesn't pass `--contacts` because it doesn't currently need to. For these new tests to use contacts, add `--contacts` plumbing. **If you need to add a contacts file:** create one in `setUp`:

```python
    def setUp(self):
        super().setUp()  # this sets up self.tmp_path, self.db_path, self.out_dir
        contacts = self.tmp_path / "contacts.csv"
        contacts.write_text("handle,name\n+15551234567,Alice\n")
        self._contacts_path = contacts

    def _redacted_run(self, extra=()):
        argv = [
            "--db", str(self.db_path),
            "--chat-id", "1",
            "--me-name", "Tester",
            "--output-dir", str(self.out_dir),
            "--contacts", str(self._contacts_path),
            *extra,
        ]
        rc = ie.main(argv)
        self.assertEqual(rc, 0)
```

Apply the override and re-run.

- [ ] **Step 3: Full suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 72 + 3 = 75 (or 72 if some pre-existing redaction test count differs — match what's now displayed).

- [ ] **Step 4: Commit**

```bash
git add tests/test_end_to_end.py
git commit -m "test(redaction): adversarial sweeps proving no PII leaks across writers"
```

---

## Task 9: Implement `suggest_names` mode

**Files:**
- Modify: `imessage_export.py`
- Modify: `tests/test_redactor.py`

A diagnostic mode that scans message body text for capitalized proper-noun candidates not already in `contacts.csv`, prints them sorted by frequency with a sample context line.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_redactor.py`:

```python
class SuggestNamesTests(unittest.TestCase):
    def _capture_stdout(self, fn):
        from io import StringIO
        old = sys.stdout
        try:
            sys.stdout = buf = StringIO()
            fn()
            return buf.getvalue()
        finally:
            sys.stdout = old

    def test_finds_proper_noun_not_in_contacts(self):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice",
                 text="Carol said she'd be here by 7."),
            _msg(2, is_from_me=0, author_label="Alice",
                 text="Carol is bringing dessert."),
        ]
        out = self._capture_stdout(
            lambda: ie.suggest_names(messages, contacts={"+15551234567": "Alice"})
        )
        self.assertIn("Carol", out)

    def test_excludes_existing_contacts(self):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", text="Alice spoke twice. Alice agreed."),
        ]
        out = self._capture_stdout(
            lambda: ie.suggest_names(messages, contacts={"+15551234567": "Alice"})
        )
        self.assertNotIn("\nAlice\n", out)

    def test_excludes_stopwords_and_singletons(self):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice",
                 text="Monday I went to The Store. The bakery was closed."),
        ]
        out = self._capture_stdout(
            lambda: ie.suggest_names(messages, contacts={})
        )
        self.assertNotIn("Monday", out)
        self.assertNotIn("The",    out)
        self.assertNotIn("I ",     out)
```

- [ ] **Step 2: Run, verify failure**

```bash
python3 -m unittest tests.test_redactor.SuggestNamesTests -v
```

Expected: `AttributeError: module 'imessage_export' has no attribute 'suggest_names'`.

- [ ] **Step 3: Implement `suggest_names`**

Append after the `Redactor` class in `imessage_export.py`:

```python
# Token patterns + stopwords for --suggest-names.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b")
_SUGGEST_STOPWORDS = {
    # Weekdays + months
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
    # Common sentence-leading words and pronouns
    "The", "This", "That", "What", "Who", "Why", "How", "When", "Where",
    "I", "My", "Me", "We", "Our", "Us", "He", "She", "It", "They", "Them",
    "But", "And", "So", "Or", "If", "Yes", "No", "OK", "Okay", "Just",
    "Hi", "Hey", "Hello", "Thanks", "Thank", "Sorry",
}


def suggest_names(messages: list[Message], contacts: dict[str, str]) -> int:
    """Print proper-noun candidates not already in `contacts`.

    Output format: comment-prefixed lines for context, one candidate per line.
    User redirects to a file, deletes false positives, passes via
    --redact-names-file.
    """
    known = {name.lower() for name in contacts.values() if name}
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}

    for m in messages:
        if not m.text:
            continue
        for match in _PROPER_NOUN_RE.finditer(m.text):
            tok = match.group(0)
            if tok in _SUGGEST_STOPWORDS:
                continue
            if tok.lower() in known:
                continue
            counts[tok] = counts.get(tok, 0) + 1
            if tok not in samples:
                # Save a short context snippet (60 chars on either side)
                start = max(0, match.start() - 60)
                end   = min(len(m.text), match.end() + 60)
                samples[tok] = m.text[start:end].replace("\n", " ").strip()

    # Drop singletons.
    counts = {k: v for k, v in counts.items() if v >= 2}

    print("# Proper-noun candidates not in contacts.csv.")
    print("# Review and remove false positives, then pass via --redact-names-file.")
    print("")
    for tok in sorted(counts, key=lambda t: (-counts[t], t)):
        print(f"# {counts[tok]}× — {samples[tok]!r}")
        print(tok)
    return 0
```

- [ ] **Step 4: Wire `--suggest-names` into `_run()`**

In `_run()`, find the line `unit = detect_date_unit(conn)`. After that, near the top (before the `--list` check), add:

```python
    if args.suggest_names:
        # Reuse export() to pull the windowed messages, then hand to suggest_names.
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
```

Place this AFTER the existing `if args.list:` block and BEFORE the existing `if args.list_contacts:` block.

- [ ] **Step 5: Run new tests**

```bash
python3 -m unittest tests.test_redactor.SuggestNamesTests -v
```

Expected: 3 pass.

- [ ] **Step 6: Integration test for `--suggest-names`**

Append to `tests/test_end_to_end.py` (new class, OR extend `EndToEndExportTests`):

```python
class SuggestNamesEndToEndTests(EndToEndExportTests):
    def test_suggest_names_finds_carol(self):
        from io import StringIO
        old = sys.stdout
        try:
            sys.stdout = buf = StringIO()
            rc = ie.main([
                "--db", str(self.db_path),
                "--chat-id", "1",
                "--me-name", "Tester",
                "--suggest-names",
            ])
            self.assertEqual(rc, 0)
            self.assertIn("Carol", buf.getvalue())
        finally:
            sys.stdout = old
```

`import sys` in that file if it isn't already (verify with grep).

- [ ] **Step 7: Full suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 75 + 3 + 1 = 79 pass.

- [ ] **Step 8: Commit**

```bash
git add imessage_export.py tests/test_redactor.py tests/test_end_to_end.py
git commit -m "feat(redactor): --suggest-names diagnostic for proper-noun candidates"
```

---

## Task 10: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `sample_output_schema.md`

Document the feature for end users (README), future AI agents (CLAUDE.md), and downstream tools (schema).

- [ ] **Step 1: Add a "Redacting before pasting to a hosted LLM" section to `README.md`**

Insert this section after the existing `## 5. Output layout` section (find that header, the section ends before `### TXT format`):

```markdown
## 6. Redacting before pasting to a hosted LLM

If you're feeding an export to a hosted model (Claude / ChatGPT / Gemini),
the safest option is still a **local model** (Ollama, LM Studio). When that
isn't an option, the exporter can produce pseudonymized parallel files.

### Quick start

```bash
# Both versions side-by-side (conversation.* + conversation_redacted.*)
python3 imessage_export.py --chat-id 42 --me-name "Ben" --redact \
  --contacts contacts.csv

# Only the redacted set (folder name itself is pseudonymized)
python3 imessage_export.py --chat-id 42 --me-name "Ben" --redact-only \
  --contacts contacts.csv
```

### What gets redacted

- **Author labels and handles** everywhere they appear → `Person A`, `Person B`,
  `Person C`, … (timeline-ordered; device owner is always `Person A`).
- **Names in message body text** that appear in your `contacts.csv` or in
  `--redact-names-file`.
- **Phone numbers** → `[PHONE]` (disable with `--no-redact-phones`).
- **Email addresses** → `[EMAIL]` (disable with `--no-redact-emails`).
- **URLs** → `[URL]` (disable with `--no-redact-urls`).

### Catching third-party names that aren't in contacts

A name mentioned in body text but not in `contacts.csv` won't be redacted on
its own. Use `--suggest-names` to scan and propose candidates:

```bash
python3 imessage_export.py --chat-id 42 --me-name "Ben" --suggest-names \
  --contacts contacts.csv > suggested_names.txt

# Edit suggested_names.txt — remove false positives — then:
python3 imessage_export.py --chat-id 42 --me-name "Ben" --redact-only \
  --contacts contacts.csv --redact-names-file suggested_names.txt
```

### `pseudonym_map.json`

Written alongside the redacted files. Contains BOTH a flat lookup
(`aliases_to_pseudonym`) for tooling AND a grouped human-audit view (`people`).

**Treat this file like a password.** Anyone with it can reverse the redaction
on the matching export. Mode is `0o600`; don't share it with the redacted
files. If you commit a redacted export, do NOT also commit the map.

### Known limitations

- Third-party names not in contacts or `--redact-names-file` won't be caught
  unless you use `--suggest-names` first.
- Common-word names in your contacts (`Will`, `Joy`) will cause false-positive
  substitutions on the lowercase form (case matching defaults to insensitive).
- Phone/email/URL regexes are best-effort; exotic formats may slip through.
- Attachment filenames stay verbatim (out of scope for v1).
```

- [ ] **Step 2: Renumber the following sections in README**

Current README section sequence (verified by `grep -n "^## " README.md`):

```
## 1. Grant Full Disk Access (one-time)
## 2. List recent chats to find the one you want
## 3. Export
## 4. Contacts mapping (handle → name)
## 5. Output layout
## 6. How authorship is reconstructed
## 7. Schema notes & gotchas
## 8. Run it
```

Insert the new redaction section as `## 6. Redacting before pasting to a hosted LLM` and bump the existing 6/7/8 to 7/8/9. Use `sed`:

```bash
sed -i '' 's/^## 8\. Run it/## 9. Run it/'                      README.md
sed -i '' 's/^## 7\. Schema notes/## 8. Schema notes/'           README.md
sed -i '' 's/^## 6\. How authorship/## 7. How authorship/'       README.md
```

Then add the new `## 6.` block as instructed in Step 1.

- [ ] **Step 3: Add redaction note to `CLAUDE.md`**

Find the "Code conventions" section. Add a bullet:

```markdown
- The `Redactor` component runs as an optional second pass over messages
  + metadata. Writers stay unaware of redaction — they always receive
  ready-to-render objects. When adding a new writer, you don't need to
  handle redaction at all; just make sure it consumes the standard
  `Message` / `metadata` shapes.
```

Find the "Privacy expectations" section. Add a bullet:

```markdown
- The `pseudonym_map.json` produced by `--redact` / `--redact-only` is
  the de-redaction key. Treat it like a password — don't share it
  alongside the redacted export.
```

- [ ] **Step 4: Document the new files + map shape in `sample_output_schema.md`**

Append:

```markdown
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
```

- [ ] **Step 5: Run the full suite to make sure nothing broke**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 79 pass.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md sample_output_schema.md
git commit -m "docs: explain --redact / --redact-only / --suggest-names + map shape"
```

---

## Task 11: Final sweep + push the branch

**Files:** none modified.

- [ ] **Step 1: Verify the full suite passes one more time**

```bash
python3 -m unittest discover -s tests -v
```

Expected: 79 pass, 0 fail.

- [ ] **Step 2: Push the `redaction` branch**

```bash
git push -u origin redaction
```

Expected: branch pushed, CI starts on GitHub.

- [ ] **Step 3: Open a PR (no merge yet — the `formatting-improvements` branch is in flight)**

```bash
gh pr create --base main --head redaction \
  --title "Add opt-in redaction / pseudonymization" \
  --body "$(cat <<'EOF'
## Summary

Adds `--redact`, `--redact-only`, `--redact-names-file`, three
`--no-redact-{phones,emails,urls}` flags, and a diagnostic
`--suggest-names` mode. Produces pseudonymized parallel files so users
can paste to hosted LLMs without leaking identifiers.

See [`docs/superpowers/specs/2026-06-06-redaction-design.md`](docs/superpowers/specs/2026-06-06-redaction-design.md) for the design.

## Test plan

- [x] All existing tests still pass (45 → 79 total)
- [x] Decoder regression tests intact
- [x] Read-only DB guards intact
- [x] New adversarial sweeps prove no real handle / contact name appears in any redacted file
- [x] `pseudonym_map.json` written 0600
- [x] `--redact-only` folder includes chat-id hash for collision avoidance
- [x] CI matrix (ubuntu + macos × Python 3.10–3.13) passes

## Merge order

This branch has a known conflict region with `formatting-improvements`
in the writer-call sequence inside `_run()`. Merge `formatting-improvements`
first, then rebase this branch and resolve the writer-section conflict.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

---

## Self-review notes (for the implementing agent)

- All tasks expect to run from `/Users/benjaminloschen/.config/superpowers/worktrees/imessage-history/redaction`. Verify with `pwd` if any path-related error happens.
- If a test passes locally but CI fails, the most common cause is shell-permission / umask differences. `tests/test_end_to_end.py::test_exports_are_tight_permissions` is the gate.
- The `_redact_text` implementation uses a Python while-loop for case-insensitive replacement (not `re.sub` with a callable) to avoid regex-metachar pitfalls on user-supplied aliases. If you "optimize" it to use `re.sub`, you'll break `test_regex_metacharacters_in_alias_safe`.
- `redact_metadata` rewrites the participants list but NOT the AI-ready header text (the header is rendered by `write_ai_ready` from the participants list and `me_name`). If you find PII leaking through the header, fix it by updating what `write_ai_ready` reads from metadata, not by adding a header-rewrite step.
- `pseudonym_map.json` perms come for free from the `umask(0o077)` already set in `main()`. Don't add a manual `os.chmod` — that's redundant and a smell.
