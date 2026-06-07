"""Textual app for `imessage-export` (Phase 2).

Only imported when the user enters the app. Importing this module pulls
in Textual + Rich + Questionary; never import from any top-level
non-tui module.
"""
from __future__ import annotations


def run() -> int:
    """Mount and run the Textual app. Returns the process exit code."""
    from .app import ImessageExportApp
    return ImessageExportApp().run() or 0
