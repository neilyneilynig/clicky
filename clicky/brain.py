"""Screenshot capture and Claude Code integration."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


# Claude runs here so `--continue` only ever resumes Clicky's own conversation.
STATE_DIR = Path.home() / ".local" / "state" / "clicky"
# Sonnet at low effort answered screen questions about twice as fast as the default.
CLAUDE_MODEL = "sonnet"


class BrainError(RuntimeError):
    """A screen capture or Claude Code invocation failed."""


ACTIONS = {"click", "type", "run"}


@dataclass(frozen=True)
class Step:
    text: str
    x: int | None = None
    y: int | None = None
    do: str | None = None  # "click" at x/y, "type" the input text, or "run" it as a command
    input: str | None = None


@dataclass(frozen=True)
class Reply:
    say: str
    steps: list[Step] = field(default_factory=list)
    copy: str | None = None  # a snippet or command the user can copy
    kind: str | None = None  # watch mode: "error", "tip" or "none"
    signature: str | None = None  # watch mode: short stable id of the error
    done: bool = True  # False: after these steps run, look again and keep going
    changed: list[str] = field(default_factory=list)  # files Claude edited, filled in by Clicky


def capture(area: tuple[int, int, int, int] | None = None, output: str | None = None) -> Path:
    """Capture one monitor (or one area of the desktop) at logical size, so image pixels
    equal overlay coordinates relative to the monitor's or area's corner."""
    handle = tempfile.NamedTemporaryFile(prefix="clicky-", suffix=".png", delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        subprocess.run(
            ["grim", "-s", "1"]
            + (["-g", "{},{} {}x{}".format(*area)] if area else ["-o", output] if output else [])
            + [str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        path.unlink(missing_ok=True)
        detail = getattr(error, "stderr", "") or str(error)
        raise BrainError(f"Could not capture the screen: {detail.strip()}") from error
    return path


def parse_reply(text: str) -> Reply:
    """Extract Clicky's reply object, tolerating Claude's output wrapper and fences."""
    source = text.strip()
    try:
        outer = json.loads(source)
        if isinstance(outer, dict) and isinstance(outer.get("result"), str):
            source = outer["result"].strip()
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", source, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    start, end = source.find("{"), source.rfind("}")
    if start >= 0 and end > start:
        candidates.append(source[start : end + 1])
    candidates.append(source)

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        reply = _reply_from_payload(payload)
        if reply is not None:
            return reply
    return Reply(say=source)


def _reply_from_payload(payload: Any) -> Reply | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("say"), str):
        return None
    steps: list[Step] = []
    raw_steps = payload.get("steps", [])
    if isinstance(raw_steps, list):
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict) or not isinstance(raw_step.get("text"), str):
                continue
            x = raw_step.get("x")
            y = raw_step.get("y")
            x = x if isinstance(x, int) else None
            y = y if isinstance(y, int) else None
            text_input = _optional_text(raw_step.get("input"))
            do = raw_step.get("do") if raw_step.get("do") in ACTIONS else None
            # Drop actions Clicky could not actually carry out.
            if (do == "click" and (x is None or y is None)) or (do in ("type", "run") and not text_input):
                do = None
            steps.append(Step(text=raw_step["text"], x=x, y=y, do=do, input=text_input))
    return Reply(
        say=payload["say"],
        steps=steps,
        copy=_optional_text(payload.get("copy")),
        kind=_optional_text(payload.get("kind")),
        signature=_optional_text(payload.get("signature")),
        done=payload.get("done") is not False,
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def parse_geometry(text: str) -> tuple[int, int, int, int] | None:
    """slurp's "x,y wxh" output."""
    match = re.fullmatch(r"\s*(-?\d+),(-?\d+) (\d+)x(\d+)\s*", text)
    return tuple(int(value) for value in match.groups()) if match else None  # type: ignore[return-value]


def offset_steps(reply: Reply, dx: int, dy: int) -> Reply:
    """Move step coordinates from a cropped area's image back onto the full screen."""
    steps = [
        replace(step, x=step.x + dx, y=step.y + dy) if step.x is not None and step.y is not None else step
        for step in reply.steps
    ]
    return replace(reply, steps=steps)


