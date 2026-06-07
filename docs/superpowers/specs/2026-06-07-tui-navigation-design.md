# TUI Navigation Improvements — Design

**Date:** 2026-06-07
**Author:** Brainstormed with Claude Code
**Scope:** Phase 2 Textual app (`imessage_export/tui/app/`)
**Out of scope:** Wizard (`imessage_export/tui/wizard.py`), core exporter, CLI

## Goal

Replace the current ad-hoc keyboard model in the Textual app with a discoverable,
conventional navigation scheme: arrow keys + Tab + Enter + Esc + mouse, with
power-user single-letter accelerators kept as an additive layer.

The pains this addresses (all confirmed by the user):

1. Message rows in the History pane aren't keyboard-focusable, so Enter-to-mark only works via mouse click.
2. Tab order between the Sidebar, History, and Action bar regions is undefined — focus jumps unpredictably and there's no visual indicator of where keystrokes will land.
3. The sidebar's filter `Input` is always focused, stealing arrow keys that the user expects to navigate the chat list.
4. There's no way to jump to the top/bottom of either pane, no page-up/page-down awareness, and no way to search within an open chat.

## Non-Goals

- No vim-style modal bindings (`v` for visual mode, `:command` footer, `j/k/gg/G`). The user explicitly redirected away from these in the Phase 2 brainstorm; see `feedback_tui_keybinds`.
- No region-jump accelerators (e.g., `Ctrl+1/2/3`) in this round. Tab is sufficient; direct jumps can be added later if Tab proves slow in practice.
- No persistent in-history search across chat switches. Search is per-chat and clears when a new chat loads.
- No new modals or actions. This spec is about navigation only — the existing W/S/R/E/Z/H/Q action bar, range-mark logic, and modal flow all stay.

## Architecture

The Textual app already has three composed regions (`Sidebar`, `HistoryView`,
`ActionBar`) plus a one-line `StatusLine`. This spec adds:

1. A **focus cycle** across those three regions using Textual's native `Tab` / `Shift+Tab` traversal, with a colored border on the active region.
2. **Keyboard-focusable message rows** in `HistoryView` so arrows, Enter, and Esc work without a mouse.
3. **fzf-style filter behavior** in the Sidebar: filter `Input` is a visual sub-element, not a Tab stop; arrows always navigate the list; typing auto-focuses the filter.
4. A **per-chat search box** in `HistoryView` opened by `/` and closed by `Esc`.
5. **Standard jump bindings** (`Home`, `End`, `PageUp`, `PageDown`) on the focusable widgets.
6. A **focus chip** at the left of the `StatusLine` showing which region is currently focused.

No new files. All changes live inside `imessage_export/tui/app/widgets.py`,
`imessage_export/tui/app/app.py`, and tests under
`tests/test_app_*.py`.

## Focus Model

### Tab cycle

Three focus stops form a horizontal cycle: **Sidebar list → History → Action bar**. `Tab` moves forward, `Shift+Tab` moves back. The cycle wraps.

Implementation: each region declares `can_focus = True` on its primary focusable
child (Sidebar's `ListView`, HistoryView itself, ActionBar's first `Button`).
Textual's default focus traversal honors widget order.

### Active-region border

The currently-focused region gets a 1-character colored border (Textual's
`border: solid $accent` style toggled via a CSS class set on focus/blur). The
Sidebar already has a right-border to separate it visually; the active style
brightens it.

### Esc behavior table

`Esc` is region-aware. It never quits the app — `Q` (or `Ctrl+C`) handles that.

| Focused region | Esc behavior |
|---|---|
| Sidebar with filter text present | Clears filter; focus stays on the sidebar list |
| Sidebar with no filter text | No-op |
| History with range marks set | Clears marks (existing `RangeMarkRequested(-1)` sentinel) |
| History with search box open | Closes search box, restores full message view |
| History idle (no marks, no search) | No-op |
| Modal open | Dismisses modal (existing behavior) |

### Single-letter accelerators

