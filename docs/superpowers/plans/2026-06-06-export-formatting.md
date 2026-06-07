# Export Formatting Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the rendered look of every export format (`.csv`, `.json`, `.txt`, `.md`, `_ai_ready.txt`) by adding day headers, gap markers, indented continuation paragraphs, and explicit placeholders for empty-looking rows.

**Architecture:** All edits land inside the existing single-file exporter `imessage_export.py`. Three new pure helpers (`format_day_label`, `format_gap`, `iter_render_events`) drive a uniform day/gap stream that the three text-based writers consume. The `format_message_body` helper is patched to render edited-but-empty rows with explicit text. CSV gains one column; JSON gains one per-message field. No new dependencies; stdlib only.

**Tech Stack:** Python 3.10+, stdlib `datetime`, `unittest`, `csv`, `json`. No `pip install`.

---

## File Structure

**Modify:** `imessage_export.py` — all writer functions, the body-rendering helper, and the analysis prompt string.

**Create:** `tests/test_formatting.py` — stdlib `unittest` regression suite covering the new helpers + writer outputs against in-memory `Message` fixtures.

**Modify:** `docs/superpowers/specs/2026-06-06-export-formatting-design.md` is the spec; no further changes during implementation.

---

## Task 1: Add formatting helpers and day/gap event stream

**Files:**
- Modify: `imessage_export.py` (insert helpers immediately before `def format_message_body`, around line 776)
- Test: `tests/test_formatting.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_formatting.py`:

```python
"""Tests for the day/gap/continuation formatting helpers used by every
text-based writer. Pure-Python; no chat.db access."""
import unittest
from dataclasses import dataclass, field
from datetime import datetime

from imessage_export import (
    GAP_THRESHOLD_SECONDS,
    format_day_label,
    format_gap,
    iter_render_events,
)


@dataclass
class Stub:
    """Minimal stand-in for Message — only the fields the renderers read."""
    timestamp: str
    author_label: str = "Ben"
    text: str = ""
    is_from_me: int = 1
    kind: str = "message"
    is_edited: int = 0
    has_attachment: int = 0
    attachment_filenames: list = field(default_factory=list)
    reaction: dict = None
    app_bundle: str = None


class FormatDayLabelTests(unittest.TestCase):
    def test_full_human_label(self):
        dt = datetime(2026, 6, 6, 9, 0, 0)
        self.assertEqual(format_day_label(dt), "Saturday, June 6, 2026")

    def test_no_leading_zero_on_day_of_month(self):
        dt = datetime(2026, 6, 1, 9, 0, 0)
        self.assertEqual(format_day_label(dt), "Monday, June 1, 2026")

    def test_two_digit_day(self):
        dt = datetime(2026, 6, 15, 9, 0, 0)
        self.assertEqual(format_day_label(dt), "Monday, June 15, 2026")


class FormatGapTests(unittest.TestCase):
    def test_minutes_only(self):
        self.assertEqual(format_gap(45 * 60), "45 min later")

    def test_minutes_threshold_boundary(self):
        self.assertEqual(format_gap(30 * 60), "30 min later")

    def test_hour_round(self):
        self.assertEqual(format_gap(3600), "1h later")

    def test_hour_and_minutes(self):
        self.assertEqual(format_gap(2 * 3600 + 15 * 60), "2h 15min later")

    def test_one_day(self):
        self.assertEqual(format_gap(86400), "1 day later")

    def test_multi_days(self):
        self.assertEqual(format_gap(3 * 86400 + 4 * 3600), "3 days later")

    def test_negative_clamped(self):
        # Defensive: malformed inputs shouldn't produce '-5 min later'.
        self.assertEqual(format_gap(-30), "0 min later")


class IterRenderEventsTests(unittest.TestCase):
    def test_empty_messages_yields_nothing(self):
        self.assertEqual(list(iter_render_events([])), [])

    def test_first_message_emits_day_then_msg(self):
        m = Stub(timestamp="2026-06-06 09:00:00")
        events = list(iter_render_events([m]))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][0], "day")
        self.assertEqual(events[0][1], datetime(2026, 6, 6, 9, 0, 0))
        self.assertEqual(events[1], ("msg", m))

    def test_two_messages_same_day_close_together_no_gap(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00")
        m2 = Stub(timestamp="2026-06-06 09:10:00")
        events = list(iter_render_events([m1, m2]))
        # day, m1, m2 — no gap because delta < threshold
        self.assertEqual([e[0] for e in events], ["day", "msg", "msg"])

    def test_two_messages_same_day_far_apart_emits_gap(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00")
        m2 = Stub(timestamp="2026-06-06 10:00:00")
        events = list(iter_render_events([m1, m2]))
        # day, m1, gap(3600s), m2
        self.assertEqual([e[0] for e in events], ["day", "msg", "gap", "msg"])
        self.assertEqual(events[2][1], 3600)

    def test_day_change_emits_day_not_gap(self):
        m1 = Stub(timestamp="2026-06-06 23:55:00")
        m2 = Stub(timestamp="2026-06-07 00:05:00")  # 10 min later in clock, new day
        events = list(iter_render_events([m1, m2]))
        # day, m1, day, m2 — no gap event because date changed
        self.assertEqual([e[0] for e in events], ["day", "msg", "day", "msg"])

    def test_unparseable_timestamp_still_yields_message(self):
        m = Stub(timestamp="not-a-timestamp")
        events = list(iter_render_events([m]))
        self.assertEqual(events, [("msg", m)])

    def test_gap_threshold_constant_value(self):
        self.assertEqual(GAP_THRESHOLD_SECONDS, 30 * 60)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m unittest tests.test_formatting -v 2>&1 | tail -20
```

