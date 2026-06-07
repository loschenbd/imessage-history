"""attributedBody decoder + tapback classifiers.

`message.text` is often NULL on modern macOS; the body lives in
`message.attributedBody`, an NSAttributedString typedstream blob.
`decode_attributed_body` extracts the UTF-8 string out of that blob with
no external dependencies. `classify_tapback` / `strip_target_guid` deal
with the related but separate "associated message" subsystem
(👍/❤️/😂 reactions and their `p:N/<GUID>` foreign keys).
"""
from __future__ import annotations

from typing import Optional

from .models import TAPBACK_NAMES


def decode_attributed_body(blob: bytes) -> Optional[str]:
    """Best-effort extraction of message text from the NSAttributedString
    typedstream blob used by modern macOS Messages when `message.text` is NULL.

    Format (simplified): ...NSString...+<length-prefix><utf-8 bytes>
    The length prefix uses Apple's typedstream encoding:
      first byte < 0x80     => length is that byte
      first byte == 0x81    => next 2 bytes are length (little-endian uint16)
      first byte == 0x82    => next 4 bytes are length (little-endian uint32)
    Wider forms (0x83/0x84) are not observed in practice on chat.db and
    are not implemented; they would indicate strings >4GiB.
    """
    if not blob:
        return None
    try:
        idx = blob.find(b"NSString")
        if idx == -1:
            return None
        plus = blob.find(b"\x2b", idx)  # '+' marks the value start
        if plus == -1 or plus + 1 >= len(blob):
            return None
        pos = plus + 1
        first = blob[pos]; pos += 1
        if first < 0x80:
            length = first
        elif first == 0x81 and pos + 1 < len(blob):
            length = int.from_bytes(blob[pos:pos+2], "little"); pos += 2
        elif first == 0x82 and pos + 3 < len(blob):
            length = int.from_bytes(blob[pos:pos+4], "little"); pos += 4
        else:
            return None
        if length <= 0 or pos + length > len(blob):
            return None
        text = blob[pos:pos+length].decode("utf-8", errors="replace")
        # Strip Unicode object-replacement character used as attachment placeholder
        return text.replace("￼", "").strip() or None
    except Exception:
        return None


def classify_tapback(amt: int) -> Optional[tuple[str, bool]]:
    """Return (name, removed) or None for non-tapbacks."""
    if 2000 <= amt <= 2006:
        return (TAPBACK_NAMES.get(amt, f"type{amt}"), False)
    if 3000 <= amt <= 3006:
        return (TAPBACK_NAMES.get(amt - 1000, f"type{amt}"), True)
    return None


def strip_target_guid(assoc_guid: str) -> Optional[str]:
    """associated_message_guid is `p:N/<GUID>` or `bp:<GUID>`. Return bare <GUID>."""
    if not assoc_guid:
        return None
    if "/" in assoc_guid:
        return assoc_guid.rsplit("/", 1)[-1]
    if assoc_guid.startswith("bp:"):
        return assoc_guid[3:]
    return assoc_guid
