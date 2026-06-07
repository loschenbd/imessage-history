"""Optional Rich + Questionary TUI for imessage-export.

This subpackage is only importable when the [tui] extra is installed:
    pip install 'imessage-history[tui]'

Modules under `tui/` may import rich and questionary at module top-level.
Core modules (`cli`, `db`, `writers`, ...) MUST NOT import anything under
`tui/` at module top-level — only inside the wizard/tables/preview dispatch
functions, so the headless path stays zero-deps.
"""