The existing global accelerators (`W` Window, `S` Settings, `R` Redact,
`E` Export, `Z` Wizard, `H` Help, `Q` Quit) keep firing globally — except
when an `Input` widget has focus. The existing suppression in
`ImessageExportApp.on_key` already handles this and stays unchanged.

### Auto-focus after chat selection

When the user selects a chat in the sidebar (`ListView.Selected` →
`Sidebar.ChatSelected`), focus automatically moves to the `HistoryView` once
the history has rendered. The user can immediately arrow through messages or
press `/` to search without an extra Tab press.

If the chat load fails or the rendered messages list is empty, focus stays on
the sidebar.

## Per-Region Behavior

### Sidebar

- **Layout:** unchanged — filter `Input` on top, `ListView` of chats below.
- **Tab stop:** only the `ListView` is a focus stop. The filter `Input` is reachable but not via Tab.
- **Arrows:** `↑` / `↓` always navigate the chat list, even while filter text is present.
- **PageUp / PageDown:** page through the chat list.
- **Home / End:** jump to first / last visible chat (`ListView.action_first` / `action_last`).
- **Type-to-filter:** when the list has focus and the user types a printable character (letter, digit, space), focus moves to the filter `Input` and the keystroke is forwarded so it appears in the filter. The list narrows incrementally as the user types. Implementation: override `on_key` on the sidebar; if the event character is printable and the list has focus, `query_one(Input).focus()` then re-post the keystroke (or call `Input.insert_text_at_cursor`).
- **Esc:** clears filter text, returns focus to the list, and refreshes the list to its unfiltered state.
- **Enter on a highlighted chat:** loads the chat (existing `ChatSelected` flow) and moves focus to the `HistoryView`.
- **Mouse click on a chat row:** loads the chat AND moves focus to the `HistoryView` (matches Enter behavior).
- **Backspace in filter:** standard `Input` behavior. If the filter becomes empty after backspace, focus moves back to the list.

### History

- **Message rows become focusable.** Each `.message-row` Static gets `can_focus = True` and a focused-row CSS class for a subtle highlight (in addition to the existing range-mark classes).
- **Arrows (↑ / ↓):** move the focused row up / down one message. The pane auto-scrolls to keep the focused row visible (`scroll_to_widget`).
- **PageUp / PageDown:** move the focused row up / down by approximately the pane's visible height.
- **Home / End:** focus the first / last message row.
- **Enter on focused row:** marks it as a range endpoint. The existing two-Enter-into-range logic stays:
  - 1st Enter: sets `range_start_msg_id`
  - 2nd Enter (on a different row): sets `range_end_msg_id`
  - 3rd Enter (on any row): resets — that row becomes the new start
- **Esc:** clears any range marks. If the search box is open, the search box's own `Esc` handler runs first (closes search), so a second Esc clears marks.
- **`/` opens search:** a one-line `Input` widget pinned at the top of `HistoryView`. Typing filters the visible message rows to those whose text body contains the query (case-insensitive substring). Day headers stay visible if any of their day's messages still match; otherwise they hide too.
- **Search behavior:**
  - The currently-focused row stays focused if it still matches. Otherwise focus moves to the first match.
  - Range marks set on hidden (filtered-out) rows are preserved in state — `apply_marks` uses message_ids, not visible rows — but their visual highlight only shows when those rows return to view.
  - Pressing Esc inside the search input closes the search bar and restores the full view.
  - Pressing Enter inside the search input moves focus to the first matching message row. The search bar stays open until Esc.
  - The search box clears automatically when the chat changes (loaded into `on_history_loaded`).
- **Mouse click on a row:** continues to mark the row (no change). Click also focuses the row.

### Action bar

