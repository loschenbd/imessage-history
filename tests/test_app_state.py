"""AppState resolution + reset-after-export logic."""
from __future__ import annotations

import unittest
from pathlib import Path

from imessage_export.tui.app.state import (
    AppState,
    _format_window,
    apply_click_mark,
    resolved_window,
    reset_after_export,
)


class TestResolvedWindow(unittest.TestCase):
    def test_no_selection_no_typed_returns_all(self):
        s = AppState()
        self.assertEqual(resolved_window(s), {"mode": "all"})

    def test_typed_only_returns_typed(self):
        s = AppState(
            typed_window={"mode": "day", "date": "2026-06-06"},
            window_source="typed",
        )
        self.assertEqual(resolved_window(s), {"mode": "day", "date": "2026-06-06"})

    def test_selection_only_returns_range_from_bracket(self):
        # Two messages at the same date — selection -> a range on that date.
        s = AppState(
            selected_chat_messages=[
                {"message_id": 1, "timestamp": "2026-06-06 09:00:00"},
                {"message_id": 2, "timestamp": "2026-06-06 10:00:00"},
                {"message_id": 3, "timestamp": "2026-06-06 11:00:00"},
            ],
            range_start_msg_id=1,
            range_end_msg_id=3,
            window_source="selection",
        )
        out = resolved_window(s)
        self.assertEqual(out["mode"], "range")
        self.assertEqual(out["from_date"], "2026-06-06")
        self.assertEqual(out["to_date"], "2026-06-06")
        self.assertEqual(out["start_time"], "09:00")
        self.assertEqual(out["end_time"], "11:00")

    def test_both_set_typed_wins_when_source_is_typed(self):
        s = AppState(
            selected_chat_messages=[
                {"message_id": 1, "timestamp": "2026-06-06 09:00:00"},
                {"message_id": 2, "timestamp": "2026-06-06 10:00:00"},
            ],
            range_start_msg_id=1, range_end_msg_id=2,
            typed_window={"mode": "all"},
            window_source="typed",
        )
        self.assertEqual(resolved_window(s), {"mode": "all"})

    def test_both_set_selection_wins_when_source_is_selection(self):
        s = AppState(
            selected_chat_messages=[
                {"message_id": 1, "timestamp": "2026-06-06 09:00:00"},
                {"message_id": 2, "timestamp": "2026-06-06 10:00:00"},
            ],
            range_start_msg_id=1, range_end_msg_id=2,
            typed_window={"mode": "all"},
            window_source="selection",
        )
        out = resolved_window(s)
        self.assertEqual(out["mode"], "range")

    def test_selection_swaps_when_end_earlier_than_start(self):
        s = AppState(
            selected_chat_messages=[
                {"message_id": 1, "timestamp": "2026-06-06 09:00:00"},
                {"message_id": 2, "timestamp": "2026-06-06 10:00:00"},
            ],
            range_start_msg_id=2,  # the LATER one as start
            range_end_msg_id=1,    # the EARLIER one as end
            window_source="selection",
        )
        out = resolved_window(s)
        self.assertEqual(out["start_time"], "09:00")
        self.assertEqual(out["end_time"], "10:00")


class TestFormatWindow(unittest.TestCase):
    def test_all_mode(self):
        self.assertEqual(_format_window({"mode": "all"}), "everything")

    def test_day_no_times(self):
        self.assertEqual(
            _format_window({"mode": "day", "date": "2026-06-06"}),
            "2026-06-06",
        )

    def test_day_with_times(self):
        self.assertEqual(
            _format_window({"mode": "day", "date": "2026-06-06",
                            "start_time": "09:00", "end_time": "17:00"}),
            "2026-06-06 09:00–17:00",
        )

    def test_day_with_only_start_time(self):
        self.assertEqual(
            _format_window({"mode": "day", "date": "2026-06-06",
                            "start_time": "09:00", "end_time": None}),
            "2026-06-06 09:00–23:59",
        )

    def test_range_no_times(self):
        self.assertEqual(
            _format_window({"mode": "range", "from_date": "2026-06-01", "to_date": "2026-06-06"}),
            "2026-06-01..2026-06-06",
        )

    def test_range_with_times(self):
        self.assertEqual(
            _format_window({"mode": "range", "from_date": "2026-06-01", "to_date": "2026-06-06",
                            "start_time": "09:00", "end_time": "17:00"}),
            "2026-06-01..2026-06-06 09:00–17:00",
        )

    def test_range_with_only_end_time(self):
        self.assertEqual(
            _format_window({"mode": "range", "from_date": "2026-06-01", "to_date": "2026-06-06",
                            "start_time": None, "end_time": "17:00"}),
            "2026-06-01..2026-06-06 00:00–17:00",
        )


class TestResetAfterExport(unittest.TestCase):
    def test_clears_range_and_typed_window(self):
        s = AppState(
            selected_chat_id=42,
            selected_chat_messages=[{"message_id": 1, "timestamp": "2026-06-06 09:00:00"}],
            range_start_msg_id=1, range_end_msg_id=1,
            typed_window={"mode": "day", "date": "2026-06-06"},
            window_source="selection",
            contacts_path=Path("/tmp/contacts.csv"),
            output_dir=Path("/tmp/out"),
            me_name="Ben",
            last_export_status=None,
        )
        reset_after_export(s, success_tag="exported 4 msgs → /tmp/out")

        # Cleared
        self.assertIsNone(s.range_start_msg_id)
        self.assertIsNone(s.range_end_msg_id)
        self.assertIsNone(s.typed_window)
        self.assertEqual(s.window_source, "all")

        # Preserved
        self.assertEqual(s.selected_chat_id, 42)
        self.assertEqual(s.selected_chat_messages, [{"message_id": 1, "timestamp": "2026-06-06 09:00:00"}])
        self.assertEqual(s.contacts_path, Path("/tmp/contacts.csv"))
        self.assertEqual(s.output_dir, Path("/tmp/out"))
        self.assertEqual(s.me_name, "Ben")

        # Tagged
        self.assertEqual(s.last_export_status, "exported 4 msgs → /tmp/out")