Expected: ImportError on `GAP_THRESHOLD_SECONDS`, `format_day_label`, `format_gap`, `iter_render_events` — none of these exist yet.

- [ ] **Step 3: Add the helpers to `imessage_export.py`**

Insert immediately before `def format_message_body(m: Message) -> str:` (around line 776):

```python
# ---------------------------------------------------------------------------
# Day / gap rendering helpers (used by .txt, _ai_ready.txt, .md)
# ---------------------------------------------------------------------------

GAP_THRESHOLD_SECONDS = 30 * 60


def format_day_label(dt: datetime) -> str:
    """Human-readable day label, e.g. 'Saturday, June 6, 2026'.

    Uses string concatenation around %A / %B / %Y to avoid relying on
    the %-d strftime token (GNU extension; not portable to Windows libc).
    """
    return dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(", %Y")


def format_gap(seconds: int) -> str:
    """Human label for a silence between two messages.

    '45 min later' / '1h later' / '2h 15min later' / '1 day later' /
    '3 days later'. Negative inputs clamp to 0 (defensive — should never
    happen because messages are read ORDER BY date ASC).
    """
    if seconds < 0:
        seconds = 0
    days = seconds // 86400
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} later"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours >= 1:
        return f"{hours}h later" if minutes == 0 else f"{hours}h {minutes}min later"
    return f"{minutes} min later"


def _parse_local_ts(ts: str) -> Optional[datetime]:
    """Parse the writer-side 'YYYY-MM-DD HH:MM:SS' local timestamp string.
    Returns None for anything that doesn't match — callers fall back to
    yielding the message verbatim with no day/gap framing."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def iter_render_events(messages):
    """Walk messages and emit ('day', dt) / ('gap', seconds) / ('msg', m).

    - 'day' fires every time the calendar date changes, including before
      the very first message.
    - 'gap' fires only when the prior message is on the SAME calendar day
      AND (current - prior) >= GAP_THRESHOLD_SECONDS, so we never emit
      a gap marker right after a day header.
    """
    prev_dt = None
    prev_date = None
    for m in messages:
        dt = _parse_local_ts(m.timestamp)
        if dt is None:
            yield ("msg", m)
            continue
        date = dt.date()
        if date != prev_date:
            yield ("day", dt)
            prev_date = date
        elif prev_dt is not None:
            delta = int((dt - prev_dt).total_seconds())
            if delta >= GAP_THRESHOLD_SECONDS:
                yield ("gap", delta)
        yield ("msg", m)
        prev_dt = dt
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m unittest tests.test_formatting -v 2>&1 | tail -20
```

Expected: All 13 tests pass. The existing 26 decoder tests are unaffected; run them together to confirm no regression:

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -10
```

Expected: `Ran 39 tests in <1s — OK`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_formatting.py imessage_export.py
git commit -m "feat: add day-header / gap / continuation formatting helpers"
```

---

## Task 2: Render edited-but-empty rows with explicit placeholder text

**Files:**
- Modify: `imessage_export.py` — `format_message_body`, around line 803.
- Test: `tests/test_formatting.py` — append new test class.

- [ ] **Step 1: Add the failing test**

Append to `tests/test_formatting.py`:

