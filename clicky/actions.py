"""Things Clicky does for the user: copy, click, type and run. Only ever on a key press."""

from __future__ import annotations

import os
import subprocess

from .brain import Step

INSTALL_HINTS = {
    "wl-copy": "sudo pacman -S wl-clipboard",
    "wtype": "sudo pacman -S wtype",
    "wlrctl": "yay -S wlrctl",
}


class ActionError(RuntimeError):
    """An action could not be carried out."""


def copy_to_clipboard(text: str) -> None:
    _run(["wl-copy"], input=text)


def perform(step: Step) -> None:
    """Carry out a step's click, typing or command."""
    if step.do == "click" and step.x is not None and step.y is not None:
        _run(["hyprctl", "dispatch", "movecursor", str(step.x), str(step.y)])
        _run(["wlrctl", "pointer", "click", "left"])
    elif step.do == "type" and step.input:
        _run(["wtype", "--", step.input])
    elif step.do == "run" and step.input:
        # Detached, so apps it opens outlive the request and Clicky never waits on them.
        subprocess.Popen(
            ["sh", "-c", step.input],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_clean_env(),
        )
    else:
        raise ActionError("This step has nothing Clicky can do for you.")


def _clean_env() -> dict[str, str]:
    """The user's environment without the layer-shell preload Clicky itself needs."""
    env = dict(os.environ)
    env.pop("LD_PRELOAD", None)
    return env


def _run(command: list[str], **options: object) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=30, **options)
    except FileNotFoundError as error:
        hint = INSTALL_HINTS.get(command[0], command[0])
        raise ActionError(f"{command[0]} is not installed. Install it with: {hint}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip() or f"exit {error.returncode}"
        raise ActionError(f"{command[0]} failed: {detail}") from error
    except subprocess.TimeoutExpired as error:
        raise ActionError(f"{command[0]} timed out.") from error
