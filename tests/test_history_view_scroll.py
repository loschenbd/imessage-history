"""Tests for HistoryView's viewport-only navigation model.

The history pane is a pure scroll surface — no keyboard cursor.
Arrows scroll the viewport by 1 row. PgUp/PgDn by viewport.
Home/End jump to top-of-loaded/bottom. Space/Shift+arrow are
unbound. Click drops a range-mark endpoint and nothing else.

These tests pin the new contract. Cursor-walk behavior lives in
the (deleted) tests/test_history_view_cursor.py history.
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
            chat_id=1, sender_handle=None, is_from_me=1, author_label="Me",
            text=f"msg {i}", has_attachment=0, attachment_filenames=[],
            kind="message", is_edited=0, reaction=None, app_bundle=None,
        )
        for i in range(n)
    ]


@unittest.skipUnless(HAS_TEXTUAL, "[tui] extra not installed")
class TestHistoryViewScroll(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    def _build_stub_app():
        from textual.app import App, ComposeResult
        from imessage_export.tui.app.widgets import HistoryView

        class _StubApp(App):
            def compose(self) -> ComposeResult:
                yield HistoryView(id="history")

        return _StubApp(), HistoryView

    async def test_down_arrow_scrolls_one_row(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(200))
            await pilot.pause()
            history.scroll_to(y=20, animate=False)
            await pilot.pause()
            before = history.scroll_y
            history.action_scroll_down()
            await pilot.pause()
            self.assertGreater(history.scroll_y, before)
            self.assertLess(history.scroll_y - before, 3,
                            f"down arrow scrolled by {history.scroll_y - before} rows, expected ~1")

    async def test_up_arrow_scrolls_one_row(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(200))
            await pilot.pause()
            history.scroll_to(y=20, animate=False)
            await pilot.pause()
            before = history.scroll_y
            history.action_scroll_up()
            await pilot.pause()
            self.assertLess(history.scroll_y, before)
            self.assertLess(before - history.scroll_y, 3,
                            f"up arrow scrolled by {before - history.scroll_y} rows, expected ~1")

    async def test_page_down_scrolls_one_viewport(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(500))
            await pilot.pause()
            history.scroll_to(y=10, animate=False)
            await pilot.pause()
            before = history.scroll_y
            vh = history._viewport_height_lines()
            history.action_page_down()
            await pilot.pause()
            delta = history.scroll_y - before
            self.assertGreater(delta, vh * 0.5,
                               f"page down scrolled {delta}, expected ~{vh}")

    async def test_page_up_scrolls_one_viewport(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(500))
            await pilot.pause()
            history.scroll_to(y=200, animate=False)
            await pilot.pause()
            before = history.scroll_y
            vh = history._viewport_height_lines()
            history.action_page_up()
            await pilot.pause()
            delta = before - history.scroll_y
            self.assertGreater(delta, vh * 0.5,
                               f"page up scrolled {delta}, expected ~{vh}")

    async def test_home_scrolls_to_top_of_loaded(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            # 3 chunks of messages but only one is rendered initially.
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 3))
            await pilot.pause()
            shown_before = history._shown_count
            history.action_scroll_top()
            await pilot.pause()
            self.assertEqual(history.scroll_y, 0)
            # Home does NOT auto-load all the way to msg 0; it scrolls
            # to the top of currently-loaded content, with the affordance
            # still visible above.
            self.assertLessEqual(history._shown_count - shown_before,
                                 HistoryView.LOAD_MORE_CHUNK,
                                 "Home auto-loaded more than one chunk")

    async def test_end_scrolls_to_bottom(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(500))
            await pilot.pause()
            history.scroll_to(y=0, animate=False)
            await pilot.pause()
            history.action_scroll_bottom()
            await pilot.pause()
            self.assertGreater(history.scroll_y, 50,
                               "end didn't scroll to the bottom")

    async def test_autoload_fires_within_5_rows_of_top(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 3))
            await pilot.pause()
            shown_before = history._shown_count
            history.scroll_to(y=0, animate=False)
            await pilot.pause()
            history.action_scroll_up()
            await pilot.pause()
            self.assertGreater(history._shown_count, shown_before,
                               "autoload didn't fire when scrolling near top")

    async def test_autoload_no_op_at_chat_start(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            # Render fewer than PREVIEW_CAP so all messages are visible
            # and there's nothing to auto-load.
            history.render_messages(_fake_messages(50))
            await pilot.pause()
            shown_before = history._shown_count
            history.scroll_to(y=0, animate=False)
            await pilot.pause()
            history.action_scroll_up()  # must not crash, must not over-load
            await pilot.pause()
            self.assertEqual(history._shown_count, shown_before)

    async def test_programmatic_scroll_y_change_triggers_autoload(self):
        """Mouse-wheel and drag-scroll bypass action_scroll_up — they
        mutate scroll_y directly. Pin that the autoload still fires."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test(size=(80, 30)) as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 3))
            await pilot.pause()
            shown_before = history._shown_count
            # Simulate a mouse-wheel scroll all the way to the top
            # (bypasses action_scroll_up entirely).
            history.scroll_to(y=2, animate=False)
            await pilot.pause()
            self.assertGreater(history._shown_count, shown_before,
                               "watch_scroll_y didn't fire autoload on programmatic scroll")

    async def test_no_cursor_state_after_render(self):
        """Guard against accidental reintroduction of the cursor model.
        If a future edit adds _cursor_msg_id back, this test fails loudly."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            self.assertFalse(
                hasattr(history, "_cursor_msg_id"),
                "_cursor_msg_id attribute exists — cursor model leaked back in",
            )

    async def test_space_does_nothing_in_history_pane(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            history.focus()
            await pilot.pause()

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda m: posted.append(m) or original_post(m)

            await pilot.press("space")
            await pilot.pause()

            marks = [m for m in posted
                     if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(marks, [],
                             "Space posted a RangeMarkRequested — should be unbound")

    async def test_shift_arrow_does_nothing(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(50))
            await pilot.pause()
            history.focus()
            await pilot.pause()
            scroll_before = history.scroll_y

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda m: posted.append(m) or original_post(m)

            await pilot.press("shift+down")
            await pilot.pause()

            # No selection-extended message (the class is gone), no
            # range mark posted, viewport unchanged. Filter to
            # HistoryView-namespaced messages — the Pilot itself posts
            # Key + Callback noise via the shared post_message channel,
            # which is irrelevant to the cursor-removal assertion.
            domain = [m for m in posted
                      if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(domain, [])
            self.assertEqual(history.scroll_y, scroll_before)

    async def test_click_drops_mark_and_nothing_else(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(20))
            await pilot.pause()

            posted: list = []
            original_post = history.post_message
            history.post_message = lambda m: posted.append(m) or original_post(m)

            class _FakeStyle:
                meta = {"msg_id": 7}

            class _FakeEvent:
                widget = history._topmost_widget
                style = _FakeStyle()
                def stop(self): pass

            history.on_click(_FakeEvent())
            await pilot.pause()

            marks = [m for m in posted
                     if isinstance(m, HistoryView.RangeMarkRequested)]
            self.assertEqual(len(marks), 1)
            self.assertEqual(marks[0].msg_id, 7)
            # No other HistoryView-namespaced messages — SelectionExtended
            # is gone. Filter to HistoryView messages (the Pilot also
            # posts Key + Callback events on the shared channel, which
            # are framework noise unrelated to the cursor-removal
            # assertion).
            history_msgs = [m for m in posted
                            if type(m).__qualname__.startswith("HistoryView.")]
            self.assertEqual(len(history_msgs), 1)

    async def test_left_arrow_bridges_to_sidebar(self):
        """Existing behavior — guard against accidental removal."""
        # Use the real app fixture; the stub app doesn't have a Sidebar
        # so left-arrow has nowhere to bridge to. This test is the only
        # one in the file that needs the full app — defer if the test
        # harness for sidebar bridging is in test_app_navigation.py
        # already (it is — `test_left_from_history_focuses_sidebar`).
        # Document the cross-reference here:
        self.skipTest("Coverage lives in tests/test_app_navigation.py::"
                      "test_left_from_history_focuses_sidebar")


if __name__ == "__main__":
    unittest.main()
