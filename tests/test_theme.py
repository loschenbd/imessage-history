"""Unit tests for the theme resolver + Rich Console builder."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from imessage_export.tui import theme


class PaletteCompletenessTests(unittest.TestCase):
    REQUIRED_KEYS = {
        "bg", "bg_alt", "bg_3", "fg",
        "accent", "accent_alt",
        "success", "warning", "error",
        "day_header", "muted", "border_soft",
    }

    def test_dawnfox_has_all_keys(self):
        self.assertEqual(set(theme.DAWNFOX.keys()), self.REQUIRED_KEYS)

    def test_terafox_has_all_keys(self):
        self.assertEqual(set(theme.TERAFOX.keys()), self.REQUIRED_KEYS)

    def test_palettes_dict_lookup(self):
        self.assertIs(theme.PALETTES["dawnfox"], theme.DAWNFOX)
        self.assertIs(theme.PALETTES["terafox"], theme.TERAFOX)

    def test_all_values_are_hex_strings(self):
        for name, pal in theme.PALETTES.items():
            for key, val in pal.items():
                self.assertTrue(
                    val.startswith("#") and len(val) == 7,
                    f"{name}.{key}={val!r} is not a 7-char #rrggbb string",
                )


class MakeConsoleTests(unittest.TestCase):
    def test_console_registers_accent_style(self):
        console = theme.make_console(theme.DAWNFOX)
        self.assertEqual(str(console.get_style("accent")), theme.DAWNFOX["accent"])

    def test_console_registers_success_style(self):
        console = theme.make_console(theme.TERAFOX)
        self.assertEqual(str(console.get_style("success")), theme.TERAFOX["success"])

    def test_console_registers_all_semantic_styles(self):
        console = theme.make_console(theme.DAWNFOX)
        for name in ("accent", "accent_alt", "success", "warning",
                     "error", "muted", "day_header"):
            console.get_style(name)


class DetectAppearanceTests(unittest.TestCase):
    def _completed(self, returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["defaults"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_dark_mode(self):
        with mock.patch.object(theme.subprocess, "run",
                               return_value=self._completed(0, "Dark\n")):
            self.assertEqual(theme.detect_appearance(), "dark")

    def test_light_mode_returncode_1(self):
        with mock.patch.object(theme.subprocess, "run",
                               return_value=self._completed(1, "")):
            self.assertEqual(theme.detect_appearance(), "light")

    def test_missing_binary(self):
        with mock.patch.object(theme.subprocess, "run",
                               side_effect=FileNotFoundError()):
            self.assertEqual(theme.detect_appearance(), "light")

    def test_timeout(self):
        with mock.patch.object(
            theme.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="defaults", timeout=2.0),
        ):
            self.assertEqual(theme.detect_appearance(), "light")


class ResolveThemeNameTests(unittest.TestCase):
    def test_cli_wins(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli="terafox", env="dawnfox", persisted="dawnfox",
            )
        self.assertEqual(name, "terafox")

    def test_env_beats_persisted(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli=None, env="terafox", persisted="dawnfox",
            )
        self.assertEqual(name, "terafox")

    def test_persisted_beats_detect(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli=None, env=None, persisted="terafox",
            )
        self.assertEqual(name, "terafox")

    def test_auto_falls_through_to_detect(self):
        with mock.patch.object(theme, "detect_appearance", return_value="dark"):
            name = theme.resolve_theme_name(
                cli=None, env="auto", persisted=None,
            )
        self.assertEqual(name, "terafox")

    def test_detect_light_means_dawnfox(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(cli=None, env=None, persisted=None)
        self.assertEqual(name, "dawnfox")

    def test_detect_dark_means_terafox(self):
        with mock.patch.object(theme, "detect_appearance", return_value="dark"):
            name = theme.resolve_theme_name(cli=None, env=None, persisted=None)
        self.assertEqual(name, "terafox")

    def test_unknown_env_treated_as_auto(self):
        with mock.patch.object(theme, "detect_appearance", return_value="light"):
            name = theme.resolve_theme_name(
                cli=None, env="catppuccin", persisted=None,
            )
        self.assertEqual(name, "dawnfox")

    def test_unknown_persisted_treated_as_auto(self):
        with mock.patch.object(theme, "detect_appearance", return_value="dark"):
            name = theme.resolve_theme_name(
                cli=None, env=None, persisted="catppuccin",
            )
        self.assertEqual(name, "terafox")


class ResolvePaletteTests(unittest.TestCase):
    def test_returns_dict_matching_resolved_name(self):
        with mock.patch.object(theme, "resolve_theme_name", return_value="dawnfox"):
            self.assertIs(theme.resolve_palette(), theme.DAWNFOX)
        with mock.patch.object(theme, "resolve_theme_name", return_value="terafox"):
            self.assertIs(theme.resolve_palette(), theme.TERAFOX)


class GetConsoleTests(unittest.TestCase):
    def test_get_console_is_singleton_per_palette(self):
        theme._reset_console_for_tests()
        c1 = theme.get_console()
        c2 = theme.get_console()
        self.assertIs(c1, c2)


class RegisterTextualThemesTests(unittest.TestCase):
    def test_register_textual_themes_idempotent(self):
        """Re-registering must not raise — on_mount can run twice in tests."""
        from imessage_export.tui import theme

        class _FakeApp:
            def __init__(self):
                self._registered: list[str] = []

            def register_theme(self, t):
                if t.name in self._registered:
                    raise ValueError(f"theme {t.name!r} already registered")
                self._registered.append(t.name)

        app = _FakeApp()
        theme.register_textual_themes(app)
        # Second call must not raise.
        theme.register_textual_themes(app)
        self.assertIn("dawnfox", app._registered)
        self.assertIn("terafox", app._registered)


if __name__ == "__main__":
    unittest.main()
