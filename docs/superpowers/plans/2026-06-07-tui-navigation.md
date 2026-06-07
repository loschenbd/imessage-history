# TUI Navigation Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Textual app's keyboard navigation conventional and discoverable — arrows + Tab + Enter + Esc + mouse — without introducing vim-style modal grammar.

**Architecture:** All changes live inside `imessage_export/tui/app/`. The `HistoryView` widget grows focusable message rows + an in-pane search input. `Sidebar` gains fzf-style filter behavior (arrows always on the list, typing auto-focuses the filter). `ActionBar` and the Tab cycle get a visible active-region border. `StatusLine` gains a `[region]` chip. `HelpModal` text is refreshed. One new `AppState` field (`history_search_query`) and one new pure helper (`filter_messages_by_query`) cover the unit-testable logic; everything else is exercised by Textual `Pilot` smoke tests in a new file `tests/test_app_navigation.py`.

**Tech Stack:** Python 3.10+, Textual 0.89.1 (pinned `>=0.79,<1.0`), Rich. stdlib `unittest` (no pytest). Tests run with the pipx venv's Python: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests`.

**Spec:** `docs/superpowers/specs/2026-06-07-tui-navigation-design.md`

---

## Preconditions

Before starting Task 1, confirm:

- [ ] Working directory is the worktree: `pwd` shows `/Users/benjaminloschen/Projects/imessage-history/.worktrees/tui-navigation-spec`.
- [ ] Branch: `git branch --show-current` returns `tui-navigation-spec`.
- [ ] Baseline tests pass: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` → `Ran 193 tests in <X>s` then `OK`.

If any check fails, stop and surface the discrepancy.

## File-by-file overview

- **`imessage_export/tui/app/state.py`** — adds `history_search_query: Optional[str] = None`; adds pure helper `filter_messages_by_query(messages, query) -> list`. Existing `reset_after_export` does NOT touch this field (search clears on chat switch, not on export).
- **`imessage_export/tui/app/widgets.py`** — bulk of the work. `HistoryView` becomes focus-aware (each row, plus a search Input). `Sidebar` overrides `on_key` for type-to-filter. `ActionBar` ensures Tab lands on the first button. `StatusLine` gains a `focus_region` slot rendered as a `[region]` chip.
- **`imessage_export/tui/app/app.py`** — auto-moves focus to the History pane after a chat loads. Subscribes to focus changes via `on_descendant_focus` and forwards the region tag to `StatusLine`. Adds the CSS rule that paints the active region's border.
- **`imessage_export/tui/app/modals.py`** — refreshes `HelpModal`'s static text only. No new behavior.
- **`tests/test_app_state.py`** — extends with two cases for the new field + helper.
- **`tests/test_app_navigation.py`** *(new file)* — Pilot-based smoke tests for the navigation flows.

---

## Task 1: AppState gains `history_search_query` + pure search helper

**Files:**
- Modify: `imessage_export/tui/app/state.py`
- Test: `tests/test_app_state.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_state.py` (end of file, in their own test classes):

```python
from imessage_export.tui.app.state import filter_messages_by_query


class TestHistorySearchQueryField(unittest.TestCase):
    def test_default_is_none(self):
        s = AppState()
        self.assertIsNone(s.history_search_query)

    def test_reset_after_export_does_not_clear_search(self):
        s = AppState(history_search_query="hello")
        reset_after_export(s, success_tag="ok")
        self.assertEqual(s.history_search_query, "hello")  # search clears on chat switch, not on export


class _Msg:
    """Lightweight stand-in for the Message dataclass used by HistoryView."""
    def __init__(self, mid, text):
        self.message_id = mid
        self.text = text
        self.timestamp = "2026-06-06 09:00:00"
        self.author_label = "Alice"


class TestFilterMessagesByQuery(unittest.TestCase):
    def test_none_query_returns_all(self):
        msgs = [_Msg(1, "hello"), _Msg(2, "world")]
        self.assertEqual(filter_messages_by_query(msgs, None), msgs)

    def test_empty_query_returns_all(self):
        msgs = [_Msg(1, "hello"), _Msg(2, "world")]
        self.assertEqual(filter_messages_by_query(msgs, ""), msgs)

    def test_case_insensitive_substring_match(self):
        msgs = [_Msg(1, "Hello there"), _Msg(2, "GOODBYE"), _Msg(3, "say hello again")]
        out = filter_messages_by_query(msgs, "HELLO")
        self.assertEqual([m.message_id for m in out], [1, 3])

    def test_no_match_returns_empty(self):
        msgs = [_Msg(1, "hello"), _Msg(2, "world")]
        self.assertEqual(filter_messages_by_query(msgs, "xyzzy"), [])

    def test_handles_none_text(self):
        # Messages can have text=None (edited/unsent rows) — must not crash.
        msgs = [_Msg(1, None), _Msg(2, "hello")]
        out = filter_messages_by_query(msgs, "hello")
        self.assertEqual([m.message_id for m in out], [2])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_state -v 2>&1 | tail -20`
Expected: ImportError for `filter_messages_by_query`, AND/OR AttributeError on `AppState.history_search_query`.

- [ ] **Step 3: Add the state field and helper**

Edit `imessage_export/tui/app/state.py`:

1. Inside `AppState`, add a new field (place it in the "ephemeral" section, after `history_loading`):

```python
    # ephemeral
    last_export_status: Optional[str] = None
    history_loading: bool = False
    history_search_query: Optional[str] = None
```

2. At end of file, add the pure helper:

```python
def filter_messages_by_query(messages: list, query: Optional[str]) -> list:
    """Return messages whose `text` contains `query` (case-insensitive).

    `messages` is a list of Message-like objects with a `.text` attribute.
    `query` of None or "" returns the input unchanged. Messages with `text=None`
    are skipped (treated as non-matching).
    """
    if not query:
        return list(messages)
    q = query.lower()
    return [m for m in messages if m.text and q in m.text.lower()]
