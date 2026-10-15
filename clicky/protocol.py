"""Shared socket protocol definitions."""

from __future__ import annotations

VALID_COMMANDS = {
    "ask", "history", "next", "explain", "area", "undo", "copy", "do", "hide", "quit", "watch on", "watch off", "watch toggle"
}


def parse_command(line: str) -> str | None:
    """Normalize and validate a command received through Clicky's socket."""
    command = line.strip().lower()
    return command if command in VALID_COMMANDS else None

