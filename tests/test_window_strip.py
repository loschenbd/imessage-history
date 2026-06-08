"""Regression coverage for Phase 2 of the export-window redesign:
the inline `WindowStrip` widget and its date-range filtering.

The strip is a sibling of HistoryView (mounted above it in the same
Vertical) and posts `WindowStrip.WindowChanged(window | None)` whenever
the user presses Apply, Clear, or one of the relative presets
(7d / 30d / Month / Year). The app handler updates
`state.typed_window` and calls `HistoryView.filter_messages(window)`,
which re-renders the preview from the filtered subset.

The tests below split the contract three ways so each is verifiable
without the others:

  * `filter_by_window` (pure function in state.py) — exercised with
    synthetic message dicts at fixed timestamps. No Textual needed.
  * `WindowStrip.preset_range` (pure static method) — date math for
    each relative preset, frozen against a hand-picked "today".
  * `HistoryView.filter_messages` round-trip — apply a window, see
    the filtered subset rendered; pass `None`, see the full chat
    restored from `_unfiltered_messages` even though render_messages
    overwrote `_all_messages`.
"""
from __future__ import annotations

import importlib
import unittest
from datetime import date

try:
    importlib.import_module("textual")
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


def _fake_messages(n: int, start_ts: str = "2026-01-01 00:00:00"):
    """`n` synthetic Message rows on consecutive days starting at
    `start_ts`. Day stride keeps the timestamps easy to filter by
    date and avoids the day-rollover edge case at hh:60."""
    from datetime import datetime, timedelta
    from imessage_export.models import Message
    base = datetime.strptime(start_ts, "%Y-%m-%d %H:%M:%S")
    out = []
    for i in range(n):
        t = base + timedelta(days=i)
        ts = t.strftime("%Y-%m-%d %H:%M:%S")
        out.append(Message(
            message_id=i,
            timestamp=ts,
            timestamp_utc=ts.replace(" ", "T") + "+00:00",
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
        ))
    return out


class TestFilterByWindow(unittest.TestCase):
    """Pure-function coverage. No Textual app needed."""

    @staticmethod
    def _msgs():
        return [
            {"message_id": 1, "timestamp": "2026-06-01 09:00:00"},
            {"message_id": 2, "timestamp": "2026-06-03 13:30:00"},
            {"message_id": 3, "timestamp": "2026-06-05 18:15:00"},
            {"message_id": 4, "timestamp": "2026-06-08 22:45:00"},
        ]

    def test_mode_all_returns_everything_unchanged(self):
        from imessage_export.tui.app.state import filter_by_window
        out = filter_by_window(self._msgs(), {"mode": "all"})
        self.assertEqual(len(out), 4)

    def test_mode_range_inclusive_endpoints(self):
        from imessage_export.tui.app.state import filter_by_window
        out = filter_by_window(self._msgs(), {
            "mode": "range", "from_date": "2026-06-03", "to_date": "2026-06-05",
        })
        self.assertEqual([m["message_id"] for m in out], [2, 3])

    def test_mode_range_with_times_narrows_within_window(self):
        from imessage_export.tui.app.state import filter_by_window
        # Date window covers all 4; time window 12:00–20:00 keeps only 2 and 3.
        out = filter_by_window(self._msgs(), {
            "mode": "range", "from_date": "2026-06-01", "to_date": "2026-06-08",
            "start_time": "12:00", "end_time": "20:00",
        })
        self.assertEqual([m["message_id"] for m in out], [2, 3])

    def test_mode_day_keeps_only_that_date(self):
        from imessage_export.tui.app.state import filter_by_window
        out = filter_by_window(self._msgs(), {"mode": "day", "date": "2026-06-05"})
        self.assertEqual([m["message_id"] for m in out], [3])

    def test_works_on_message_dataclass_too(self):
        """The helper must accept both slim {message_id, timestamp}
        dicts AND the full Message dataclass (HistoryView passes the
        latter on its way to render)."""
        from imessage_export.tui.app.state import filter_by_window
        msgs = _fake_messages(7)
        out = filter_by_window(msgs, {
            "mode": "range", "from_date": "2026-01-03", "to_date": "2026-01-05",
        })
        self.assertEqual([m.message_id for m in out], [2, 3, 4])

    def test_missing_date_fields_dont_explode(self):
        """A range window with only `from_date` defaults `to_date` to
        the far future — the helper shouldn't trip over the absent key."""
        from imessage_export.tui.app.state import filter_by_window
        out = filter_by_window(self._msgs(), {
            "mode": "range", "from_date": "2026-06-05",
        })
        self.assertEqual([m["message_id"] for m in out], [3, 4])


