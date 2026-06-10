"""Regression coverage for HistoryView's click-to-mark-a-range path.

After the per-message-row widgets were retired in favor of a single-Static
blob per chunk (PR #22), the original `apply_marks` design — which keyed
range highlighting off CSS classes on `.message-row` widgets — could no
longer fire. To keep the export-by-clicking feature alive without
reintroducing per-row widgets, every message line in the blob is rendered
with `meta={"msg_id": m.message_id}` on its Rich style spans. Textual
surfaces that meta dict via `event.style.meta` when the user clicks the
line; `HistoryView.on_click` reads it and posts `RangeMarkRequested`
exactly like the per-row path used to.

These tests verify that contract: clicks routed through `event.style.meta`
post the right message id, and clicks that landed off any message line
(no meta) do not.
"""
from __future__ import annotations

import importlib
import unittest

try:
    importlib.import_module("textual")
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


def _fake_messages(n: int):
    """`n` cheap synthetic Message rows on a single calendar day so the
    day-header logic fires once and blob construction stays fast."""
    from imessage_export.models import Message
    return [
        Message(
            message_id=i,
            timestamp=f"2026-01-01 {(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}",
            timestamp_utc=f"2026-01-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
            chat_id=1,
            sender_handle=None,
            is_from_me=1,
            author_label="Me",
            text=f"msg {i}",
            has_attachment=0,
            attachment_filenames=[],
            kind="message",
            is_edited=0,
            reaction=None,
            app_bundle=None,
        )
        for i in range(n)
    ]