- **Tab stop:** the first `Button` (Window) is focused when Tab enters the action bar.
- **← / →:** move focus between buttons (Textual's default `Button` traversal).
- **Enter / Space:** activates the focused button.
- **Letter accelerators:** unchanged — W/S/R/E/Z/H/Q fire globally except when an `Input` has focus.

### Status line

- Adds a `[region]` chip on the far left, dim-styled:
  - `[sidebar]` when sidebar list or filter has focus
  - `[history]` when HistoryView, a message row, or the in-pane search box has focus
  - `[actions]` when any action-bar Button has focus
  - `[modal]` when a modal screen is on top
- Implementation: hook the app's `on_descendant_focus` (Textual emits a focus event we can subscribe to via `on_focus` on the App), look up which region owns the focused widget, and call `StatusLine.update_from_state` with the new tag.
- The rest of the status line (`window: … · output: … · contacts: … · redact: …`) is unchanged.

## Help Modal

The existing `HelpModal` content is rewritten to document the new bindings.
Suggested layout:

```
Navigation
  Tab / Shift+Tab          Cycle focus: Sidebar → History → Actions
  ↑ ↓                       Move within the focused region
  Home / End               Jump to top / bottom
  PageUp / PageDown        Page within the focused region
  Esc                      Context-aware: clear filter, clear marks, close search

Sidebar
  (type letters)           Filter the chat list
  Enter                    Open the highlighted chat

History
  Enter                    Mark range endpoint (1st = start, 2nd = end)
  /                        Search within this chat
  Esc                      Clear search / clear marks

Actions
  W S R E Z H Q            Window / Settings / Redact / Export / Wizard / Help / Quit
                           (work globally except while typing in an input)
```

No new help system — same modal, refreshed text only.

## State Changes

The shared `AppState` dataclass needs one new optional field:

```python
@dataclass
class AppState:
    ...
    history_search_query: Optional[str] = None  # active in-history search text, or None when search bar closed
```

Range mark state, window state, contacts, etc. all stay as-is. The
`history_search_query` is *not* persisted to defaults — it resets per chat
and per session.

## Testing

The existing test layout (`tests/test_app_state.py`, `tests/test_app_workers.py`,
`tests/test_app_smoke.py`) extends naturally:

- **Unit tests** for any new pure functions (e.g., a `filter_messages(messages, query) -> list` helper if extracted).
- **Pilot smoke tests** for the navigation flows:
  - Tab cycle visits sidebar → history → actions and wraps
  - Type-to-filter focuses sidebar input and narrows the list
  - `/` opens the history search bar; Esc closes it
  - Enter on a focused message row sets `range_start_msg_id`
  - Esc on history with marks clears them; Esc on history with search closes search first
  - Selecting a chat moves focus into the history pane
- **No new threading or worker tests** — the navigation changes don't touch the export worker, contacts scan worker, or DB layer.

## Risks & Open Questions

- **Focusable Static rows in Textual.** Textual `Static` widgets are focusable when `can_focus=True`, but they don't render a default focus indicator. We'll add a CSS class. Need to verify on Textual 0.89.1 (the pinned version) that focus events fire on `Static` and that `scroll_to_widget` honors them.
- **Type-to-filter re-posting keystrokes.** Forwarding the first typed key from the list to the filter input is the tricky bit — `Input.insert_text_at_cursor` is the cleanest API in 0.89.1. If it doesn't work, fallback is to consume the key, focus the input, and have the user retype (still good UX because they're already typing fast).
- **Esc precedence between search and marks.** Both are bound to Esc on the history region. Resolution: the search Input has higher precedence because it owns focus when open. With search closed, Esc on the history widget itself runs the clear-marks handler.

## Acceptance Criteria

1. From the keyboard alone (no mouse), I can: open the app, navigate the chat list with arrows, narrow it by typing, Enter to open a chat, focus auto-moves to history, arrow through messages, press Enter twice to mark a range, press E to export, and Esc to clear marks afterward.
2. The active region is always visually obvious via border + status chip.
3. `/` in the history opens a search bar that filters messages incrementally; Esc closes it.
4. Tab and Shift-Tab cycle predictably and never land in a dead zone.
5. The existing W/S/R/E/Z/H/Q accelerators still fire from anywhere except while typing in an input.
6. All existing tests still pass; the new Pilot tests for the flows above pass.