class TestPresetRange(unittest.TestCase):
    """Pure date math, frozen against a known `today`."""

    def setUp(self):
        from imessage_export.tui.app.widgets import WindowStrip
        self.WindowStrip = WindowStrip
        # Pick a today that exercises the month/year boundary cleanly.
        self.today = date(2026, 6, 8)

    def test_7d_returns_today_minus_7(self):
        fd, td = self.WindowStrip.preset_range("ws-preset-7d", self.today)
        self.assertEqual(fd, "2026-06-01")
        self.assertEqual(td, "2026-06-08")

    def test_30d_returns_today_minus_30(self):
        fd, td = self.WindowStrip.preset_range("ws-preset-30d", self.today)
        self.assertEqual(fd, "2026-05-09")
        self.assertEqual(td, "2026-06-08")

    def test_month_returns_first_of_month(self):
        fd, td = self.WindowStrip.preset_range("ws-preset-month", self.today)
        self.assertEqual(fd, "2026-06-01")
        self.assertEqual(td, "2026-06-08")

    def test_year_returns_jan_1(self):
        fd, td = self.WindowStrip.preset_range("ws-preset-year", self.today)
        self.assertEqual(fd, "2026-01-01")
        self.assertEqual(td, "2026-06-08")

    def test_unknown_preset_returns_none(self):
        self.assertIsNone(self.WindowStrip.preset_range("ws-preset-junk", self.today))


@unittest.skipUnless(HAS_TEXTUAL, "[tui] extra not installed")
class TestHistoryViewFilterRoundTrip(unittest.IsolatedAsyncioTestCase):
    """The integration that ties WindowStrip → HistoryView together:
    apply a window, see the filtered subset rendered; clear it, see
    the full chat restored — even though the original `_all_messages`
    was overwritten in-place by the filtered render."""

    @staticmethod
    def _build_stub_app():
        from textual.app import App, ComposeResult
        from imessage_export.tui.app.widgets import HistoryView

        class _StubApp(App):
            def compose(self) -> ComposeResult:
                yield HistoryView(id="history")

        return _StubApp(), HistoryView

    async def test_filter_then_clear_restores_unfiltered(self):
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(10))
            await pilot.pause()
            self.assertEqual(len(history._unfiltered_messages), 10)
            self.assertEqual(len(history._all_messages), 10)

            # Narrow to a 3-day slice in the middle.
            history.filter_messages({
                "mode": "range",
                "from_date": "2026-01-03",
                "to_date": "2026-01-05",
            })
            await pilot.pause()
            self.assertEqual(len(history._all_messages), 3)
            # The unfiltered cache must survive the filtered render.
            self.assertEqual(len(history._unfiltered_messages), 10)

            # Clear filter → full chat back.
            history.filter_messages(None)
            await pilot.pause()
            self.assertEqual(len(history._all_messages), 10)
            self.assertEqual(len(history._unfiltered_messages), 10)

    async def test_filter_does_not_overwrite_unfiltered(self):
        """Calling `render_messages` directly (the chat-load worker
        path) DOES replace `_unfiltered_messages`. Calling
        `filter_messages` (the WindowStrip path) must NOT — otherwise
        the user can never get back to the full chat."""
        app, HistoryView = self._build_stub_app()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(8))
            await pilot.pause()

            history.filter_messages({
                "mode": "range",
                "from_date": "2026-01-01",
                "to_date": "2026-01-02",
            })
            await pilot.pause()
            self.assertEqual(len(history._all_messages), 2)
            self.assertEqual(len(history._unfiltered_messages), 8)


if __name__ == "__main__":
    unittest.main()
