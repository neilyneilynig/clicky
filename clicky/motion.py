"""Pure geometry for Clicky's pointer animation and bubble placement."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

Point = tuple[float, float]


@dataclass(frozen=True)
class Monitor:
    """One output in Hyprland's desktop layout, in logical (scaled) pixels. Clicky's
    overlay covers one monitor at a time and draws in its local coordinates."""

    name: str
    x: int
    y: int
    width: int
    height: int

    def contains(self, point: Point) -> bool:
        return self.x <= point[0] < self.x + self.width and self.y <= point[1] < self.y + self.height

    def to_local(self, point: Point) -> Point:
        return point[0] - self.x, point[1] - self.y

    def to_global(self, point: Point) -> Point:
        return point[0] + self.x, point[1] + self.y


def parse_monitors(monitors: Iterable[dict]) -> list[Monitor]:
    """Monitors from `hyprctl monitors -j`, sized in logical pixels like the cursor."""
    return [
        Monitor(
            str(m["name"]),
            int(m["x"]),
            int(m["y"]),
            round(int(m["width"]) / float(m["scale"])),
            round(int(m["height"]) / float(m["scale"])),
        )
        for m in monitors
    ]


def monitor_at(monitors: Iterable[Monitor], point: Point) -> Monitor | None:
    return next((monitor for monitor in monitors if monitor.contains(point)), None)

# Clicky replaces the mouse cursor, so its tip sits exactly on the hotspot.
FOLLOW_OFFSET = (0.0, 0.0)
BUBBLE_GAP = (22.0, 24.0)
SCREEN_MARGIN = 8.0


def ease_toward(current: Point, target: Point, factor: float) -> Point:
    """Move a fraction of the way to target, snapping once within half a pixel."""
    dx, dy = target[0] - current[0], target[1] - current[1]
    if math.hypot(dx, dy) < 0.5:
        return target
    return current[0] + dx * factor, current[1] + dy * factor


def follow_point(cursor: Point) -> Point:
    """Where Clicky rests while it is acting as the mouse cursor."""
    return cursor[0] + FOLLOW_OFFSET[0], cursor[1] + FOLLOW_OFFSET[1]


def bubble_origin(pointer: Point, size: tuple[int, int], screen: tuple[int, int]) -> Point:
    """Top-left of the speech bubble: below-right of the pointer, flipped near edges."""
    width, height = size
    screen_width, screen_height = screen
    x = pointer[0] + BUBBLE_GAP[0]
    y = pointer[1] + BUBBLE_GAP[1]
    if x + width > screen_width - SCREEN_MARGIN:
        x = pointer[0] - BUBBLE_GAP[0] - width
    if y + height > screen_height - SCREEN_MARGIN:
        y = pointer[1] - BUBBLE_GAP[1] - height
    x = max(SCREEN_MARGIN, min(x, screen_width - SCREEN_MARGIN - width))
    y = max(SCREEN_MARGIN, min(y, screen_height - SCREEN_MARGIN - height))
    return x, y
