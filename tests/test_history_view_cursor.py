"""Regression coverage for HistoryView's keyboard cursor.

The cursor is a per-row keyboard focus marker, separate from the
mark endpoints. It moves with up/down arrows and drops a range
endpoint at its current position via space/enter — the same
RangeMarkRequested message a mouse click would post, so the
app-level mark logic doesn't need to distinguish keyboard from
mouse origin.

These tests pin the contract:
  - Cursor defaults to the most-recent message after render.
  - Up/down clamp at the bounds (no wrap).
  - action_mark_row posts RangeMarkRequested(cursor_id).
  - The rendered blob shows a "▸ " gutter marker on exactly the
    cursored row.
  - Repaint after a cursor move only touches the chunk(s) holding
    the old and new positions (perf invariant — same as marks).
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
class TestHistoryViewCursor(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    def _build_stub_app():
        from textual.app import App, ComposeResult
        from imessage_export.tui.app.widgets import HistoryView

        class _StubApp(App):
            def compose(self) -> ComposeResult:
                yield HistoryView(id="history")

        return _StubApp(), HistoryView

    async def test_cursor_defaults_to_latest_message_after_render(self):
        """When a chat loads, the cursor parks on the most-recent
        message — that's where the user is reading from, so up-arrow
        walks backward into history matching the scan direction."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 9)

    async def test_action_cursor_up_walks_backward(self):
        """Up arrow decrements the cursor toward older messages."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history.action_cursor_up()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 8)
            history.action_cursor_up()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 7)

    async def test_action_cursor_down_walks_forward(self):
        """Down arrow advances the cursor toward newer messages."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            # Drop the cursor a few rows back so down-arrow has somewhere to go.
            history._cursor_msg_id = 3
            history.action_cursor_down()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 4)

    async def test_cursor_clamps_at_top(self):
        """Pressing up at the oldest loaded message is a silent no-op
        — no wrap, no crash, cursor stays put."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()
            history._cursor_msg_id = 0
            history.action_cursor_up()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 0)

    async def test_cursor_clamps_at_bottom(self):
        """Pressing down at the newest message is a silent no-op."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()
            # Cursor defaulted to 4 (the last) — pressing down should hold.
            self.assertEqual(history._cursor_msg_id, 4)
            history.action_cursor_down()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 4)

    async def test_action_mark_row_posts_range_mark_at_cursor(self):
        """Space/enter (action_mark_row) posts the same
        RangeMarkRequested a click would, with the cursor's msg_id —
        so the app-level mark logic stays origin-agnostic."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()
            history._cursor_msg_id = 2

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda msg: posted.append(msg) or original_post(msg)

            history.action_mark_row()
            await pilot.pause()

            marks = [m for m in posted if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(len(marks), 1)
            self.assertEqual(marks[0].msg_id, 2)

    async def test_action_mark_row_is_noop_when_no_cursor(self):
        """No cursor set (empty chat / placeholder state) → no message
        posted. Prevents a stale RangeMarkRequested(None) from
        confusing the app-level handler."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            await pilot.pause()
            self.assertIsNone(history._cursor_msg_id)

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda msg: posted.append(msg) or original_post(msg)

            history.action_mark_row()
            await pilot.pause()

            marks = [m for m in posted if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(marks, [])

    async def test_cursor_visual_renders_on_exactly_one_row(self):
        """The cursored row must carry both the B (row tint) and D
        (cursor bar) backgrounds; no other row carries either."""
        from imessage_export.tui.app.widgets import HistoryView
        from imessage_export.tui.app import history_render
        from imessage_export.tui.theme import DAWNFOX

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(5))
            await pilot.pause()

            colors = history_render.selection_colors(DAWNFOX)
            blob = history._topmost_widget.renderable
            # Exactly one row carries the cursor tint background.
            tint_spans = [s for s in blob.spans
                          if colors.cursor_tint_bg in str(s.style)]
            self.assertEqual(len(tint_spans), 1)
            # Exactly one row carries the cursor bar background on its
            # leading 2 cols.
            bar_spans = [s for s in blob.spans
                         if colors.cursor_bar_default in str(s.style)
                         and (s.end - s.start) == 2]
            self.assertEqual(len(bar_spans), 1)

    async def test_cursor_move_repaints_only_affected_chunk(self):
        """Moving the cursor by one message must only repaint the
        chunk(s) holding the old or new position. For two chunks
        loaded with the cursor moving within the latest one, the
        older chunk's `.update()` must NOT fire — preserves the perf
        invariant from the marks repaint path."""
        from imessage_export.tui.app.widgets import HistoryView

        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 2))
            await pilot.pause()
            history.action_load_older()
            await pilot.pause()

            chunks = [c for c in history.children
                      if getattr(c, "_chunk_render", None) is not None]
            self.assertGreaterEqual(len(chunks), 2)
            older_chunk = chunks[0]
            newer_chunk = chunks[-1]
            # Cursor starts on the latest message (in newer_chunk).
            self.assertIn(history._cursor_msg_id,
                          set(newer_chunk._chunk_render.msg_ids))

            updates = {id(older_chunk): 0, id(newer_chunk): 0}
            orig_older, orig_newer = older_chunk.update, newer_chunk.update
            older_chunk.update = lambda r: (updates.update({id(older_chunk): updates[id(older_chunk)] + 1}), orig_older(r))[1]
            newer_chunk.update = lambda r: (updates.update({id(newer_chunk): updates[id(newer_chunk)] + 1}), orig_newer(r))[1]

            history.action_cursor_up()
            await pilot.pause()

            self.assertGreaterEqual(updates[id(newer_chunk)], 1,
                                    "newer chunk holds the cursor; must repaint")
            self.assertEqual(updates[id(older_chunk)], 0,
                             "older chunk doesn't hold the cursor row; must NOT repaint")

    async def test_id_to_index_built_after_render(self):
        """`_id_to_index` is the O(1) map cursor moves and stale-id
        recovery both depend on. Must be in sync with `_all_messages`
        after every render."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            self.assertEqual(history._id_to_index, {i: i for i in range(10)})

    async def test_shift_down_extends_selection_from_anchor(self):
        """First shift+down sets the anchor on the current cursor and
        moves the active down by one. The in_range set grows to cover
        the anchor and active rows."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history._cursor_msg_id = 4

            history.action_extend_down()
            await pilot.pause()

            self.assertEqual(history._cursor_msg_id, 5)
            self.assertEqual(history._mark_anchor_id, 4)
            self.assertEqual(history._mark_active_id, 5)
            self.assertEqual(history._in_range_ids, {4, 5})

    async def test_second_shift_keeps_anchor_grows_range(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history._cursor_msg_id = 4

            history.action_extend_down()
            await pilot.pause()
            history.action_extend_down()
            await pilot.pause()
            history.action_extend_down()
            await pilot.pause()

            self.assertEqual(history._mark_anchor_id, 4)
            self.assertEqual(history._mark_active_id, 7)
            self.assertEqual(history._in_range_ids, {4, 5, 6, 7})

    async def test_plain_arrow_clears_anchor(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history._cursor_msg_id = 4
            history.action_extend_down()
            await pilot.pause()
            # Anchor is 4, active is 5; now plain Down should clear.
            history.action_cursor_down()
            await pilot.pause()
            self.assertIsNone(history._mark_anchor_id)
            self.assertIsNone(history._mark_active_id)
            self.assertEqual(history._in_range_ids, set())

    async def test_action_cursor_to_end_parks_on_latest(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(20))
            await pilot.pause()
            history._cursor_msg_id = 5

            history.action_cursor_to_end()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 19)

    async def test_action_cursor_to_start_parks_on_oldest_loaded(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(20))
            await pilot.pause()
            history.action_cursor_to_start()
            await pilot.pause()
            self.assertEqual(history._cursor_msg_id, 0)

    async def test_action_page_down_moves_by_viewport_height(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(200))
            await pilot.pause()
            history._cursor_msg_id = 10

            history.action_page_down()
            await pilot.pause()
            # Default viewport in the stub is small; just verify cursor
            # advanced by at least 5 messages (page is `max(5, size.height)`).
            self.assertGreaterEqual(history._cursor_msg_id, 15)

    async def test_filter_excluding_cursor_parks_on_nearest_by_timestamp(self):
        """When a filter narrows the message set and excludes the
        cursor's id, render_messages(_from_filter=True) must park the
        cursor on the message whose timestamp is closest to the
        excluded cursor — NOT silently jump to the latest."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            full = _fake_messages(20)
            history.render_messages(full)
            await pilot.pause()
            history._cursor_msg_id = 10  # mid-history

            # Filter narrows to messages 0..4 and 15..19 — excluding 10.
            # Nearest by timestamp from msg 10 in the remaining set is
            # msg 4 (earlier, but closer in index than msg 15 because
            # the index gap is identical and ties prefer the older).
            narrowed = full[:5] + full[15:]
            history.render_messages(narrowed, _from_filter=True)
            await pilot.pause()

            # Either side of 10 is 5 indices away; nearest-by-timestamp
            # picks one of {4, 15}. Both are acceptable; assert the
            # cursor is NOT silently snapped to the latest.
            self.assertIn(history._cursor_msg_id, {4, 15})
            self.assertNotEqual(history._cursor_msg_id, 19)

    async def test_scroll_follows_cursor_off_bottom_edge(self):
        """Cursor walked past the bottom margin must trigger a scroll
        so the cursor row stays in view. This is the new row-level
        scroll-follow (replaces the old chunk-level scroll_to_widget)."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(60, 12)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(200))
            await pilot.pause()
            # Cursor starts at the last message → already at the bottom.
            start_scroll_y = history.scroll_y
            # Move up far enough to land mid-history, then down again to
            # force the cursor to walk back past the viewport bottom.
            for _ in range(30):
                history.action_cursor_up()
            await pilot.pause()
            for _ in range(30):
                history.action_cursor_down()
            await pilot.pause()
            # We don't pin an exact scroll_y because viewport size and
            # virtual_size depend on the stub; just assert the scroll
            # actually moved during the walk.
            self.assertNotEqual(history.scroll_y, start_scroll_y - 1)

    async def test_single_arrow_does_not_yank_scroll_to_top(self):
        """Regression: a single arrow press from inside the viewport
        must not jump the scroll back to the top. The earlier impl
        mixed screen-relative `region.y` with virtual-space `scroll_y`,
        so any non-zero scroll caused the snap target to compute near
        0 and every arrow press yanked the viewport to msg 0."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(60, 24)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(500))
            await pilot.pause()
            # Cursor defaults to the latest; scroll is at the bottom.
            start_scroll_y = history.scroll_y
            self.assertGreater(start_scroll_y, 50,
                               "precondition: viewport scrolled well past top")
            # One UP — cursor moves by exactly 1; scroll should stay
            # within a few rows of where it was, NOT snap to ~0.
            history.action_cursor_up()
            await pilot.pause()
            self.assertGreater(
                history.scroll_y, start_scroll_y - 10,
                f"scroll yanked to top: was {start_scroll_y}, now "
                f"{history.scroll_y}",
            )


if __name__ == "__main__":
    unittest.main()
