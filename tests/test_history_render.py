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


class TestClassify(unittest.TestCase):
    """RowKind taxonomy: NORMAL vs every flavor of non-body row.

    The redesign renders REACTION/UNSENT/APP/EMPTY_* as italic+muted
    footnotes — separate from speaker headers and indented bodies —
    so classification has to be a fixed enum the renderer can switch on.
    """

    def test_normal_message_with_body(self):
        m = _msg(1, text="hello there")
        self.assertEqual(history_render.classify(m), history_render.RowKind.NORMAL)

    def test_tapback_kind_is_reaction(self):
        m = _msg(1, kind="tapback")
        m.reaction = {"type": "Loved", "target_text": "ok"}
        self.assertEqual(history_render.classify(m), history_render.RowKind.REACTION)

    def test_unsent_kind(self):
        m = _msg(1, kind="unsent", text="")
        self.assertEqual(history_render.classify(m), history_render.RowKind.UNSENT)

    def test_app_kind(self):
        m = _msg(1, kind="app", text="")
        m.app_bundle = "com.apple.messages.URLBalloonProvider"
        self.assertEqual(history_render.classify(m), history_render.RowKind.APP)

    def test_empty_message_with_attachment(self):
        m = _msg(1, text="")
        m.has_attachment = 1
        self.assertEqual(
            history_render.classify(m),
            history_render.RowKind.EMPTY_ATTACHMENT,
        )

    def test_empty_message_no_attachment(self):
        # No text, no attachment — the silent "edited / retracted /
        # unknown" row that used to render as a blank speaker line.
        m = _msg(1, text="")
        self.assertEqual(
            history_render.classify(m),
            history_render.RowKind.EMPTY_NO_CONTENT,
        )

    def test_normal_wins_over_attachment_when_text_present(self):
        # Text + attachment together — NORMAL renders the body; the
        # attachment is surfaced elsewhere (writers/exports). Keeps
        # the renderer from accidentally swapping a real reply for
        # "(attachment)".
        m = _msg(1, text="here is the file")
        m.has_attachment = 1
        self.assertEqual(history_render.classify(m), history_render.RowKind.NORMAL)


