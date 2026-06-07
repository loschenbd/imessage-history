"""imessage_export — Export a single iMessage conversation from the local
macOS Messages database (~/Library/Messages/chat.db) into AI-ready files
with explicit authorship on every message.

Package layout (one module per concern, mirroring the section banners in
the original single-file `imessage_export.py`):

    timestamps.py   — Apple-epoch ↔ datetime conversion
    decoder.py      — attributedBody NSAttributedString extractor;
                       tapback classification
    models.py       — `Message` dataclass; tapback name/glyph tables
    db.py           — chat.db open / introspection / chat-discovery SQL;
                       `chat_label` folder-name helper
    contacts.py     — handle (phone/email) → human name resolution
    window.py       — `TimeWindow` + argparse-driven window resolution
    export.py       — SQL → `Message` list + metadata dict
    writers.py      — CSV / JSON / TXT / Markdown / AI-ready writers;
                       day-header + gap-marker rendering helpers
    redactor.py     — opt-in pseudonymization; `--suggest-names` helper
                       (not in the original restructure plan — promoted
                       to its own module to keep writers.py focused)
    cli.py          — argparse + `main` entry point

Read-only: `chat.db` is opened with `mode=ro&immutable=1` plus
`PRAGMA query_only=ON`.

REQUIRES macOS Full Disk Access for the process running this package
(Terminal.app, iTerm2, Ghostty, or python3 itself). System Settings ▸
Privacy & Security ▸ Full Disk Access ▸ add your terminal app.
"""
from __future__ import annotations

# Backward-compat re-exports — preserve `import imessage_export as ie; ie.foo`
# semantics from the single-file era so existing tests and downstream
# consumers don't need to learn the new module layout. New code should
# import from the submodules directly.
from .timestamps import *           # noqa: F401,F403
from .decoder    import *           # noqa: F401,F403
from .models     import *           # noqa: F401,F403
from .db         import *           # noqa: F401,F403
from .contacts   import *           # noqa: F401,F403
from .window     import *           # noqa: F401,F403
from .export     import *           # noqa: F401,F403
from .writers    import *           # noqa: F401,F403
from .redactor   import *           # noqa: F401,F403
from .cli        import *           # noqa: F401,F403

# `from x import *` skips underscore-prefixed names; re-export the few
# private helpers tests reach into explicitly.
from .redactor import _excel_letters, _PROPER_NOUN_RE, _SUGGEST_STOPWORDS  # noqa: F401
