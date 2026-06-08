"""Layer 1 pure unit tests for history_render module.

These tests run without a Textual app — they exercise the formatting,
caching, and span-overlay logic in isolation. Fast (< 50 ms total)
and catch regressions in pure-function semantics before the
HistoryView integration tests run.
"""
from __future__ import annotations

import unittest

from imessage_export.models import Message
from imessage_export.tui.app import history_render


def _msg(message_id: int, *, ts="2026-01-01 09:00:00", text="hello",
         speaker="Me", kind="message") -> Message:
    return Message(
        message_id=message_id,
        timestamp=ts,
        timestamp_utc=ts.replace(" ", "T") + "+00:00",
        chat_id=1,
        sender_handle=None,
        is_from_me=1,
        author_label=speaker,
        text=text,
        has_attachment=0,
        attachment_filenames=[],
        kind=kind,
        is_edited=0,
        reaction=None,
        app_bundle=None,
    )


class TestFormatRow(unittest.TestCase):

    def test_single_line_body(self):
        segments, line_count = history_render.format_row(_msg(1, text="hi"), {})
        # Segments: [gutter, ts, speaker, body, newline]
        self.assertEqual(len(segments), 5)
        gutter_text, _ = segments[0]
        self.assertEqual(gutter_text, "  ")  # always 2 spaces — no ▸ content
        ts_text, _ = segments[1]
        self.assertEqual(ts_text, "[9:00 AM] ")
        speaker_text, _ = segments[2]
        self.assertEqual(speaker_text, "Me: ")
        body_text, _ = segments[3]
        self.assertEqual(body_text, "hi")
        nl_text, _ = segments[4]
        self.assertEqual(nl_text, "\n")
        self.assertEqual(line_count, 1)

    def test_multi_line_body_wraps_with_12_col_indent(self):
        segments, line_count = history_render.format_row(
            _msg(2, text="line 1\nline 2"), {})
        body_text, _ = segments[3]
        self.assertEqual(body_text, "line 1\n" + " " * 12 + "line 2")
        self.assertEqual(line_count, 2)

    def test_three_line_body_counts_three(self):
        segments, line_count = history_render.format_row(
            _msg(3, text="a\nb\nc"), {})
        self.assertEqual(line_count, 3)

    def test_empty_body(self):
        segments, line_count = history_render.format_row(
            _msg(4, text=""), {})
        body_text, _ = segments[3]
        self.assertEqual(body_text, "")
        self.assertEqual(line_count, 1)

    def test_click_meta_present_on_every_segment(self):
        from rich.style import Style
        segments, _ = history_render.format_row(_msg(7, text="hi"), {})
        # Every segment's style carries meta={"msg_id": 7}.
        for text, style in segments:
            self.assertIsInstance(style, Style)
            self.assertEqual(style.meta.get("msg_id"), 7)


if __name__ == "__main__":
    unittest.main()