class TestApplyClickMark(unittest.TestCase):
    """Date-picker-style click semantics for range marks.

    These tests pin down behavior the user can rely on when clicking
    messages to define an export window: first click anchors start,
    second click sets end, then subsequent clicks move whichever
    endpoint is nearer (extending the range if outside, shrinking it
    if inside). Clicking the same point twice is always a no-op so a
    misclick doesn't reset the work.
    """

    @staticmethod
    def _state_with(n: int) -> AppState:
        return AppState(
            selected_chat_messages=[
                {"message_id": i, "timestamp": f"2026-06-06 {(8 + i):02d}:00:00"}
                for i in range(1, n + 1)
            ]
        )

    def test_first_click_sets_start(self):
        s = self._state_with(5)
        self.assertTrue(apply_click_mark(s, 2))
        self.assertEqual(s.range_start_msg_id, 2)
        self.assertIsNone(s.range_end_msg_id)

    def test_second_click_sets_end(self):
        s = self._state_with(5)
        apply_click_mark(s, 2)
        self.assertTrue(apply_click_mark(s, 4))
        self.assertEqual(s.range_start_msg_id, 2)
        self.assertEqual(s.range_end_msg_id, 4)

    def test_second_click_on_start_is_noop(self):
        s = self._state_with(5)
        apply_click_mark(s, 2)
        self.assertFalse(apply_click_mark(s, 2))
        self.assertEqual(s.range_start_msg_id, 2)
        self.assertIsNone(s.range_end_msg_id)

    def test_third_click_outside_extends_range_via_nearest_endpoint(self):
        """Click far before start → start moves to the click (range
        extended backward). Click far after end → end moves to the
        click (range extended forward)."""
        s = self._state_with(10)
        apply_click_mark(s, 4)
        apply_click_mark(s, 7)
        # Click well before start: start moves.
        self.assertTrue(apply_click_mark(s, 1))
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), (1, 7))
        # Click well after end: end moves.
        self.assertTrue(apply_click_mark(s, 10))
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), (1, 10))

    def test_third_click_inside_shrinks_via_nearest_endpoint(self):
        """Click inside [start, end] → the closer endpoint moves to
        the click, shrinking the range."""
        s = self._state_with(10)
        apply_click_mark(s, 2)
        apply_click_mark(s, 9)
        # Click closer to start (index 3 vs index 9): start moves up to 3.
        self.assertTrue(apply_click_mark(s, 3))
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), (3, 9))
        # Click closer to end (index 8 vs index 3): end moves down to 8.
        self.assertTrue(apply_click_mark(s, 8))
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), (3, 8))

    def test_third_click_on_existing_endpoint_is_noop(self):
        s = self._state_with(10)
        apply_click_mark(s, 3)
        apply_click_mark(s, 7)
        self.assertFalse(apply_click_mark(s, 3))
        self.assertFalse(apply_click_mark(s, 7))
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), (3, 7))

    def test_adjust_normalizes_start_before_end(self):
        """If the nearest-endpoint move would put start AFTER end on
        the timeline, swap them — exports always expect start < end."""
        s = self._state_with(10)
        apply_click_mark(s, 3)
        apply_click_mark(s, 5)
        # Click at index 8 — closer to end (5). End moves to 8: (3, 8). Normal.
        apply_click_mark(s, 8)
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), (3, 8))
        # Now click at index 1 — closer to start (3). Start moves to 1: (1, 8).
        apply_click_mark(s, 1)
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), (1, 8))

    def test_third_click_with_stale_start_id_is_safe_noop(self):
        """Defensive: if `range_start_msg_id` survived a chat switch and
        no longer appears in `selected_chat_messages`, the third click
        must not crash on `list.index(stale_id)`. (App-level cleanup
        in `on_history_loaded` is the primary defense; this is the
        belt-and-suspenders unit guarantee.)"""
        s = self._state_with(5)
        # Manually wedge a stale id into the marks, like the bug seen
        # in the wild after a fast chat switch.
        s.range_start_msg_id = 9999  # not in messages
        s.range_end_msg_id = 3       # in messages
        before = (s.range_start_msg_id, s.range_end_msg_id)
        self.assertFalse(apply_click_mark(s, 4))
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), before)

    def test_unknown_msg_id_is_safe_noop(self):
        """A click for a message not in selected_chat_messages must
        leave state untouched rather than crash on .index()."""
        s = self._state_with(5)
        apply_click_mark(s, 2)
        apply_click_mark(s, 4)
        before = (s.range_start_msg_id, s.range_end_msg_id)
        self.assertFalse(apply_click_mark(s, 9999))
        self.assertEqual((s.range_start_msg_id, s.range_end_msg_id), before)


if __name__ == "__main__":
    unittest.main()
