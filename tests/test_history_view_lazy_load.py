"""Regression coverage for HistoryView's load-older-messages path.

History of this code (and why it ended up here):

PRs #24/#25 added an auto-load-on-scroll-up mechanism: a reactive
`watch_scroll_y` watcher fired `_load_more_older` whenever the user
scrolled within `LOAD_MORE_THRESHOLD` lines of the top. The mechanism
shipped with — and progressively grew defenses against — three races
against Textual's async layout pass:

1. Stale `_recent_widget` reference after `remove_children()` (because
   Textual's `_is_mounted` is sticky-True forever after first mount)
   caused `mount(..., before=<orphaned>)` to crash with `MountError`.
2. The mount target and scroll anchor were both pinned to the recent
   chunk, so load-2+ interleaved chunks in the wrong chronological
   order AND snapped the user back to the recent chunk on every load.
3. `call_after_refresh(scroll_to_widget(prev_top, top=True))` sometimes
   landed `scroll_y` at/near 0 because the freshly mounted chunk's
   height hadn't been folded into the parent virtual_region yet. The
   layout-settle cascade fired `watch_scroll_y` repeatedly with
   scroll_y already in the threshold band, runaway-loading every
   remaining chunk in one burst.

After fixing all three, the architecture was still latently racy: the
implicit trigger (the watcher) made every load a fight against
"intermediate layout state". We replaced auto-load with an explicit "o"
binding (`action_load_older`) — no watcher, no callback, no anchor.
The user presses "o" when they want more history. That eliminates the
entire class of races: there's no implicit caller to re-enter, nothing
to race against `virtual_size`, no stale-state path.

These tests host HistoryView in a minimal stub App to keep them
independent of the full ImessageExportApp's bootstrap (which would race
its own first-chat auto-load against our synthetic state setup).
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
class TestHistoryViewProgressiveLoad(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    def _build_stub_app():
        from textual.app import App, ComposeResult
        from imessage_export.tui.app.widgets import HistoryView

        class _StubApp(App):
            def compose(self) -> ComposeResult:
                yield HistoryView(id="history")

        return _StubApp(), HistoryView

    async def test_show_placeholder_clears_lazy_load_state(self):
        """After show_placeholder() the progressive-load state must be
        cleared — `_all_messages` from the prior chat, `_shown_count`, and
        the now-orphaned `_topmost_widget` / `_load_more_widget` refs.
        Otherwise a follow-up render_messages for the new chat would
        compute its hidden_count off the prior chat's totals, and any
        code path that reads those references would touch detached
        widgets."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)

            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP + 500))

            self.assertEqual(history._shown_count, HistoryView.PREVIEW_CAP)
            self.assertIsNotNone(history._topmost_widget)
            # Affordance must be mounted whenever there's at least one
            # unshown older message — discoverable trigger for the user.
            self.assertIsNotNone(history._load_more_widget)
            self.assertGreater(len(history._all_messages), history._shown_count)

            history.show_loading()

            self.assertEqual(history._all_messages, [])
            self.assertEqual(history._shown_count, 0)
            self.assertIsNone(history._topmost_widget)
            self.assertIsNone(history._load_more_widget)
            self.assertIsNone(history._beginning_widget)

            await pilot.pause()

    async def test_action_load_older_keeps_chunks_in_chronological_order(self):
        """Each "o" press mounts the next older chunk ABOVE the previous
        topmost (not above the recent chunk), and advances
        `_topmost_widget` to the freshly mounted widget. DOM order from
        top to bottom must be: oldest-loaded → ... → recent."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)

            # Three full chunks worth of messages so we can fire two
            # follow-up loads after the initial render.
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 3))
            await pilot.pause()
            recent = history._topmost_widget
            self.assertIsNotNone(recent)

            history.action_load_older()
            await pilot.pause()
            older_1 = history._topmost_widget
            self.assertIsNotNone(older_1)
            self.assertIsNot(older_1, recent, "topmost must advance off recent")

            history.action_load_older()
            await pilot.pause()
            older_2 = history._topmost_widget
            self.assertIsNotNone(older_2)
            self.assertIsNot(older_2, older_1, "topmost must advance again")

            # The top indicators (load-more affordance / beginning marker)
            # also live in `children`; filter them out so the assertion
            # focuses on the chunks' chronological order.
            indicators = {history._load_more_widget, history._beginning_widget}
            chunks = [w for w in history.children if w not in indicators]
            self.assertEqual(
                chunks,
                [older_2, older_1, recent],
                "Chunk DOM order must be oldest-loaded → newest (top-to-bottom)",
            )

    async def test_action_load_older_stops_when_all_messages_loaded(self):
        """`action_load_older` is a no-op when `_shown_count` has caught
        up to `_all_messages`. Without this guard the load would mount
        an empty chunk widget every time the user pressed "o"."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            # Two chunks total — render fills one, action fills the other.
            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 2))
            await pilot.pause()

            history.action_load_older()
            await pilot.pause()
            self.assertEqual(history._shown_count, len(history._all_messages))
            count_before = len(list(history.children))

            # Subsequent presses do nothing (no new widgets mounted,
            # _shown_count doesn't advance past total).
            history.action_load_older()
            history.action_load_older()
            await pilot.pause()

            self.assertEqual(history._shown_count, len(history._all_messages))
            self.assertEqual(len(list(history.children)), count_before)

    async def test_load_more_affordance_lifecycle(self):
        """The clickable affordance must be present whenever there are
        still hidden messages and gone the moment they're all loaded — so
        the user always has a visible trigger, and a stale "click to load"
        never lies after everything is shown.

        Also verifies the click path actually advances the load (mirroring
        what `o` would do), since the affordance is the discoverable
        entry-point for users who don't know the key binding."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)

            history.render_messages(_fake_messages(HistoryView.PREVIEW_CAP * 2))
            await pilot.pause()

            # Hidden > 0 → affordance must be in the DOM, above the
            # topmost chunk.
            self.assertIsNotNone(history._load_more_widget)
            self.assertIs(history._load_more_widget.parent, history)
            kids = list(history.children)
            self.assertEqual(kids[0], history._load_more_widget)

            # Simulate a click on the affordance — should advance the
            # load the same as pressing "o".
            shown_before = history._shown_count

            class _FakeClickEvent:
                def __init__(self, widget):
                    self.widget = widget
                def stop(self):
                    pass

            history.on_click(_FakeClickEvent(history._load_more_widget))
            await pilot.pause()

            self.assertGreater(history._shown_count, shown_before)

            # All loaded now → affordance must be removed and replaced
            # by the "beginning of conversation" marker so the user sees
            # a clear endpoint instead of nothing at the top.
            self.assertEqual(history._shown_count, len(history._all_messages))
            self.assertIsNone(history._load_more_widget)
            self.assertIsNotNone(history._beginning_widget)
            self.assertIs(history._beginning_widget.parent, history)

    async def test_small_chat_renders_beginning_marker_immediately(self):
        """A chat with fewer messages than PREVIEW_CAP has nothing to
        progressively load. The render must still mount the
        "Beginning of conversation" marker so the top of the scroll
        isn't empty — the user always gets feedback about where they
        are. (Reproduces the screenshot the user flagged: a 4-message
        chat with no marker at the top.)"""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(4))
            await pilot.pause()

            self.assertIsNone(history._load_more_widget,
                              "no hidden messages → no load-more affordance")
            self.assertIsNotNone(history._beginning_widget,
                                 "all-loaded → beginning marker MUST be visible")
            self.assertIs(history._beginning_widget.parent, history)
            kids = list(history.children)
            self.assertEqual(kids[0], history._beginning_widget,
                             "beginning marker must be the first child of HistoryView")

    async def test_action_load_older_is_safe_on_empty_history(self):
        """`action_load_older` must be a no-op when there's no chat
        loaded (placeholder showing). Otherwise pressing "o" while the
        sidebar is empty / loading would crash on a None _topmost or
        mount an empty-slice widget."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.show_placeholder("Pick a chat from the left.")
            await pilot.pause()

            history.action_load_older()  # must not raise
            await pilot.pause()

            self.assertEqual(history._all_messages, [])
            self.assertEqual(history._shown_count, 0)
            self.assertTrue(history._placeholder_visible)


if __name__ == "__main__":
    unittest.main()
