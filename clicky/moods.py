"""Clicky's little personality: bounces, wiggles, emoji reactions and naps."""

from __future__ import annotations

import math

from .motion import Point

SLEEP_AFTER_SECONDS = 300
EMOJI_SECONDS = 2.0
BOUNCE_SECONDS = 0.6
WIGGLE_SECONDS = 0.8

EMOJI = {"answer": "✨", "error": "⚠️", "tip": "💡", "done": "✅"}


class Mood:
    def __init__(self) -> None:
        self.event: str | None = None
        self.event_at = 0.0
        self.last_position: Point | None = None
        self.last_move = 0.0

    def trigger(self, event: str, now: float) -> None:
        self.event = event
        self.event_at = now

    def transform(self, now: float) -> tuple[float, float]:
        """(scale, rotation in radians) for the pointer right now."""
        elapsed = now - self.event_at
        if self.event == "error" and elapsed < WIGGLE_SECONDS:
            return 1.0, 0.35 * math.sin(elapsed * 30) * (1 - elapsed / WIGGLE_SECONDS)
        if self.event in ("answer", "done", "tip") and elapsed < BOUNCE_SECONDS:
            return 1.0 + 0.3 * math.sin(math.pi * elapsed / BOUNCE_SECONDS), 0.0
        return 1.0, 0.0

    def emoji(self, now: float) -> str | None:
        if self.event and now - self.event_at < EMOJI_SECONDS:
            return EMOJI.get(self.event)
        return None

    def moved(self, position: Point, now: float) -> None:
        last = self.last_position
        if last is None or math.hypot(position[0] - last[0], position[1] - last[1]) > 2:
            self.last_position = position
            self.last_move = now

    def asleep(self, now: float) -> bool:
        return now - self.last_move > SLEEP_AFTER_SECONDS
