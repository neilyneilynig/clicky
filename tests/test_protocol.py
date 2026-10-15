import unittest

from clicky.protocol import parse_command


class CommandTests(unittest.TestCase):
    def test_normalizes_valid_commands(self):
        self.assertEqual(parse_command(" WATCH ON \n"), "watch on")

    def test_rejects_unknown_commands(self):
        self.assertIsNone(parse_command("delete everything"))


    def test_accepts_the_new_action_commands(self):
        self.assertEqual([parse_command(c) for c in ("explain", "copy", "do")], ["explain", "copy", "do"])