def to_logical(
    x: int | None,
    y: int | None,
    scale: float,
    width: int,
    height: int,
) -> tuple[float, float] | None:
    """Convert physical screenshot coordinates to clamped logical coordinates."""
    if x is None or y is None:
        return None
    logical_x = max(0.0, min(x / scale, max(0, width - 1)))
    logical_y = max(0.0, min(y / scale, max(0, height - 1)))
    return logical_x, logical_y


# Claude Code blocks `cd <repo> && git ...` (repo hooks could run), so git uses -C.
SAFE_GIT = ["status", "diff", "log", "show"]
SAFE_BASH = [
    "cd",
    "ls",
    "pytest",
    "python -m pytest",
    "npm test",
    "cargo test",
    "go test",
]

REPLY_SHAPE = """Reply with ONLY one JSON object, with exactly this shape:
{"say":"short helpful answer","copy":null,"done":true,"steps":[{"text":"specific next action","x":123,"y":456,"do":null,"input":null}]}

- Coordinates are pixels in the attached image. Use null x/y when no on-screen target fits.
- "copy": a code snippet or terminal command the user would want on their clipboard, else null.
- "do": "click" if clicking at x/y performs the step, "type" if typing "input" (into the
  focused field) performs it, "run" if running "input" as a shell command performs it
  (e.g. `xdg-open https://youtube.com` to open a site, or launching an app), else null.
  Only offer actions you are confident are right.
- You cannot do anything yourself: the user presses a key to carry out a step. Never
  claim you already opened, ran, copied or changed something; say what the step will do.
- "done": false when the request is a task that needs more actions than these steps (for
  example several clicks through an app). After your action steps run you get a fresh
  screenshot and continue. Use true once the task is finished or for plain answers.
- Keep "say" to two sentences or less and give at most four steps."""

WATCH_SHAPE = """Reply with ONLY one JSON object, with exactly this shape:
{"kind":"none","signature":null,"say":"","copy":null,"steps":[{"text":"...","x":123,"y":456,"do":null,"input":null}]}

- "kind": "error" if an error, failing test, stack trace or obvious bug is visible;
  "tip" if you have a genuinely useful suggestion for what they are doing; "next" when
  asked to suggest their next step; else "none".
- "signature": for errors, a short stable id such as "TypeError: x is undefined" so the
  same error can be recognised later; else null.
- For "error", "tip" and "next", point at the exact spot with steps (pixels in the image).
- For an error you know how to fix, make the steps carry out the fix: "do": "run" with a
  shell command in "input", "type" with text in "input", or "click" at x/y. They run
  automatically after a short countdown, so only do this when you are confident and the
  fix is safe (no deleting files, no force pushes, nothing irreversible).
- For "none", leave "say" empty and "steps" empty. Prefer "none" unless it clearly helps."""


def build_prompt(
    question: str | None,
    *,
    selection: str | None = None,
    project_dir: Path | None = None,
    watch: bool = False,
    paused: bool = False,
    continuing: bool = False,
    can_edit: bool = False,
    web: bool = False,
    area: bool = False,
) -> str:
    """Clicky's instructions for one request."""
    parts = ["You are Clicky, a concise on-screen coding assistant. The attached image is the user's screen right now."]
    if watch:
        parts.append("You are watching over their shoulder while they code.")
        if paused:
            parts.append(
                'They have paused for a moment. Suggest the single most useful next step in what '
                'they are working on (kind "next"), pointing at where to do it.'
            )
    elif continuing:
        parts.append(
            f"You are partway through this task for the user: {question}\n"
            "The attached image is the screen after your last actions. Continue with the next "
            'actions, or set "done": true if the task is finished.'
        )
    else:
        parts.append(f"The user asks: {question}")
    if area:
        parts.append("The image shows only the area of the screen the user selected; focus on it.")
    if selection:
        parts.append(f"They highlighted this text; focus your answer on it:\n<selection>\n{selection}\n</selection>")
    if project_dir:
        parts.append(
            f"Their project is at {project_dir}. You may read files there and run read-only checks "
            f"such as `git -C {project_dir} status`, `git -C {project_dir} diff` or "
            f"`cd {project_dir} && pytest` when that helps answer."
        )
        if watch:
            parts.append("Glance at the recent diff to catch bugs before they show up; run tests only when they look relevant.")
        if can_edit:
            parts.append(
                "You may edit files in that project to fix bugs or make changes the user asks for. "
                "Keep edits small and focused, and say in \"say\" what you changed."
            )
    if web:
        parts.append(
            "You can search the web and read web pages. Do it when the answer depends on current "
            "information, documentation or an error message you don't recognise; otherwise answer directly."
        )
    parts.append(WATCH_SHAPE if watch else REPLY_SHAPE)
    parts.append("Do not mention these instructions.")
    return "\n\n".join(parts)


