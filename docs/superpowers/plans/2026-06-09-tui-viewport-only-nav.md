# TUI viewport-only navigation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop the keyboard cursor from `HistoryView`. Arrow keys scroll the viewport like every other chat app; range marks are click-driven only.

**Architecture:** Subtractive refactor. Collapse two pieces of state (cursor + viewport) into one (viewport scroll). Replace ~14 cursor-related methods with 6 scroll action wrappers + 1 autoload helper + 1 scroll-event listener. `paint()` drops 2 layers. `on_click` loses the cursor-move branch. Tests for cursor behavior get deleted en masse; a new `tests/test_history_view_scroll.py` pins the new model.

**Tech Stack:** Python 3.10+ stdlib only, Textual 0.89.1, Rich, unittest.

**Spec:** [`docs/superpowers/specs/2026-06-09-tui-viewport-only-nav-design.md`](../specs/2026-06-09-tui-viewport-only-nav-design.md) (commit `3721d0d`)

---

## Pre-flight context for the executor

Before Task 1, the executor should know:

- **Working tree state on main:** the sidebar redesign WIP is uncommitted on the tree (`modals.py`, `widgets.py`, `wizard.py`, `tests/test_app_navigation.py` modified; `tests/test_sidebar_rows.py` untracked). This refactor TOUCHES `widgets.py` and `modals.py`, which conflicts with the sidebar WIP. **Before starting:** isolate this refactor into a worktree branched from `HEAD` so the sidebar WIP stays put on main. Use `superpowers:using-git-worktrees`.
- **Inside the worktree, before any code changes:** run `git stash push -u -m "viewport-nav-baseline-WIP-carry-forward" -- imessage_export/tui/app/modals.py imessage_export/tui/app/widgets.py imessage_export/tui/wizard.py tests/test_app_navigation.py tests/test_sidebar_rows.py` to set the sidebar WIP aside in this worktree. (The stash stays attached to the worktree's branch; merging back to main later does NOT lose it.)
- After the stash, verify the test suite is GREEN on the clean tree:
  ```bash
  python3 -m unittest discover -s tests 2>&1 | tail -3
  # Expected: Ran 324 tests in ~16s ... OK (skipped=1)
  ```
- This baseline IS the contract — every task must keep the suite green (modulo tests being deliberately deleted in Task 9/10/11).

---

### Task 1: Write the new viewport-scroll test file (RED)

**Files:**
- Create: `tests/test_history_view_scroll.py`

- [ ] **Step 1: Write the failing test file**

```python
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
            # range mark posted, viewport unchanged.
            self.assertEqual(posted, [])
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
            # No other posted messages — SelectionExtended is gone.
            self.assertEqual(len(posted), 1)

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
```

- [ ] **Step 2: Run the new tests — confirm they fail because new actions don't exist**

Run: `python3 -m unittest tests.test_history_view_scroll -v 2>&1 | tail -20`

Expected: 12 failures (1 skip), errors like `AttributeError: 'HistoryView' object has no attribute 'action_scroll_up'`. This is the RED gate before we implement.

- [ ] **Step 3: Commit**

```bash
git add tests/test_history_view_scroll.py
git commit -m "test: viewport-only nav contract for HistoryView (RED)

13 tests pinning the new model: arrow=1 row, PgUp/Dn=viewport,
Home=top-of-loaded, End=bottom, autoload within 5 rows of top,
Space/Shift+arrow unbound, click drops a mark and nothing else,
no cursor state on the instance.

These tests will go GREEN as subsequent tasks add the new methods.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Replace HistoryView.BINDINGS + add scroll action methods

**Files:**
- Modify: `imessage_export/tui/app/widgets.py` (BINDINGS table; add 6 action methods + 1 constant)

- [ ] **Step 1: Locate the current BINDINGS and the cursor action methods**

```bash
grep -n "^    BINDINGS\|action_cursor_up\|action_cursor_down\|action_extend_up\|action_extend_down\|action_cursor_to_start\|action_cursor_to_end\|action_page_up\|action_page_down\|action_mark_row" imessage_export/tui/app/widgets.py
```

- [ ] **Step 2: Replace the BINDINGS table**

The current table (around line 926, immediately after the `RangeMarkRequested` class) binds 12 keys. Replace with:

```python
    BINDINGS = [
        ("up", "scroll_up", "Scroll up"),
        ("down", "scroll_down", "Scroll down"),
        ("pageup", "page_up", "Page up"),
        ("pagedown", "page_down", "Page down"),
        ("home", "scroll_top", "Top of loaded"),
        ("end", "scroll_bottom", "Latest message"),
        ("o", "load_older", "Load older messages"),
        ("escape", "clear_marks", "Clear range marks"),
    ]
```

Old bindings removed: `up→cursor_up`, `down→cursor_down`, `shift+up→extend_up`, `shift+down→extend_down`, `home→cursor_to_start`, `end→cursor_to_end`, `pageup→page_up` (cursor-paged), `pagedown→page_down` (cursor-paged), `space→mark_row`, `enter→mark_row`. `o`, `escape` unchanged.

- [ ] **Step 3: Add `AUTOLOAD_TOP_MARGIN` class constant**

Immediately above the new BINDINGS, add:

```python
    AUTOLOAD_TOP_MARGIN = 5  # rows from top of loaded content that
                              # trigger an auto-load of the next older chunk
```

- [ ] **Step 4: Add the 6 new scroll action methods**

Find the existing `action_load_older` method (around line 802). Add the new actions immediately above it (or wherever the existing action methods cluster — group them so other engineers can find them):

```python
    def action_scroll_up(self) -> None:
        """Scroll viewport up by 1 row; auto-load older chunk if near top."""
        self.scroll_relative(y=-1, animate=False)
        self._check_autoload_threshold()

    def action_scroll_down(self) -> None:
        """Scroll viewport down by 1 row."""
        self.scroll_relative(y=1, animate=False)

    def action_page_up(self) -> None:
        """Scroll viewport up by one viewport height; auto-load if near top."""
        self.scroll_relative(y=-self._viewport_height_lines(), animate=False)
        self._check_autoload_threshold()

    def action_page_down(self) -> None:
        """Scroll viewport down by one viewport height."""
        self.scroll_relative(y=self._viewport_height_lines(), animate=False)

    def action_scroll_top(self) -> None:
        """Scroll to the top of currently-loaded content; auto-load if older
        chunks exist (the affordance stays visible regardless)."""
        self.scroll_to(y=0, animate=False)
        self._check_autoload_threshold()

    def action_scroll_bottom(self) -> None:
        """Scroll to the bottom (latest message)."""
        self.scroll_end(animate=False)
```

- [ ] **Step 5: Run only Task 1's tests that don't depend on autoload**

Run: `python3 -m unittest tests.test_history_view_scroll.TestHistoryViewScroll.test_down_arrow_scrolls_one_row tests.test_history_view_scroll.TestHistoryViewScroll.test_up_arrow_scrolls_one_row tests.test_history_view_scroll.TestHistoryViewScroll.test_page_down_scrolls_one_viewport tests.test_history_view_scroll.TestHistoryViewScroll.test_page_up_scrolls_one_viewport tests.test_history_view_scroll.TestHistoryViewScroll.test_home_scrolls_to_top_of_loaded tests.test_history_view_scroll.TestHistoryViewScroll.test_end_scrolls_to_bottom -v 2>&1 | tail -15`

Expected: 6 tests pass. (Autoload tests still fail until Task 3.)

- [ ] **Step 6: Commit**

```bash
git add imessage_export/tui/app/widgets.py
git commit -m "feat: HistoryView viewport-scroll actions + new BINDINGS

action_scroll_up/down/page_up/page_down/scroll_top/scroll_bottom
delegate to scroll_relative/scroll_to/scroll_end. New BINDINGS
table drops shift+arrow, Space/Enter, and the cursor-walk bindings.
Cursor methods still present but unreachable from keys — Task 5
removes them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Add `_check_autoload_threshold` + `on_scroll` listener

**Files:**
- Modify: `imessage_export/tui/app/widgets.py` (add 2 methods)

- [ ] **Step 1: Add `_check_autoload_threshold`**

Place immediately after `action_scroll_bottom` from Task 2:

```python
    def _check_autoload_threshold(self) -> None:
        """If the viewport is within AUTOLOAD_TOP_MARGIN of the top and
        older messages are still hidden, fire one action_load_older.

        Bounded by the chat size: action_load_older itself short-circuits
        when _shown_count >= len(_all_messages). The existing scroll-
        restore logic in action_load_older keeps the user's reading
        position stable across the mount, so from the user's perspective
        it feels like infinite scroll.
        """
        if self._shown_count >= len(self._all_messages):
            return
        if self.scroll_y < self.AUTOLOAD_TOP_MARGIN:
            self.action_load_older()
```

- [ ] **Step 2: Add `on_scroll` so mouse-wheel scrolling also auto-loads**

Place immediately after `_check_autoload_threshold`:

```python
    def on_scroll(self, event) -> None:
        """Mouse-wheel / trackpad scroll: re-check autoload threshold so
        scroll-to-top with the wheel works the same as arrow keys."""
        self._check_autoload_threshold()
```

- [ ] **Step 3: Run the autoload tests**

Run: `python3 -m unittest tests.test_history_view_scroll.TestHistoryViewScroll.test_autoload_fires_within_5_rows_of_top tests.test_history_view_scroll.TestHistoryViewScroll.test_autoload_no_op_at_chat_start -v 2>&1 | tail -5`

Expected: both pass.

- [ ] **Step 4: Run the full new test file**

Run: `python3 -m unittest tests.test_history_view_scroll -v 2>&1 | tail -10`

Expected: 12 pass, 1 skipped (the cross-reference skip).

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py
git commit -m "feat: HistoryView near-top scroll auto-loads next chunk

_check_autoload_threshold fires action_load_older when scroll_y is
within AUTOLOAD_TOP_MARGIN (5 rows) of the top AND older messages
are still hidden. on_scroll wires the check up to mouse-wheel.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Verify full suite is still green before stripping cursor code

- [ ] **Step 1: Run the full suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: all tests pass (the cursor tests in `test_history_view_cursor.py` still pass because the cursor methods are still present — they're just unreachable from keys).

If any test fails, STOP and diagnose. The cursor methods coexist with the new scroll methods at this point; the only behavior change is which keys trigger which methods.

---

### Task 5: Delete `tests/test_history_view_cursor.py`

**Files:**
- Delete: `tests/test_history_view_cursor.py`

- [ ] **Step 1: Confirm the file's tests all test cursor-only behavior**

```bash
grep -E "^    async def test_|^class " tests/test_history_view_cursor.py | head -30
```

Sanity-check: 26 test methods, all referencing `_cursor_msg_id`, `action_cursor_*`, `action_extend_*`, `action_mark_row`, or `_scroll_cursor_into_view`. All going away.

- [ ] **Step 2: Delete the file**

```bash
git rm tests/test_history_view_cursor.py
```

- [ ] **Step 3: Run the suite — cursor code still in widgets.py, but nothing tests it now**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: all tests pass. Test count drops by 26.

- [ ] **Step 4: Commit**

```bash
git commit -m "test: drop test_history_view_cursor.py — cursor model removed

26 tests pinning behavior the viewport-only refactor removes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Strip cursor state and cursor methods from `HistoryView`

**Files:**
- Modify: `imessage_export/tui/app/widgets.py`

This is the largest task — purely subtractive but touches many lines. Work top-to-bottom in the file so attribute references and method definitions get removed in one pass.

- [ ] **Step 1: Delete `SelectionExtended` inner class**

Find it (around line 926, just after `RangeMarkRequested`). Delete the entire class:

```python
    class SelectionExtended(TextualMessage):
        """Shift+arrow updated the visual selection — tell the app so it
        can mirror (start, end) into AppState's range_*_msg_id, otherwise
        Export ignores the selection and falls back to mode="all"."""
        def __init__(self, start_id: int, end_id: int) -> None:
            super().__init__()
            self.start_id = start_id
            self.end_id = end_id
```

- [ ] **Step 2: Delete cursor attribute initializations in `__init__`**

Find the four cursor-related attribute initializers (around line 521-524):

```python
        self._cursor_msg_id: int | None = None
        self._id_to_index: dict[int, int] = {}
        self._mark_anchor_id: int | None = None
        self._mark_active_id: int | None = None
```

Delete all four lines.

- [ ] **Step 3: Add the replacement `_loaded_ids` attribute**

In the same `__init__`, where the cursor attributes used to be, add:

```python
        # Set of every msg_id present in the loaded chat — used by
        # on_click to drop stale-meta clicks after a filter or chat
        # switch. O(1) membership lookup, rebuilt in render_messages.
        self._loaded_ids: set[int] = set()
```

- [ ] **Step 4: Delete the cursor-rebuild block in `render_messages`**

Find the `loaded_ids = {m.message_id for m in self._all_messages}` block and the surrounding cursor-related code (around lines 596-607). Replace with:

```python
        self._loaded_ids = {m.message_id for m in self._all_messages}
```

And delete the lines that update `_cursor_msg_id`, `_id_to_index`, `_mark_anchor_id`, `_mark_active_id`.

The "chat switch clears shift+arrow anchor" reset block (added during the cursor fixes — `self._mark_anchor_id = None; self._mark_active_id = None` near the top of `render_messages`) also goes — those attributes don't exist anymore.

- [ ] **Step 5: Delete the cursor action methods**

Delete these methods entirely (all of them are in `widgets.py`, search the names with grep to find exact lines):

- `action_cursor_up`
- `action_cursor_down`
- `action_extend_up`
- `action_extend_down`
- `action_cursor_to_start`
- `action_cursor_to_end`
- `action_mark_row`
- `_move_cursor`
- `_extend_selection`
- `_jump_cursor_to`
- `_scroll_cursor_into_view`
- `_ensure_id_rendered`
- `_nearest_loaded_by_timestamp` (verify with grep that no other caller exists)

Also delete the `SCROLL_MARGIN = 2` class constant — only `_scroll_cursor_into_view` used it.

Keep `_viewport_height_lines` — it's still used by the new `action_page_up` / `action_page_down`.

Keep the OLD `action_page_up` / `action_page_down` only if they're a different method than Task 2's new ones (they should now BE Task 2's new ones, since Task 2 replaced them — verify there's only one of each).

- [ ] **Step 6: Simplify `on_click` — drop the cursor-move branch**

Find the `# Move the keyboard cursor to the clicked row` block in `on_click` (added during the cursor session). Delete the entire block:

```python
                # Move the keyboard cursor to the clicked row so the
                # next ↑/↓ walks from the row the user just clicked,
                # not from wherever the cursor happened to be (usually
                # the latest message). Repaint the old + new cursor row.
                old_cursor = self._cursor_msg_id
                clicked_id = int(msg_id)
                if old_cursor != clicked_id:
                    self._cursor_msg_id = clicked_id
                    affected = {clicked_id}
                    if old_cursor is not None:
                        affected.add(old_cursor)
                    self._repaint_for_ids(affected)
                self.post_message(self.RangeMarkRequested(clicked_id))
                event.stop()
                return
```

Replace with the minimal post:

```python
                self.post_message(self.RangeMarkRequested(int(msg_id)))
                event.stop()
                return
```

Also update the stale-id guard immediately above it from `_id_to_index` to `_loaded_ids`:

```python
                if int(msg_id) not in self._loaded_ids:
                    event.stop()
                    return
```

- [ ] **Step 7: Search for any remaining cursor references**

```bash
grep -n "_cursor_msg_id\|_id_to_index\|_mark_anchor_id\|_mark_active_id\|_scroll_cursor_into_view\|_ensure_id_rendered\|_move_cursor\|_extend_selection\|_jump_cursor_to\|SelectionExtended\|SCROLL_MARGIN\|_nearest_loaded_by_timestamp\|action_cursor_up\|action_cursor_down\|action_extend_up\|action_extend_down\|action_cursor_to_start\|action_cursor_to_end\|action_mark_row" imessage_export/tui/app/widgets.py
```

Expected: empty output. If any reference remains, find and remove it.

- [ ] **Step 8: Run the full suite — should still pass**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: all tests pass. `paint()` callers in tests are still passing a `cursor_id` argument — that's fine for now because Task 7 hasn't touched `paint()` yet.

Wait — actually if Task 5 deleted `test_history_view_cursor.py` and that was the only test using `_cursor_msg_id`, this should pass. But `test_history_render.py` may call `paint(chunk, cursor_id, marks, palette)` — that still works because `paint()`'s signature hasn't changed yet.

- [ ] **Step 9: Commit**

```bash
git add imessage_export/tui/app/widgets.py
git commit -m "refactor: strip keyboard cursor state and methods from HistoryView

Drop _cursor_msg_id, _id_to_index, _mark_anchor_id, _mark_active_id;
SelectionExtended message class; 13 cursor-related methods; the
cursor-move branch from on_click. Replace _id_to_index with a
_loaded_ids set whose only use is the on_click stale-id guard.

paint() still takes cursor_id — Task 7 simplifies it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Simplify `paint()` — drop `cursor_id` and Layers 2 & 3

**Files:**
- Modify: `imessage_export/tui/app/history_render.py`
- Modify: `imessage_export/tui/app/widgets.py` (callers of `paint`)
- Modify: `tests/test_history_render.py` (paint() calls)

- [ ] **Step 1: Rewrite `paint()` in `history_render.py`**

Find the existing `def paint(chunk, cursor_id, marks, palette)` (around line 648). Replace the entire function body with:

```python
def paint(
    chunk: _ChunkRender,
    marks: MarkState,
    palette: dict,
) -> Text:
    """Clone the chunk's cached base and overlay selection spans.

    The cached `base` is never mutated — every paint returns a clone.
    Endpoint background always wins; in-range layers under it. No
    cursor visual — viewport scroll position is the only "where am I"
    indicator.
    """
    out = chunk.base.copy()
    colors = selection_colors(palette)
    endpoints = {marks.anchor_id, marks.active_id} - {None}

    for msg_id in chunk.msg_ids:
        start, end = chunk.row_offsets[msg_id]
        is_endpoint = msg_id in endpoints
        is_in_range = (not is_endpoint) and msg_id in marks.in_range_ids

        if is_endpoint and colors.endpoint_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.endpoint_bg}"),
                start, end,
            )
        elif is_in_range and colors.range_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.range_bg}"),
                start, end,
            )

    return out
