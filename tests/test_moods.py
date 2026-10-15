import unittest

from clicky.moods import SLEEP_AFTER_SECONDS, Mood


class MoodTests(unittest.TestCase):
    def test_resting_pointer_is_unchanged(self):
        self.assertEqual(Mood().transform(5.0), (1.0, 0.0))

    def test_answer_bounces_then_settles(self):
        mood = Mood()
        mood.trigger("answer", 10.0)

        scale, _angle = mood.transform(10.3)
        self.assertGreater(scale, 1.05)
        self.assertEqual(mood.transform(11.0), (1.0, 0.0))

    def test_error_wiggles_and_shows_a_warning(self):
        mood = Mood()
        mood.trigger("error", 0.0)

        _scale, angle = mood.transform(0.05)
        self.assertNotEqual(angle, 0.0)
        self.assertEqual(mood.emoji(0.5), "⚠️")
        self.assertIsNone(mood.emoji(5.0))

    def test_each_event_has_its_emoji(self):
        mood = Mood()
        for event, emoji in (("tip", "💡"), ("done", "✅"), ("answer", "✨")):
            mood.trigger(event, 0.0)
            self.assertEqual(mood.emoji(0.1), emoji)

    def test_falls_asleep_when_idle_and_wakes_on_movement(self):
        mood = Mood()
        mood.moved((0.0, 0.0), 0.0)

        self.assertFalse(mood.asleep(SLEEP_AFTER_SECONDS - 1))
        self.assertTrue(mood.asleep(SLEEP_AFTER_SECONDS + 1))
        mood.moved((5.0, 0.0), SLEEP_AFTER_SECONDS + 2)
        self.assertFalse(mood.asleep(SLEEP_AFTER_SECONDS + 3))

    def test_tiny_jitter_does_not_count_as_movement(self):
        mood = Mood()
        mood.moved((0.0, 0.0), 0.0)
        mood.moved((0.5, 0.0), SLEEP_AFTER_SECONDS + 1)

        self.assertTrue(mood.asleep(SLEEP_AFTER_SECONDS + 2))
