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
