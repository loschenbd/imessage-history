"""Data model for a single exported message.

The `Message` dataclass is the in-memory representation produced by
`export()` and consumed by every writer. The tapback constants live
beside it because `Message.reaction.type` values map back into them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Message:
    message_id: int
    timestamp: str           # ISO8601 in local tz
    timestamp_utc: str       # ISO8601 in UTC
    chat_id: int
    sender_handle: Optional[str]
    is_from_me: int
    author_label: str
    text: str
    has_attachment: int
    attachment_filenames: list = field(default_factory=list)
    # "message" (default), "tapback", "unsent", or "app"
    kind: str = "message"
    is_edited: int = 0
    # For kind == "tapback":
    reaction: Optional[dict] = None       # {"type": "Loved", "target_message_id": int, "target_text": str, "target_author": str}
    # For kind == "app" (link preview / iMessage app payload with no plain text):
    app_bundle: Optional[str] = None


# Tapback type codes (from associated_message_type).
# 2000-2006 = added; 3000-3006 = removed (display as "removed-Loved" etc.)
TAPBACK_NAMES = {
    2000: "Loved", 2001: "Liked", 2002: "Disliked",
    2003: "Laughed", 2004: "Emphasized", 2005: "Questioned",
    2006: "Sticker",
}
TAPBACK_GLYPHS = {
    "Loved": "♡", "Liked": "👍", "Disliked": "👎",
    "Laughed": "😂", "Emphasized": "‼️", "Questioned": "❓",
    "Sticker": "🩹",
}
