"""Low-cost screen-change detection for Clicky's watch mode."""

from __future__ import annotations

from pathlib import Path
import subprocess

from .brain import BrainError, Reply

CHECK_SECONDS = 10
MIN_ASK_GAP_SECONDS = 20
PAUSE_SECONDS = 30
STUCK_AFTER_SECONDS = 180
TIP_GAP_SECONDS = 120


def changed(previous: bytes | None, current: bytes, threshold: float = 8.0) -> bool:
    """Return whether two equal-sized grayscale thumbnails differ materially."""
    if previous is None or len(previous) != len(current):
        return True
    if not current:
        return False
    difference = sum(abs(a - b) for a, b in zip(previous, current)) / len(current)
    return difference >= threshold


def capture_thumbnail(width: int = 96, height: int = 60) -> bytes:
    """Capture a small grayscale thumbnail without writing a persistent image."""
    try:
        completed = subprocess.run(
            ["grim", "-t", "ppm", "-"],
            check=True,
            capture_output=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", b"").decode(errors="replace") or str(error)
        raise BrainError(f"Could not capture the screen: {detail.strip()}") from error
    return _ppm_thumbnail(completed.stdout, width, height)


def _ppm_thumbnail(image: bytes, width: int, height: int) -> bytes:
    header, separator, pixels = image.partition(b"\n255\n")
    if not separator:
        raise BrainError("grim returned an invalid PPM screenshot.")
    parts = header.split()
    if len(parts) != 3 or parts[0] != b"P6":
        raise BrainError("grim returned an unsupported screenshot format.")
    source_width, source_height = int(parts[1]), int(parts[2])
    expected = source_width * source_height * 3
    if len(pixels) < expected:
        raise BrainError("grim returned an incomplete screenshot.")

    output = bytearray(width * height)
    for target_y in range(height):
        source_y = min(source_height - 1, target_y * source_height // height)
        for target_x in range(width):
            source_x = min(source_width - 1, target_x * source_width // width)
            offset = (source_y * source_width + source_x) * 3
            red, green, blue = pixels[offset : offset + 3]
            output[target_y * width + target_x] = (77 * red + 150 * green + 29 * blue) >> 8
    return bytes(output)



class WatchPolicy:
    """Decides when watch mode asks Claude and which of its replies are worth showing."""

    def __init__(self, min_ask_gap: float = MIN_ASK_GAP_SECONDS, tip_gap: float = TIP_GAP_SECONDS) -> None:
        self.min_ask_gap = min_ask_gap
        self.tip_gap = tip_gap
        self.last_ask: float | None = None
        self.last_change = 0.0
        self.coached = False
        self.last_tip: float | None = None
        self.error_signature: str | None = None
        self.error_since = 0.0
        self.stuck_offered = False

    def should_ask(self, now: float, changed: bool) -> str | None:
        """"change" to review new screen content, "pause" to coach the next step, else None."""
        if changed:
            self.last_change = now
            self.coached = False
        if self.last_ask is not None and now - self.last_ask < self.min_ask_gap:
            return None
        if changed:
            reason = "change"
        elif not self.coached and now - self.last_change >= PAUSE_SECONDS:
            self.coached = True
            reason = "pause"
        else:
            return None
        self.last_ask = now
        return reason

    def on_reply(self, reply: Reply, now: float) -> Reply | None:
        if reply.kind == "error" and reply.say.strip():
            signature = reply.signature or reply.say
            if signature == self.error_signature:
                return self.on_quiet(now)
            self.error_signature = signature
            self.error_since = now
            self.stuck_offered = False
            return reply
        if reply.kind != "error":
            self.error_signature = None
        if reply.kind == "next" and reply.say.strip():
            return reply
        if reply.kind == "tip" and reply.say.strip():
            if self.last_tip is None or now - self.last_tip >= self.tip_gap:
                self.last_tip = now
                return reply
        return None

    def on_quiet(self, now: float) -> Reply | None:
        """Called when nothing new was asked: offer help if an error has lingered."""
        if self.error_signature is None or self.stuck_offered:
            return None
        if now - self.error_since < STUCK_AFTER_SECONDS:
            return None
        self.stuck_offered = True
        return Reply(say=f"Still stuck on {self.error_signature}? Press Super+Shift+C and I'll dig in.")
