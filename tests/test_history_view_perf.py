"""Layer 3 perf budget for HistoryView's repaint path.

Pins the post-refactor invariant: a single _repaint_for_ids call on a
4k-message chat must complete in well under 2 ms — that's the budget
for click-to-mark latency to feel instant on the next interaction.

Skipped on CI (`IMESSAGE_SKIP_PERF=1`) to avoid noise; runs locally as
a regression catch when the rendering pipeline gets touched.
"""
from __future__ import annotations

import importlib
import os
import time
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
@unittest.skipIf(os.environ.get("IMESSAGE_SKIP_PERF") == "1", "perf test skipped")
class TestHistoryViewPerf(unittest.IsolatedAsyncioTestCase):

    async def test_repaint_budget_under_2ms_per_call_on_4k(self):
        from textual.app import App, ComposeResult
        from imessage_export.tui.app.widgets import HistoryView

        class _StubApp(App):
            def compose(self) -> ComposeResult:
                yield HistoryView(id="history")

        app = _StubApp()
        async with app.run_test() as pilot:
            history = app.query_one(HistoryView)
            history.render_messages(_fake_messages(4000))
            await pilot.pause()

            # Warm the cache (first repaint after mount may be slower).
            history._repaint_for_ids({0})
            await pilot.pause()

            start = time.perf_counter()
            N = 100
            for i in range(N):
                history._repaint_for_ids({i % len(history._all_messages)})
            elapsed = time.perf_counter() - start

            per_call_ms = (elapsed / N) * 1000
            self.assertLess(
                per_call_ms, 2.0,
                f"repaint budget exceeded: {per_call_ms:.2f} ms/call "
                f"(want < 2 ms; 4k-msg chunk, 100-call average)",
            )


if __name__ == "__main__":
    unittest.main()
