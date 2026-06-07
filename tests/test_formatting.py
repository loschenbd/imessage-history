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
    timestamp: str = "2026-06-06 09:00:00"
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
        self.assertEqual([e[0] for e in events], ["day", "msg", "msg"])

    def test_two_messages_same_day_far_apart_emits_gap(self):
        m1 = Stub(timestamp="2026-06-06 09:00:00")
        m2 = Stub(timestamp="2026-06-06 10:00:00")
        events = list(iter_render_events([m1, m2]))
        self.assertEqual([e[0] for e in events], ["day", "msg", "gap", "msg"])
        self.assertEqual(events[2][1], 3600)

    def test_day_change_emits_day_not_gap(self):
        m1 = Stub(timestamp="2026-06-06 23:55:00")
        m2 = Stub(timestamp="2026-06-07 00:05:00")
        events = list(iter_render_events([m1, m2]))
        self.assertEqual([e[0] for e in events], ["day", "msg", "day", "msg"])

    def test_unparseable_timestamp_still_yields_message(self):
        m = Stub(timestamp="not-a-timestamp")
        events = list(iter_render_events([m]))
        self.assertEqual(events, [("msg", m)])

    def test_gap_threshold_constant_value(self):
        self.assertEqual(GAP_THRESHOLD_SECONDS, 30 * 60)


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
        self.assertIn("Day headers", out)
        self.assertIn("Indented", out)

    def test_full_datetime_prefix_preserved(self):
        m = Stub(timestamp="2026-06-06 09:00:00", text="hi")
        out = self._write([m])
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


if __name__ == "__main__":
    unittest.main()
