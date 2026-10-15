import unittest

from clicky.watcher import changed


class ChangeDetectionTests(unittest.TestCase):
    def test_detects_new_or_different_thumbnails(self):
        self.assertTrue(changed(None, bytes([1, 2, 3])))
        self.assertTrue(changed(bytes([1, 2]), bytes([1, 2, 3])))
        self.assertTrue(changed(bytes([0, 0]), bytes([20, 20]), threshold=8))

    def test_ignores_small_changes(self):
        self.assertFalse(changed(bytes([10, 10]), bytes([14, 14]), threshold=8))



from clicky.brain import Reply, Step
from clicky.watcher import WatchPolicy


def error(signature, say="Boom"):
    return Reply(say, [Step("here", 1, 2)], kind="error", signature=signature)


class WatchPolicyTests(unittest.TestCase):
    def test_asks_on_change_at_most_every_20_seconds(self):
        policy = WatchPolicy()

        self.assertIsNone(policy.should_ask(0, changed=False))
        self.assertEqual(policy.should_ask(1, changed=True), "change")
        self.assertIsNone(policy.should_ask(10, changed=True))
        self.assertEqual(policy.should_ask(21, changed=True), "change")

    def test_coaches_once_after_a_30_second_pause(self):
        policy = WatchPolicy()
        policy.should_ask(0, changed=True)

        self.assertIsNone(policy.should_ask(20, changed=False))
        self.assertEqual(policy.should_ask(30, changed=False), "pause")
        self.assertIsNone(policy.should_ask(90, changed=False))
        policy.should_ask(100, changed=True)
        self.assertEqual(policy.should_ask(130, changed=False), "pause")

    def test_next_step_suggestions_are_shown(self):
        policy = WatchPolicy()
        coach = Reply("Now add a test", kind="next")

        self.assertEqual(policy.on_reply(coach, 0), coach)

    def test_a_new_error_shows_once(self):
        policy = WatchPolicy()

        self.assertEqual(policy.on_reply(error("E1"), 0), error("E1"))
        self.assertIsNone(policy.on_reply(error("E1"), 60))

    def test_offers_help_when_the_same_error_lingers_three_minutes(self):
        policy = WatchPolicy()
        policy.on_reply(error("TypeError in app.js", "A long explanation of the error."), 0)

        self.assertIsNone(policy.on_quiet(100))
        offer = policy.on_quiet(180)
        self.assertIn("Still stuck", offer.say)
        self.assertIn("TypeError in app.js", offer.say)
        self.assertIsNone(policy.on_quiet(400))

    def test_clean_screen_clears_the_error(self):
        policy = WatchPolicy()
        policy.on_reply(error("E1"), 0)
        policy.on_reply(Reply("", kind="none"), 50)

        self.assertIsNone(policy.on_quiet(500))
        self.assertIsNotNone(policy.on_reply(error("E1"), 600))

    def test_tips_are_rate_limited_to_one_per_two_minutes(self):
        policy = WatchPolicy()
        tip = Reply("Try a dict", kind="tip")

        self.assertEqual(policy.on_reply(tip, 0), tip)
        self.assertIsNone(policy.on_reply(tip, 60))
        self.assertEqual(policy.on_reply(tip, 120), tip)

    def test_none_and_empty_replies_stay_silent(self):
        policy = WatchPolicy()

        self.assertIsNone(policy.on_reply(Reply("", kind="none"), 0))
        self.assertIsNone(policy.on_reply(Reply("  ", kind="tip"), 0))

    def test_gaps_can_be_tuned(self):
        policy = WatchPolicy(min_ask_gap=60, tip_gap=600)
        tip = Reply("Try a dict", kind="tip")

        self.assertEqual(policy.should_ask(0, changed=True), "change")
        self.assertIsNone(policy.should_ask(30, changed=True))
        policy.on_reply(tip, 0)
        self.assertIsNone(policy.on_reply(tip, 300))