```

Do NOT modify `reset_after_export` — the test confirms `history_search_query` is preserved across exports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_state -v 2>&1 | tail -10`
Expected: all tests in `tests.test_app_state` pass.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/state.py tests/test_app_state.py
git commit -m "Nav Task 1: AppState.history_search_query + filter_messages_by_query helper."
```

---

## Task 2: HistoryView message rows become keyboard-focusable

**Files:**
- Modify: `imessage_export/tui/app/widgets.py` (HistoryView class)
- Test: `tests/test_app_navigation.py` *(new file)*

Goal: each rendered `.message-row` Static is focusable. Arrow keys move focus row-to-row (Textual's default focus traversal handles ordering). Enter on a focused row continues to fire `RangeMarkRequested` (existing behavior via the `action_mark_row` binding). A new CSS class `is-focused` paints a subtle highlight on the focused row.

- [ ] **Step 1: Create `tests/test_app_navigation.py` with failing tests**

Create the file with this content:

```python
"""Pilot-based smoke tests for TUI navigation behavior.

Each test boots the app against the fixture chat.db, selects the first
chat, waits for messages to render, and then exercises a specific keyboard
flow. Same patching pattern as test_app_smoke.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_sample_db import build  # noqa: E402


def _patched_app_context(tmpdir_name: str):
    db_path = Path(tmpdir_name) / "chat.db"
    build(db_path)
    defaults_path = Path(tmpdir_name) / "recent.json"
    return mock.patch.multiple(
        "imessage_export.tui.app.app",
        DEFAULT_DB=db_path,
    ), mock.patch(
        "imessage_export.tui.defaults.DEFAULT_PATH", defaults_path,
    ), mock.patch(
        "imessage_export.tui.app.app.ImessageExportApp._offer_contacts_scan",
        return_value=None,
    )


async def _boot_and_select_first_chat(pilot, app):
    """Helper: post ChatSelected for the first chat and wait for history to load."""
    from imessage_export.tui.app.widgets import Sidebar
    sidebar = app.query_one(Sidebar)
    sidebar.post_message(Sidebar.ChatSelected(sidebar._all_chats[0]["chat_id"]))
    await pilot.pause()
    for _ in range(40):
        if not app.state.history_loading and app.state.selected_chat_messages:
            break
        await pilot.pause(delay=0.05)


class TestHistoryRowFocus(unittest.IsolatedAsyncioTestCase):
    async def test_message_rows_are_focusable(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                self.assertGreater(len(rows), 0)
                # Every rendered row must declare itself focusable.
                for row in rows:
                    self.assertTrue(row.can_focus, msg=f"row {row} should be focusable")

    async def test_enter_on_focused_row_marks_range(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                self.assertGreater(len(rows), 0)
                # Focus the first row, then press Enter.
                rows[0].focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                # First Enter sets range_start_msg_id.
                expected_id = getattr(rows[0], "data_msg_id", None)
                self.assertIsNotNone(expected_id)
                self.assertEqual(app.state.range_start_msg_id, expected_id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation -v 2>&1 | tail -20`
Expected: `test_message_rows_are_focusable` fails (rows have `can_focus = False` by default on Static). `test_enter_on_focused_row_marks_range` likely also fails because the row can't take focus.

- [ ] **Step 3: Make rendered message rows focusable**

In `imessage_export/tui/app/widgets.py`, inside `HistoryView.render_messages`, set `can_focus = True` on each row Static and add an `is-focused` CSS class hook. Replace the existing loop with:

```python
        last_date = None
        for m in messages:
            ts = m.timestamp  # "YYYY-MM-DD HH:MM:SS"
            day = ts[:10]
            if day != last_date:
                dt = datetime.strptime(day, "%Y-%m-%d")
                header = f"── {dt.strftime('%A, %B %-d, %Y')} ──"
                self.mount(Static(header, classes="day-header"))
                last_date = day
            row = Static(self._format_row(m), classes="message-row")
            row.can_focus = True
            row.data_msg_id = m.message_id  # type: ignore[attr-defined]
            self.mount(row)
```

Add a CSS rule for focused rows. In `HistoryView.DEFAULT_CSS`, append a new selector AFTER the existing `.message-row.is-in-range` rule:

```css
    HistoryView > .message-row:focus {
        background: $accent 50%;
    }
```

The full updated `DEFAULT_CSS` should be:

```python
    DEFAULT_CSS = """
    HistoryView {
        padding: 0 2;
    }
    HistoryView > .day-header {
        color: $accent;
        text-style: bold;
        padding: 1 0 0 0;
    }
    HistoryView > .message-row {
        padding: 0;
    }
    HistoryView > .message-row.is-selected-endpoint {
        background: $accent 30%;
    }
    HistoryView > .message-row.is-in-range {
        background: $accent 15%;
    }
    HistoryView > .message-row:focus {
        background: $accent 50%;
    }
    HistoryView > #history-placeholder {
        padding: 2 0;
    }
    """
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation -v 2>&1 | tail -20`
Expected: both tests pass.

Then run the full suite to confirm no regression:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `Ran <N> tests` then `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_app_navigation.py
git commit -m "Nav Task 2: HistoryView rows are focusable with focus highlight."
```

---

## Task 3: HistoryView gains Home/End/PageUp/PageDown jump bindings

**Files:**
- Modify: `imessage_export/tui/app/widgets.py` (HistoryView class)
- Test: `tests/test_app_navigation.py`

Goal: with focus on a message row, Home focuses the first message, End focuses the last, PageUp/PageDown jumps focus by approximately the pane's visible height.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app_navigation.py` (after `TestHistoryRowFocus`):

```python
class TestHistoryJumpBindings(unittest.IsolatedAsyncioTestCase):
    async def test_end_focuses_last_message_home_focuses_first(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                # Focus the middle row to start somewhere non-trivial.
                rows[len(rows) // 2].focus()
                await pilot.pause()

                history.action_jump_end()
                await pilot.pause()
                self.assertIs(app.focused, rows[-1])

                history.action_jump_home()
                await pilot.pause()
                self.assertIs(app.focused, rows[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestHistoryJumpBindings -v 2>&1 | tail -10`
Expected: AttributeError — `action_jump_end` / `action_jump_home` do not exist.

- [ ] **Step 3: Add the jump bindings and actions**

In `imessage_export/tui/app/widgets.py`, extend the `HistoryView.BINDINGS` list to include jump keys:

```python
    BINDINGS = [
        ("enter", "mark_row", "Mark range endpoint"),
        ("space", "mark_row", "Mark range endpoint"),
        ("escape", "clear_marks", "Clear marks"),
        ("home", "jump_home", "First message"),
        ("end", "jump_end", "Last message"),
        ("pageup", "jump_pageup", "Page up"),
        ("pagedown", "jump_pagedown", "Page down"),
    ]
```

Add four new action methods to the `HistoryView` class (place them after `action_clear_marks`):

```python
    def action_jump_home(self) -> None:
        rows = list(self.query(".message-row"))
        if rows:
            rows[0].focus()

    def action_jump_end(self) -> None:
        rows = list(self.query(".message-row"))
        if rows:
            rows[-1].focus()

    def action_jump_pageup(self) -> None:
        rows = list(self.query(".message-row"))
        if not rows:
            return
        try:
            idx = rows.index(self.app.focused)
        except ValueError:
            rows[0].focus()
            return
        step = max(1, self.size.height - 2)
        rows[max(0, idx - step)].focus()

    def action_jump_pagedown(self) -> None:
        rows = list(self.query(".message-row"))
        if not rows:
            return
        try:
            idx = rows.index(self.app.focused)
        except ValueError:
            rows[-1].focus()
            return
        step = max(1, self.size.height - 2)
        rows[min(len(rows) - 1, idx + step)].focus()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestHistoryJumpBindings -v 2>&1 | tail -10`
Expected: test passes.

Then the full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_app_navigation.py
git commit -m "Nav Task 3: HistoryView Home/End/PageUp/PageDown jump bindings."
```

---

## Task 4: HistoryView in-pane search bar

**Files:**
- Modify: `imessage_export/tui/app/widgets.py` (HistoryView class)
- Test: `tests/test_app_navigation.py`

Goal: `/` opens an `Input` pinned at the top of the history pane. Typing filters rendered rows incrementally using `filter_messages_by_query`. Esc closes the search box and restores the unfiltered view. Enter inside the search box focuses the first matching row (search bar stays open).

Implementation choice: instead of literally hiding/showing rows (which would interact awkwardly with the day-header rendering), keep the full message list cached on the widget and re-render `render_messages` with the filtered subset when the query changes. Restore on close.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app_navigation.py`:

```python
class TestHistorySearch(unittest.IsolatedAsyncioTestCase):
    async def test_slash_opens_search_input(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                # Focus a message row first so / is routed to the history.
                rows = list(history.query(".message-row"))
                rows[0].focus()
                await pilot.pause()
                await pilot.press("slash")
                await pilot.pause()
                # The search input should exist AND have focus.
                search = history.query("#history-search")
                self.assertEqual(len(search), 1)
                self.assertEqual(app.focused, search[0])

    async def test_typing_in_search_filters_rendered_rows(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                # Grab a substring guaranteed to appear in at least one message.
                full_messages = history._all_messages
                # Pick the first non-empty body and use its first 4 chars.
                token = next((m.text[:4] for m in full_messages if m.text and len(m.text) >= 4), None)
                self.assertIsNotNone(token, "fixture should have at least one >=4-char message")

                history.open_search()
                await pilot.pause()
                history.apply_search(token)
                await pilot.pause()
                rendered_rows = list(history.query(".message-row"))
                # All rendered rows correspond to messages whose text contains the token.
                # (Token comparison case-insensitive.)
                ids = {getattr(r, "data_msg_id", None) for r in rendered_rows}
                expected_ids = {m.message_id for m in full_messages if m.text and token.lower() in m.text.lower()}
                self.assertEqual(ids, expected_ids)

    async def test_esc_in_search_closes_and_restores(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                full_count = len(history.query(".message-row"))
                history.open_search()
                history.apply_search("xyzzy-no-match")
                await pilot.pause()
                self.assertEqual(len(history.query(".message-row")), 0)
                history.close_search()
                await pilot.pause()
                self.assertEqual(len(history.query("#history-search")), 0)
                self.assertEqual(len(history.query(".message-row")), full_count)
                self.assertIsNone(app.state.history_search_query)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestHistorySearch -v 2>&1 | tail -20`
Expected: all three tests fail (`open_search`, `apply_search`, `close_search` don't exist; `_all_messages` cache doesn't exist).

- [ ] **Step 3: Add search support to HistoryView**

In `imessage_export/tui/app/widgets.py`, modify `HistoryView`:

1. Update `__init__` to add the message cache:

```python
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._placeholder_visible = True
        self._all_messages: list = []
```

2. In `render_messages`, cache the full list at the top of the method:

```python
    def render_messages(self, messages: list) -> None:
        """Render `messages` (list[Message]) into the pane."""
        self._all_messages = list(messages)
        self.remove_children()
        ...
```

(Keep everything else in `render_messages` the same.)

3. Extend `BINDINGS` with the slash key:

```python
    BINDINGS = [
        ("enter", "mark_row", "Mark range endpoint"),
        ("space", "mark_row", "Mark range endpoint"),
        ("escape", "clear_marks", "Clear marks"),
        ("home", "jump_home", "First message"),
        ("end", "jump_end", "Last message"),
        ("pageup", "jump_pageup", "Page up"),
        ("pagedown", "jump_pagedown", "Page down"),
        ("slash", "open_search", "Search"),
    ]
```

4. Add the search action + the three public methods called by tests + a search-Input subclass binding. Place these after the jump actions:

```python
    def action_open_search(self) -> None:
        self.open_search()

    def open_search(self) -> None:
        """Mount the search input at the top of the pane and focus it."""
        if self.query("#history-search"):
            self.query_one("#history-search", Input).focus()
            return
        search = Input(placeholder="Search this chat… (Esc to close)", id="history-search")
        # Mount BEFORE the first existing child so the search bar sits at the top.
        self.mount(search, before=0 if self.children else None)
        search.focus()

    def apply_search(self, query: str) -> None:
        """Re-render the pane filtered to messages matching `query`."""
        from .state import filter_messages_by_query
        self.app.state.history_search_query = query or None
        # Preserve the search Input across the re-render by detaching it first.
        had_search = bool(self.query("#history-search"))
        search_value = ""
        if had_search:
            search_value = self.query_one("#history-search", Input).value
        filtered = filter_messages_by_query(self._all_messages, query)
        # render_messages clears children, so we re-mount the search input after.
        full = self._all_messages
        self.render_messages(filtered)
        self._all_messages = full  # render_messages overwrote cache; restore
        if had_search:
            search = Input(placeholder="Search this chat… (Esc to close)", id="history-search", value=search_value)
            self.mount(search, before=0 if self.children else None)
            search.focus()

    def close_search(self) -> None:
        """Remove the search input and restore the unfiltered view."""
        for s in self.query("#history-search"):
            s.remove()
        self.app.state.history_search_query = None
        cache = list(self._all_messages)
        self.render_messages(cache)
        self._all_messages = cache  # render_messages reset cache to the same list; keep explicit

    def on_input_changed(self, event) -> None:
        """As the user types in the search box, re-filter incrementally."""
        if event.input.id == "history-search":
            self.apply_search(event.value)

    def on_input_submitted(self, event) -> None:
        """Enter inside the search box focuses the first matching row."""
        if event.input.id == "history-search":
            for row in self.query(".message-row"):
                row.focus()
                return
```

5. The existing `action_clear_marks` (bound to `escape`) must NOT fire when the search Input has focus — the Input's own escape handler should take precedence. Textual's binding precedence already handles this (a focused widget's bindings win over its container's), but we belt-and-suspenders by handling Esc in the Input via Textual's standard `Input` blur behavior. To make Esc close the search bar explicitly, add an `on_key` override on `HistoryView` that intercepts `escape` when the search input has focus:

```python
    def on_key(self, event) -> None:
        focused = self.app.focused
        if event.key == "escape" and focused is not None and getattr(focused, "id", None) == "history-search":
            self.close_search()
            event.prevent_default()
            event.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestHistorySearch -v 2>&1 | tail -20`
Expected: all three search tests pass.

Then the full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_app_navigation.py
git commit -m "Nav Task 4: HistoryView /-search with incremental filter and Esc-to-close."
```

---

## Task 5: Search box clears automatically on chat switch

**Files:**
- Modify: `imessage_export/tui/app/app.py` (on_history_loaded)
- Modify: `imessage_export/tui/app/widgets.py` (HistoryView.render_messages — defensive cleanup)
- Test: `tests/test_app_navigation.py`

Goal: when the user picks a different chat, any open search box and persisted query are wiped.

- [ ] **Step 1: Write failing test**

Append to `tests/test_app_navigation.py`:

```python
class TestSearchClearsOnChatSwitch(unittest.IsolatedAsyncioTestCase):
    async def test_switching_chats_closes_search_and_clears_query(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                history.open_search()
                history.apply_search("hello")
                await pilot.pause()
                self.assertEqual(app.state.history_search_query, "hello")

                # Need a SECOND chat for the switch test. The fixture builds many
                # — pick the second by chat_id.
                sidebar = app.query_one(Sidebar)
                if len(sidebar._all_chats) < 2:
                    self.skipTest("fixture has fewer than 2 chats")
                second_chat_id = sidebar._all_chats[1]["chat_id"]
                sidebar.post_message(Sidebar.ChatSelected(second_chat_id))
                await pilot.pause()
                for _ in range(40):
                    if app.state.selected_chat_id == second_chat_id and not app.state.history_loading:
                        break
                    await pilot.pause(delay=0.05)

                self.assertEqual(len(history.query("#history-search")), 0)
                self.assertIsNone(app.state.history_search_query)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestSearchClearsOnChatSwitch -v 2>&1 | tail -10`
Expected: failure — search input persists across chat switch.

- [ ] **Step 3: Wipe search state on history load**

In `imessage_export/tui/app/app.py`, modify `on_history_loaded` to close any open search before rendering the new chat:

```python
    def on_history_loaded(self, event: HistoryLoaded) -> None:
        if event.chat_id != self.state.selected_chat_id:
            return
        self.state.selected_chat_messages = [
            {"message_id": m.message_id, "timestamp": m.timestamp} for m in event.messages
        ]
        self.state.history_loading = False
        self.state.history_search_query = None
        history = self.query_one(HistoryView)
        # Remove any leftover search input from the previous chat.
        for s in history.query("#history-search"):
            s.remove()
        history.render_messages(event.messages)
        self._refresh_status()
```

The two new lines are the `history_search_query = None` reset and the `for s in history.query("#history-search"): s.remove()` cleanup.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestSearchClearsOnChatSwitch -v 2>&1 | tail -10`
Expected: pass.

Then full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/app.py tests/test_app_navigation.py
git commit -m "Nav Task 5: Wipe history search on chat switch."
```

---

## Task 6: Sidebar — arrows always navigate the list, type-to-filter, Esc clears filter

**Files:**
- Modify: `imessage_export/tui/app/widgets.py` (Sidebar class)
- Test: `tests/test_app_navigation.py`

Goal: when the Sidebar's `ListView` has focus, typing a printable character (letter, digit, space) auto-focuses the filter `Input` and starts filtering. Esc clears the filter and returns focus to the list. Arrow keys always navigate the list, even when the filter has focus.

Implementation tactic: override `on_key` on the `Sidebar` container. If the list has focus and the key is a single printable character, focus the filter and forward the character via `Input.insert_text_at_cursor`. If the filter has focus and the key is up/down/page-up/page-down/home/end, focus the list and let the key fall through (or replay it).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app_navigation.py`:

```python
class TestSidebarTypeToFilter(unittest.IsolatedAsyncioTestCase):
    async def test_typing_letter_when_list_focused_filters(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar
            from textual.widgets import Input, ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                # Boot, but don't select a chat — we're testing sidebar behavior.
                # Wait for the sidebar to fill.
                for _ in range(20):
                    sidebar = app.query_one(Sidebar)
                    if sidebar._all_chats:
                        break
                    await pilot.pause(delay=0.05)
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                lv.focus()
                await pilot.pause()
                await pilot.press("a")
                await pilot.pause()
                filter_input = sidebar.query_one("#sidebar-filter", Input)
                self.assertEqual(app.focused, filter_input)
                self.assertEqual(filter_input.value, "a")

    async def test_esc_in_filter_clears_and_refocuses_list(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar
            from textual.widgets import Input, ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                for _ in range(20):
                    sidebar = app.query_one(Sidebar)
                    if sidebar._all_chats:
                        break
                    await pilot.pause(delay=0.05)
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                lv.focus()
                await pilot.pause()
                await pilot.press("x")
                await pilot.pause()
                filter_input = sidebar.query_one("#sidebar-filter", Input)
                self.assertEqual(filter_input.value, "x")
                await pilot.press("escape")
                await pilot.pause()
                self.assertEqual(filter_input.value, "")
                self.assertEqual(app.focused, lv)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestSidebarTypeToFilter -v 2>&1 | tail -10`
Expected: `test_typing_letter_when_list_focused_filters` fails — the keystroke goes to the ListView, doesn't appear in the filter. `test_esc_in_filter_clears_and_refocuses_list` may also fail since Esc behavior isn't wired.

- [ ] **Step 3: Override `on_key` on the Sidebar**

In `imessage_export/tui/app/widgets.py`, add an `on_key` handler to `Sidebar`. Place it after `select_chat_id`:

```python
    def on_key(self, event) -> None:
        from textual.widgets import Input, ListView
        list_view = self.query_one("#sidebar-list", ListView)
        filter_input = self.query_one("#sidebar-filter", Input)
        focused = self.app.focused

        # When the list has focus and the user types a printable single character,
        # redirect it to the filter input.
        if focused is list_view and event.character and event.character.isprintable() and len(event.character) == 1:
            filter_input.focus()
            filter_input.insert_text_at_cursor(event.character)
            event.prevent_default()
            event.stop()
            return

        # When the filter has focus and Esc is pressed, clear and refocus the list.
        if focused is filter_input and event.key == "escape":
            filter_input.value = ""
            list_view.focus()
            event.prevent_default()
            event.stop()
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestSidebarTypeToFilter -v 2>&1 | tail -10`
Expected: both pass.

Then full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py tests/test_app_navigation.py
git commit -m "Nav Task 6: Sidebar type-to-filter + Esc-clears-filter."
```

---

## Task 7: Auto-focus HistoryView after a chat loads

**Files:**
- Modify: `imessage_export/tui/app/app.py` (on_history_loaded)
- Test: `tests/test_app_navigation.py`

Goal: once the history finishes rendering, the App moves focus to the first message row so the user can immediately arrow-navigate or press `/`. If there are no messages, focus stays on the sidebar.

- [ ] **Step 1: Write failing test**

Append to `tests/test_app_navigation.py`:

```python
class TestAutoFocusHistoryAfterChatSelect(unittest.IsolatedAsyncioTestCase):
    async def test_focus_moves_to_first_message_after_load(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                self.assertGreater(len(rows), 0)
                # After load + render, focus should be on the first message row.
                self.assertIs(app.focused, rows[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestAutoFocusHistoryAfterChatSelect -v 2>&1 | tail -10`
Expected: failure — `app.focused` is probably the sidebar list or None.

- [ ] **Step 3: Move focus after render**

In `imessage_export/tui/app/app.py`, extend `on_history_loaded` to focus the first message row when there is one. Modify the method (full updated version below — note this builds on Task 5's changes):

```python
    def on_history_loaded(self, event: HistoryLoaded) -> None:
        if event.chat_id != self.state.selected_chat_id:
            return
        self.state.selected_chat_messages = [
            {"message_id": m.message_id, "timestamp": m.timestamp} for m in event.messages
        ]
        self.state.history_loading = False
        self.state.history_search_query = None
        history = self.query_one(HistoryView)
        for s in history.query("#history-search"):
            s.remove()
        history.render_messages(event.messages)
        self._refresh_status()
        # Move focus to the first message row so the user can arrow / press /.
        rows = list(history.query(".message-row"))
        if rows:
            rows[0].focus()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestAutoFocusHistoryAfterChatSelect -v 2>&1 | tail -10`
Expected: pass.

Then full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/app.py tests/test_app_navigation.py
git commit -m "Nav Task 7: Auto-focus first message row after chat loads."
```

---

## Task 8: Active-region border + Tab cycle setup

**Files:**
- Modify: `imessage_export/tui/app/app.py` (CSS, on_descendant_focus handler)
- Modify: `imessage_export/tui/app/widgets.py` (Sidebar / HistoryView / ActionBar — add region-id class)
- Test: `tests/test_app_navigation.py`

Goal: the region currently owning focus gets a `region-active` CSS class. CSS paints its border with `$accent`. Tab / Shift+Tab cycles between the three regions in order: Sidebar → History → Action bar.

Tab cycling is already Textual's default for focusable widgets. We just need to verify the focus traversal lands in the right widget per region. To make Tab pick the right next region cleanly, we set the focus order via the widget tree (composition order) — which already matches Sidebar → History → ActionBar.

- [ ] **Step 1: Write failing test**

Append to `tests/test_app_navigation.py`:

```python
class TestActiveRegionBorder(unittest.IsolatedAsyncioTestCase):
    async def test_region_active_class_follows_focus(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar, ActionBar

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                sidebar = app.query_one(Sidebar)
                history = app.query_one(HistoryView)
                action_bar = app.query_one(ActionBar)

                # Right after chat load, history is the active region (Task 7).
                self.assertTrue(history.has_class("region-active"))
                self.assertFalse(sidebar.has_class("region-active"))
                self.assertFalse(action_bar.has_class("region-active"))

                # Focus the sidebar list — sidebar becomes active.
                sidebar.query_one("#sidebar-list").focus()
                await pilot.pause()
                self.assertTrue(sidebar.has_class("region-active"))
                self.assertFalse(history.has_class("region-active"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestActiveRegionBorder -v 2>&1 | tail -10`
Expected: failure — no `region-active` class anywhere.

- [ ] **Step 3: Add the region-active class and focus tracking**

In `imessage_export/tui/app/app.py`, extend the App's CSS and add an `on_descendant_focus` handler.

1. Update the `CSS` class attribute:

```python
    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        height: 1fr;
    }
    Sidebar.region-active {
        border-right: thick $accent;
    }
    HistoryView.region-active {
        border-left: thick $accent;
    }
    ActionBar.region-active {
        border-top: thick $accent;
    }
    """
