"""Smoke tests for the styled error panels in tui/errors.py.

These exercise each renderer end-to-end with capsys-style stderr capture
so a future refactor can't silently break the FDA-denied / no-match /
malformed-contacts UX paths.
"""
from __future__ import annotations

import importlib
import io
import sys
import unittest

try:
    errors = importlib.import_module("imessage_export.tui.errors")
    theme = importlib.import_module("imessage_export.tui.theme")
    HAS_TUI = True
except ImportError:
    errors = None
    theme = None
    HAS_TUI = False


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
class ErrorRendererSmokeTests(unittest.TestCase):

    def setUp(self):
        # Reset the singletons so we control the destination.
        theme._reset_console_for_tests()
        # Redirect sys.stderr so the captured Console writes into our buffer.
        # The stderr singleton captures sys.stderr at construction time, so we
        # have to patch BEFORE the first get_stderr_console() call, which
        # _reset_console_for_tests already arranges.
        self._buf = io.StringIO()
        self._orig_stderr = sys.stderr
        sys.stderr = self._buf

    def tearDown(self):
        sys.stderr = self._orig_stderr
        theme._reset_console_for_tests()

    def test_fda_denied_renders(self):
        errors.fda_denied("authorization denied")
        out = self._buf.getvalue()
        self.assertIn("Full Disk Access denied", out)
        self.assertIn("System Settings", out)
        self.assertIn("authorization denied", out)

    def test_no_chats_match_renders(self):
        errors.no_chats_match("zzzzzzz")
        out = self._buf.getvalue()
        self.assertIn("No chats matched", out)
        self.assertIn("zzzzzzz", out)

    def test_contacts_malformed_renders(self):
        errors.contacts_malformed("/tmp/contacts.csv", 7, "bad column count")
        out = self._buf.getvalue()
        self.assertIn("contacts.csv could not be parsed", out)
        self.assertIn("/tmp/contacts.csv", out)
        self.assertIn("Row:", out)
        self.assertIn("7", out)
        self.assertIn("bad column count", out)


@unittest.skipUnless(HAS_TUI, "[tui] extra not installed")
class ErrorRendererStderrRoutingTests(unittest.TestCase):

    def setUp(self):
        theme._reset_console_for_tests()

    def tearDown(self):
        theme._reset_console_for_tests()

    def test_renders_to_stderr_not_stdout(self):
        """The error console must land on fd 2 so users piping stdout to
        a file/grep still see the error UX."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        orig_out, orig_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_buf, err_buf
        try:
            errors.fda_denied("test reason")
        finally:
            sys.stdout, sys.stderr = orig_out, orig_err
        self.assertEqual(out_buf.getvalue(), "", "panel leaked to stdout")
        self.assertIn("Full Disk Access denied", err_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
