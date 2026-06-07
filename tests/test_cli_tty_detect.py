"""TTY-detect dispatch in cli.main()."""
from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imessage_export import cli


class TTYDetectTests(unittest.TestCase):

    def test_piped_no_args_prints_help(self):
        buf = io.StringIO()
        with mock.patch.object(sys.stdout, "isatty", return_value=False), \
             mock.patch.object(sys.stderr, "isatty", return_value=False), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("sys.stdout", buf):
            os.environ.pop("CI", None)
            os.environ.pop("NONINTERACTIVE", None)
            rc = cli.main([])
        self.assertEqual(rc, 2)
        self.assertIn("usage", buf.getvalue().lower() + " ")

    def test_ci_env_forces_help_even_on_tty(self):
        buf = io.StringIO()
        with mock.patch.object(sys.stdout, "isatty", return_value=True), \
             mock.patch.object(sys.stderr, "isatty", return_value=True), \
             mock.patch.dict(os.environ, {"CI": "1"}, clear=False), \
             mock.patch("sys.stdout", buf):
            rc = cli.main([])
        self.assertEqual(rc, 2)

    def test_tty_no_args_calls_app_dispatch(self):
        with mock.patch.object(sys.stdout, "isatty", return_value=True), \
             mock.patch.object(sys.stderr, "isatty", return_value=True), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("imessage_export.cli._run_app") as run_app:
            os.environ.pop("CI", None)
            os.environ.pop("NONINTERACTIVE", None)
            run_app.return_value = 0
            rc = cli.main([])
        run_app.assert_called_once()
        self.assertEqual(rc, 0)

    def test_tty_wizard_flag_calls_wizard_dispatch(self):
        with mock.patch.object(sys.stdout, "isatty", return_value=True), \
             mock.patch.object(sys.stderr, "isatty", return_value=True), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("imessage_export.cli._run_wizard") as run_wiz:
            os.environ.pop("CI", None)
            os.environ.pop("NONINTERACTIVE", None)
            run_wiz.return_value = 0
            rc = cli.main(["--wizard"])
        run_wiz.assert_called_once()
        self.assertEqual(rc, 0)

    def test_app_flag_calls_app_dispatch(self):
        with mock.patch.object(sys.stdout, "isatty", return_value=True), \
             mock.patch.object(sys.stderr, "isatty", return_value=True), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("imessage_export.cli._run_app") as run_app:
            os.environ.pop("CI", None)
            os.environ.pop("NONINTERACTIVE", None)
            run_app.return_value = 0
            rc = cli.main(["--app"])
        run_app.assert_called_once()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
