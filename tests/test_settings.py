import json
import tempfile
import unittest
from pathlib import Path

from clicky.settings import COLORS, DEFAULTS, Settings, css_rgb


class SettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.json"

    def test_defaults_when_no_file(self):
        self.assertEqual(Settings(self.path).values, DEFAULTS)

    def test_changes_persist(self):
        Settings(self.path).set("color", "green")

        self.assertEqual(Settings(self.path).get("color"), "green")

    def test_invalid_values_fall_back_to_defaults(self):
        self.path.write_text(json.dumps({"color": "plaid", "countdown": 99, "model": "gpt", "watch_level": 3}))

        self.assertEqual(Settings(self.path).values, DEFAULTS)

    def test_rejects_invalid_set(self):
        settings = Settings(self.path)

        with self.assertRaises(ValueError):
            settings.set("countdown", -1)

    def test_watch_level_gives_ask_and_tip_gaps(self):
        settings = Settings(self.path)
        settings.set("watch_level", "quiet")

        self.assertEqual(settings.watch_gaps(), (60, 600))

    def test_accent_and_css(self):
        settings = Settings(self.path)
        settings.set("color", "purple")

        self.assertEqual(settings.accent(), COLORS["purple"])
        self.assertEqual(css_rgb(COLORS["blue"]), "59, 130, 245")