@unittest.skipUnless(HAS_TEXTUAL, "[tui] extra not installed")
class TestHistoryViewRangeMarks(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    def _build_stub_app():
        from textual.app import App, ComposeResult
        from imessage_export.tui.app.widgets import HistoryView

        class _StubApp(App):
            def compose(self) -> ComposeResult:
                yield HistoryView(id="history")

        return _StubApp(), HistoryView

    async def test_blob_carries_msg_id_meta_per_line(self):
        """The rendered blob's spans must carry `meta={"msg_id": ...}`
        for every message line. This is the data contract that
        click-to-mark relies on; without it Textual has nothing to
        surface in `event.style.meta` and clicks become silent."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            # Pull the rendered blob off the topmost chunk and inspect
            # the spans for msg_id meta.
            blob = history._topmost_widget.renderable
            metas = {
                span.style.meta.get("msg_id")
                for span in blob.spans
                if hasattr(span.style, "meta") and span.style.meta
            }
            # Every fake message id must show up as meta on at least one span.
            self.assertEqual(metas, {0, 1, 2, 3, 4})

    async def test_on_click_with_meta_posts_range_mark(self):
        """A click event whose `style.meta` contains a `msg_id` must
        post a RangeMarkRequested with that id."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()

            class _FakeStyle:
                meta = {"msg_id": 7}

            class _FakeEvent:
                widget = history._topmost_widget
                style = _FakeStyle()
                _stopped = False
                def stop(self) -> None:
                    self._stopped = True

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda msg: posted.append(msg) or original_post(msg)

            evt = _FakeEvent()
            history.on_click(evt)
            await pilot.pause()

            marks = [m for m in posted if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(len(marks), 1)
            self.assertEqual(marks[0].msg_id, 7)
            self.assertTrue(evt._stopped, "on_click must stop the event after handling")

    async def test_on_click_off_a_line_is_a_noop(self):
        """A click landing where there is no meta (e.g. on the day
        header or whitespace, or outside the topmost chunk widget
        entirely) must NOT post RangeMarkRequested."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            class _FakeStyle:
                meta = None  # no meta → no msg_id

            class _FakeEvent:
                widget = history._topmost_widget
                style = _FakeStyle()
                def stop(self) -> None:
                    pass

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda msg: posted.append(msg) or original_post(msg)

            history.on_click(_FakeEvent())
            await pilot.pause()

            marks = [m for m in posted if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(marks, [])

    async def test_apply_marks_updates_in_range_set(self):
        """apply_marks must populate `_mark_start_id`, `_mark_end_id`,
        and `_in_range_ids` from the provided messages list. The set
        is what `history_render.paint` consults to decide which lines
        get the in-range tint, so wiring this correctly is the load-
        bearing bit of the Phase 3 visual highlight."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()

            messages = [{"message_id": m.message_id, "timestamp": m.timestamp}
                        for m in history._all_messages]
            history.apply_marks(2, 6, messages)
            await pilot.pause()

            self.assertEqual(history._mark_start_id, 2)
            self.assertEqual(history._mark_end_id, 6)
            self.assertEqual(history._in_range_ids, {2, 3, 4, 5, 6})

    async def test_apply_marks_clears_when_both_ids_none(self):
        """apply_marks(None, None, _) is the Esc-clear path — must wipe
        all mark state (start, end, in_range set) so a follow-up
        re-render paints no highlights."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(8))
            await pilot.pause()

            messages = [{"message_id": m.message_id, "timestamp": m.timestamp}
                        for m in history._all_messages]
            history.apply_marks(1, 4, messages)
            await pilot.pause()
            self.assertTrue(history._in_range_ids)  # populated

            history.apply_marks(None, None, messages)
            await pilot.pause()

            self.assertIsNone(history._mark_start_id)
            self.assertIsNone(history._mark_end_id)
            self.assertEqual(history._in_range_ids, set())

    async def test_apply_marks_with_stale_id_clears_visuals(self):
        """If the marks reference an id that's NOT in the current
        messages list (chat-switch race), apply_marks must wipe the
        visual state instead of crashing on `list.index(stale_id)`."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            messages = [{"message_id": m.message_id, "timestamp": m.timestamp}
                        for m in history._all_messages]
            # 999 is not in messages (only 0..4 are) — must not crash.
            history.apply_marks(2, 999, messages)
            await pilot.pause()

            self.assertIsNone(history._mark_start_id)
            self.assertIsNone(history._mark_end_id)
            self.assertEqual(history._in_range_ids, set())

    async def test_apply_marks_repaints_topmost_chunk(self):
        """The mark visual must propagate into the actual rendered
        Static — apply_marks should call `.update()` on every chunk
        widget so the blob picks up the new row backgrounds. Without
        this the user sees marks tracked in state but no visual
        confirmation in the chat.

        The visual scheme paints the full row: endpoints get the
        `accent_alt` background, in-range get the `accent` background,
        both with the theme's `bg` as the foreground for contrast.
        We assert on the presence of "on <hex>" in the line-level
        styles — the load-bearing observable user-facing change.
        """
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            # Use selection_colors so this test follows the palette
            # adapter rather than hard-coding theme hex values.
            from imessage_export.tui.app import history_render
            from imessage_export.tui.theme import DAWNFOX
            colors = history_render.selection_colors(DAWNFOX)
            endpoint_bg = colors.endpoint_bg
            range_bg = colors.range_bg
            self.assertTrue(endpoint_bg and range_bg,
                            "test theme must expose accent + accent_alt — "
                            "otherwise the background-highlight contract is moot")

            # Before marks: no ROW-level span carries either selection
            # background. The cursor bar (2-col span) shares the
            # `accent_alt` hex with `endpoint_bg`, so we filter the
            # leading-2-col cursor-bar spans out — only wider, row-level
            # backgrounds are evidence of selection paint.
            def _row_level_spans(blob):
                return [s for s in blob.spans if (s.end - s.start) > 2]

            blob_before = history._topmost_widget.renderable
            self.assertFalse(
                any(endpoint_bg in str(s.style) or range_bg in str(s.style)
                    for s in _row_level_spans(blob_before)),
                "no selection backgrounds expected before apply_marks",
            )

            messages = [{"message_id": m.message_id, "timestamp": m.timestamp}
                        for m in history._all_messages]
            history.apply_marks(1, 3, messages)
            await pilot.pause()

            # After marks(1, 3): the rendered blob's row-level style
            # spans must include BOTH the endpoint background (msgs 1
            # and 3) and the in-range background (msg 2).
            blob_after = history._topmost_widget.renderable
            style_strs = [str(s.style) for s in _row_level_spans(blob_after)]
            self.assertTrue(
                any(endpoint_bg in s for s in style_strs),
                f"endpoint background ({endpoint_bg}) MUST appear after apply_marks(1, 3)",
            )
            self.assertTrue(
                any(range_bg in s for s in style_strs),
                f"in-range background ({range_bg}) MUST appear after apply_marks(1, 3)",
            )

    async def test_click_on_load_more_affordance_does_not_post_range_mark(self):
        """Clicking the "Load X older messages" affordance must trigger
        a load and stop the event, NOT fall through to range-mark
        routing (the affordance is a separate Static widget that
        carries no msg_id meta, but we want a defensive guarantee
        that we never confuse the two)."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 2))
            await pilot.pause()

            self.assertIsNotNone(history._load_more_widget)
            shown_before = history._shown_count

            class _FakeEvent:
                widget = history._load_more_widget
                style = None
                def stop(self) -> None: pass

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda msg: posted.append(msg) or original_post(msg)

            history.on_click(_FakeEvent())
            await pilot.pause()

            self.assertGreater(history._shown_count, shown_before,
                               "click on affordance must advance the load")
            marks = [m for m in posted if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(marks, [],
                             "affordance click must not be mistaken for a range mark")

    async def test_on_click_with_stale_meta_msg_id_is_silently_dropped(self):
        """If the click's style.meta refers to a msg_id no longer in
        _loaded_ids (chat-switch race / mid-prune click), on_click
        must NOT post RangeMarkRequested — silent drop is the contract."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            class _FakeStyle:
                meta = {"msg_id": 99999}  # not in the loaded set

            class _FakeEvent:
                widget = history._topmost_widget
                style = _FakeStyle()
                def stop(self): pass

            posted = []
            original_post = history.post_message
            history.post_message = lambda m: posted.append(m) or original_post(m)

            history.on_click(_FakeEvent())
            await pilot.pause()

            marks = [m for m in posted if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(marks, [], "stale meta must not post RangeMarkRequested")

    async def test_apply_marks_skips_chunks_outside_selection(self):
        """apply_marks must NOT repaint chunks whose ids don't intersect
        the old-or-new selection. This is the load-bearing optimization
        that keeps click feedback snappy on long chats — without it,
        every click rebuilds every mounted chunk's blob (which got
        flagged as sluggish in real use).

        Build a 2-chunk history, mark a range inside chunk B, and
        verify that chunk A's Static was NOT updated.
        """
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            # Load enough messages to force a second (older) chunk on
            # action_load_older so we have two chunks to observe.
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 2))
            await pilot.pause()
            history.action_load_older()
            await pilot.pause()

            # Identify the two chunk Statics (those with a _chunk_render).
            chunks = [c for c in history.children
                      if getattr(c, "_chunk_render", None) is not None]
            self.assertGreaterEqual(len(chunks), 2,
                                    "test needs ≥2 chunks to verify the skip")
            chunk_a, chunk_b = chunks[0], chunks[-1]

            # Pick an id that lives ONLY in chunk_b — anchors the range
            # inside that chunk, leaving chunk_a outside the selection.
            chunk_a_ids = set(chunk_a._chunk_render.msg_ids)
            chunk_b_ids = set(chunk_b._chunk_render.msg_ids)
            target_id = next(iter(chunk_b_ids))
            self.assertNotIn(target_id, chunk_a_ids,
                             "pre-condition: target lives in chunk_b only")

            # Spy on .update() calls per chunk.
            updates: dict[int, int] = {id(chunk_a): 0, id(chunk_b): 0}
            orig_a_update, orig_b_update = chunk_a.update, chunk_b.update
            chunk_a.update = lambda r: (updates.update({id(chunk_a): updates[id(chunk_a)] + 1}), orig_a_update(r))[1]
            chunk_b.update = lambda r: (updates.update({id(chunk_b): updates[id(chunk_b)] + 1}), orig_b_update(r))[1]

            messages = [{"message_id": m.message_id, "timestamp": m.timestamp}
                        for m in history._all_messages]
            # Single-endpoint mark inside chunk_b only.
            history.apply_marks(target_id, target_id, messages)
            await pilot.pause()

            self.assertEqual(updates[id(chunk_b)], 1,
                             "chunk_b contains the mark; must be repainted exactly once")
            self.assertEqual(updates[id(chunk_a)], 0,
                             "chunk_a has no marked ids; must NOT be repainted "
                             "(this is the perceived-sluggishness fix)")


if __name__ == "__main__":
    unittest.main()
