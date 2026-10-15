import unittest

from clicky.motion import bubble_origin, ease_toward, follow_point


class EaseTests(unittest.TestCase):
    def test_moves_a_fraction_of_the_way(self):
        self.assertEqual(ease_toward((0.0, 0.0), (100.0, 50.0), 0.25), (25.0, 12.5))

    def test_snaps_when_close(self):
        self.assertEqual(ease_toward((99.8, 50.1), (100.0, 50.0), 0.25), (100.0, 50.0))


class FollowTests(unittest.TestCase):
    def test_sits_exactly_on_the_cursor(self):
        self.assertEqual(follow_point((640.0, 400.0)), (640.0, 400.0))


class BubbleTests(unittest.TestCase):
    def test_places_bubble_below_right_of_pointer(self):
        self.assertEqual(bubble_origin((100.0, 100.0), (200, 60), (1280, 800)), (122.0, 124.0))

    def test_flips_left_and_up_near_the_far_edges(self):
        x, y = bubble_origin((1250.0, 780.0), (200, 60), (1280, 800))

        self.assertLessEqual(x + 200, 1280 - 8)
        self.assertLessEqual(y + 60, 800 - 8)
        self.assertLess(x, 1250.0)
        self.assertLess(y, 780.0)

    def test_never_leaves_the_screen(self):
        self.assertEqual(bubble_origin((0.0, 0.0), (2000, 900), (1280, 800)), (8.0, 8.0))
