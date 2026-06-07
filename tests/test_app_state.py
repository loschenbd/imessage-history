"""AppState resolution + reset-after-export logic."""
from __future__ import annotations

import unittest
from pathlib import Path

from imessage_export.tui.app.state import AppState, _format_window, resolved_window, reset_after_export


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


if __name__ == "__main__":
    unittest.main()