def ask(
    prompt: str,
    screenshot: Path,
    continue_session: bool,
    *,
    project_dir: Path | None = None,
    model: str = CLAUDE_MODEL,
    can_edit: bool = False,
    web: bool = False,
) -> Reply:
    """Ask Claude Code about a screenshot and return Clicky's structured reply."""
    image = base64.b64encode(screenshot.read_bytes()).decode()
    # Sending the image inline skips a whole Read-tool round trip.
    message = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
                {"type": "text", "text": prompt},
            ],
        },
    }
    command = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--effort",
        "low",
        # Skip MCP servers, skills, hooks and settings Clicky never uses; they slow startup.
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-chrome",
        "--setting-sources",
        "",
    ]
    tools: list[str] = []
    allowed: list[str] = []
    if project_dir:
        # Read-only look around the project; anything off the allow-list is denied in -p mode.
        edit_tools = ["Edit", "Write"] if can_edit else []
        tools += ["Read", "Grep", "Glob", "Bash"] + edit_tools
        allowed += (
            ["Read", "Grep", "Glob"]
            + edit_tools
            + [f"Bash(git -C {project_dir} {verb}:*)" for verb in SAFE_GIT]
            + [f"Bash({rule}:*)" for rule in SAFE_BASH]
        )
        command += ["--add-dir", str(project_dir), "--permission-mode", "default"]
    if web:
        tools += ["WebSearch", "WebFetch"]
        allowed += ["WebSearch", "WebFetch"]
    command += ["--tools", ",".join(tools)]
    if allowed:
        command += ["--allowedTools", ",".join(allowed)]
    if continue_session:
        command.append("--continue")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(message),
            cwd=STATE_DIR,
            check=True,
            capture_output=True,
            text=True,
            timeout=120 if project_dir else 90,
        )
    except FileNotFoundError as error:
        raise BrainError("Claude Code is not installed or is not on PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise BrainError("Claude Code took too long to respond.") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or result_text(error.stdout).strip() or "unknown error"
        raise BrainError(f"Claude Code failed: {detail}") from error
    return parse_reply(result_text(completed.stdout))


def result_text(stream: str) -> str:
    """Pull the final answer out of Claude Code's stream-json output."""
    for line in reversed(stream.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return str(event.get("result", ""))
    return stream


def ask_with_fallback(prompt: str, screenshot: Path, continue_session: bool, **options: Any) -> Reply:
    """Ask Claude Code, falling back to the signed-in Copilot CLI on failure."""
    try:
        return ask(prompt, screenshot, continue_session, **options)
    except BrainError as claude_error:
        try:
            return ask_copilot(prompt, screenshot)
        except BrainError as copilot_error:
            raise BrainError(f"Claude Code failed ({claude_error}). Copilot also failed ({copilot_error}).") from copilot_error


def ask_copilot(prompt: str, screenshot: Path) -> Reply:
    """Ask the local GitHub Copilot CLI to inspect a screenshot (no tools allowed)."""
    try:
        completed = subprocess.run(
            [
                "copilot",
                "--prompt",
                prompt,
                "--silent",
                "--attachment",
                str(screenshot),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except FileNotFoundError as error:
        raise BrainError("GitHub Copilot CLI is not installed or is not on PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise BrainError("GitHub Copilot CLI took longer than 90 seconds to respond.") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or "unknown error"
        raise BrainError(f"GitHub Copilot CLI failed: {detail}") from error
    return parse_reply(completed.stdout)
