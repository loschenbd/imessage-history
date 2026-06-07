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


if __name__ == "__main__":
    unittest.main()