```

2. Add an `on_descendant_focus` handler (place after `_persist_defaults`):

```python
    def on_descendant_focus(self, event) -> None:
        """Whenever focus changes, mark exactly one region as active."""
        from .widgets import Sidebar, HistoryView, ActionBar
        # Walk up from the focused widget to find which region owns it.
        active_region = None
        w = event.widget
        while w is not None:
            if isinstance(w, (Sidebar, HistoryView, ActionBar)):
                active_region = w
                break
            w = w.parent
        for region_cls in (Sidebar, HistoryView, ActionBar):
            try:
                region = self.query_one(region_cls)
            except Exception:
                continue
            region.set_class(region is active_region, "region-active")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestActiveRegionBorder -v 2>&1 | tail -10`
Expected: pass.

Full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/app.py tests/test_app_navigation.py
git commit -m "Nav Task 8: Active-region border via region-active CSS class."
```

---

## Task 9: StatusLine focus chip

**Files:**
- Modify: `imessage_export/tui/app/widgets.py` (StatusLine class)
- Modify: `imessage_export/tui/app/app.py` (pass active region tag to StatusLine)
- Test: `tests/test_app_navigation.py`

Goal: the leftmost portion of the status line shows `[sidebar]`, `[history]`, `[actions]`, or `[modal]`, dim-styled. The rest of the line stays as today.

