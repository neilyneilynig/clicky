"""Clicky's command-line entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys

from .protocol import VALID_COMMANDS


def socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise RuntimeError("XDG_RUNTIME_DIR is not set; run Clicky inside your Wayland session.")
    return Path(runtime_dir) / "clicky.sock"


def send_command(command: str) -> None:
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(path))
            client.sendall(f"{command}\n".encode())
    except FileNotFoundError as error:
        raise RuntimeError("Clicky is not running. Start it with `clicky run`.") from error
    except OSError as error:
        raise RuntimeError(f"Could not contact Clicky: {error}") from error


# The bin/clicky wrapper must not touch the cursor when this run never started.
ALREADY_RUNNING = 3


def already_running() -> bool:
    """Whether another Clicky is answering on the socket (two would fight over the screen)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(socket_path()))
        return True
    except (OSError, RuntimeError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Control the Clicky on-screen assistant.")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run", "ask", "history", "area", "undo", "next", "explain", "copy", "do", "hide", "quit", "watch"),
    )
    parser.add_argument("value", nargs="?", choices=("on", "off", "toggle"))
    args = parser.parse_args(argv)

    if args.command == "run":
        if args.value:
            parser.error("run does not take a value")
        if already_running():
            print("clicky: already running. Use `clicky quit` first to restart it.", file=sys.stderr)
            return ALREADY_RUNNING
        from .overlay import run

        run()
        return 0

    command = args.command if args.command != "watch" else f"watch {args.value or 'toggle'}"
    if command not in VALID_COMMANDS:
        parser.error("only `watch` accepts on, off, or toggle")
    try:
        send_command(command)
    except RuntimeError as error:
        print(f"clicky: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

