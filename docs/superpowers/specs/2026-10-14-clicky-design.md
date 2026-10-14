# Clicky — Design

**Date:** 2026-10-14
**Status:** Approved in chat, pending spec review

## Purpose

Clicky is an on-screen AI cursor buddy for Hyprland. When you're coding something
complicated, you ask it a question; it looks at your screen, answers in a speech
bubble next to the cursor, and physically points at the relevant spot, walking you
through multi-step instructions. An optional watch mode offers unprompted tips.

Success: press a hotkey, type "why is this failing?", and within ~10–20 s Clicky
glides to the offending line and explains it, with follow-up steps on demand.

## Constraints

- Platform: Arch Linux, Hyprland (Wayland), single monitor eDP-2, 1920×1200 physical,
  scale 1.5 (1280×800 logical).
- Brain: the user's Claude Code login via `claude -p` (no API key, no extra cost).
  Slower per call (several seconds); acceptable.
- Input: typing only. No voice.
- Stack: Python 3 + GTK4 + gtk4-layer-shell via PyGObject (system packages, already
  installed). Run with `/usr/bin/python3` so system `gi` is available.

## Components

All under `~/Projects/clicky/`, package `clicky/`.

### `clicky/overlay.py` — the overlay app
- Full-screen GTK4 window on the layer-shell OVERLAY layer, anchored to all edges,
  transparent, with an empty input region so all clicks pass through.
- Draws, via a `Gtk.DrawingArea` + cairo:
  - **Clicky**: a small glowing pointer-buddy sprite offset from the cursor
    (bottom-right, +18/+18 logical px).
  - **Speech bubble**: rounded rect with wrapped text, kept on-screen.
  - **Target marker**: pulsing ring at the pointed location plus an arrow from
    Clicky to it.
  - **Step counter**: "2/4" in the bubble when there are multiple steps.
  - **Thinking** state: animated dots in the bubble.
- Cursor follow: polls `hyprctl cursorpos` (~30 Hz) while idle; eases toward targets.
- **Ask box**: when `ask` is triggered, sets layer-shell keyboard mode to EXCLUSIVE
  and sets the input region to the box only, shows a `Gtk.Entry` near the cursor.
  Enter submits; Esc cancels. Afterwards keyboard mode returns to NONE and input
  region to empty.
- Listens on a Unix socket `$XDG_RUNTIME_DIR/clicky.sock` for line commands:
  `ask`, `next`, `watch on|off|toggle`, `hide`, `quit`.

### `clicky/brain.py` — talking to Claude
- `capture() -> path`: `grim` screenshot to a temp PNG (physical pixels).
- `ask(question, screenshot, continue_session) -> Reply`: runs
  `claude -p --output-format json --allowedTools Read [--continue] "<prompt>"` with the prompt telling
  Claude to Read the screenshot file path, the screen size in screenshot pixels, and
  to respond ONLY with JSON:
  `{"say": str, "steps": [{"text": str, "x": int|null, "y": int|null}]}`.
  Timeout 90 s.
- `parse_reply(text) -> Reply`: extracts the JSON object (tolerates code fences /
  surrounding prose). If parsing fails, returns `Reply(say=text, steps=[])`.
- `to_logical(x, y, scale) -> (x, y)`: screenshot px → logical px (divide by scale,
  clamp to screen). Scale read from `hyprctl monitors -j`.
- Runs in a worker thread; results delivered to GTK via `GLib.idle_add`.

### `clicky/watcher.py` — watch mode
- Every 90 s (when enabled, and not busy, and no bubble showing): capture a
  downscaled screenshot, compare with the last one (mean pixel difference over a
  small grayscale thumbnail). If changed beyond threshold, ask Claude
  "Is there anything clearly worth flagging to help the user? If not, return
  `{"say": "", "steps": []}`." Empty `say` → stay silent. Watch requests never use `--continue`, so they don't
  pollute the ask conversation.
- Pure function `changed(prev_thumb, cur_thumb, threshold) -> bool` is unit-tested.

### `clicky/cli.py` + `bin/clicky` — command line
- `clicky` (no args) or `clicky run`: start the overlay.
- `clicky ask|next|hide|quit`, `clicky watch on|off|toggle`: send to the socket.
  If the overlay isn't running, print a clear error.

### Hyprland keybinds (added to the user's hypr config)
- `Super+Shift+C` → `clicky ask`
- `Super+Shift+N` → `clicky next`
- `Super+Shift+W` → `clicky watch toggle`
- Esc inside the ask box cancels; `clicky hide` dismisses a bubble.
- Autostart via `exec-once = clicky run`.

## Data flow

1. Hotkey → `clicky ask` → socket → overlay shows ask box by cursor.
2. Enter → overlay enters *thinking*, worker thread: `capture()` → `ask()` →
   `parse_reply()` → coordinates converted to logical.
3. Overlay shows `say`; if steps exist, glides to step 1 target and shows its text
   with counter.
4. `clicky next` advances; after the last step (or `hide`), Clicky returns to
   following the cursor.
5. A new question within the same overlay run uses `--continue` so follow-ups keep
   context. The first question of a run starts fresh.

## Error handling

- `claude` missing / non-zero exit / timeout → bubble shows a short error message.
- Unparseable reply → shown as plain text, no pointing.
- Step with null coords → text shown at Clicky's current spot, no marker.
- Coordinates outside screen → clamped.
- `grim` failure → error bubble.
- Only one request in flight; `ask` while thinking is ignored (bubble says so).

## Testing

- Unit tests (pytest): `parse_reply` (clean JSON, fenced JSON, prose-wrapped,
  garbage), `to_logical` (scale + clamp), `watcher.changed`, socket command parsing.
- Manual/visual: run the overlay, trigger commands via CLI, verify with `grim`
  screenshots that Clicky, bubble, and markers render and clicks pass through.

## Out of scope

Voice input/output, API-key backend, multi-monitor support, settings UI.
