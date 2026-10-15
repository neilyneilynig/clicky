"""Past questions and answers, kept across restarts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time

from .brain import Reply, Step


@dataclass(frozen=True)
class Entry:
    question: str
    reply: Reply
    at: float = 0.0


class History:
    def __init__(self, path: Path, limit: int = 50) -> None:
        self.path = path
        self.limit = limit
        self.entries: list[Entry] = self._load()

    def add(self, question: str, reply: Reply) -> None:
        self.entries = (self.entries + [Entry(question, reply, time.time())])[-self.limit :]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(entry) for entry in self.entries]))
        except OSError:
            pass

    def _load(self) -> list[Entry]:
        try:
            raw = json.loads(self.path.read_text())
            return [_entry(item) for item in raw][-self.limit :]
        except (OSError, ValueError, TypeError, KeyError):
            return []


def _entry(item: dict) -> Entry:
    reply = dict(item["reply"])
    reply["steps"] = [Step(**step) for step in reply.get("steps", [])]
    return Entry(item["question"], Reply(**reply), item.get("at", 0.0))