```

This removes:
- The `cursor_id` parameter
- Layer 2 (cursor row tint on body + speaker header)
- Layer 3 (cursor bar on body + speaker header)

`chunk.header_offsets` stays in the dataclass and stays populated — it's still needed for click routing on speaker headers (`style.meta`).

- [ ] **Step 2: Update `HistoryView` callers of `paint()`**

Find all `paint(` calls in `widgets.py`. There are 3:

1. In `render_messages` (around line 619):
   ```python
   decorated = history_render.paint(chunk, self._cursor_msg_id, marks, palette)
   ```
   Change to:
   ```python
   decorated = history_render.paint(chunk, marks, palette)
   ```

2. In `action_load_older` (around line 833):
   ```python
   older_decorated = history_render.paint(
       chunk, self._cursor_msg_id, marks, self._palette())
   ```
   Change to:
   ```python
   older_decorated = history_render.paint(
       chunk, marks, self._palette())
   ```

3. In `_repaint_for_ids` — find by `grep -n "history_render.paint" imessage_export/tui/app/widgets.py`. Apply the same signature change.

- [ ] **Step 3: Update `paint()` calls in `tests/test_history_render.py`**

```bash
grep -n "history_render.paint(" tests/test_history_render.py
```

For every call site, drop the `cursor_id` arg. Most callers pass `None` — those become `paint(chunk, marks, palette)`. Use Edit with `replace_all=true` for the common pattern:

```bash
# Inspect first
grep -A1 "history_render.paint(" tests/test_history_render.py | head -20
```

If the call form is consistent (likely `history_render.paint(chunk, None, marks, palette)`), use a single Edit with replace_all to drop the `None,` argument.

- [ ] **Step 4: Delete the four cursor-visual tests from `tests/test_history_render.py`**

```bash
grep -n "def test_cursor_visual_renders_on_exactly_one_row\|def test_cursor_tint_on_speaker_header\|def test_cursor_bar_on_speaker_header\|def test_cursor_bar_color_against_selection_bg" tests/test_history_render.py
```

Delete each of these test methods. Some may not exist with these exact names — grep for `cursor` to find the actual names and verify only cursor-visual tests are deleted (mark-painting tests stay).

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add imessage_export/tui/app/history_render.py imessage_export/tui/app/widgets.py tests/test_history_render.py
git commit -m "refactor: paint() drops cursor_id and Layer 2/3

paint(chunk, marks, palette) — Layer 1 (selection bg) survives; the
cursor row tint and cursor bar are gone. header_offsets stays on
_ChunkRender for click routing on speaker headers.

Callers in widgets.py drop the cursor_id arg. Four cursor-visual
tests in test_history_render.py deleted; ~60 tests survive.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Delete app-level `on_history_view_selection_extended` handler

**Files:**
- Modify: `imessage_export/tui/app/app.py`

- [ ] **Step 1: Find and delete the handler**

```bash
grep -n "on_history_view_selection_extended\|SelectionExtended" imessage_export/tui/app/app.py
```

Delete the entire method (added during the cursor session):

```python
    def on_history_view_selection_extended(self, event: HistoryView.SelectionExtended) -> None:
        """Shift+arrow extended the keyboard selection. Mirror the new
        (start, end) into AppState so Export bracket-resolves against
        the selection instead of falling back to mode="all"."""
        self.state.range_start_msg_id = event.start_id
        self.state.range_end_msg_id = event.end_id
        self.state.window_source = "selection"
        self.state.last_export_status = None
        self._refresh_status()
```

- [ ] **Step 2: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add imessage_export/tui/app/app.py
git commit -m "refactor: drop on_history_view_selection_extended handler

The SelectionExtended message class died in widgets.py — the app-
level handler that received it has no caller.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Trim `tests/test_app_navigation.py`

**Files:**
- Modify: `tests/test_app_navigation.py`

- [ ] **Step 1: Find shift+arrow / cursor-related tests**

```bash
grep -nE "shift.?\+.?(up|down|arrow)|action_extend|action_cursor|SelectionExtended|_cursor_msg_id" tests/test_app_navigation.py
```

- [ ] **Step 2: Delete matching tests, keep the focus-bridge tests**

For each match, look at the enclosing test method. If the test pins shift+arrow or cursor behavior, delete the whole method. The chat-list↔history bridge tests (Left arrow / Right arrow / Enter to focus history) stay.

Specifically guard `test_left_from_history_focuses_sidebar` and `test_right_from_list_focuses_history` — those are the cross-pane navigation tests this refactor depends on.

- [ ] **Step 3: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app_navigation.py
git commit -m "test: drop shift+arrow / cursor assertions in test_app_navigation

Keep sidebar↔history focus-bridge tests. Removed any test that
pinned behavior the viewport-only refactor deletes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Update HelpModal text in `modals.py`

**Files:**
- Modify: `imessage_export/tui/app/modals.py`

- [ ] **Step 1: Find the History section in HelpModal**

```bash
grep -n "History\|Shift+\|cursor\|Space\|Mark cursor" imessage_export/tui/app/modals.py
```

- [ ] **Step 2: Replace the History block**

The current History section reads (approximately):

```
History
  ↑ ↓                    Move keyboard cursor one message
  Shift+↑ Shift+↓        Extend selection from a fixed anchor
  Home / End            Jump to oldest / newest
  PageUp / PageDown     Move cursor by viewport height
  Space / Enter         Mark cursor row as range endpoint (1st = start, 2nd = end)
  click a message       Move cursor to that row and drop an endpoint
  click after both set  Move nearest endpoint to the click (extend / shrink)
  o / click banner      Load 2,000 older messages
  Esc                   Clear marks
```

Replace with:

```
History
  ↑ ↓                    Scroll up / down by 1 row
  PageUp / PageDown     Scroll by one viewport
  Home / End            Top of loaded / latest message
  click a message       Drop a range endpoint (1st = start, 2nd = end)
  click after both set  Move nearest endpoint to the click (extend / shrink)
  o / click banner      Load 2,000 older messages
  Esc                   Clear marks
```

Also update the global Navigation block — replace `↑ ↓ Move within the focused region` with `↑ ↓ Scroll history / move within the sidebar list`.

- [ ] **Step 3: Find the help-text test if one exists**

```bash
grep -rn "HelpModal\|help text\|Shift+" tests/
```

If a test pins specific lines of the help text, update its expected strings. If no test pins it, no test change.

- [ ] **Step 4: Run the suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/modals.py tests/
git commit -m "docs: HelpModal text matches viewport-only nav

Drop references to keyboard cursor, Shift+arrow, Space/Enter, and
'move cursor' behaviors. Arrows are 1-row scroll; click drops
range endpoints; mouse is the only mark interaction.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Manual smoke test in the TUI

This step doesn't produce a commit. It produces evidence that the refactor works end-to-end.

- [ ] **Step 1: Launch the TUI on a real chat**

```bash
python3 -m imessage_export --app
```

If running over Claude Code without a real TTY, drive it via tmux (see `verify` skill for the harness).

- [ ] **Step 2: Walk the smoke checklist from the spec**

For each item below, perform the action and assert what you see. Capture a tmux pane snapshot for any failure.

1. ✅ Press ↓ — viewport scrolls down 1 row, no jump.
2. ✅ Press ↑ many times — viewport scrolls up 1 row each, never yanks.
3. ✅ PageUp/PageDown — viewport scrolls by ~viewport height each.
4. ✅ Home — viewport at top of loaded; "Load older" affordance visible.
5. ✅ End — viewport at bottom showing latest message.
6. ✅ Scroll near top with arrows — older chunk auto-mounts, reading position stable.
7. ✅ Click two messages — range highlights between them.
8. ✅ Click a third message — nearest endpoint moves.
9. ✅ Esc — marks clear.
10. ✅ Type-to-filter from sidebar — message set narrows, marks clear if endpoints filtered out.
11. ✅ Press Space inside history — nothing happens (was: drop mark).
12. ✅ Press Shift+Down inside history — nothing happens.
13. ✅ Press Left arrow inside history — focus moves to sidebar.

- [ ] **Step 3: Report results**

If all 13 pass, the refactor is verified. If any fails, root-cause and patch (and add a regression test to `tests/test_history_view_scroll.py`).

---

### Task 12: Final guard — confirm nothing references dead state

- [ ] **Step 1: Sweep the source tree for any leftover cursor references**

```bash
grep -rn "_cursor_msg_id\|_id_to_index\|_mark_anchor_id\|_mark_active_id\|SelectionExtended\|_scroll_cursor_into_view\|_ensure_id_rendered\|action_cursor_up\|action_cursor_down\|action_extend_up\|action_extend_down\|action_cursor_to_start\|action_cursor_to_end\|action_mark_row\|_move_cursor\|_extend_selection\|_jump_cursor_to\|_nearest_loaded_by_timestamp\|SCROLL_MARGIN" imessage_export/ tests/ docs/superpowers/specs/2026-06-09-tui-viewport-only-nav-design.md
```

Expected: only the design spec (`docs/superpowers/specs/2026-06-09-tui-viewport-only-nav-design.md`) and the plan (this file) reference these names, as expected. No `imessage_export/` or `tests/` source hits.

- [ ] **Step 2: Confirm the full test suite count**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -3
```

Expected: ~298 tests pass, 1 skipped. (Baseline 324 minus 26 deleted cursor tests, plus 12 new scroll tests, minus the 4 cursor-visual tests deleted from `test_history_render.py`, minus any deleted from `test_app_navigation.py`. Exact number depends on Task 9 / Task 10 deletions — verify it lands in the 295-305 range.)

- [ ] **Step 3: Restore sidebar WIP if this work was done in a worktree**

Per the pre-flight, the executor stashed sidebar WIP. After this refactor merges back to `main`, the sidebar WIP can be popped onto main or moved to its own branch via `git stash apply stash@{0}` from main.

If working in a worktree: this stash lives on the worktree's branch. Merging the worktree branch to main does not move the stash. Document the stash entry's existence in the PR description so the next session knows where to find it.

---

## Self-review notes

- Spec section §1 (bindings table) → Tasks 2, 10.
- Spec §2 (auto-load on near-top) → Task 3.
- Spec §3 (state that dies) → Task 6 (and Task 8 for the app handler).
- Spec §4 (state that stays) → No task — preserved by Tasks 6 & 7's explicit "do not touch".
- Spec §5 (state that's new) → Tasks 2 & 3.
- Spec §6 (paint() simplify) → Task 7.
- Spec §7 (click data flow) → Task 6 step 6.
- Spec §8 (arrow data flow) → Tasks 2 & 3.
- Spec §9 (files touched) → Tasks 2-10.
- Spec edge cases → Covered by the new tests in Task 1 and the existing `apply_marks` defensive branch (untouched).
- Spec testing strategy (delete cursor file, trim render+nav tests, add scroll tests) → Tasks 1, 5, 7, 9.
- Spec acceptance criteria → Task 12 (suite) + Task 11 (smoke).
