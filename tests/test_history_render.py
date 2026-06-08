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


class TestChunkRender(unittest.TestCase):

    def test_build_assembles_base_text_from_format_row(self):
        msgs = [_msg(1, text="hi"), _msg(2, text="there")]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        # The unstyled base Text holds the concatenation of all rows
        # plus exactly one day header at the top.
        plain = chunk.base.plain
        self.assertIn("── Thursday, January 1, 2026 ──\n", plain)
        self.assertIn("[9:00 AM] Me: hi\n", plain)
        self.assertIn("[9:00 AM] Me: there\n", plain)

    def test_row_offsets_slice_back_to_each_rows_text(self):
        msgs = [_msg(1, text="hi"), _msg(2, text="there")]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        for m in msgs:
            start, end = chunk.row_offsets[m.message_id]
            slice_text = chunk.base.plain[start:end]
            # Each row's slice contains its gutter, ts, speaker, body,
            # and trailing newline — exactly what format_row produced.
            self.assertTrue(slice_text.startswith("  "))   # gutter
            self.assertIn(f"Me: {m.text}", slice_text)
            self.assertTrue(slice_text.endswith("\n"))

    def test_row_line_counts_match_body_wraps(self):
        msgs = [_msg(1, text="single"), _msg(2, text="two\nlines"),
                _msg(3, text="three\nfour\nfive")]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        self.assertEqual(chunk.row_line_counts[1], 1)
        self.assertEqual(chunk.row_line_counts[2], 2)
        self.assertEqual(chunk.row_line_counts[3], 3)

    def test_day_header_prefix_count_advances_at_boundary(self):
        # Two messages same day, then one on a new day.
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00"),
            _msg(2, ts="2026-01-01 10:00:00"),
            _msg(3, ts="2026-01-02 09:00:00"),
        ]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        # `day_header_prefix_count[i]` is the count of day-header lines
        # rendered ABOVE msg_ids[i] in the blob (i.e. the cumulative
        # header count at the time msg_ids[i] is emitted). For msg 1:
        # the day-1 header sits above it → 1. For msg 2: still under
        # the same day-1 header → 1. For msg 3: both day-1 and day-2
        # headers sit above it → 2.
        self.assertEqual(chunk.day_header_prefix_count, [1, 1, 2])

    def test_msg_ids_preserved_in_order(self):
        msgs = [_msg(5), _msg(3), _msg(8)]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        self.assertEqual(chunk.msg_ids, [5, 3, 8])

    def test_row_offsets_round_trip_across_day_boundary(self):
        """Multi-day chunks insert a `\n` separator before each new
        day-header. Those separator bytes must NOT bleed into either
        row's slice — row_offsets must continue to bracket exactly the
        rendered message line plus its trailing newline, with the
        separator and the header living in the gap between rows.
        Task 4's paint() relies on this clean bracketing to add
        selection spans without smearing onto the day-header text."""
        msgs = [_msg(1, ts="2026-01-01 09:00:00", text="a"),
                _msg(2, ts="2026-01-02 09:00:00", text="b")]
        chunk = history_render._ChunkRender.build(msgs, contacts={})
        for m in msgs:
            s, e = chunk.row_offsets[m.message_id]
            slice_text = chunk.base.plain[s:e]
            self.assertIn(f"Me: {m.text}", slice_text)
            self.assertTrue(slice_text.endswith("\n"))
            # The slice must NOT include the day header text — clicks
            # on the header (which the painter never reaches) must not
            # smear into adjacent rows' offsets.
            self.assertNotIn("──", slice_text)

    def test_build_empty_messages_returns_empty_chunk(self):
        """Build with no messages must return an empty-but-valid
        _ChunkRender — used by HistoryView during placeholder/loading
        states. Must NOT raise (empty for-loop is the contract)."""
        chunk = history_render._ChunkRender.build([], contacts={})
        self.assertEqual(chunk.msg_ids, [])
        self.assertEqual(chunk.row_offsets, {})
        self.assertEqual(chunk.row_line_counts, {})
        self.assertEqual(chunk.day_header_prefix_count, [])
        self.assertEqual(chunk.base.plain, "")
        self.assertIsNone(chunk.widget)


class TestSelectionColors(unittest.TestCase):

    def test_dawnfox_palette(self):
        from imessage_export.tui.theme import DAWNFOX
        c = history_render.selection_colors(DAWNFOX)
        self.assertEqual(c.endpoint_bg, DAWNFOX["accent_alt"])
        self.assertEqual(c.range_bg, DAWNFOX["accent"])
        self.assertEqual(c.cursor_tint_bg, DAWNFOX["bg_alt"])
        # Cursor bar's default color is accent_alt; when on an endpoint
        # row (already accent_alt bg), the painter flips it to accent.
        self.assertEqual(c.cursor_bar_default, DAWNFOX["accent_alt"])
        self.assertEqual(c.cursor_bar_on_endpoint, DAWNFOX["accent"])
        self.assertEqual(c.cursor_bar_on_in_range, DAWNFOX["accent_alt"])
        self.assertEqual(c.contrast_fg, DAWNFOX["bg"])

    def test_terafox_palette(self):
        from imessage_export.tui.theme import TERAFOX
        c = history_render.selection_colors(TERAFOX)
        self.assertEqual(c.endpoint_bg, TERAFOX["accent_alt"])
        self.assertEqual(c.range_bg, TERAFOX["accent"])
        self.assertEqual(c.cursor_tint_bg, TERAFOX["bg_alt"])
        self.assertEqual(c.contrast_fg, TERAFOX["bg"])

    def test_missing_keys_return_empty_strings(self):
        sparse = {"accent": "#000000"}  # only one of the keys present
        c = history_render.selection_colors(sparse)
        self.assertEqual(c.range_bg, "#000000")
        self.assertEqual(c.endpoint_bg, "")
        self.assertEqual(c.contrast_fg, "")


if __name__ == "__main__":
    unittest.main()