```python
from imessage_export import format_message_body


class FormatMessageBodyEditedEmptyTests(unittest.TestCase):
    def test_edited_with_text_keeps_old_marker(self):
        m = Stub(text="hello", is_edited=1)
        self.assertEqual(format_message_body(m), "[edited] hello")

    def test_edited_with_no_text_no_attachment_uses_explicit_marker(self):
        m = Stub(text="", is_edited=1)
        self.assertEqual(format_message_body(m), "[edited; text not available]")

    def test_edited_with_attachment_only_keeps_old_marker(self):
        m = Stub(
            text="", is_edited=1, has_attachment=1,
            attachment_filenames=["photo.jpg"],
        )
        self.assertEqual(
            format_message_body(m),
            "[edited] [Attachments: photo.jpg]",
        )

    def test_unedited_empty_message_unchanged(self):
        m = Stub(text="", is_edited=0)
        self.assertEqual(format_message_body(m), "")
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m unittest tests.test_formatting.FormatMessageBodyEditedEmptyTests -v
```

Expected: `test_edited_with_no_text_no_attachment_uses_explicit_marker` fails with `'[edited]' != '[edited; text not available]'`. Others pass.

- [ ] **Step 3: Patch `format_message_body`**

In `imessage_export.py`, replace:

```python
    if m.is_edited and m.kind == "message":
        parts.insert(0, "[edited]")
```

with:

```python
    if m.is_edited and m.kind == "message":
        if parts:
            parts.insert(0, "[edited]")
        else:
            parts.append("[edited; text not available]")
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m unittest tests.test_formatting -v 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export.py tests/test_formatting.py
git commit -m "feat: explicit placeholder for edited-but-empty messages"
```

---

## Task 3: Rewrite `write_txt` to use day headers, gap markers, indented continuation

**Files:**
- Modify: `imessage_export.py` — `format_txt_line` removed, `render_txt_message` added, `write_txt` rewritten. Lines ~813-821.
- Test: `tests/test_formatting.py` — append new test class.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_formatting.py`:

```python
from pathlib import Path
import tempfile

from imessage_export import render_txt_message, write_txt


class RenderTxtMessageTests(unittest.TestCase):
    def test_single_line_full_time(self):
        m = Stub(timestamp="2026-06-06 09:00:08", author_label="Ben", text="hi")
        self.assertEqual(
            render_txt_message(m, time_format="full"),
            "[2026-06-06 09:00:08] Ben: hi",
        )

    def test_single_line_time_only(self):
        m = Stub(timestamp="2026-06-06 09:00:08", author_label="Ben", text="hi")
        self.assertEqual(
            render_txt_message(m, time_format="time"),
            "[09:00:08] Ben: hi",
        )

    def test_multi_paragraph_indents_continuation(self):
        m = Stub(
            timestamp="2026-06-06 09:00:08",
            author_label="Mallory",
            text="Para one.\n\nPara two.\n\nPara three.",
        )
        out = render_txt_message(m, time_format="time")
        self.assertEqual(
            out,
            "[09:00:08] Mallory: Para one.\n"
            "\n"
            "    Para two.\n"
            "\n"
            "    Para three.",
        )


