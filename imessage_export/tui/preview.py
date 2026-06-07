"""Render conversation.md inline with Rich + paging. Full body, no truncation."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown


def show_markdown(path: Path) -> None:
    """Render `path` as Markdown in Rich's pager.

    Full message bodies (no truncation) per UX spec — the pager handles long
    conversations gracefully.
    """
    console = Console()
    text = path.read_text()
    md = Markdown(text)
    with console.pager(styles=True):
        console.print(md)
