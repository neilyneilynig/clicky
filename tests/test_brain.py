import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clicky.brain import STATE_DIR, BrainError, Reply, Step, ask, ask_with_fallback, build_prompt, offset_steps, parse_geometry, parse_reply, result_text, to_logical


class ParseReplyTests(unittest.TestCase):
    def test_parses_clean_json(self):
        reply = parse_reply('{"say":"Fix this","steps":[{"text":"Click here","x":120,"y":60}]}')

        self.assertEqual(reply, Reply("Fix this", [reply.steps[0]]))
        self.assertEqual((reply.steps[0].text, reply.steps[0].x, reply.steps[0].y), ("Click here", 120, 60))

    def test_parses_fenced_json(self):
        reply = parse_reply('Here you go:\n```json\n{"say":"Try again","steps":[]}\n```')

        self.assertEqual(reply, Reply("Try again"))

    def test_parses_copy_actions_and_watch_fields(self):
        reply = parse_reply(json.dumps({
            "say": "Typo on line 3",
            "copy": "git add -p",
            "kind": "error",
            "signature": "NameError: foo",
            "steps": [
                {"text": "Click the file", "x": 10, "y": 20, "do": "click"},
                {"text": "Type the fix", "x": None, "y": None, "do": "type", "input": "foo = 1"},
                {"text": "Bogus action", "do": "explode"},
            ],
        }))

        self.assertEqual(reply.copy, "git add -p")
        self.assertEqual((reply.kind, reply.signature), ("error", "NameError: foo"))
        self.assertEqual([step.do for step in reply.steps], ["click", "type", None])
        self.assertEqual(reply.steps[1].input, "foo = 1")

    def test_click_needs_coordinates_and_type_or_run_need_input(self):
        reply = parse_reply('{"say":"x","steps":[{"text":"a","do":"click"},{"text":"b","do":"type"},{"text":"c","do":"run"}]}')

        self.assertEqual([step.do for step in reply.steps], [None, None, None])

    def test_parses_a_run_step(self):
        reply = parse_reply('{"say":"x","steps":[{"text":"Open it","do":"run","input":"xdg-open https://youtube.com"}]}')

        self.assertEqual((reply.steps[0].do, reply.steps[0].input), ("run", "xdg-open https://youtube.com"))

    def test_done_defaults_to_true_and_can_be_false(self):
        self.assertTrue(parse_reply('{"say":"x","steps":[]}').done)
        self.assertFalse(parse_reply('{"say":"x","steps":[],"done":false}').done)

    def test_returns_plain_text_when_no_json_exists(self):
        self.assertEqual(parse_reply("This is ordinary text."), Reply("This is ordinary text."))


class AreaTests(unittest.TestCase):
    def test_offsets_steps_from_the_cropped_image_back_to_the_screen(self):
        reply = Reply("x", [Step("a", 10, 20, do="click"), Step("b")])

        moved = offset_steps(reply, 100, 50)

        self.assertEqual([(s.x, s.y) for s in moved.steps], [(110, 70), (None, None)])
        self.assertEqual(moved.steps[0].do, "click")

    def test_area_prompt_says_the_image_is_a_crop(self):
        self.assertIn("selected", build_prompt("what is this?", area=True))

    def test_parses_slurp_geometry(self):
        self.assertEqual(parse_geometry("12,34 560x78\n"), (12, 34, 560, 78))
        self.assertIsNone(parse_geometry(""))


class CoordinateTests(unittest.TestCase):
    def test_scales_and_clamps_coordinates(self):
        self.assertEqual(to_logical(1920, 1200, 1.5, 1280, 800), (1279, 799))
        self.assertEqual(to_logical(-2, 4, 1.5, 1280, 800), (0.0, 2.6666666666666665))
        self.assertIsNone(to_logical(None, 4, 1.5, 1280, 800))


class FallbackTests(unittest.TestCase):
    @patch("clicky.brain.ask_copilot", return_value=Reply("Copilot reply"))
    @patch("clicky.brain.ask", side_effect=BrainError("Claude unavailable"))
    def test_uses_copilot_when_claude_fails(self, _ask, copilot):
        reply = ask_with_fallback("Help", Path("/tmp/screen.png"), False)

        self.assertEqual(reply, Reply("Copilot reply"))
        copilot.assert_called_once_with("Help", Path("/tmp/screen.png"))


class ClaudeInvocationTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"\x89PNG fake")
        handle.close()
        self.screenshot = Path(handle.name)
        self.addCleanup(self.screenshot.unlink)

    @patch("clicky.brain.subprocess.run")
    def test_sends_the_screenshot_inline_with_no_tools(self, run):
        run.return_value.stdout = STREAM_RESULT

        reply = ask("Help", self.screenshot, False)

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--input-format") + 1], "stream-json")
        self.assertIn("--strict-mcp-config", command)
        message = json.loads(run.call_args.kwargs["input"])
        image, text = message["message"]["content"]
        self.assertEqual(image["source"]["data"], base64.b64encode(b"\x89PNG fake").decode())
        self.assertIn("Help", text["text"])
        self.assertEqual(reply, Reply("ok"))

    @patch("clicky.brain.subprocess.run")
    def test_runs_claude_in_clickys_own_directory(self, run):
        # --continue resumes the latest session in the cwd; keep Clicky's separate.
        run.return_value.stdout = STREAM_RESULT

        ask("Help", self.screenshot, True)

        self.assertEqual(run.call_args.kwargs["cwd"], STATE_DIR)
        self.assertIn("--continue", run.call_args.args[0])


    @patch("clicky.brain.subprocess.run")
    def test_project_gets_read_tools_and_a_safe_bash_allow_list(self, run):
        run.return_value.stdout = STREAM_RESULT

        ask("Help", self.screenshot, False, project_dir=Path("/home/me/app"))

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob,Bash")
        self.assertEqual(command[command.index("--add-dir") + 1], "/home/me/app")
        self.assertEqual(command[command.index("--permission-mode") + 1], "default")
        allowed = command[command.index("--allowedTools") + 1].split(",")
        self.assertIn("Bash(git -C /home/me/app status:*)", allowed)
        self.assertIn("Bash(pytest:*)", allowed)
        self.assertFalse(any("rm" in rule for rule in allowed))

    @patch("clicky.brain.subprocess.run")
    def test_editing_adds_edit_and_write_tools(self, run):
        run.return_value.stdout = STREAM_RESULT

        ask("Help", self.screenshot, False, project_dir=Path("/home/me/app"), can_edit=True)

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob,Bash,Edit,Write")
        allowed = command[command.index("--allowedTools") + 1].split(",")
        self.assertIn("Edit", allowed)
        self.assertIn("Write", allowed)

    @patch("clicky.brain.subprocess.run")
    def test_read_only_by_default(self, run):
        run.return_value.stdout = STREAM_RESULT

        ask("Help", self.screenshot, False, project_dir=Path("/home/me/app"))

        allowed = run.call_args.args[0][run.call_args.args[0].index("--allowedTools") + 1].split(",")
        self.assertNotIn("Edit", allowed)

    @patch("clicky.brain.subprocess.run")
    def test_web_adds_search_tools_without_a_project(self, run):
        run.return_value.stdout = STREAM_RESULT

        ask("Help", self.screenshot, False, web=True)

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(command[command.index("--allowedTools") + 1], "WebSearch,WebFetch")

    @patch("clicky.brain.subprocess.run")
    def test_web_combines_with_project_tools(self, run):
        run.return_value.stdout = STREAM_RESULT

        ask("Help", self.screenshot, False, project_dir=Path("/home/me/app"), web=True)

        command = run.call_args.args[0]
        self.assertTrue(command[command.index("--tools") + 1].endswith(",WebSearch,WebFetch"))
        self.assertIn("WebFetch", command[command.index("--allowedTools") + 1].split(","))

    @patch("clicky.brain.subprocess.run")
    def test_model_can_be_chosen(self, run):
        run.return_value.stdout = STREAM_RESULT

        ask("Help", self.screenshot, False, model="haiku")

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "haiku")


class PromptTests(unittest.TestCase):
    def test_question_prompt_mentions_selection_and_project(self):
        prompt = build_prompt("why?", selection="x = 1/0", project_dir=Path("/home/me/app"))

        self.assertIn("why?", prompt)
        self.assertIn("x = 1/0", prompt)
        self.assertIn("/home/me/app", prompt)
        self.assertIn('"copy"', prompt)
        self.assertIn('"run"', prompt)

    def test_watch_prompt_asks_for_kind_and_signature(self):
        prompt = build_prompt(None, watch=True)

        self.assertIn('"kind"', prompt)
        self.assertIn('"signature"', prompt)
        self.assertIn("fix", prompt)

    def test_question_prompt_explains_done_and_editing(self):
        prompt = build_prompt("fix it", project_dir=Path("/home/me/app"), can_edit=True)

        self.assertIn('"done"', prompt)
        self.assertIn("edit files", prompt)

    def test_web_prompt_mentions_searching(self):
        self.assertIn("search the web", build_prompt("what's new in react?", web=True))

    def test_continue_prompt_restates_the_goal(self):
        prompt = build_prompt("play lofi on youtube", continuing=True)

        self.assertIn("play lofi on youtube", prompt)
        self.assertIn("after your last actions", prompt)

    def test_paused_watch_prompt_asks_for_the_next_step(self):
        prompt = build_prompt(None, watch=True, paused=True, project_dir=Path("/home/me/app"))

        self.assertIn('"next"', prompt)
        self.assertIn("paused", prompt)
        self.assertIn("git -C /home/me/app diff", prompt)


class StreamResultTests(unittest.TestCase):
    def test_picks_the_result_line(self):
        self.assertEqual(result_text(STREAM_RESULT), '{"say":"ok","steps":[]}')

    def test_falls_back_to_raw_output(self):
        self.assertEqual(result_text("plain words"), "plain words")


STREAM_RESULT = "\n".join([
    json.dumps({"type": "system", "subtype": "init"}),
    json.dumps({"type": "result", "result": '{"say":"ok","steps":[]}'}),
])
