"""GTK4 layer-shell overlay for Clicky."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Callable
from ctypes import CDLL

import cairo

# This must load before PyGObject loads GTK and libwayland-client.
CDLL("libgtk4-layer-shell.so")
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell, Pango, PangoCairo  # noqa: E402

from .actions import ActionError, copy_to_clipboard, perform
from .brain import (
    STATE_DIR,
    BrainError,
    Reply,
    Step,
    ask_with_fallback,
    build_prompt,
    capture,
    offset_steps,
    parse_geometry,
    to_logical,
)
from .context import (
    changed_files,
    dirty_contents,
    focused_project_dir,
    project_snapshot,
    selection_text,
    undo_edits,
)
from .history import Entry, History
from .motion import Monitor, Point, bubble_origin, ease_toward, follow_point, monitor_at, parse_monitors
from .moods import Mood
from .panel import AskPanel, panel_css
from .settings import Settings
from .protocol import parse_command
from .watcher import CHECK_SECONDS, WatchPolicy, capture_thumbnail, changed


ANSWER_DISMISS_SECONDS = 30
WATCH_DISMISS_SECONDS = 15
STATUS_DISMISS_SECONDS = 3
TOAST_SECONDS = 2.5
CAPTURE_SETTLE_MS = 120
TASK_STEP_SECONDS = 1.5  # countdown once a task is underway
TASK_READ_SECONDS = 2.5  # pause on a non-action step mid-task before moving on
TASK_SETTLE_MS = 1500  # let the screen catch up before the next round's screenshot
TASK_MAX_ROUNDS = 10
KEYS = {
    "next": "Super+Shift+G",
    "do": "Super+Shift+R",
    "copy": "Super+Shift+S",
    "cancel": "Super+Shift+G",
    "undo": "Super+Shift+Z",
}
TRAIL_LENGTH = 14
MONITOR_REFRESH_SECONDS = 2.0

BUBBLE_WIDTH = 320
BUBBLE_PADDING = 12
# Clicky's colour; updated in place from settings so every drawing helper follows it.
ACCENT = [0.23, 0.51, 0.96]
SNIPPET_LIMIT = 600


class ClickyApplication(Gtk.Application):
    """Owns Clicky's display, request lifecycle, and local control socket."""

    def __init__(self) -> None:
        super().__init__(application_id="com.heycliky.App")
        self.window: Gtk.ApplicationWindow | None = None
        self.canvas: Gtk.DrawingArea | None = None
        self.layout: Gtk.Fixed | None = None
        self.panel: AskPanel | None = None
        self.panel_anchor: Point = (0.0, 0.0)
        self.cursor: Point = (0.0, 0.0)  # in the overlay's monitor coordinates
        self.cursor_global: Point = (0.0, 0.0)  # in Hyprland's desktop layout
        self.pointer: Point = (0.0, 0.0)
        self.reply: Reply | None = None
        self.step_index = 0
        self.thinking = False
        self.capturing = False
        self.toast: str | None = None
        self.toast_until = 0.0
        self.cursor_hidden = False
        self.auto_do_at: float | None = None
        self.auto_kind = "do"
        self.watch_busy = False
        self.history = History(STATE_DIR / "history.json")
        self.settings = Settings(STATE_DIR / "settings.json")
        ACCENT[:] = self.settings.accent()
        self.watch_policy = WatchPolicy(*self.settings.watch_gaps())
        self.css: Gtk.CssProvider | None = None
        self.mood = Mood()
        self.trail: deque[Point] = deque(maxlen=TRAIL_LENGTH)
        self.ask_area: tuple[int, int, int, int] | None = None
        self.last_edit: tuple[Path, list[str], dict[str, bytes]] | None = None
        self.request_seq = 0
        self.request_label = ""
        self.task_goal: str | None = None
        self.task_project: Path | None = None
        self.task_round = 0
        self.ask_project: Path | None = None
        self.watch_enabled = False
        self.last_thumbnail: bytes | None = None
        self.conversation_started = False
        self.socket: socket.socket | None = None
        self.socket_thread: threading.Thread | None = None
        self.socket_file: Path | None = None
        self.dismiss_source: int | None = None
        self.geometry = (1.0, 1920, 1080)
        self.monitors: list[Monitor] = []
        self.monitors_read_at = 0.0
        self.monitor = Monitor("", 0, 0, 1920, 1080)
        self.hypr_socket = _hyprland_socket_path()
        self.connect("shutdown", self._cleanup)

    def do_activate(self) -> None:
        if self.window:
            self.window.present()
            return

        self.monitors = self._read_monitors()
        self.cursor_global = self._cursor_position()
        self.monitor = monitor_at(self.monitors, self.cursor_global) or (self.monitors or [self.monitor])[0]
        self.geometry = (1.0, self.monitor.width, self.monitor.height)
        _scale, width, height = self.geometry

        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_decorated(False)
        self.window.set_title("Hey Clicky")
        self.window.add_css_class("clicky-overlay")
        self.css = Gtk.CssProvider()
        self._apply_css()
        Gtk.StyleContext.add_provider_for_display(
            self.window.get_display(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        # A full-screen, click-through layer: drawing coordinates equal screen coordinates.
        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, "clicky")
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.OVERLAY)
        for edge in (
            Gtk4LayerShell.Edge.TOP,
            Gtk4LayerShell.Edge.BOTTOM,
            Gtk4LayerShell.Edge.LEFT,
            Gtk4LayerShell.Edge.RIGHT,
        ):
            Gtk4LayerShell.set_anchor(self.window, edge, True)
        Gtk4LayerShell.set_exclusive_zone(self.window, -1)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        self._pin_to_monitor()

        self.layout = Gtk.Fixed()
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_draw_func(self._draw)
        self.canvas.set_size_request(width, height)
        self.layout.put(self.canvas, 0, 0)

        self.panel = AskPanel(
            self._submit_question, self._pick_history, self._hide_ask_box, self.settings, self._change_setting
        )
        self.panel.set_visible(False)
        self.layout.put(self.panel, 0, 0)
        self.window.set_child(self.layout)
        self.window.connect("realize", self._make_click_through)
        self.window.connect("map", self._make_click_through)
        self.window.present()

        self.cursor = self.monitor.to_local(self.cursor_global)
        self.pointer = follow_point(self.cursor)
        self._start_socket()
        GLib.timeout_add(16, self._tick)
        GLib.timeout_add_seconds(CHECK_SECONDS, self._watch_tick)
        if os.environ.get("CLICKY_SIMULATE"):
            GLib.timeout_add_seconds(1, self._simulate_reply)

    def _simulate_reply(self) -> bool:
        """Debug: a canned walkthrough so animations can be checked without Claude."""
        _scale, width, height = self.geometry
        corner = lambda fx, fy: (round(width * fx), round(height * fy))
        steps = [Step(text, *corner(fx, fy), do=do, input=typed) for text, fx, fy, do, typed in (
            ("This is the top-left area.", 0.15, 0.15, None, None),
            ("Now over to the right.", 0.8, 0.3, "run", "true"),
            ("And down here at the bottom.", 0.45, 0.85, None, None),
        )]
        reply = Reply("Simulated walkthrough.", steps, copy="git status --short")
        self.mood.trigger("answer", time.monotonic())
        self._present_reply(reply, ANSWER_DISMISS_SECONDS)
        return False

    def _apply_css(self) -> None:
        if self.css:
            self.css.load_from_string(".clicky-overlay { background: transparent; }" + panel_css(tuple(ACCENT)))

    def _change_setting(self, key: str, value: object) -> None:
        if key != "_resize":
            try:
                self.settings.set(key, value)
            except ValueError:
                return
            if key == "color":
                ACCENT[:] = self.settings.accent()
                self._apply_css()
            elif key == "watch_level":
                self.watch_policy.min_ask_gap, self.watch_policy.tip_gap = self.settings.watch_gaps()
        # The panel may have grown or shrunk: keep it on screen and clickable.
        GLib.idle_add(self._place_panel)

    def _cleanup(self, *_args: object) -> None:
        self._hide_system_cursor(False)
        if self.socket:
            self.socket.close()
        if self.socket_file:
            self.socket_file.unlink(missing_ok=True)

    def _start_socket(self) -> None:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime_dir:
            self._show_message("Clicky needs XDG_RUNTIME_DIR. Start it inside Hyprland.")
            return
        self.socket_file = Path(runtime_dir) / "clicky.sock"
        self.socket_file.unlink(missing_ok=True)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.socket_file))
        self.socket.listen()
        self.socket_thread = threading.Thread(target=self._serve_socket, daemon=True)
        self.socket_thread.start()

    def _serve_socket(self) -> None:
        assert self.socket
        while True:
            try:
                connection, _ = self.socket.accept()
            except OSError:
                return
            with connection:
                command = parse_command(connection.recv(128).decode(errors="replace"))
            if command:
                GLib.idle_add(self._handle_command, command)

    def _handle_command(self, command: str) -> bool:
        _log(f"command {command}")
        if command in ("ask", "history"):
            self._show_ask_box()
        elif command == "next":
            if self.auto_do_at is not None and self.auto_kind == "do":
                self.auto_do_at = None
                self._show_toast("Cancelled. Super+Shift+R to do it anyway.")
            elif self.thinking:
                self._stop()
            else:
                self._next_step()
        elif command == "explain":
            self._explain_selection()
        elif command == "area":
            self._select_area()
        elif command == "undo":
            self._undo_last_edit()
        elif command == "copy":
            self._copy_snippet()
        elif command == "do":
            self._do_step()
        elif command == "hide":
            if self._panel_open():
                self._hide_ask_box()
            self.task_goal = None
            self._hide_reply()
        elif command == "quit":
            self.quit()
        else:
            self.watch_enabled = command != "watch off" if command != "watch toggle" else not self.watch_enabled
            status = "on" if self.watch_enabled else "off"
            self._show_message(f"Watching mode is {status}.")
        return False

    # --- motion -----------------------------------------------------------------

    def _tick(self) -> bool:
        if self.auto_do_at is not None and time.monotonic() >= self.auto_do_at:
            self.auto_do_at = None
            self._do_step() if self.auto_kind == "do" else self._next_step()
        self.cursor_global = self._cursor_position()
        self._follow_monitor()
        self.cursor = self.monitor.to_local(self.cursor_global)
        self.mood.moved(self.cursor, time.monotonic())
        target = self._pointer_target()
        flying = target != follow_point(self.cursor)
        if flying or self._returning():
            self.trail.append(self.pointer)
        elif self.trail:
            self.trail.popleft()
        # Following: sit exactly on the mouse. Flying: glide, and give the real cursor back.
        self.pointer = ease_toward(self.pointer, target, 0.12) if flying or self._returning() else target
        # Only stand in for the cursor on the monitor Clicky is drawn on; anywhere else
        # the real cursor would simply vanish.
        on_overlay = self.monitor.contains(self.cursor_global)
        self._hide_system_cursor(on_overlay and not flying and self.pointer == target)
        if self.canvas:
            self.canvas.queue_draw()
        return True

    def _returning(self) -> bool:
        """Still gliding back to the mouse after pointing somewhere else."""
        cursor = follow_point(self.cursor)
        return math.hypot(self.pointer[0] - cursor[0], self.pointer[1] - cursor[1]) > 40

    def _follow_monitor(self) -> None:
        """Move the overlay to whichever monitor the mouse is on. It stays put while a
        walkthrough or question is under way, since its steps belong to that screen."""
        if self.monitor.contains(self.cursor_global):
            return
        now = time.monotonic()
        found = monitor_at(self.monitors, self.cursor_global)
        if found is None and now - self.monitors_read_at >= MONITOR_REFRESH_SECONDS:
            self.monitors = self._read_monitors()  # a monitor was plugged in or rearranged
            found = monitor_at(self.monitors, self.cursor_global)
        busy = self.thinking or self.capturing or self._current_step() or self._panel_open()
        if found is None or busy:
            return
        self.monitor = found
        self.geometry = (1.0, found.width, found.height)
        self.trail.clear()
        self.pointer = follow_point(found.to_local(self.cursor_global))
        if self.canvas:
            self.canvas.set_size_request(found.width, found.height)
        self._pin_to_monitor()

    def _pin_to_monitor(self) -> None:
        if not self.window:
            return
        monitors = self.window.get_display().get_monitors()
        for index in range(monitors.get_n_items()):
            gdk_monitor = monitors.get_item(index)
            if gdk_monitor.get_connector() == self.monitor.name:
                Gtk4LayerShell.set_monitor(self.window, gdk_monitor)
                return

    def _hide_system_cursor(self, hidden: bool) -> None:
        if hidden != self.cursor_hidden:
            self.cursor_hidden = hidden
            _hyprland_request(self.hypr_socket, f"keyword cursor:invisible {str(hidden).lower()}")

    def _pointer_target(self) -> Point:
        """Fly to the current step's on-screen spot, otherwise tag along with the mouse."""
        step = self._current_step()
        if step and not self.thinking:
            spot = self._step_spot(step)
            if spot:
                return spot
        return follow_point(self.cursor)

    def _step_spot(self, step: Step) -> Point | None:
        _scale, width, height = self.geometry
        return to_logical(step.x, step.y, 1.0, width, height)

    def _cursor_position(self) -> Point:
        reply = _hyprland_request(self.hypr_socket, "j/cursorpos")
        if reply:
            try:
                position = json.loads(reply)
                return float(position["x"]), float(position["y"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass
        return self.cursor

    def _read_monitors(self) -> list[Monitor]:
        self.monitors_read_at = time.monotonic()
        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return parse_monitors(json.loads(result.stdout))
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return self.monitors

    # --- asking -----------------------------------------------------------------

    def _panel_open(self) -> bool:
        return bool(self.panel and self.panel.get_visible())

    def _show_ask_box(self) -> None:
        if self.thinking:
            self._show_toast("Clicky is still thinking. Super+Shift+G stops it.")
            return
        assert self.panel and self.layout and self.window
        self._hide_reply()
        # Remember the project now, while the user's own window still has focus.
        self.ask_project = focused_project_dir()
        self.ask_area = None
        self.panel_anchor = follow_point(self.cursor)
        self.panel.open(self.history.entries)
        self.panel.set_visible(True)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
        self._place_panel()
        self.panel.entry.grab_focus()

    def _place_panel(self) -> bool:
        if not (self.panel and self.layout and self._panel_open()):
            return False
        _scale, width, height = self.geometry
        size = self.panel.preferred_size()
        x, y = bubble_origin(self.panel_anchor, size, (width, height))
        self.layout.move(self.panel, x, y)
        self._set_input_region(round(x), round(y), *size)
        return False

    def _select_area(self) -> None:
        if self.thinking:
            self._show_toast("Clicky is still thinking. Super+Shift+G stops it.")
            return

        def pick() -> None:
            try:
                result = subprocess.run(["slurp"], capture_output=True, text=True, timeout=120)
                area = parse_geometry(result.stdout) if result.returncode == 0 else None
            except (FileNotFoundError, subprocess.SubprocessError):
                area = None
            GLib.idle_add(self._area_selected, area)

        # slurp waits for the drag, so it must not run on the drawing thread.
        threading.Thread(target=pick, daemon=True).start()

    def _area_selected(self, area: tuple[int, int, int, int] | None) -> bool:
        if not area or area[2] < 4 or area[3] < 4:
            self._show_toast("No area selected.")
            return False
        self._show_ask_box()
        self.ask_area = (*map(round, self.monitor.to_local(area[:2])), area[2], area[3])
        self._show_toast("Ask about the highlighted area.")
        return False

    def _submit_question(self, question: str) -> None:
        self._hide_ask_box()
        project = self.ask_project
        # Every question may turn into a task Claude keeps working on until it says done.
        self.task_goal = question
        self.task_project = project
        self.task_round = 0
        area = self.ask_area
        self.ask_area = None
        prompt = build_prompt(question, project_dir=project, can_edit=project is not None, web=True, area=area is not None)
        self._start_request(prompt, project, can_edit=project is not None, label=question, area=area)

    def _pick_history(self, entry: Entry) -> None:
        self._hide_ask_box()
        self.task_goal = None
        self._present_reply(entry.reply, ANSWER_DISMISS_SECONDS)

    def _explain_selection(self) -> None:
        if self.thinking:
            self._show_toast("Clicky is still thinking.")
            return
        selection = selection_text()
        if not selection:
            self._show_toast("Highlight some text first.")
            return
        project = focused_project_dir()
        question = "Explain the highlighted text. If it contains a bug or error, say how to fix it."
        self.task_goal = None
        self._start_request(
            build_prompt(question, selection=selection, project_dir=project, web=True), project, label="Explain selection"
        )

    def _start_request(
        self,
        prompt: str,
        project: Path | None,
        can_edit: bool = False,
        label: str = "",
        area: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._hide_reply()
        self.thinking = True
        self.request_label = label
        self.request_seq += 1
        self._run_question(
            prompt,
            self._receive_reply,
            area=area,
            continue_session=self.conversation_started,
            project_dir=project,
            can_edit=can_edit,
            web=True,
            model=self.settings.get("model"),
        )

    def _task_active(self) -> bool:
        return bool(self.task_goal and self.reply and not self.reply.done)

    def _continue_task(self) -> None:
        if self.task_round + 1 >= TASK_MAX_ROUNDS:
            self.task_goal = None
            self._hide_reply()
            self._show_toast(f"Stopped after {TASK_MAX_ROUNDS} rounds.")
            return
        self.task_round += 1
        goal, project = self.task_goal or "", self.task_project
        self._hide_reply()
        self.thinking = True
        seq = self.request_seq

        def go() -> bool:
            if seq == self.request_seq and self.task_goal == goal:
                prompt = build_prompt(goal, continuing=True, project_dir=project, can_edit=project is not None, web=True)
                self._start_request(prompt, project, can_edit=project is not None, label=f"↻ {goal}")
            return False

        GLib.timeout_add(TASK_SETTLE_MS, go)

    def _stop(self) -> None:
        """Abandon whatever Clicky is working on; its late reply will be ignored."""
        self.request_seq += 1
        self.thinking = False
        self.task_goal = None
        self._hide_reply()
        self._show_toast("Stopped.")

    def _undo_last_edit(self) -> None:
        if not self.last_edit:
            self._show_toast("Nothing to undo.")
            return
        project, changed, backup = self.last_edit
        self.last_edit = None
        restored = undo_edits(project, changed, backup)
        self.mood.trigger("answer", time.monotonic())
        self._show_toast(f"Undid Clicky's changes to {len(restored)} file{'s' if len(restored) != 1 else ''}.")

    def _remember_edit(self, project: Path, changed: list[str], backup: dict[str, bytes]) -> bool:
        self.last_edit = (project, changed, backup)
        return False

    def _copy_snippet(self) -> None:
        if not self.reply or not self.reply.copy:
            self._show_toast("Nothing to copy.")
            return
        try:
            copy_to_clipboard(self.reply.copy)
        except ActionError as error:
            self._show_toast(str(error))
            return
        self._show_toast("Copied!")

    def _arm_auto_do(self) -> None:
        """Steps with an action do themselves after a short, cancellable countdown; mid-task
        steps without one move on by themselves after a moment."""
        step = self._current_step()
        countdown = self.settings.get("countdown")
        if step and step.do and countdown > 0:
            self.auto_kind = "do"
            delay = min(TASK_STEP_SECONDS, countdown) if self.task_round > 0 else countdown
        elif step and self._task_active():
            self.auto_kind = "advance"
            delay = TASK_READ_SECONDS
        else:
            self.auto_do_at = None
            return
        self.auto_do_at = time.monotonic() + delay

    def _do_step(self) -> None:
        self.auto_do_at = None
        step = self._current_step()
        if not step or not step.do:
            self._show_toast("Nothing to do for this step.")
            return
        # The real cursor must be visible and Clicky out of the way before acting.
        self._hide_system_cursor(False)
        if step.x is not None and step.y is not None:
            x, y = self.monitor.to_global((step.x, step.y))
            step = replace(step, x=round(x), y=round(y))
        try:
            perform(step)
        except ActionError as error:
            self._show_toast(str(error))
            return
        self._next_step()

    def _show_toast(self, message: str) -> None:
        self.toast = message
        self.toast_until = time.monotonic() + TOAST_SECONDS

    def _hide_ask_box(self) -> None:
        assert self.panel and self.window
        self.panel.set_visible(False)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        self._make_click_through()

    def _run_question(
        self,
        prompt: str,
        callback: Callable[[Reply, int], bool],
        hide_self: bool = True,
        area: tuple[int, int, int, int] | None = None,
        **options: object,
    ) -> None:
        seq = self.request_seq

        def worker() -> None:
            started = time.monotonic()
            screenshot: Path | None = None
            project = options.get("project_dir") if options.get("can_edit") else None
            monitor = self.monitor
            try:
                try:
                    # Screenshot exactly the overlay's monitor so image pixels equal its coordinates.
                    shot_area = (*map(round, monitor.to_global(area[:2])), area[2], area[3]) if area else None
                    screenshot = capture(shot_area, output=None if area else monitor.name or None)
                finally:
                    if hide_self:
                        GLib.idle_add(self._end_capture)
                before = project_snapshot(project) if isinstance(project, Path) else {}
                backup = dirty_contents(project) if isinstance(project, Path) else {}
                reply = ask_with_fallback(prompt, screenshot, **options)
                if area:
                    reply = offset_steps(reply, area[0], area[1])
                if isinstance(project, Path):
                    changed = changed_files(before, project_snapshot(project))
                    reply = replace(reply, changed=changed)
                    if changed:
                        GLib.idle_add(self._remember_edit, project, changed, backup)
            except BrainError as error:
                reply = Reply(say=str(error), kind="failure")
            finally:
                if screenshot:
                    screenshot.unlink(missing_ok=True)
            _log(
                f"{callback.__name__} {time.monotonic() - started:.1f}s steps={len(reply.steps)} "
                f"done={reply.done} changed={reply.changed} {reply.say[:120]!r}"
            )
            GLib.idle_add(callback, reply, seq)

        if not hide_self:
            threading.Thread(target=worker, daemon=True).start()
            return
        # Clicky hides itself for the screenshot so Claude only sees your screen.
        self.capturing = True
        if self.canvas:
            self.canvas.queue_draw()
        GLib.timeout_add(CAPTURE_SETTLE_MS, lambda: threading.Thread(target=worker, daemon=True).start())

    def _end_capture(self) -> bool:
        self.capturing = False
        return False

    def _receive_reply(self, reply: Reply, seq: int) -> bool:
        if seq != self.request_seq:
            return False  # stopped, or superseded by a newer request
        self.thinking = False
        self.conversation_started = True
        self.history.add(self.request_label, reply)
        now = time.monotonic()
        if reply.kind == "failure":
            self.mood.trigger("error", now)
        elif reply.done and self.task_round > 0:
            self.mood.trigger("done", now)
        else:
            self.mood.trigger("answer", now)
        if reply.done:
            self.task_goal = None
        self._present_reply(reply, ANSWER_DISMISS_SECONDS)
        if self.task_goal and not reply.steps:
            self._continue_task()
        return False

    def _present_reply(self, reply: Reply, dismiss_seconds: int) -> None:
        self.reply = reply
        self.step_index = 0
        # Walkthroughs stay up until you step through them; plain answers fade.
        if reply.steps:
            self._cancel_dismiss()
        else:
            self._schedule_dismiss(dismiss_seconds)
        self._arm_auto_do()

    def _next_step(self) -> None:
        if not self.reply or self.step_index + 1 >= len(self.reply.steps):
            if self._task_active():
                self._continue_task()
                return
            self.task_goal = None
            self._hide_reply()
            return
        self.step_index += 1
        self._arm_auto_do()

    def _hide_reply(self) -> None:
        self.auto_do_at = None
        self.reply = None
        self.step_index = 0
        self._cancel_dismiss()

    def _show_message(self, message: str) -> None:
        self.reply = Reply(say=message)
        self.step_index = 0
        self._schedule_dismiss(STATUS_DISMISS_SECONDS)

    def _watch_tick(self) -> bool:
        busy = self.thinking or self.watch_busy or self.reply or self.task_goal or self._panel_open()
        if not self.watch_enabled or busy:
            return True
        try:
            thumbnail = capture_thumbnail()
        except BrainError:
            return True
        now = time.monotonic()
        screen_changed = changed(self.last_thumbnail, thumbnail)
        self.last_thumbnail = thumbnail
        reason = self.watch_policy.should_ask(now, screen_changed)
        if reason:
            project = focused_project_dir()
            # Background checks stay invisible: no dots, and the pointer never blinks out.
            self.watch_busy = True
            self._run_question(
                build_prompt(None, watch=True, paused=reason == "pause", project_dir=project),
                self._receive_watch_reply,
                hide_self=False,
                continue_session=False,
                model=self.settings.get("watch_model"),
                project_dir=project,
            )
        else:
            offer = self.watch_policy.on_quiet(now)
            if offer:
                self._present_reply(offer, WATCH_DISMISS_SECONDS)
        return True

    def _receive_watch_reply(self, reply: Reply, _seq: int) -> bool:
        self.watch_busy = False
        if self.thinking or self.reply or self.task_goal or self._panel_open():
            return False  # the user started something meanwhile; don't interrupt it
        shown = self.watch_policy.on_reply(reply, time.monotonic())
        if shown:
            self.mood.trigger("error" if shown.kind == "error" else "tip", time.monotonic())
            self._present_reply(shown, WATCH_DISMISS_SECONDS)
        return False

    def _schedule_dismiss(self, seconds: int) -> None:
        self._cancel_dismiss()
        self.dismiss_source = GLib.timeout_add_seconds(seconds, self._dismiss_timeout)

    def _cancel_dismiss(self) -> None:
        if self.dismiss_source is not None:
            GLib.source_remove(self.dismiss_source)
            self.dismiss_source = None

    def _dismiss_timeout(self) -> bool:
        _log("auto-dismiss")
        self.dismiss_source = None
        self._hide_reply()
        return False

    def _make_click_through(self, *_args: object) -> None:
        self._set_input_region(0, 0, 0, 0)

    def _set_input_region(self, x: int, y: int, width: int, height: int) -> None:
        assert self.window
        surface = self.window.get_surface()
        if surface is None:
            return
        region = cairo.Region()
        if width and height:
            region.union(cairo.Region(cairo.RectangleInt(x, y, width, height)))
        surface.set_input_region(region)

    # --- drawing ----------------------------------------------------------------

    def _current_step(self) -> Step | None:
        if self.reply and self.reply.steps:
            return self.reply.steps[self.step_index]
        return None

    def _draw(self, _area: Gtk.DrawingArea, context: cairo.Context, _width: int, _height: int) -> None:
        if self.capturing:
            return
        now = time.monotonic()
        step = self._current_step()
        spot = self._step_spot(step) if step and not self.thinking else None
        if spot:
            _draw_target_ring(context, spot, now)
        if self.ask_area and self._panel_open():
            _draw_area(context, self.ask_area)
        _draw_trail(context, self.trail)
        idle = not (self.thinking or self.reply or self._panel_open() or self.task_goal)
        asleep = idle and self.mood.asleep(now)
        scale, angle = self.mood.transform(now)
        _draw_pointer(context, self.pointer, scale, angle, alpha=0.55 if asleep else 1.0)
        if asleep:
            _draw_sleep(context, self.pointer, now)
        emoji = self.mood.emoji(now)
        if emoji:
            _draw_emoji(context, emoji, self.pointer, now - self.mood.event_at)
        if self.thinking:
            _draw_thinking(context, self.pointer, now)
            return

        _scale, width, height = self.geometry
        markup = self._bubble_markup(now)
        if markup:
            self._draw_bubble(context, markup, (width, height))
        if self.toast and now < self.toast_until:
            _draw_toast(context, self.toast, self.pointer, (width, height))

    def _bubble_markup(self, now: float) -> str | None:
        if not self.reply:
            return None
        escape = GLib.markup_escape_text
        parts = []
        step = self._current_step()
        if self.reply.say and (not step or self.step_index == 0):
            parts.append(f"<b>{escape(self.reply.say)}</b>" if step else escape(self.reply.say))
        if step:
            parts.append(escape(step.text))
            if step.do == "run" and step.input:
                # Always show exactly what Super+Shift+R would run.
                parts.append(f"<span font_family='monospace' background='#1e293b' foreground='#fde68a'> $ {escape(step.input[:SNIPPET_LIMIT])} </span>")
        if self.reply.changed:
            files = ", ".join(self.reply.changed[:6]) + (" …" if len(self.reply.changed) > 6 else "")
            parts.append(f"<span foreground='#86efac'>✎ Changed: {escape(files)}</span>")
        if self.reply.copy:
            snippet = self.reply.copy[:SNIPPET_LIMIT]
            parts.append(f"<span font_family='monospace' background='#1e293b' foreground='#bfdbfe'> {escape(snippet)} </span>")
        hints = []
        if step:
            total = len(self.reply.steps)
            action = "next" if self.step_index + 1 < total else "done"
            counting = step.do is not None and self.auto_do_at is not None
            # While counting down, the same key cancels instead of moving on.
            hints.append(f"{self.step_index + 1}/{total}" if counting else f"{self.step_index + 1}/{total} · {KEYS['next']} {action}")
            if step.do and self.auto_do_at is not None:
                verb = {"run": "Running", "type": "Typing", "click": "Clicking"}[step.do]
                left = max(1, math.ceil(self.auto_do_at - now))
                hints.append(f"{verb} in {left}… {KEYS['cancel']} cancel")
            elif step.do:
                verb = {"run": "run it", "type": "type it", "click": "click it"}[step.do]
                hints.append(f"{KEYS['do']} {verb}")
        if self.reply.copy:
            hints.append(f"{KEYS['copy']} copy")
        if self.reply.changed and self.last_edit:
            hints.append(f"{KEYS['undo']} undo")
        if self._task_active():
            hints.append(f"task round {self.task_round + 1}/{TASK_MAX_ROUNDS}")
        if hints:
            parts.append(f"<span size='small' alpha='65%'>{escape(' · '.join(hints))}</span>")
        return "\n".join(parts)

    def _draw_bubble(self, context: cairo.Context, markup: str, screen: tuple[int, int]) -> None:
        layout = PangoCairo.create_layout(context)
        layout.set_font_description(Pango.FontDescription.from_string("Sans 11"))
        layout.set_width((BUBBLE_WIDTH - 2 * BUBBLE_PADDING) * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_markup(markup, -1)
        _ink, logical = layout.get_pixel_extents()
        size = (BUBBLE_WIDTH, logical.height + 2 * BUBBLE_PADDING)
        x, y = bubble_origin(self.pointer, size, screen)

        context.set_source_rgba(0.05, 0.08, 0.12, 0.94)
        _rounded_rectangle(context, x, y, size[0], size[1], 12)
        context.fill_preserve()
        context.set_source_rgba(*ACCENT, 0.9)
        context.set_line_width(1.5)
        context.stroke()
        context.set_source_rgb(0.94, 0.97, 1.0)
        context.move_to(x + BUBBLE_PADDING, y + BUBBLE_PADDING)
        PangoCairo.show_layout(context, layout)


def _draw_pointer(
    context: cairo.Context, tip: Point, scale: float = 1.0, angle: float = 0.0, alpha: float = 1.0
) -> None:
    """Clicky itself: a glowing cursor-style triangle whose tip is the point. Bounces
    (scale) and wiggles (angle) pivot on the tip, so it never drifts off its target."""
    shape = [(0, 0), (0, 22), (6, 16.5), (16, 16)]
    context.save()
    context.translate(*tip)
    context.rotate(angle)
    context.scale(scale, scale)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    for spread, glow in ((7, 0.10), (4, 0.18)):
        _polygon(context, (0, 0), shape)
        context.set_source_rgba(*ACCENT, glow * alpha)
        context.set_line_width(spread * 2)
        context.stroke()
    _polygon(context, (0, 0), shape)
    context.set_source_rgba(*ACCENT, alpha)
    context.fill_preserve()
    context.set_source_rgba(1, 1, 1, alpha)
    context.set_line_width(1.5)
    context.stroke()
    context.restore()


def _draw_trail(context: cairo.Context, trail: deque[Point]) -> None:
    for index, (x, y) in enumerate(trail):
        fade = (index + 1) / (len(trail) + 1)
        context.new_path()
        context.set_source_rgba(*ACCENT, 0.35 * fade)
        context.arc(x + 4, y + 8, 2 + 4 * fade, 0, 2 * math.pi)
        context.fill()


def _draw_emoji(context: cairo.Context, emoji: str, tip: Point, age: float) -> None:
    """A little reaction that floats up beside Clicky and fades."""
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(Pango.FontDescription.from_string("Noto Color Emoji 14"))
    layout.set_text(emoji, -1)
    context.save()
    context.move_to(tip[0] + 16, tip[1] - 26 - age * 10)
    context.push_group()
    PangoCairo.show_layout(context, layout)
    context.pop_group_to_source()
    context.paint_with_alpha(max(0.0, 1 - age / 2))
    context.restore()


def _draw_sleep(context: cairo.Context, tip: Point, now: float) -> None:
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 9"))
    for index in range(2):
        phase = (now * 0.5 + index * 0.5) % 1.0
        layout.set_text("z", -1)
        context.set_source_rgba(*ACCENT, 0.9 * (1 - phase))
        context.move_to(tip[0] + 14 + index * 7 + phase * 6, tip[1] - 6 - phase * 18)
        PangoCairo.show_layout(context, layout)


def _draw_area(context: cairo.Context, area: tuple[int, int, int, int]) -> None:
    x, y, width, height = area
    context.new_path()
    context.set_source_rgba(*ACCENT, 0.12)
    context.rectangle(x, y, width, height)
    context.fill_preserve()
    context.set_source_rgba(*ACCENT, 0.9)
    context.set_line_width(2)
    context.set_dash([6, 4])
    context.stroke()
    context.set_dash([])


def _draw_toast(context: cairo.Context, text: str, tip: Point, screen: tuple[int, int]) -> None:
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 10"))
    layout.set_text(text, -1)
    _ink, logical = layout.get_pixel_extents()
    size = (logical.width + 24, logical.height + 12)
    x = max(8, min(tip[0] + 22, screen[0] - 8 - size[0]))
    y = max(8, tip[1] - size[1] - 10)
    context.set_source_rgba(*ACCENT, 0.95)
    _rounded_rectangle(context, x, y, size[0], size[1], size[1] / 2)
    context.fill()
    context.set_source_rgb(1, 1, 1)
    context.move_to(x + 12, y + 6)
    PangoCairo.show_layout(context, layout)


def _draw_thinking(context: cairo.Context, tip: Point, now: float) -> None:
    """A small pill of bouncing dots beside the pointer while Claude works."""
    x, y = tip[0] + 22, tip[1] + 20
    context.set_source_rgba(0.05, 0.08, 0.12, 0.92)
    _rounded_rectangle(context, x, y, 46, 22, 11)
    context.fill_preserve()
    context.set_source_rgba(*ACCENT, 0.9)
    context.set_line_width(1.5)
    context.stroke()
    for index in range(3):
        lift = max(0.0, math.sin(now * 6 - index * 0.9)) * 3
        context.set_source_rgba(*ACCENT, 0.55 + lift / 7)
        context.arc(x + 13 + index * 10, y + 11 - lift, 3, 0, 2 * math.pi)
        context.fill()


def _draw_target_ring(context: cairo.Context, spot: Point, now: float) -> None:
    phase = (now * 1.2) % 1.0
    context.new_path()
    context.set_source_rgba(*ACCENT, 0.9 * (1 - phase))
    context.set_line_width(3)
    context.arc(spot[0], spot[1], 10 + 22 * phase, 0, 2 * math.pi)
    context.stroke()
    context.set_source_rgba(*ACCENT, 0.35)
    context.arc(spot[0], spot[1], 6, 0, 2 * math.pi)
    context.fill()


def _polygon(context: cairo.Context, origin: Point, points: list[tuple[float, float]]) -> None:
    context.new_path()
    context.move_to(origin[0] + points[0][0], origin[1] + points[0][1])
    for px, py in points[1:]:
        context.line_to(origin[0] + px, origin[1] + py)
    context.close_path()


def _rounded_rectangle(
    context: cairo.Context, x: float, y: float, width: float, height: float, radius: float
) -> None:
    context.new_sub_path()
    context.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    context.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    context.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    context.arc(x + radius, y + radius, radius, math.pi, math.pi * 1.5)
    context.close_path()


def _log(message: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "clicky.log").open("a") as log:
            log.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


def _hyprland_socket_path() -> Path | None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime_dir or not signature:
        return None
    return Path(runtime_dir) / "hypr" / signature / ".socket.sock"


def _hyprland_request(path: Path | None, request: str) -> str | None:
    """Ask Hyprland over its IPC socket; much cheaper than spawning hyprctl every frame."""
    if path is None:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.2)
            client.connect(str(path))
            client.sendall(request.encode())
            chunks = []
            while chunk := client.recv(4096):
                chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")
    except OSError:
        return None


def run() -> None:
    ClickyApplication().run(None)
