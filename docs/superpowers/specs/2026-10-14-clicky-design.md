# Clicky — Design

**Date:** 2026-10-14
**Status:** Implemented

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

---

## Revision 2 (2026-10-15): faster, more capable, Clicky is the cursor

Approved in chat. Supersedes the sections above where they conflict.

### Speed
- Screenshot captured at logical size (`grim -s 1`), so image pixels equal overlay
  coordinates. Sent inline (`--input-format stream-json`) instead of via the Read tool.
- `claude -p` runs with Sonnet at low effort and skips MCP servers, skills, hooks and
  settings. Measured ~6 s end to end (was ~11 s).
- Clicky hides itself for ~120 ms while the screenshot is taken; while Claude works it
  shows only a small bouncing-dots pill.

### Clicky is the cursor
- The blue pointer's tip sits exactly on the mouse (no offset) and Hyprland's cursor is
  hidden (`cursor:invisible`). When Clicky flies to a target, the real cursor is shown
  again. The cursor is restored on quit and by the `bin/clicky` wrapper on crash.

### Keys
| Key | Command | Does |
|---|---|---|
| Super+Shift+C | `ask` | Ask box |
| Super+Shift+G | `next` | Next step / dismiss |
| Super+Shift+I | `explain` | Explain or fix the highlighted (primary selection) text |
| Super+Shift+S | `copy` | Copy the answer's snippet/command to the clipboard |
| Super+Shift+R | `do` | Perform the current step (click there or type its text) |
| Super+Shift+W | `watch toggle` | Watch mode |

### Reply schema
`{"say", "copy": str|null, "steps": [{"text", "x", "y", "do": "click"|"type"|null, "input": str|null}]}`.
Watch replies add `"kind": "error"|"tip"|"none"` and `"signature"` (short stable id of
the error). A `copy` value is shown monospaced in the bubble with a copy hint; a step
with `do` shows a "Super+Shift+R do it" hint. Nothing is clicked or typed without the
key press.

### Run & check
When the focused window is a terminal whose shell sits in a project directory (has
`.git` or a common project marker), Claude gets `Read`, `Grep`, `Glob` and a Bash
allow-list scoped to that directory: `git status/diff/log/show`, `ls`, and test
runners (`pytest`, `npm test`, `cargo test`, `go test`). Anything else is denied. Test
runners execute project code; accepted.

### Actions
`wtype` types text; `hyprctl dispatch movecursor` + `wlrctl pointer click left` clicks.
Missing tools produce a bubble saying which package to install.

### Watch mode
- Screen-change check every 20 s (local thumbnail diff, free).
- Claude (Haiku, low effort) at most once per 45 s, only when the screen changed.
- `error` replies show immediately with steps that point at the problem.
- Same `signature` still present ≥ 3 min later → "Still stuck on …? Super+Shift+C".
- `tip` replies at most once per 10 min.
