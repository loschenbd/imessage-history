"""Last-used wizard answers persisted to ~/.config/imessage-export/recent.json.

Schema:
    {
      "version": 1,
      "contacts_path":   "<absolute path>"   | null,
      "output_dir":      "<absolute path>"   | null,
      "me_name":         "Ben"              | null,
      "last_chat_id":    142                | null,
      "last_used":       "<ISO-8601>"       | null,
      "theme_override":  "dawnfox"|"terafox"| null
    }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1
DEFAULT_PATH = Path.home() / ".config" / "imessage-export" / "recent.json"


@dataclass
class Defaults:
    contacts_path: Optional[str] = None
    output_dir: Optional[str] = None
    me_name: Optional[str] = None
    last_chat_id: Optional[int] = None
    last_used: Optional[str] = None
    theme_override: Optional[str] = None


def load(path: Path = DEFAULT_PATH) -> Defaults:
    """Return a Defaults object. Missing file or unknown schema -> empty."""
    if not path.exists():
        return Defaults()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return Defaults()
    if data.get("version") != SCHEMA_VERSION:
        return Defaults()
    raw_theme = data.get("theme_override")
    if raw_theme not in ("dawnfox", "terafox", None):
        raw_theme = None
    return Defaults(
        contacts_path=data.get("contacts_path"),
        output_dir=data.get("output_dir"),
        me_name=data.get("me_name"),
        last_chat_id=data.get("last_chat_id"),
        last_used=data.get("last_used"),
        theme_override=raw_theme,
    )


def save(d: Defaults, path: Path = DEFAULT_PATH) -> None:
    """Write defaults to disk with 0o600 perms and 0o700 parent dirs."""
    _ensure_dir(path.parent)
    payload = {
        "version": SCHEMA_VERSION,
        **asdict(d),
        "last_used": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        os.chmod(tmp, 0o600)
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)
    os.chmod(path, 0o600)


def _ensure_dir(d: Path) -> None:
    """Create d and any missing parents under d, all with 0o700."""
    parts = []
    cur = d
    while not cur.exists():
        parts.append(cur)
        cur = cur.parent
    for p in reversed(parts):
        p.mkdir(mode=0o700)