- [ ] **Step 1: Write failing test**

Append to `tests/test_app_navigation.py`:

```python
class TestStatusLineFocusChip(unittest.IsolatedAsyncioTestCase):
    async def test_chip_reflects_focused_region(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import HistoryView, Sidebar, StatusLine

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                status = app.query_one(StatusLine)
                # After chat load, focus is on history (Task 7).
                rendered = str(status.renderable)
                self.assertIn("[history]", rendered)

                sidebar = app.query_one(Sidebar)
                sidebar.query_one("#sidebar-list").focus()
                await pilot.pause()
                rendered = str(status.renderable)
                self.assertIn("[sidebar]", rendered)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestStatusLineFocusChip -v 2>&1 | tail -10`
Expected: failure — no chip in the status line text.

- [ ] **Step 3: Add `focus_region` to StatusLine and update it on focus change**

In `imessage_export/tui/app/widgets.py`, modify `StatusLine`:

```python
class StatusLine(Static):
    """One-line summary of resolved state."""

    DEFAULT_CSS = """
    StatusLine {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._focus_region: str = "sidebar"

    def set_focus_region(self, region: str) -> None:
        """Set the focus chip tag and refresh the rendered line."""
        self._focus_region = region
        # Re-render using the current AppState if the app is mounted.
        try:
            self.update_from_state(self.app.state)  # type: ignore[attr-defined]
        except Exception:
            pass

    def update_from_state(self, state) -> None:
        chip = f"[{self._focus_region}]"
        if state.last_export_status:
            self.update(f"{chip}  {state.last_export_status}")
            return
        from .state import resolved_window, _format_window
        w = resolved_window(state)
        window_str = _format_window(w)
        source = {
            "selection": "from selection",
            "typed":     "from Window modal",
            "all":       "everything",
        }[state.window_source]
        contacts_str = f"contacts: {state.contacts_path.name}" if state.contacts_path else "contacts: none"
        redact_str = "redact: on" if state.redact else "redact: off"
        self.update(
            f"{chip}  window: {window_str} ({source}) · output: {state.output_dir} · {contacts_str} · {redact_str}"
        )
```

