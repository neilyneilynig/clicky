import tempfile
import unittest
from pathlib import Path

from clicky.brain import Reply, Step
from clicky.history import History


class HistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "history.json"

    def test_round_trips_replies_with_steps(self):
        reply = Reply("Do it", [Step("Click", 1, 2, do="click")], copy="ls", changed=["app.py +1 -0"])
        history = History(self.path)
        history.add("how?", reply)

        loaded = History(self.path)

        self.assertEqual([(e.question, e.reply) for e in loaded.entries], [("how?", reply)])

    def test_keeps_only_the_newest_entries(self):
        history = History(self.path, limit=3)
        for index in range(5):
            history.add(f"q{index}", Reply(f"a{index}"))

        self.assertEqual([e.question for e in History(self.path, limit=3).entries], ["q2", "q3", "q4"])

    def test_a_corrupt_file_starts_empty(self):
        self.path.write_text("{not json")

        self.assertEqual(History(self.path).entries, [])