class TestRenderBody(unittest.TestCase):
    """`_render_body` returns (body_text, style_spec) per RowKind.

    These tests pin the literal body strings and the style spec class
    ("muted", "muted italic", etc.) so the renderer can't silently
    drop italics or change the snippet shape under us.
    """

    def test_normal_returns_text_in_default_style(self):
        m = _msg(1, text="hi there")
        body, style = history_render._render_body(m, history_render.RowKind.NORMAL)
        self.assertEqual(body, "hi there")
        self.assertEqual(style, "")

    def test_reaction_uses_glyph_and_quoted_snippet(self):
        m = _msg(1, kind="tapback")
        m.reaction = {"type": "Loved", "target_text": "dinner at 7?"}
        body, style = history_render._render_body(
            m, history_render.RowKind.REACTION)
        self.assertEqual(body, '♡ to "dinner at 7?"')
        self.assertEqual(style, "muted italic")

    def test_reaction_long_target_is_capped_with_ellipsis(self):
        # Cap exists so the reaction footnote can't push the body column
        # off-screen — the target was already shown when it was sent.
        long_target = "x" * 100
        m = _msg(1, kind="tapback")
        m.reaction = {"type": "Liked", "target_text": long_target}
        body, _ = history_render._render_body(
            m, history_render.RowKind.REACTION)
        self.assertTrue(body.endswith('…"'))
        # The snippet between the quotes is _REACTION_SNIPPET_MAX cells.
        opening = body.index('"') + 1
        closing = body.rindex('"')
        self.assertEqual(closing - opening,
                         history_render._REACTION_SNIPPET_MAX + 1)

    def test_reaction_missing_target_text_renders_empty_quotes(self):
        # Defensive: a tapback row exported before target_text capture
        # landed has no snippet. Don't crash, just show the glyph
        # against an empty quoted snippet so the row still parses.
        m = _msg(1, kind="tapback")
        m.reaction = {"type": "Liked"}
        body, _ = history_render._render_body(
            m, history_render.RowKind.REACTION)
        self.assertEqual(body, '👍 to ""')

    def test_reaction_unknown_type_falls_back_to_generic_marker(self):
        m = _msg(1, kind="tapback")
        m.reaction = {"type": "Mystery", "target_text": "ok"}
        body, _ = history_render._render_body(
            m, history_render.RowKind.REACTION)
        # Any non-empty placeholder is fine; the row must still read.
        self.assertIn('to "ok"', body)
        self.assertNotEqual(body.split(" to ", 1)[0], "")

    def test_unsent_body(self):
        m = _msg(1, kind="unsent", text="")
        body, style = history_render._render_body(
            m, history_render.RowKind.UNSENT)
        self.assertEqual(body, "(unsent)")
        self.assertEqual(style, "muted italic")

    def test_app_known_bundle_uses_short_name(self):
        m = _msg(1, kind="app", text="")
        m.app_bundle = "com.apple.messages.URLBalloonProvider"
        body, style = history_render._render_body(
            m, history_render.RowKind.APP)
        self.assertEqual(body, "(URL preview · app payload)")
        self.assertEqual(style, "muted italic")

    def test_app_extension_balloon_prefix_collapses_to_generic_label(self):
        m = _msg(1, kind="app", text="")
        m.app_bundle = "com.apple.messages.MSMessageExtensionBalloonPlugin:foo:bar"
        body, _ = history_render._render_body(m, history_render.RowKind.APP)
        self.assertEqual(body, "(App message · app payload)")

    def test_app_unknown_bundle_renders_bundle_id_verbatim(self):
        m = _msg(1, kind="app", text="")
        m.app_bundle = "com.third.party.unknown"
        body, _ = history_render._render_body(m, history_render.RowKind.APP)
        self.assertEqual(body, "(com.third.party.unknown · app payload)")

    def test_app_no_bundle_renders_generic_label(self):
        m = _msg(1, kind="app", text="")
        body, _ = history_render._render_body(m, history_render.RowKind.APP)
        self.assertEqual(body, "(app payload)")

    def test_empty_attachment_body(self):
        m = _msg(1, text="")
        m.has_attachment = 1
        body, style = history_render._render_body(
            m, history_render.RowKind.EMPTY_ATTACHMENT)
        self.assertEqual(body, "(attachment)")
        self.assertEqual(style, "muted italic")

    def test_empty_no_content_body(self):
        m = _msg(1, text="")
        body, style = history_render._render_body(
            m, history_render.RowKind.EMPTY_NO_CONTENT)
        self.assertEqual(body, "(no content)")
        self.assertEqual(style, "muted italic")

    def test_edited_appends_muted_marker_to_normal_body(self):
        m = _msg(1, text="oh hi")
        m.is_edited = 1
        body, _ = history_render._render_body(m, history_render.RowKind.NORMAL)
        # Spec: append ` (edited)` after the body in muted (non-italic).
        # `_render_body` returns the body STRING with the marker inline;
        # the styling layer is the renderer's problem.
        self.assertEqual(body, "oh hi (edited)")

    def test_edited_appends_to_reaction_body_too(self):
        m = _msg(1, kind="tapback")
        m.reaction = {"type": "Loved", "target_text": "ok"}
        m.is_edited = 1
        body, _ = history_render._render_body(
            m, history_render.RowKind.REACTION)
        self.assertEqual(body, '♡ to "ok" (edited)')