In `imessage_export/tui/app/app.py`, extend `on_descendant_focus` (added in Task 8) to also tell the status line which region is active. Append to the bottom of that method:

```python
        # Update the status chip too.
        from .widgets import StatusLine
        tag = "sidebar"
        if active_region is not None:
            from .widgets import HistoryView, ActionBar
            if isinstance(active_region, HistoryView):
                tag = "history"
            elif isinstance(active_region, ActionBar):
                tag = "actions"
            else:
                tag = "sidebar"
        try:
            self.query_one(StatusLine).set_focus_region(tag)
        except Exception:
            pass
```

Also handle the `[modal]` tag: in `App.push_screen` we don't override directly, but a quick approach is to set `set_focus_region("modal")` in each modal's `on_mount` — easier: in `app.py`, override `on_screen_resume` and `on_screen_suspend`. But to keep this small and YAGNI, we'll only handle the three main regions in this task. `[modal]` can be a follow-up if needed — the active region just keeps its previous tag while a modal is up.

(If the user later asks for the `[modal]` tag, it's one block added to `on_screen_suspend` / `on_screen_resume`. Skipping for now.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestStatusLineFocusChip -v 2>&1 | tail -10`
Expected: pass.

Full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/widgets.py imessage_export/tui/app/app.py tests/test_app_navigation.py
git commit -m "Nav Task 9: StatusLine focus chip showing active region."
```

---

## Task 10: HelpModal — refreshed text content

**Files:**
- Modify: `imessage_export/tui/app/modals.py` (HelpModal class)
- Test: `tests/test_app_navigation.py`

Goal: replace the existing HelpModal body text with one that documents the new bindings: Tab cycle, arrow navigation per region, Esc behavior table, `/` for search, and the existing letter accelerators.

- [ ] **Step 1: Write failing test**

Append to `tests/test_app_navigation.py`:

```python
class TestHelpModalText(unittest.IsolatedAsyncioTestCase):
    async def test_help_modal_documents_new_bindings(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.modals import HelpModal
            from textual.widgets import Static

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                await _boot_and_select_first_chat(pilot, app)
                # Open the help modal and inspect its body Static text.
                app.push_screen(HelpModal())
                await pilot.pause()
                modal = app.screen
                self.assertIsInstance(modal, HelpModal)
                body_text = "\n".join(
                    str(s.renderable) for s in modal.query(Static)
                )
                # New navigation lines must appear.
                self.assertIn("Tab", body_text)
                self.assertIn("Sidebar", body_text)
                self.assertIn("History", body_text)
                self.assertIn("/", body_text)
                # Existing accelerator legend must still be there.
                self.assertIn("Export", body_text)
                self.assertIn("Redact", body_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestHelpModalText -v 2>&1 | tail -10`
Expected: failure — the current text contains "Arrow keys: navigate", which is too thin and lacks region-specific guidance.

- [ ] **Step 3: Rewrite HelpModal body**

In `imessage_export/tui/app/modals.py`, replace the `Static(...)` body inside `HelpModal.compose` with this longer block:

```python
            yield Static(
                "Navigation\n"
                "  Tab / Shift+Tab       Cycle focus: Sidebar → History → Actions\n"
                "  ↑ ↓                    Move within the focused region\n"
                "  Home / End            Jump to top / bottom\n"
                "  PageUp / PageDown     Page within the focused region\n"
                "  Esc                   Context-aware: clear filter / clear marks / close search\n"
                "\n"
                "Sidebar\n"
                "  (type letters)        Filter the chat list\n"
                "  Enter                 Open the highlighted chat\n"
                "\n"
                "History\n"
                "  Enter                 Mark range endpoint (1st = start, 2nd = end)\n"
                "  /                     Search within this chat\n"
                "  Esc                   Clear search / clear marks\n"
                "\n"
                "Actions (work globally except while typing in an input)\n"
                "  W  Window…   S  Settings…   R  Redact…   E  Export\n"
                "  Z  Wizard    H/?  Help       Q  Quit\n"
            )
```

(Replace the existing `yield Static(...)` call entirely with the new one.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestHelpModalText -v 2>&1 | tail -10`
Expected: pass.

Full suite:
Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add imessage_export/tui/app/modals.py tests/test_app_navigation.py
git commit -m "Nav Task 10: HelpModal text refresh documenting new bindings."
```

---

## Task 11: End-to-end keyboard-only happy path smoke test

**Files:**
- Test: `tests/test_app_navigation.py`

Goal: prove the whole user story works keyboard-only — open app, narrow chat list by typing, Enter to load, arrow-and-Enter to mark two endpoints, confirm range state set, press Esc to clear.

- [ ] **Step 1: Write the test**

Append to `tests/test_app_navigation.py`:

```python
class TestKeyboardOnlyHappyPath(unittest.IsolatedAsyncioTestCase):
    async def test_full_keyboard_flow_to_range_set_and_cleared(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        ctx_db, ctx_def, ctx_scan = _patched_app_context(tmpdir.name)
        with ctx_db, ctx_def, ctx_scan:
            from imessage_export.tui.app.app import ImessageExportApp
            from imessage_export.tui.app.widgets import Sidebar, HistoryView
            from textual.widgets import ListView

            app = ImessageExportApp()
            async with app.run_test() as pilot:
                # Wait until sidebar has chats.
                for _ in range(20):
                    sidebar = app.query_one(Sidebar)
                    if sidebar._all_chats:
                        break
                    await pilot.pause(delay=0.05)
                sidebar = app.query_one(Sidebar)
                lv = sidebar.query_one(ListView)
                lv.focus()
                await pilot.pause()

                # Pick the first chat with Enter (no filtering needed for fixture).
                first_chat_id = sidebar._all_chats[0]["chat_id"]
                sidebar.post_message(Sidebar.ChatSelected(first_chat_id))
                await pilot.pause()
                for _ in range(40):
                    if not app.state.history_loading and app.state.selected_chat_messages:
                        break
                    await pilot.pause(delay=0.05)

                # Focus auto-moved to first message row (Task 7).
                history = app.query_one(HistoryView)
                rows = list(history.query(".message-row"))
                self.assertGreater(len(rows), 1)
                self.assertIs(app.focused, rows[0])

                # Enter marks the first row, then End + Enter marks the last.
                await pilot.press("enter")
                await pilot.pause()
                history.action_jump_end()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                # State should reflect a complete range.
                self.assertIsNotNone(app.state.range_start_msg_id)
                self.assertIsNotNone(app.state.range_end_msg_id)
                self.assertEqual(app.state.window_source, "selection")

                # Esc clears marks.
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsNone(app.state.range_start_msg_id)
                self.assertIsNone(app.state.range_end_msg_id)
```

- [ ] **Step 2: Run the test**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest tests.test_app_navigation.TestKeyboardOnlyHappyPath -v 2>&1 | tail -10`
Expected: pass on first run — every behavior the test exercises was implemented in Tasks 1–7.

If it doesn't pass:
- Inspect which assertion failed.
- The most likely culprit is `app.focused` not being `rows[0]` after chat load (Task 7 issue). If so, add a short polling loop:

  ```python
  for _ in range(10):
      if app.focused is rows[0]:
          break
      await pilot.pause(delay=0.05)
  ```

  Pilot.pause doesn't always wait for `call_later` callbacks; a brief poll absorbs that.

- [ ] **Step 3: Full suite passes**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app_navigation.py
git commit -m "Nav Task 11: End-to-end keyboard-only happy-path smoke test."
```

---

## Task 12: Push the branch + open a PR

**Files:** none

- [ ] **Step 1: Verify everything still passes**

Run: `/Users/benjaminloschen/.local/pipx/venvs/imessage-history/bin/python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK`.

Also confirm the branch state:
```bash
git log --oneline -15
git status --short
```
Expected: 12 navigation commits (Tasks 1 through 11 plus the initial spec and gitignore commits), clean working tree.

- [ ] **Step 2: Rebase onto current main**

```bash
git fetch origin main
git rebase origin/main
```

If conflicts arise, resolve them (most likely just a documentation file conflict), `git add` the resolved files, `git rebase --continue`.

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin tui-navigation-spec
gh pr create --title "TUI navigation: conventional bindings + focusable rows + /-search" --body "$(cat <<'EOF'
## Summary
- Tab cycles Sidebar → History → Actions with a visible active border
- Message rows become keyboard-focusable; arrows + Enter work without a mouse
- Sidebar gets fzf-style type-to-filter (arrows always go to list, typing focuses filter, Esc clears)
- `/` opens an in-history search bar (incremental filter; Esc closes; Enter focuses first match)
- StatusLine gains a `[region]` focus chip
- HelpModal text refreshed for the new bindings

Spec: `docs/superpowers/specs/2026-06-07-tui-navigation-design.md`

## Test plan
- [ ] `python3 -m unittest discover -s tests` passes (193+11 new ≈ 204 tests)
- [ ] Manual smoke: open `imessage-export`, navigate without touching the mouse
- [ ] Manual smoke: open `imessage-export`, use only the mouse — confirm no regression

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Done**

Report the PR URL back to the user.

---

## Self-Review

**Spec coverage:**

- §"Focus Model — Tab cycle" → Task 8
- §"Focus Model — Active-region border" → Task 8
- §"Focus Model — Esc behavior table" → Tasks 2 (history mark-clear retained), 4 (Esc closes search), 6 (Esc clears filter); modal dismiss already exists pre-spec
- §"Focus Model — Single-letter accelerators" → unchanged, no task needed
- §"Focus Model — Auto-focus after chat selection" → Task 7
- §"Per-Region — Sidebar — Tab stop" → Task 8 (via region grouping)
- §"Per-Region — Sidebar — arrows always on list" → Task 6
- §"Per-Region — Sidebar — type-to-filter" → Task 6
- §"Per-Region — Sidebar — Esc clears filter" → Task 6
- §"Per-Region — Sidebar — Enter loads + focus to history" → Task 7
- §"Per-Region — History — focusable rows" → Task 2
- §"Per-Region — History — Enter marks" → existing behavior, exercised by Task 2 test
- §"Per-Region — History — Esc clears marks" → existing behavior, retained
- §"Per-Region — History — Home/End/PageUp/PageDown" → Task 3
- §"Per-Region — History — `/` opens search" → Task 4
- §"Per-Region — History — Esc closes search" → Task 4
- §"Per-Region — History — Enter focuses first match" → Task 4
- §"Per-Region — History — Search clears on chat switch" → Task 5
- §"Per-Region — Action bar — Tab focuses first button" → Task 8 (region focus + composition order already places Window first; left/right between buttons is Textual default for `Button`s)
- §"Per-Region — Action bar — letter accelerators" → unchanged
- §"Per-Region — Status line — focus chip" → Task 9
- §"Help Modal — refreshed text" → Task 10
- §"State Changes — history_search_query" → Task 1
- §"Acceptance Criteria — end-to-end keyboard flow" → Task 11

All spec requirements have implementing tasks. The `[modal]` chip tag is intentionally deferred — noted in Task 9.

**Placeholder scan:**

No "TBD" / "TODO" / "implement later" / "add appropriate error handling" / "similar to Task N" in the plan. All steps include complete code blocks for code changes, exact commands, and explicit expected output.

**Type consistency:**

- `history_search_query` — same identifier in `AppState` (Task 1) and `on_history_loaded` reset (Task 5) and StatusLine reads (Task 9).
- `filter_messages_by_query` — same identifier in `state.py` (Task 1) and `widgets.py` (Task 4 — imported relatively).
- `open_search` / `apply_search` / `close_search` — same method names across Task 4 test and Task 4 implementation, plus Task 5 implicitly relies on `close_search` semantics.
- `_all_messages` cache — same attribute name across Task 4 implementation and Task 4 / Task 5 tests.
- `region-active` CSS class — same name in Task 8 widget tags + test.
- `set_focus_region` — same method name on `StatusLine` across Task 9 implementation and `on_descendant_focus` extension.

All consistent.
