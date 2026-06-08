"""AppState dataclass + window-resolution helpers.

Pure logic. No Textual or Rich imports — these helpers must be unit-testable
without any UI dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional


WindowSource = Literal["selection", "typed", "all"]


@dataclass
class AppState:
    # data loaded from DB / defaults
    chats: list[dict] = field(default_factory=list)
    contacts: dict = field(default_factory=dict)

    # selection
    selected_chat_id: Optional[int] = None
    selected_chat_messages: list = field(default_factory=list)

    # range
    range_start_msg_id: Optional[int] = None
    range_end_msg_id: Optional[int] = None
    typed_window: Optional[dict] = None
    window_source: WindowSource = "all"

    # settings (mirror defaults.json plus redaction)
    contacts_path: Optional[Path] = None
    output_dir: Path = field(default_factory=lambda: Path.cwd() / "exports")
    me_name: str = "Me"
    redact: dict = field(default_factory=dict)

    # ephemeral
    last_export_status: Optional[str] = None
    history_loading: bool = False


def resolved_window(state: AppState) -> dict:
    """Return the window dict to apply for an export.

    Precedence is driven by `state.window_source`:
      - "selection": derive a {mode: "range", from_date, to_date, start_time,
        end_time} from the two marked messages.
      - "typed":     return `state.typed_window` unchanged.
      - "all":       no time filter.
    """
    if state.window_source == "selection" and state.range_start_msg_id and state.range_end_msg_id:
        return _bracket_to_window(state)
    if state.window_source == "typed" and state.typed_window:
        return state.typed_window
    return {"mode": "all"}


def _bracket_to_window(state: AppState) -> dict:
    """Convert two marked message ids into a range window."""
    msg_by_id: dict[int, Any] = {m["message_id"]: m for m in state.selected_chat_messages}
    a = msg_by_id.get(state.range_start_msg_id)
    b = msg_by_id.get(state.range_end_msg_id)
    if not a or not b:
        return {"mode": "all"}
    # Swap so earlier is start.
    if a["timestamp"] > b["timestamp"]:
        a, b = b, a
    return {
        "mode": "range",
        "from_date": a["timestamp"][:10],
        "to_date":   b["timestamp"][:10],
        "start_time": a["timestamp"][11:16],
        "end_time":   b["timestamp"][11:16],
    }


def _format_window(window: dict) -> str:
    """Return a human-readable one-line description of a window dict.

    Examples:
      {"mode": "all"}                                   → "everything"
      {"mode": "day", "date": "2026-06-06"}             → "2026-06-06"
      {"mode": "day", "date": "…", "start_time": "09:00", "end_time": "17:00"} → "2026-06-06 09:00–17:00"
      {"mode": "range", "from_date": "2026-06-01", "to_date": "2026-06-06"}    → "2026-06-01..2026-06-06"
      (range + times)                                   → "2026-06-01..2026-06-06 09:00–17:00"
    """
    mode = window.get("mode", "all")
    if mode == "all":
        return "everything"
    if mode == "day":
        parts = [window["date"]]
        st = window.get("start_time")
        et = window.get("end_time")
        if st or et:
            parts.append(f"{st or '00:00'}–{et or '23:59'}")
        return " ".join(parts)
    if mode == "range":
        base = f"{window['from_date']}..{window['to_date']}"
        st = window.get("start_time")
        et = window.get("end_time")
        if st or et:
            base += f" {st or '00:00'}–{et or '23:59'}"
        return base
    return repr(window)


def filter_by_window(messages, window: dict) -> list:
    """Return the subset of `messages` that falls inside `window`.

    `messages` may be either Message dataclasses or the smaller
    `{"message_id", "timestamp"}` dicts that live on
    `state.selected_chat_messages` — both expose `.timestamp` /
    `["timestamp"]` in the same `YYYY-MM-DD HH:MM:SS` string shape, so
    the comparison is a stable lexical string compare.

    Mode handling:
      - `all`   → return the list unchanged.
      - `day`   → keep messages whose date prefix matches.
      - `range` → keep messages between from_date and to_date inclusive.
    `start_time` / `end_time` further restrict on `HH:MM` regardless of
    mode (applied after the date filter).

    Pure function — no UI, used by both the inline WindowStrip live
    filter and the test suite.
    """
    def _ts(m):
        return m.timestamp if hasattr(m, "timestamp") else m["timestamp"]

    mode = window.get("mode", "all")
    if mode == "all":
        return list(messages)
    result = list(messages)
    if mode == "day":
        d = window.get("date")
        if d:
            result = [m for m in result if _ts(m)[:10] == d]
    elif mode == "range":
        fd = window.get("from_date") or ""
        td = window.get("to_date") or "9999-12-31"
        result = [m for m in result if fd <= _ts(m)[:10] <= td]
    st = window.get("start_time")
    et = window.get("end_time")
    if st:
        result = [m for m in result if _ts(m)[11:16] >= st]
    if et:
        result = [m for m in result if _ts(m)[11:16] <= et]
    return result


def apply_click_mark(state: AppState, msg_id: int) -> bool:
    """Date-picker style range-mark click handler. Returns True iff the
    state was mutated.

    Click semantics:
      1. No marks yet           → set start.
      2. Only start set
         - click on start       → no-op.
         - click elsewhere      → set end.
      3. Both marks set
         - click on an existing endpoint → no-op.
         - click elsewhere               → move the endpoint that is
           nearer (by index in `selected_chat_messages`) to the click.
           This extends the range when the click lands outside the
           current span and shrinks it when it lands inside. After
           the move, start is normalized to precede end.

    Esc (handled by the caller, not here) is the explicit "clear and
    start over" path.
    """
    prev = (state.range_start_msg_id, state.range_end_msg_id)
    if state.range_start_msg_id is None:
        state.range_start_msg_id = msg_id
    elif state.range_end_msg_id is None:
        if msg_id == state.range_start_msg_id:
            return False
        state.range_end_msg_id = msg_id
    else:
        if msg_id == state.range_start_msg_id or msg_id == state.range_end_msg_id:
            return False
        ids_in_order = [m["message_id"] for m in state.selected_chat_messages]
        try:
            start_idx = ids_in_order.index(state.range_start_msg_id)
            end_idx = ids_in_order.index(state.range_end_msg_id)
            new_idx = ids_in_order.index(msg_id)
        except ValueError:
            # Unknown message id — defensive no-op rather than a
            # half-mutated state.
            return False
        # Move whichever endpoint sits closer to the click (ties go to
        # the start endpoint — arbitrary but predictable).
        if abs(new_idx - start_idx) <= abs(new_idx - end_idx):
            state.range_start_msg_id = msg_id
        else:
            state.range_end_msg_id = msg_id
        # Normalize so start always precedes end on the timeline.
        if ids_in_order.index(state.range_start_msg_id) > ids_in_order.index(state.range_end_msg_id):
            state.range_start_msg_id, state.range_end_msg_id = (
                state.range_end_msg_id,
                state.range_start_msg_id,
            )
    return (state.range_start_msg_id, state.range_end_msg_id) != prev


def reset_after_export(state: AppState, *, success_tag: str) -> None:
    """Clear the range/window state after a successful export.

    Keeps the chat selection and the persistent settings (contacts_path,
    output_dir, me_name, redact). Sets `last_export_status` so the status
    line can show the success banner until the next mutation.
    """
    state.range_start_msg_id = None
    state.range_end_msg_id = None
    state.typed_window = None
    state.window_source = "all"
    state.last_export_status = success_tag
