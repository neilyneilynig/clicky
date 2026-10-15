import unittest
from unittest.mock import call, patch

from clicky.actions import ActionError, copy_to_clipboard, perform
from clicky.brain import Step


@patch("clicky.actions.subprocess.run")
class ActionTests(unittest.TestCase):
    def test_copy_pipes_text_to_wl_copy(self, run):
        copy_to_clipboard("git add -p")

        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["wl-copy"])
        self.assertEqual(run.call_args.kwargs["input"], "git add -p")

    def test_click_moves_the_mouse_then_clicks(self, run):
        perform(Step("Save", x=100, y=200, do="click"))

        self.assertEqual(
            [c.args[0] for c in run.call_args_list],
            [["hyprctl", "dispatch", "movecursor", "100", "200"], ["wlrctl", "pointer", "click", "left"]],
        )

    def test_type_sends_the_input_with_wtype(self, run):
        perform(Step("Fix", do="type", input="--force"))

        self.assertEqual(run.call_args, call(["wtype", "--", "--force"], check=True, capture_output=True, text=True, timeout=30))

    @patch("clicky.actions.subprocess.Popen")
    def test_run_starts_the_command_detached(self, popen, run):
        perform(Step("Open YouTube", do="run", input="xdg-open https://youtube.com"))

        self.assertEqual(popen.call_args.args[0], ["sh", "-c", "xdg-open https://youtube.com"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("LD_PRELOAD", popen.call_args.kwargs["env"])
        run.assert_not_called()

    def test_step_without_an_action_is_an_error(self, run):
        with self.assertRaises(ActionError):
            perform(Step("Look here", x=1, y=2))
        run.assert_not_called()

    def test_missing_tool_names_the_package_to_install(self, run):
        run.side_effect = FileNotFoundError("wtype")

        with self.assertRaisesRegex(ActionError, "pacman -S wtype"):
            perform(Step("Fix", do="type", input="x"))
