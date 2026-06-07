"""The headless code path must not import rich or questionary at module
load time. If it does, importing imessage_export.cli adds 150-250ms of
import cost to every headless invocation — defeating the [tui] split."""
from __future__ import annotations

import subprocess
import sys
import unittest


class HeadlessImportPurityTests(unittest.TestCase):

    def test_importing_cli_does_not_pull_rich(self):
        script = (
            "import sys, imessage_export.cli; "
            "imessage_export.cli.build_parser(); "
            "leaked = [m for m in ('rich', 'questionary') if m in sys.modules]; "
            "print('LEAKED:', leaked)"
        )
        out = subprocess.check_output(
            [sys.executable, "-c", script], text=True
        ).strip()
        self.assertEqual(out, "LEAKED: []", f"unexpected: {out!r}")


if __name__ == "__main__":
    unittest.main()
