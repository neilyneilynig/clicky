import unittest

from clicky.motion import Monitor, monitor_at, parse_monitors


# The real layout that hid the cursor: HDMI at x=1280, a 1.5x-scaled laptop panel at x=3200.
HYPRCTL = [
    {"name": "eDP-2", "x": 3200, "y": 0, "width": 1920, "height": 1200, "scale": 1.5},
    {"name": "HDMI-A-1", "x": 1280, "y": 0, "width": 1920, "height": 1080, "scale": 1.0},
]


class ParseMonitorsTests(unittest.TestCase):
    def test_uses_logical_size(self):
        self.assertEqual(
            parse_monitors(HYPRCTL),
            [Monitor("eDP-2", 3200, 0, 1280, 800), Monitor("HDMI-A-1", 1280, 0, 1920, 1080)],
        )


class MonitorAtTests(unittest.TestCase):
    def setUp(self):
        self.monitors = parse_monitors(HYPRCTL)

    def test_finds_the_monitor_under_the_cursor(self):
        self.assertEqual(monitor_at(self.monitors, (3510.0, 457.0)).name, "eDP-2")
        self.assertEqual(monitor_at(self.monitors, (1500.0, 900.0)).name, "HDMI-A-1")

    def test_none_when_off_every_monitor(self):
        self.assertIsNone(monitor_at(self.monitors, (100.0, 100.0)))


class MonitorCoordinateTests(unittest.TestCase):
    def test_converts_between_desktop_and_monitor_coordinates(self):
        laptop = Monitor("eDP-2", 3200, 0, 1280, 800)

        self.assertEqual(laptop.to_local((3510.0, 457.0)), (310.0, 457.0))
        self.assertEqual(laptop.to_global((310.0, 457.0)), (3510.0, 457.0))
        self.assertTrue(laptop.contains((3510.0, 457.0)))
        self.assertFalse(laptop.contains((3100.0, 457.0)))


if __name__ == "__main__":
    unittest.main()
