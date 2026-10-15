"""User-adjustable settings, saved next to Clicky's other state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

COLORS = {
    "blue": (0.23, 0.51, 0.96),
    "purple": (0.66, 0.33, 0.97),
    "green": (0.13, 0.77, 0.37),
    "orange": (0.98, 0.45, 0.09),
    "pink": (0.93, 0.28, 0.6),
}
MODELS = ("sonnet", "opus", "haiku")
# Seconds between watch-mode questions, and between unprompted tips.
WATCH_LEVELS = {"quiet": (60, 600), "normal": (20, 120), "chatty": (10, 45)}
MAX_COUNTDOWN = 10

DEFAULTS: dict[str, Any] = {
    "countdown": 3,  # 0 = never act on its own; wait for Super+Shift+R
    "model": "sonnet",
    "watch_model": "sonnet",
    "watch_level": "normal",
    "color": "blue",
}

VALID = {
    "countdown": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_COUNTDOWN,
    "model": lambda v: v in MODELS,
    "watch_model": lambda v: v in MODELS,
    "watch_level": lambda v: v in WATCH_LEVELS,
    "color": lambda v: v in COLORS,
}


class Settings:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.values = dict(DEFAULTS)
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = {}
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key in VALID and VALID[key](value):
                    self.values[key] = value

    def get(self, key: str) -> Any:
        return self.values[key]

    def set(self, key: str, value: Any) -> None:
        if key not in VALID or not VALID[key](value):
            raise ValueError(f"invalid setting {key}={value!r}")
        self.values[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.values, indent=2))
        except OSError:
            pass

    def accent(self) -> tuple[float, float, float]:
        return COLORS[self.values["color"]]

    def watch_gaps(self) -> tuple[int, int]:
        return WATCH_LEVELS[self.values["watch_level"]]


def css_rgb(color: tuple[float, float, float]) -> str:
    return ", ".join(str(round(channel * 255)) for channel in color)