class WriteTxtTests(unittest.TestCase):
    def _write(self, messages) -> str:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.txt"
            write_txt(p, messages)
            return p.read_text()

    def test_single_message_day_header_present(self):
        m = Stub(timestamp="2026-06-06 09:00:08", text="hi")
        out = self._write([m])
        self.assertIn("── Saturday, June 6, 2026 ──", out)
        self.assertIn("[09:00:08] Ben: hi", out)

    def test_gap_marker_inserted_mid_day(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        m2 = Stub(timestamp="2026-06-06 10:00:00", text="back")
        out = self._write([m1, m2])
        self.assertIn("── 1h later ──", out)

    def test_no_gap_when_close_together(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        m2 = Stub(timestamp="2026-06-06 09:05:00", text="back")
        out = self._write([m1, m2])
        self.assertNotIn("later ──", out)

    def test_day_change_emits_second_day_header_no_gap(self):
        m1 = Stub(timestamp="2026-06-06 23:55:00", text="night")
        m2 = Stub(timestamp="2026-06-07 00:05:00", text="morning")
        out = self._write([m1, m2])
        self.assertIn("── Saturday, June 6, 2026 ──", out)
        self.assertIn("── Sunday, June 7, 2026 ──", out)
        self.assertNotIn("later ──", out)

    def test_indented_continuation_in_output(self):
        m = Stub(
            timestamp="2026-06-06 09:00:00",
            author_label="Mallory",
            text="One.\n\nTwo.",
        )
        out = self._write([m])
        self.assertIn("[09:00:00] Mallory: One.", out)
        self.assertIn("    Two.", out)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m unittest tests.test_formatting.RenderTxtMessageTests tests.test_formatting.WriteTxtTests -v
```

Expected: ImportError on `render_txt_message`, and `write_txt` is the old single-line-per-message version (some tests pass, indentation tests fail).

- [ ] **Step 3: Replace `format_txt_line` and `write_txt`**

In `imessage_export.py`, delete the existing `format_txt_line` function (lines ~813-815) and the existing `write_txt` (lines ~818-821). Replace with:

```python
def render_txt_message(m: "Message", *, time_format: str = "full",
                       indent: str = "    ") -> str:
    """Render one message as one or more plain-text lines.

    time_format:
      'full' → '[YYYY-MM-DD HH:MM:SS] Author: body'
      'time' → '[HH:MM:SS] Author: body'

    Multi-paragraph bodies leave the first line on the speaker line and
    indent each subsequent non-blank paragraph by `indent`. Blank lines
    between paragraphs stay literally blank for visual separation.
    """
    if time_format == "time" and " " in m.timestamp:
        time_part = m.timestamp.split(" ", 1)[1]
    else:
        time_part = m.timestamp
    prefix = f"[{time_part}] {m.author_label}: "
    body = format_message_body(m)
    if "\n" not in body:
        return (prefix + body).rstrip()
    first, *rest = body.split("\n")
    lines = [(prefix + first).rstrip()]
    for line in rest:
        lines.append((indent + line) if line.strip() else "")
    return "\n".join(lines).rstrip()


def write_txt(path: Path, messages: list["Message"]):
    """Plain-text export with day headers, gap markers, and time-only line
    prefixes. The full date for each line is carried by the day header
    above it."""
    with path.open("w") as f:
        first = True
        for event in iter_render_events(messages):
            kind = event[0]
            if kind == "day":
                if not first:
                    f.write("\n")
                f.write(f"── {format_day_label(event[1])} ──\n\n")
                first = False
            elif kind == "gap":
                f.write(f"\n── {format_gap(event[1])} ──\n\n")
            else:
                f.write(render_txt_message(event[1], time_format="time") + "\n")
                first = False
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m unittest tests.test_formatting -v 2>&1 | tail -10
```

Expected: all formatting tests pass; existing decoder tests still pass:

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export.py tests/test_formatting.py
git commit -m "feat(txt): day headers, gap markers, indented continuation lines"
```

---

## Task 4: Rewrite `write_ai_ready` (keeps full datetime per line)

**Files:**
- Modify: `imessage_export.py` — `write_ai_ready`, lines ~824-856.
- Test: `tests/test_formatting.py` — append new test class.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_formatting.py`:

```python
from imessage_export import write_ai_ready


class WriteAiReadyTests(unittest.TestCase):
    META = {
        "participants": [{"resolved_name": "Mallory", "handle": "+14026608922"}],
        "me_name": "Ben",
        "message_count": 1,
        "actual_first_local": "2026-06-06 09:00:00",
        "actual_last_local": "2026-06-06 09:00:00",
        "window": {
            "local_start": "2026-06-06 08:30:00",
            "local_end": "2026-06-06 16:00:00",
            "utc_start": "2026-06-06T15:30:00+00:00",
            "utc_end": "2026-06-06T23:00:00+00:00",
            "tz": "PDT",
        },
    }

    def _write(self, messages, meta=None) -> str:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.txt"
            write_ai_ready(p, messages, meta or self.META)
            return p.read_text()

    def test_header_documents_day_header_convention(self):
        m = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        out = self._write([m])
        # Header must explain the new conventions so the LLM knows to ignore
        # day/gap markers and recognize indented continuations.
        self.assertIn("Day headers", out)
        self.assertIn("Indented", out)

    def test_full_datetime_prefix_preserved(self):
        m = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        out = self._write([m])
        # Even though there's a day header, AI-ready keeps the full date
        # on every message line for unambiguous attribution.
        self.assertIn("[2026-06-06 09:00:00] Ben: hi", out)

    def test_day_header_and_gap_marker_appear(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        m2 = Stub(timestamp="2026-06-06 10:00:00", text="back")
        out = self._write([m1, m2], meta={
            **self.META,
            "message_count": 2,
            "actual_last_local": "2026-06-06 10:00:00",
        })
        self.assertIn("── Saturday, June 6, 2026 ──", out)
        self.assertIn("── 1h later ──", out)
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m unittest tests.test_formatting.WriteAiReadyTests -v
```

Expected: all three new tests fail (current header doesn't mention day headers; current output has no day/gap markers).

- [ ] **Step 3: Replace `write_ai_ready`**

In `imessage_export.py`, replace the entire existing `write_ai_ready` body (lines ~824-856) with:

```python
def write_ai_ready(path: Path, messages: list["Message"], metadata: dict):
    """LLM-fed export. Same day-header / gap-marker / indented-continuation
    conventions as conversation.txt, but EACH MESSAGE LINE keeps the full
    [YYYY-MM-DD HH:MM:SS] prefix so an LLM never has to scan upward for the
    date when reasoning about attribution."""
    parts = metadata["participants"]
    participant_list = ", ".join(
        f"{p['resolved_name']} <{p['handle']}>" for p in parts
    ) or "(none resolved)"
    win = metadata["window"]
    header = [
        "iMessage conversation export — AI-ready",
        f"Participants (excluding 'Me'): {participant_list}",
        f"Me label: {metadata['me_name']}",
        f"Message count: {metadata['message_count']}",
        f"Date range (local): {metadata['actual_first_local']} → {metadata['actual_last_local']}",
        f"Requested window (local, {win['tz']}): {win['local_start']} → {win['local_end']}",
        f"Requested window (UTC): {win['utc_start']} → {win['utc_end']}",
        "Format: [YYYY-MM-DD HH:MM:SS] <Speaker>: <message>",
        "Day headers (── Day, Month D, Year ──) and gap markers "
        "(── X min later ──) are navigation aids inserted by the exporter, "
        "not authored content.",
        "Indented continuation lines (4 spaces) belong to the speaker on "
        "the line above.",
        "-" * 72,
        "",
    ]
    footer = [
        "",
        "-" * 72,
        "All messages above are attributed by exported sender metadata from "
        "iMessage where available. 'Me' = sender is the device owner "
        f"({metadata['me_name']}). Other speakers were resolved from the "
        "Messages handle table and, where provided, a local contacts CSV. "
        "Unmapped handles fall back to phone/email; truly missing handles "
        "appear as 'Unknown'.",
    ]
    with path.open("w") as f:
        f.write("\n".join(header))
        first = True
        for event in iter_render_events(messages):
            kind = event[0]
            if kind == "day":
                if not first:
                    f.write("\n")
                f.write(f"── {format_day_label(event[1])} ──\n\n")
                first = False
            elif kind == "gap":
                f.write(f"\n── {format_gap(event[1])} ──\n\n")
            else:
                f.write(render_txt_message(event[1], time_format="full") + "\n")
                first = False
        f.write("\n".join(footer) + "\n")
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export.py tests/test_formatting.py
git commit -m "feat(ai-ready): day headers + gap markers; header documents conventions"
```

---

## Task 5: Rewrite `write_markdown` for day headers, gap markers, time-only per-message line, empty-edited fallback

**Files:**
- Modify: `imessage_export.py` — `write_markdown`, lines ~859-906.
- Test: `tests/test_formatting.py` — append new test class.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_formatting.py`:

```python
from imessage_export import write_markdown


class WriteMarkdownTests(unittest.TestCase):
    META = {
        "participants": [{"resolved_name": "Mallory", "handle": "+14026608922"}],
        "me_name": "Ben",
        "message_count": 1,
        "actual_first_local": "2026-06-06 09:00:00",
        "actual_last_local": "2026-06-06 09:00:00",
        "window": {
            "local_start": "2026-06-06 08:30:00",
            "local_end": "2026-06-06 16:00:00",
            "utc_start": "2026-06-06T15:30:00+00:00",
            "utc_end": "2026-06-06T23:00:00+00:00",
            "tz": "PDT",
        },
        "chats": [{"display_name": "", "style": 0, "chat_identifier": "+14026608922", "is_group": False}],
    }

    def _write(self, messages, meta=None) -> str:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.md"
            write_markdown(p, messages, meta or self.META)
            return p.read_text()

    def test_day_header_uses_h2(self):
        m = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        out = self._write([m])
        self.assertIn("## Saturday, June 6, 2026", out)

    def test_per_message_header_drops_date(self):
        m = Stub(timestamp="2026-06-06 09:00:00", author_label="Ben", text="hi")
        out = self._write([m])
        self.assertIn("**09:00:00 · Ben**", out)
        # The old format included the full date — should be gone now.
        self.assertNotIn("**2026-06-06 09:00:00 · Ben**", out)

    def test_empty_edited_renders_placeholder(self):
        m = Stub(timestamp="2026-06-06 09:00:00", author_label="Mallory",
                 is_from_me=0, is_edited=1, text="")
        out = self._write([m])
        self.assertIn("_(edited; text not available)_", out)

    def test_gap_marker_renders_as_italic(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        m2 = Stub(timestamp="2026-06-06 10:00:00", text="back")
        out = self._write(
            [m1, m2],
            meta={**self.META, "message_count": 2,
                  "actual_last_local": "2026-06-06 10:00:00"},
        )
        self.assertIn("_── 1h later ──_", out)
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m unittest tests.test_formatting.WriteMarkdownTests -v
```

Expected: all four new tests fail (current MD writer uses full date in headers, no `##` day headers, no italic gap markers, blank body on empty-edited).

- [ ] **Step 3: Replace `write_markdown`**

In `imessage_export.py`, replace the entire body of `write_markdown` (lines ~859-906) with:

```python
def write_markdown(path: Path, messages: list["Message"], metadata: dict):
    """Notion/Obsidian-friendly markdown. Day headers (## Day, Mon D, Year),
    italic gap markers, per-message bold header with TIME-only prefix
    (the day is already in the header above), and an explicit fallback
    when an edited message has no recoverable text."""
    parts = metadata["participants"]
    participant_list = ", ".join(
        f"{p['resolved_name']} `<{p['handle']}>`" for p in parts
    ) or "_(none resolved)_"
    win = metadata["window"]
    title = chat_label(metadata)
    lines = [
        f"# iMessage conversation: {title}",
        "",
        f"**Participants** (excluding _Me_): {participant_list}  ",
        f"**Me label:** {metadata['me_name']}  ",
        f"**Messages:** {metadata['message_count']}  ",
        f"**Date range (local):** {metadata['actual_first_local']} → "
        f"{metadata['actual_last_local']}  ",
        f"**Window (local, {win['tz']}):** {win['local_start']} → {win['local_end']}  ",
        f"**Window (UTC):** {win['utc_start']} → {win['utc_end']}",
        "",
        "---",
        "",
    ]
    with path.open("w") as f:
        f.write("\n".join(lines))
        for event in iter_render_events(messages):
            kind = event[0]
            if kind == "day":
                f.write(f"\n## {format_day_label(event[1])}\n\n")
                continue
            if kind == "gap":
                f.write(f"_── {format_gap(event[1])} ──_\n\n")
                continue
            m = event[1]
            time_str = m.timestamp.split(" ", 1)[1] if " " in m.timestamp else m.timestamp
            f.write(f"**{time_str} · {m.author_label}**")
            if m.is_edited and m.kind == "message":
                f.write(" _(edited)_")
            f.write("\n\n")
            if m.kind == "tapback" and m.reaction:
                r = m.reaction
                rtype = r["type"]
                target_text = r.get("target_text") or ""
                target_author = r.get("target_author") or ""
                if target_text:
                    snippet = target_text if len(target_text) <= 120 else target_text[:117] + "…"
                    f.write(f"_{rtype}_ → **{target_author}**: > {snippet}\n\n")
                else:
                    f.write(f"_{rtype}_ (target message not in window)\n\n")
            elif m.kind == "unsent":
                f.write("_(unsent)_\n\n")
            elif m.kind == "app":
                f.write(f"_(app payload: `{m.app_bundle}`)_\n\n")
            elif m.text:
                f.write(m.text.rstrip() + "\n\n")
            elif m.is_edited:
                f.write("_(edited; text not available)_\n\n")
            if m.attachment_filenames:
                f.write(f"_Attachments: {', '.join(m.attachment_filenames)}_\n\n")
            elif m.has_attachment and not m.text and m.kind == "message":
                f.write("_(attachment)_\n\n")
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export.py tests/test_formatting.py
git commit -m "feat(md): day headers, gap markers, time-only per-message, empty-edited fallback"
```

---

## Task 6: CSV — add `local_date` column

**Files:**
- Modify: `imessage_export.py` — `write_csv`, lines ~740-767.
- Test: `tests/test_formatting.py` — append.

- [ ] **Step 1: Add the failing test**

Append to `tests/test_formatting.py`:

```python
import csv as csvmod

from imessage_export import write_csv


class WriteCsvLocalDateTests(unittest.TestCase):
    def test_local_date_column_present_and_populated(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        m2 = Stub(timestamp="2026-06-07 10:00:00", text="next day")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.csv"
            write_csv(p, [m1, m2])
            with p.open() as f:
                rows = list(csvmod.DictReader(f))
        self.assertIn("local_date", rows[0])
        self.assertEqual(rows[0]["local_date"], "2026-06-06")
        self.assertEqual(rows[1]["local_date"], "2026-06-07")

    def test_local_date_column_position_after_timestamp(self):
        m = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.csv"
            write_csv(p, [m])
            header = p.read_text().splitlines()[0].split(",")
        ts_idx = header.index("timestamp")
        self.assertEqual(header[ts_idx + 1], "local_date")
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m unittest tests.test_formatting.WriteCsvLocalDateTests -v
```

Expected: both fail — `local_date` not in current writer.

- [ ] **Step 3: Patch `write_csv`**

In `imessage_export.py`, in `write_csv`, change the `fields` list and the row-build loop:

```python
def write_csv(path: Path, messages: list[Message]):
    fields = [
        "message_id", "timestamp", "local_date", "timestamp_utc", "chat_id",
        "sender_handle", "is_from_me", "author_label",
        "kind", "is_edited", "reaction_type", "reaction_target",
        "app_bundle",
        "text", "has_attachment", "attachment_filenames",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for m in messages:
            row = asdict(m)
            row["attachment_filenames"] = "|".join(m.attachment_filenames)
            row["local_date"] = m.timestamp[:10] if m.timestamp else ""
            if m.reaction:
                row["reaction_type"] = m.reaction.get("type", "")
                target = m.reaction.get("target_text") or ""
                row["reaction_target"] = (target[:120] + "…") if len(target) > 120 else target
            else:
                row["reaction_type"] = ""
                row["reaction_target"] = ""
            w.writerow(row)
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export.py tests/test_formatting.py
git commit -m "feat(csv): add local_date column for easy day-filtering in spreadsheets"
```

---

## Task 7: JSON — add `gap_seconds_before` per-message field

**Files:**
- Modify: `imessage_export.py` — `write_json`, lines ~770-773.
- Test: `tests/test_formatting.py` — append.

- [ ] **Step 1: Add the failing test**

Append to `tests/test_formatting.py`:

```python
import json as jsonmod

from imessage_export import write_json


class WriteJsonGapTests(unittest.TestCase):
    META = {"chats": [], "participants": [], "me_name": "Ben",
            "message_count": 0, "window": {}}

    def _write(self, messages) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            write_json(p, messages, self.META)
            return jsonmod.loads(p.read_text())

    def test_first_message_gap_is_zero(self):
        m = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        m.timestamp_utc = "2026-06-06T16:00:00+00:00"
        payload = self._write([m])
        self.assertEqual(payload["messages"][0]["gap_seconds_before"], 0)

    def test_subsequent_gap_in_seconds(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        m1.timestamp_utc = "2026-06-06T16:00:00+00:00"
        m2 = Stub(timestamp="2026-06-06 10:30:00", text="back")
        m2.timestamp_utc = "2026-06-06T17:30:00+00:00"
        payload = self._write([m1, m2])
        self.assertEqual(payload["messages"][0]["gap_seconds_before"], 0)
        self.assertEqual(payload["messages"][1]["gap_seconds_before"], 5400)
```

NOTE: `Stub` doesn't declare `timestamp_utc` by default — the test sets it as an attribute. `dataclasses.asdict` requires the field to be declared, so update the `Stub` dataclass to add `timestamp_utc: str = ""` BEFORE running. Edit the Stub class at the top of `tests/test_formatting.py`:

```python
@dataclass
class Stub:
    timestamp: str
    timestamp_utc: str = ""           # ← add this
    author_label: str = "Ben"
    text: str = ""
    # … (rest unchanged)
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m unittest tests.test_formatting.WriteJsonGapTests -v
```

Expected: failures — `gap_seconds_before` not in current payload.

- [ ] **Step 3: Patch `write_json`**

Replace the existing `write_json` body:

```python
def write_json(path: Path, messages: list[Message], metadata: dict):
    """JSON export. Adds a `gap_seconds_before` field per message (computed
    from successive `timestamp_utc` values). First message has 0. Lets
    downstream consumers mirror the gap markers without re-parsing
    timestamps."""
    msg_dicts = []
    prev_dt = None
    for m in messages:
        d = asdict(m)
        gap = 0
        if m.timestamp_utc:
            try:
                dt = datetime.fromisoformat(m.timestamp_utc)
                if prev_dt is not None:
                    gap = int((dt - prev_dt).total_seconds())
                prev_dt = dt
            except (ValueError, TypeError):
                pass
        d["gap_seconds_before"] = gap
        msg_dicts.append(d)
    payload = {"metadata": metadata, "messages": msg_dicts}
    with path.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
```

NOTE: `dataclasses.asdict(m)` works because `Message.timestamp_utc` already exists on the real `Message` class (line 137 of `imessage_export.py`). The Stub change in Step 1 is only needed to make the test's `asdict` call match the real-Message behavior.

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export.py tests/test_formatting.py
git commit -m "feat(json): add gap_seconds_before per message"
```

---

## Task 8: Update `ANALYSIS_PROMPT` to document new conventions

**Files:**
- Modify: `imessage_export.py` — `ANALYSIS_PROMPT` constant, lines ~909-919.

- [ ] **Step 1: Add the failing test**

Append to `tests/test_formatting.py`:

```python
from imessage_export import ANALYSIS_PROMPT


class AnalysisPromptTests(unittest.TestCase):
    def test_documents_day_header_convention(self):
        self.assertIn("Day headers", ANALYSIS_PROMPT)

    def test_documents_indented_continuation(self):
        self.assertIn("indented", ANALYSIS_PROMPT.lower())
        # The note about indented lines belonging to the prior speaker
        # must be present so an LLM doesn't misattribute paragraphs.
        self.assertIn("continuation", ANALYSIS_PROMPT.lower())
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m unittest tests.test_formatting.AnalysisPromptTests -v
```

Expected: both fail.

- [ ] **Step 3: Replace `ANALYSIS_PROMPT`**

In `imessage_export.py`, replace the `ANALYSIS_PROMPT` constant with:

```python
ANALYSIS_PROMPT = """\
Analyze this iMessage conversation. Respect speaker attribution exactly as
labeled — each line is prefixed [YYYY-MM-DD HH:MM:SS] <Speaker>: <message>
and the speaker label is authoritative; do not relabel speakers or guess
the author of a line. Identify major themes, emotional shifts, conflict
patterns, communication habits, unanswered bids for connection, and any
notable timeline changes. Quote sparingly and only when a specific phrase
matters; prefer paraphrase. If a message is empty but marked
[Attachment], treat it as a non-text exchange of the labeled speaker.
Timestamps are in the exporter's local timezone as noted in the header.

Day headers (── Saturday, June 6, 2026 ──) and gap markers
(── 57 min later ──) are navigation aids inserted by the exporter — they
are not authored content; ignore them when quoting. An indented line
under a [time] Speaker: line is a continuation paragraph of that same
speaker's preceding message.
"""
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: full suite passes (`OK`).

- [ ] **Step 5: Commit**

```bash
git add imessage_export.py tests/test_formatting.py
git commit -m "docs(prompt): document day headers and continuation conventions for LLMs"
```

---

## Task 9: Final verification — end-to-end smoke test

**Files:** none modified.

- [ ] **Step 1: Run the full suite**

```bash
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: total test count = 26 (original) + roughly 30 new = ~56; line ends with `OK`.

- [ ] **Step 2: Sanity-check the syntax compiles**

```bash
python3 -c "import imessage_export; print('module loads:', bool(imessage_export.write_txt))"
```

Expected: `module loads: True`.

- [ ] **Step 3 (optional, requires Full Disk Access): re-export the existing window and diff against pre-change**

If `chat.db` is accessible to this terminal:

```bash
python3 imessage_export.py --participant Mallory \
    --date 2026-06-06 --start-time 08:30 --end-time 16:00 \
    --me-name Ben --contacts contacts.csv \
    --output-dir /tmp/imessage-export-new
diff -ru exports/Mallory/2026-06-06 /tmp/imessage-export-new/Mallory/2026-06-06 | head -200
```

Expected: the diff shows day headers, gap markers, indented continuation paragraphs in `.txt` / `_ai_ready.txt` / `.md`; `local_date` column in `.csv`; `gap_seconds_before` field in `.json`. The first message (currently rendering as `Mallory: [edited]` with no body) now reads `Mallory: [edited; text not available]`.

If `chat.db` is NOT accessible (e.g., running outside Full Disk Access), skip this step — the unit tests above already exercise every code path.

- [ ] **Step 4: Final commit if anything dangling**

```bash
git status
```

Expected: clean working tree (everything committed via Tasks 1-8).

---

## Self-Review

**Spec coverage:**
- §"New helpers" → Task 1
- §"`conversation.txt`" → Task 3
- §"`conversation_ai_ready.txt`" → Task 4
- §"`conversation.md`" → Task 5
- §"`conversation.csv`" → Task 6
- §"`conversation.json`" → Task 7
- §"Empty-message rendering" → Tasks 2 (helper) + 5 (markdown parallel path)
- §"Analysis prompt" → Task 8
- §"Migration" / "Risks" → no task needed (documentation only)
- §"Verification" → Task 9

All spec sections mapped.

**Placeholder scan:** no TBD / TODO / "appropriate error handling" / "similar to Task N" anywhere. Each step contains the actual code.

**Type consistency:**
- `format_day_label(dt)` — same signature in Task 1 helpers and Task 5's `write_markdown`.
- `format_gap(seconds: int)` — same signature in Task 1, Task 3, Task 4, Task 5.
- `iter_render_events(messages)` — consumed identically in Tasks 3, 4, 5 (`event[0]` tag string, `event[1]` payload).
- `render_txt_message(m, *, time_format, indent)` — defined in Task 3, reused identically in Task 4.
- `Stub` test fixture — Task 7 adds `timestamp_utc: str = ""` to it (called out inline so no surprise).

Plan complete.