class TestFormatRunSingleMessage(unittest.TestCase):
    """Per-run emit shape on the simplest case: a one-message run.

    The locked contract:
      <blank line>?                  ← omitted when suppress_leading_blank
      <speaker>  ·  <h:mm AM/PM>     ← header line, meta = run[0].id
      <2-cell indent><body>          ← body line, meta = run[0].id
    """

    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX

    def _plain(self, segments):
        return "".join(t for t, _ in segments)

    def test_one_message_emits_blank_header_body(self):
        run = [_msg(1, text="hi")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=False, palette=self.palette)
        # "\n" separator + speaker line + body line.
        self.assertEqual(self._plain(result.segments),
                         "\nMe  ·  9:00 AM\n  hi\n")

    def test_suppress_leading_blank_drops_separator(self):
        # First run after a day header — the day header already
        # provides whitespace, so a second blank reads as a gap.
        run = [_msg(1, text="hi")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        self.assertEqual(self._plain(result.segments),
                         "Me  ·  9:00 AM\n  hi\n")

    def test_header_range_brackets_header_line(self):
        # The chunk builder layers cursor highlight on the header span
        # when cursor_id == run[0].message_id — this range pins where.
        run = [_msg(1, text="hi")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=False, palette=self.palette)
        plain = self._plain(result.segments)
        hs, he = result.header_range
        self.assertEqual(plain[hs:he], "Me  ·  9:00 AM\n")

    def test_body_range_brackets_body_line(self):
        # Selection bg paints across this range — must NOT include the
        # speaker header or the leading blank.
        run = [_msg(1, text="hi")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=False, palette=self.palette)
        plain = self._plain(result.segments)
        bs, be = result.msg_body_ranges[1]
        self.assertEqual(plain[bs:be], "  hi\n")

    def test_speaker_color_is_from_me_uses_accent_alt(self):
        # is_from_me=1 (default in _msg) → bold accent_alt for the
        # speaker name. Color is baked into the segment Style at build
        # time using palette hex (theme switch is rebuild-on-next-load,
        # not live).
        run = [_msg(1, text="hi", speaker="Me")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        speaker_seg = next(s for t, s in result.segments if t.startswith("Me"))
        rendered_style = str(speaker_seg)
        self.assertIn("bold", rendered_style)
        self.assertIn(self.palette["accent_alt"].lower(),
                      rendered_style.lower())

    def test_speaker_color_other_uses_accent(self):
        run = [_msg(1, text="hi", speaker="Bob")]
        run[0].is_from_me = 0
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        speaker_seg = next(s for t, s in result.segments if t.startswith("Bob"))
        rendered_style = str(speaker_seg)
        self.assertIn("bold", rendered_style)
        self.assertIn(self.palette["accent"].lower(), rendered_style.lower())

    def test_separator_and_time_are_muted(self):
        # The "  ·  " separator and the "h:mm AM/PM" time are both
        # rendered in palette muted so the speaker name dominates.
        run = [_msg(1, text="hi", speaker="Me")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        # Walk segments — find the dot separator and the time, confirm
        # their styles mention the muted hex.
        muted = self.palette["muted"].lower()
        sep_style = next(s for t, s in result.segments if "·" in t)
        time_style = next(s for t, s in result.segments if "AM" in t or "PM" in t)
        self.assertIn(muted, str(sep_style).lower())
        self.assertIn(muted, str(time_style).lower())

    def test_meta_msg_id_on_header_and_body_segments(self):
        # Header line click routes to run[0]'s msg_id so clicking on
        # "Beautiful Wife at 7:10 PM" behaves like clicking the body.
        run = [_msg(7, text="hi")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        plain = self._plain(result.segments)
        hs, he = result.header_range
        bs, be = result.msg_body_ranges[7]
        # Every non-blank-separator segment within [hs, be) carries meta.
        cursor = 0
        for text, style in result.segments:
            seg_start = cursor
            cursor += len(text)
            seg_end = cursor
            # Skip the leading blank separator (when present) — it's
            # outside [hs, be) and intentionally meta-less.
            if seg_end <= hs:
                continue
            self.assertEqual(
                style.meta.get("msg_id"), 7,
                f"segment {text!r} (range {seg_start}-{seg_end}) "
                f"missing msg_id meta",
            )

    def test_reaction_renders_muted_italic_body(self):
        run = [_msg(1, kind="tapback", speaker="Bob")]
        run[0].is_from_me = 0
        run[0].reaction = {"type": "Loved", "target_text": "ok"}
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        plain = self._plain(result.segments)
        # Header + indented body footnote.
        self.assertIn('  ♡ to "ok"\n', plain)
        # The body segment carries muted italic — the renderer wires
        # both the palette muted color and the italic modifier.
        body_seg = next(s for t, s in result.segments if "♡" in t)
        rendered = str(body_seg).lower()
        self.assertIn("italic", rendered)
        self.assertIn(self.palette["muted"].lower(), rendered)

    def test_line_count_width_aware_wraps_long_body(self):
        # _BODY_INDENT(2) + 30 char body = 32 cells; wrapped at width=20
        # is 2 rendered rows.
        run = [_msg(1, text="a" * 30)]
        result = history_render.format_run(
            run, contacts={}, width=20,
            suppress_leading_blank=True, palette=self.palette)
        self.assertEqual(result.msg_line_counts[1], 2)

    def test_line_count_width_none_falls_back_to_logical_lines(self):
        run = [_msg(1, text="a\nb\nc")]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        # 3 logical lines.
        self.assertEqual(result.msg_line_counts[1], 3)


class TestFormatRunMultiMessage(unittest.TestCase):
    """Multi-message runs: header, then run[0] body, then continuation
    lines with right-padded times that share a body column.
    """

    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX

    def _plain(self, segments):
        return "".join(t for t, _ in segments)

    def test_two_message_run_emits_header_two_bodies(self):
        run = [
            _msg(1, ts="2026-01-01 09:00:00", text="hey"),
            _msg(2, ts="2026-01-01 09:05:00", text="how are you"),
        ]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        plain = self._plain(result.segments)
        # Header, then run[0] body (no inline time), then continuation
        # body with inline time padded to the run's widest time.
        # Both times are "9:00 AM" / "9:05 AM" — 7 chars, max=7.
        # Body column = _BODY_INDENT(2) + 7 + 2 = 11. So
        # continuation prefix is "  9:05 AM  " (2+7+2 = 11 cells).
        self.assertEqual(plain,
                         "Me  ·  9:00 AM\n"
                         "  hey\n"
                         "  9:05 AM  how are you\n")

    def test_three_message_run_aligns_continuation_body_column(self):
        # Mixed-width times in the run force right-padding on the
        # shorter labels so continuation bodies start at the same
        # column. Pinning this catches accidental drop of the
        # max(time_len) pre-pass.
        run = [
            _msg(1, ts="2026-01-01 09:00:00", text="a"),
            _msg(2, ts="2026-01-01 09:30:00", text="b"),
            _msg(3, ts="2026-01-01 12:00:00", text="c"),  # 12:00 PM = 8 chars
        ]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        plain = self._plain(result.segments)
        # max time len = 8 ("12:00 PM"). Body column = 2 + 8 + 2 = 12.
        # Continuations: "  9:30 AM   b" (2 + "9:30 AM" + " " pad + "  ")
        # and "  12:00 PM  c" (2 + "12:00 PM" + "  ").
        self.assertEqual(plain,
                         "Me  ·  9:00 AM\n"
                         "  a\n"
                         "  9:30 AM   b\n"
                         "  12:00 PM  c\n")

    def test_continuation_body_ranges_per_message(self):
        # Selection bg + cursor highlight on a continuation paint
        # across its body_range alone — must NOT smear onto the
        # speaker header or the previous message's body.
        run = [
            _msg(1, ts="2026-01-01 09:00:00", text="a"),
            _msg(2, ts="2026-01-01 09:05:00", text="b"),
        ]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        plain = self._plain(result.segments)
        bs1, be1 = result.msg_body_ranges[1]
        bs2, be2 = result.msg_body_ranges[2]
        self.assertEqual(plain[bs1:be1], "  hey\n".replace("hey", "a"))
        # The continuation body range covers indent + padded time +
        # gap + body + trailing newline. Confirms time prefix is
        # claimed by the continuation msg's range — clicking on the
        # time still routes to that msg.
        self.assertEqual(plain[bs2:be2], "  9:05 AM  b\n")

    def test_continuation_segments_carry_their_own_msg_id_meta(self):
        # Each continuation row's segments tag with that msg's id so
        # click-to-mark on a continuation drops the mark on the right
        # message — not the run's head.
        run = [
            _msg(1, ts="2026-01-01 09:00:00", text="a"),
            _msg(2, ts="2026-01-01 09:05:00", text="b"),
        ]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        plain = self._plain(result.segments)
        bs2, _ = result.msg_body_ranges[2]
        # Find the segment(s) inside [bs2, be2) — each must carry id=2.
        cursor = 0
        for text, style in result.segments:
            seg_start = cursor
            cursor += len(text)
            seg_end = cursor
            if seg_start >= bs2 and seg_end > seg_start:
                # Segment inside (or overlapping) msg 2's body range.
                self.assertEqual(
                    style.meta.get("msg_id"), 2,
                    f"continuation segment {text!r} missing id=2",
                )

    def test_continuation_line_count_wrap_aware(self):
        # Continuation row body line is `indent + padded_time + gap +
        # body`. Wrap math must use that full prefix or long-body
        # continuations undercount their rendered rows and the cursor
        # screen-y drifts off-by-one per long continuation.
        run = [
            _msg(1, ts="2026-01-01 09:00:00", text="short"),
            _msg(2, ts="2026-01-01 09:00:00", text="z" * 50),
        ]
        # Both times 7 chars; body column = 2 + 7 + 2 = 11.
        # Continuation row at width=20: 11 + 50 = 61 cells →
        # ceil(61/20) = 4 rows.
        result = history_render.format_run(
            run, contacts={}, width=20,
            suppress_leading_blank=True, palette=self.palette)
        # msg 1's body line: "  short" + "\n" = 7 cells; wraps to 1 row.
        self.assertEqual(result.msg_line_counts[1], 1)
        self.assertGreaterEqual(result.msg_line_counts[2], 3)

    def test_continuation_with_reaction_kind_uses_muted_italic_body(self):
        # Reaction continuation in a same-speaker run — the time prefix
        # stays muted (it's metadata, not part of the footnote body),
        # but the body itself renders muted+italic.
        run = [
            _msg(1, ts="2026-01-01 09:00:00", text="a", speaker="Bob"),
            _msg(2, ts="2026-01-01 09:05:00", kind="tapback", speaker="Bob"),
        ]
        run[0].is_from_me = 0
        run[1].is_from_me = 0
        run[1].reaction = {"type": "Loved", "target_text": "ok"}
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        plain = self._plain(result.segments)
        self.assertIn('  9:05 AM  ♡ to "ok"\n', plain)
        # The body segment of the continuation carries italic.
        body_seg = next(s for t, s in result.segments
                        if "♡" in t)
        self.assertIn("italic", str(body_seg).lower())

    def test_run_header_meta_still_routes_to_run_head_only(self):
        # The speaker line carries run[0].message_id no matter how
        # many continuations follow — clicking on the speaker name
        # behaves like clicking the first body line.
        run = [
            _msg(7, ts="2026-01-01 09:00:00", text="a"),
            _msg(8, ts="2026-01-01 09:05:00", text="b"),
            _msg(9, ts="2026-01-01 09:10:00", text="c"),
        ]
        result = history_render.format_run(
            run, contacts={}, width=None,
            suppress_leading_blank=True, palette=self.palette)
        hs, he = result.header_range
        cursor = 0
        for text, style in result.segments:
            seg_start = cursor
            cursor += len(text)
            seg_end = cursor
            if seg_start >= hs and seg_end <= he:
                self.assertEqual(
                    style.meta.get("msg_id"), 7,
                    f"header segment {text!r} should route to run[0]",
                )


class TestRunGrouping(unittest.TestCase):
    """`_ChunkRender.build` groups consecutive messages into runs.

    Split conditions (any one triggers a new run):
      - different `author_label`
      - different calendar day
      - same speaker but ≥ 30 minute gap from the previous message

    The number of speaker headers in the rendered plain text equals
    the number of runs.
    """

    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX

    def _count_runs(self, plain: str) -> int:
        # Each run header is `<speaker>  ·  <h:mm AM/PM>`. The "  ·  "
        # separator appears nowhere else in the rendered blob (and is
        # NOT a substring of any time / body shape we render). Counting
        # those is the cheapest way to assert run boundaries.
        return plain.count("  ·  ")

    def test_three_messages_same_speaker_within_30min_one_run(self):
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00"),
            _msg(2, ts="2026-01-01 09:15:00"),
            _msg(3, ts="2026-01-01 09:25:00"),
        ]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        self.assertEqual(self._count_runs(chunk.base.plain), 1)

    def test_gap_exactly_30min_splits_run(self):
        # 9:00 → 9:30 is a 30-minute gap (`>= 30` per spec). The
        # boundary matters: under 30 min stays in the run, exactly 30
        # min starts a new one.
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00"),
            _msg(2, ts="2026-01-01 09:30:00"),
        ]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        self.assertEqual(self._count_runs(chunk.base.plain), 2)

    def test_gap_below_30min_keeps_one_run(self):
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00"),
            _msg(2, ts="2026-01-01 09:29:00"),
        ]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        self.assertEqual(self._count_runs(chunk.base.plain), 1)

    def test_speaker_change_starts_new_run(self):
        # Same minute, different speaker — still two runs.
        m1 = _msg(1, ts="2026-01-01 09:00:00", speaker="Me")
        m2 = _msg(2, ts="2026-01-01 09:00:30", speaker="Bob")
        m2.is_from_me = 0
        chunk = history_render._ChunkRender.build(
            [m1, m2], contacts={}, palette=self.palette)
        self.assertEqual(self._count_runs(chunk.base.plain), 2)

    def test_day_boundary_always_starts_new_run(self):
        # Same speaker, < 30 min wall-clock apart? No — different
        # calendar day overrides the time gap rule.
        msgs = [
            _msg(1, ts="2026-01-01 23:55:00"),
            _msg(2, ts="2026-01-02 00:10:00"),  # only 15 min later
        ]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        # Two day headers, two runs.
        plain = chunk.base.plain
        self.assertEqual(plain.count("Thursday, January 1, 2026"), 1)
        self.assertEqual(plain.count("Friday, January 2, 2026"), 1)
        self.assertEqual(self._count_runs(plain), 2)

    def test_first_run_after_day_header_has_no_leading_blank(self):
        # The day header already provides whitespace. A leading blank
        # there would read as two visual gaps between the day rule and
        # the speaker.
        msgs = [_msg(1, ts="2026-01-01 09:00:00", text="hi")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        plain = chunk.base.plain
        # Format: <day-header-line>\n<speaker line>\n<body>\n
        # No double-blank between day header and speaker.
        self.assertNotIn("──\n\n", plain.replace(" ", ""))
        # Speaker line is directly after the day header newline.
        day_end = plain.index("2026") + len("2026 ──\n")
        # Tolerate single-line of further rule chars from full-width
        # rendering — but the very next char must NOT be "\n".
        # (We assert this loosely by searching for the speaker token
        # within a few chars of the day header line end.)
        self.assertIn("Me", plain[day_end:day_end + 30])

    def test_non_first_run_emits_blank_separator(self):
        # Two same-day runs (different speakers) — the second run's
        # speaker line must be preceded by a blank separator so the
        # runs read as visually distinct blocks.
        m1 = _msg(1, ts="2026-01-01 09:00:00", speaker="Me", text="hi")
        m2 = _msg(2, ts="2026-01-01 09:01:00", speaker="Bob", text="yo")
        m2.is_from_me = 0
        chunk = history_render._ChunkRender.build(
            [m1, m2], contacts={}, palette=self.palette)
        plain = chunk.base.plain
        # Look for the blank line between "  hi\n" (msg 1's body) and
        # "Bob" (msg 2's speaker header).
        self.assertIn("hi\n\nBob", plain)


class TestDayHeader(unittest.TestCase):
    """Day separator: full-width rule when chunk knows its width,
    short-rule fallback when it doesn't."""

    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX

    def test_full_width_rule_when_width_known(self):
        msgs = [_msg(1, ts="2026-01-01 09:00:00")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette, width=60)
        plain = chunk.base.plain
        # The day-header line is `<rule><spaces+label+spaces><rule>`
        # filling exactly `width` cells. Pinning the exact cell count
        # catches accidental off-by-one in the math.
        header_line = next(line for line in plain.split("\n")
                           if "Thursday" in line)
        self.assertEqual(len(header_line), 60)
        # The label sits between two `─` rules, with 2-space padding
        # on each side.
        self.assertIn("  Thursday, January 1, 2026  ", header_line)
        # Rule chars on both sides.
        self.assertTrue(header_line.startswith("─"))
        self.assertTrue(header_line.endswith("─"))

    def test_short_rule_fallback_when_width_none(self):
        msgs = [_msg(1, ts="2026-01-01 09:00:00")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        # No width → keep the old short-rule format so chunks built
        # before mount don't render with a 0-wide rule.
        self.assertIn("── Thursday, January 1, 2026 ──\n", chunk.base.plain)


class TestChunkRenderHeaderOffsets(unittest.TestCase):
    """`header_offsets[run[0]]` lets the painter highlight the speaker
    line when the cursor is on the run's head message. Body span
    selection bg, by contrast, never touches the speaker line —
    `row_offsets` is body-only."""

    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX

    def test_header_offsets_brackets_speaker_line(self):
        msgs = [_msg(1, ts="2026-01-01 09:00:00", text="hi")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        hs, he = chunk.header_offsets[1]
        slice_text = chunk.base.plain[hs:he]
        self.assertEqual(slice_text, "Me  ·  9:00 AM\n")

    def test_header_offsets_only_for_run_heads(self):
        # In a run of 3 messages, only run[0] gets a header_offsets
        # entry. Continuations have row_offsets (body span) but no
        # speaker line to point at.
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00", text="a"),
            _msg(2, ts="2026-01-01 09:05:00", text="b"),
            _msg(3, ts="2026-01-01 09:10:00", text="c"),
        ]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        self.assertIn(1, chunk.header_offsets)
        self.assertNotIn(2, chunk.header_offsets)
        self.assertNotIn(3, chunk.header_offsets)

    def test_row_offsets_exclude_speaker_header(self):
        # Critical: row_offsets[run[0]] must NOT include the speaker
        # line bytes — otherwise selecting a range would smear endpoint
        # bg onto the speaker header (spec: "Header lines do not
        # carry selection bg").
        msgs = [_msg(1, ts="2026-01-01 09:00:00", text="hi")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        rs, re = chunk.row_offsets[1]
        self.assertEqual(chunk.base.plain[rs:re], "  hi\n")
        # And the header range must NOT overlap the body range.
        hs, he = chunk.header_offsets[1]
        self.assertLessEqual(he, rs)


class TestChunkRender(unittest.TestCase):

    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX

    def test_build_assembles_base_text_from_format_run(self):
        # Two messages in one run (same speaker, same minute) — one
        # speaker header + two indented body lines.
        msgs = [_msg(1, text="hi"), _msg(2, text="there")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        plain = chunk.base.plain
        # Day header in short-rule form (no width supplied here).
        self.assertIn("── Thursday, January 1, 2026 ──\n", plain)
        # New shape: speaker header, then 2-cell-indented bodies. msg 2
        # is a continuation so it carries the inline (padded) time.
        self.assertIn("Me  ·  9:00 AM\n", plain)
        self.assertIn("  hi\n", plain)
        self.assertIn("  9:00 AM  there\n", plain)
        # No speaker label on body lines.
        self.assertNotIn("Me: ", plain)

    def test_row_offsets_bracket_body_only(self):
        msgs = [_msg(1, text="hi"), _msg(2, text="there")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        # row_offsets[1] is "  hi\n" (run head: indent + body + newline).
        s1, e1 = chunk.row_offsets[1]
        self.assertEqual(chunk.base.plain[s1:e1], "  hi\n")
        # row_offsets[2] is "  9:00 AM  there\n" (continuation: indent
        # + padded time + gap + body + newline). Selection bg paints
        # over the time prefix too — clicking it routes to msg 2.
        s2, e2 = chunk.row_offsets[2]
        self.assertEqual(chunk.base.plain[s2:e2], "  9:00 AM  there\n")

    def test_row_line_counts_match_body_wraps(self):
        # Three separate runs (>30 min between each) — each msg counts
        # its own body line(s).
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00", text="single"),
            _msg(2, ts="2026-01-01 10:00:00", text="two\nlines"),
            _msg(3, ts="2026-01-01 11:00:00", text="three\nfour\nfive"),
        ]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        self.assertEqual(chunk.row_line_counts[1], 1)
        self.assertEqual(chunk.row_line_counts[2], 2)
        self.assertEqual(chunk.row_line_counts[3], 3)

    def test_prefix_lines_above_counts_all_non_body_lines(self):
        # Three messages, each in its own run (>30 min apart).
        # Non-body lines tally:
        #   msg 1: day-1 header (1) + run-1 speaker header (1) → 2
        #   msg 2: day-1 header + run-1 speaker + run-2 blank-sep + run-2
        #          speaker → 4
        #   msg 3: msg 2's prefix + day-2 separator-blank + day-2 header
        #          + run-3 speaker → 4 + 3 = 7
        # (Continuations within a single run would just add ROW lines,
        # which prefix_lines_above intentionally does not count — the
        # scroll math adds row_line_counts separately.)
        msgs = [
            _msg(1, ts="2026-01-01 09:00:00"),
            _msg(2, ts="2026-01-01 10:00:00"),
            _msg(3, ts="2026-01-02 09:00:00"),
        ]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        self.assertEqual(chunk.prefix_lines_above, [2, 4, 7])

    def test_msg_ids_preserved_in_order(self):
        msgs = [_msg(5), _msg(3), _msg(8)]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        self.assertEqual(chunk.msg_ids, [5, 3, 8])

    def test_row_offsets_round_trip_across_day_boundary(self):
        """row_offsets must keep day-header bytes out of the body slice
        — paint()'s selection bg uses row_offsets, and we don't want it
        smearing onto either day header or run blank separators."""
        msgs = [_msg(1, ts="2026-01-01 09:00:00", text="a"),
                _msg(2, ts="2026-01-02 09:00:00", text="b")]
        chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)
        for m in msgs:
            s, e = chunk.row_offsets[m.message_id]
            slice_text = chunk.base.plain[s:e]
            self.assertIn(m.text, slice_text)
            self.assertTrue(slice_text.endswith("\n"))
            self.assertNotIn("──", slice_text)
            # And the speaker header isn't in the body slice either.
            self.assertNotIn("  ·  ", slice_text)

    def test_build_empty_messages_returns_empty_chunk(self):
        chunk = history_render._ChunkRender.build(
            [], contacts={}, palette=self.palette)
        self.assertEqual(chunk.msg_ids, [])
        self.assertEqual(chunk.row_offsets, {})
        self.assertEqual(chunk.header_offsets, {})
        self.assertEqual(chunk.row_line_counts, {})
        self.assertEqual(chunk.prefix_lines_above, [])
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


class TestPaint(unittest.TestCase):
    def setUp(self):
        from imessage_export.tui.theme import DAWNFOX
        self.palette = DAWNFOX
        self.colors = history_render.selection_colors(DAWNFOX)
        # Five messages, each in its own run (different timestamps with
        # >30 min gaps) so each gets a discrete row_offsets entry and
        # paint() can test per-msg span layering without continuation
        # entanglement.
        msgs = [
            _msg(i, text=f"m{i}",
                 ts=f"2026-01-01 0{i}:00:00")  # 0:00, 1:00, ..., 4:00
            for i in range(5)
        ]
        self.chunk = history_render._ChunkRender.build(
            msgs, contacts={}, palette=self.palette)

    def _spans_within(self, text, start, end, contains: str) -> bool:
        """True if any span overlaps [start, end] and its style str
        contains the given substring."""
        return any(
            s.start >= start and s.end <= end and contains in str(s.style)
            for s in text.spans
        )

    def test_paint_no_state_returns_clone_with_no_extra_spans(self):
        before_span_count = len(self.chunk.base.spans)
        out = history_render.paint(
            self.chunk,
            marks=history_render.MarkState(None, None, frozenset()),
            palette=self.palette,
        )
        # Paint must always clone — never return the cached base
        # (otherwise repeated paints accumulate spans on the cache).
        self.assertIsNot(out, self.chunk.base)
        self.assertEqual(out.plain, self.chunk.base.plain)
        self.assertEqual(len(out.spans), before_span_count)

    def test_paint_endpoint_adds_endpoint_bg(self):
        out = history_render.paint(
            self.chunk,
            marks=history_render.MarkState(
                anchor_id=1, active_id=1, in_range_ids=frozenset({1})),
            palette=self.palette,
        )
        start, end = self.chunk.row_offsets[1]
        self.assertTrue(self._spans_within(out, start, end,
                                           self.colors.endpoint_bg))

    def test_paint_in_range_row_gets_range_bg_not_endpoint(self):
        out = history_render.paint(
            self.chunk,
            marks=history_render.MarkState(
                anchor_id=1, active_id=3, in_range_ids=frozenset({1, 2, 3})),
            palette=self.palette,
        )
        # msg 2 is strictly between anchor and active → in_range bg.
        start, end = self.chunk.row_offsets[2]
        self.assertTrue(self._spans_within(out, start, end,
                                           self.colors.range_bg))
        # msg 1 and msg 3 are endpoints → endpoint bg.
        s1, e1 = self.chunk.row_offsets[1]
        self.assertTrue(self._spans_within(out, s1, e1, self.colors.endpoint_bg))

    def test_paint_does_not_mutate_chunk_base(self):
        before_plain = self.chunk.base.plain
        before_spans = list(self.chunk.base.spans)
        history_render.paint(
            self.chunk,
            marks=history_render.MarkState(1, 3, frozenset({1, 2, 3})),
            palette=self.palette,
        )
        self.assertEqual(self.chunk.base.plain, before_plain)
        self.assertEqual(self.chunk.base.spans, before_spans)


class TestMarkState(unittest.TestCase):
    def test_dataclass_is_hashable_and_frozen(self):
        m = history_render.MarkState(1, 5, frozenset({1, 2, 3, 4, 5}))
        # Frozen + hashable so HistoryView can cheaply compare/cache.
        hash(m)
        with self.assertRaises(Exception):
            m.anchor_id = 99  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
