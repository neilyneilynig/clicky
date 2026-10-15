"""The ask panel: recent conversation, quick actions and the question box."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .history import Entry
from .settings import COLORS, MAX_COUNTDOWN, MODELS, WATCH_LEVELS, Settings, css_rgb

PANEL_WIDTH = 380
HISTORY_SHOWN = 6
HISTORY_MAX_HEIGHT = 230

QUICK_ACTIONS = [
    ("Fix this error", "Find the error or failing test on my screen and fix it."),
    ("Explain screen", "Explain what is on my screen right now."),
    ("What next?", "What should I do next in what I'm working on?"),
    ("Commit message", "Write a git commit message for my current changes. Put it in copy."),
    ("Summarize", "Summarize what is on my screen in a few short points."),
]

_PANEL_CSS = """
.clicky-panel {
    background: rgba(13, 20, 31, 0.97);
    border: 1.5px solid rgba({accent}, 0.95);
    border-radius: 16px;
    padding: 10px;
    box-shadow: 0 0 0 4px rgba({accent}, 0.18), 0 10px 28px rgba(0, 0, 0, 0.5);
}
.clicky-history-item {
    background: rgba(30, 41, 59, 0.7);
    border-radius: 10px;
    padding: 6px 8px;
    color: #cbd5e1;
}
.clicky-history-item:hover { background: rgba({accent}, 0.25); }
.clicky-history-question { color: #93c5fd; font-weight: bold; }
.clicky-chip {
    background: rgba({accent}, 0.16);
    color: #bfdbfe;
    border: 1px solid rgba({accent}, 0.5);
    border-radius: 999px;
    padding: 2px 10px;
    min-height: 0;
    font-size: 12px;
}
.clicky-chip:hover { background: rgba({accent}, 0.4); color: white; }
.clicky-entry {
    background: rgba(2, 6, 23, 0.9);
    color: #eef4ff;
    caret-color: rgb({accent});
    border: 1px solid rgba({accent}, 0.6);
    border-radius: 12px;
    padding: 0 12px;
    min-height: 40px;
    font-size: 15px;
    box-shadow: none;
    outline: none;
}
.clicky-entry > text > placeholder { color: rgba(238, 244, 255, 0.45); }
.clicky-entry > image { color: rgb({accent}); margin-right: 8px; }
.clicky-panel-hint { color: rgba(203, 213, 225, 0.55); font-size: 11px; }
.clicky-settings label { color: #cbd5e1; }
.clicky-swatch { min-width: 22px; min-height: 22px; padding: 0; border-radius: 999px; border: 2px solid transparent; }
.clicky-swatch-selected { border-color: white; }
"""


def panel_css(accent: tuple[float, float, float]) -> str:
    # Plain replace, not str.format: the CSS is full of braces.
    return _PANEL_CSS.replace("{accent}", css_rgb(accent))


class AskPanel(Gtk.Box):
    """Emits on_submit(question) for typed or quick questions, on_pick(entry) for history."""

    def __init__(
        self,
        on_submit: Callable[[str], None],
        on_pick: Callable[[Entry], None],
        on_cancel: Callable[[], None],
        settings: Settings,
        on_setting: Callable[[str, object], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.on_submit = on_submit
        self.on_pick = on_pick
        self.on_cancel = on_cancel
        self.settings = settings
        self.on_setting = on_setting
        self.add_css_class("clicky-panel")
        self.set_size_request(PANEL_WIDTH, -1)

        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.history_scroll = Gtk.ScrolledWindow()
        self.history_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.history_scroll.set_max_content_height(HISTORY_MAX_HEIGHT)
        self.history_scroll.set_propagate_natural_height(True)
        self.history_scroll.set_child(self.history_box)
        self.append(self.history_scroll)

        chips = Gtk.FlowBox()
        chips.set_selection_mode(Gtk.SelectionMode.NONE)
        chips.set_max_children_per_line(5)
        chips.set_row_spacing(6)
        chips.set_column_spacing(6)
        for label, question in QUICK_ACTIONS:
            chip = Gtk.Button(label=label)
            chip.add_css_class("clicky-chip")
            chip.set_focus_on_click(False)
            chip.connect("clicked", lambda _button, q=question: self.on_submit(q))
            chips.append(chip)
        gear = Gtk.Button(label="⚙ Settings")
        gear.add_css_class("clicky-chip")
        gear.set_focus_on_click(False)
        gear.connect("clicked", lambda _button: self._toggle_settings())
        chips.append(gear)
        self.append(chips)

        self.settings_box = self._settings_view()
        self.settings_box.set_visible(False)
        self.append(self.settings_box)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Ask Clicky anything, or give it a task…")
        self.entry.add_css_class("clicky-entry")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "starred-symbolic")
        self.entry.connect("activate", self._activate)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.entry.add_controller(keys)
        self.append(self.entry)

        hint = Gtk.Label(label="Enter to send · Esc to close · click an old answer to see it again")
        hint.add_css_class("clicky-panel-hint")
        hint.set_xalign(0)
        self.append(hint)

    def open(self, entries: list[Entry]) -> None:
        while child := self.history_box.get_first_child():
            self.history_box.remove(child)
        recent = entries[-HISTORY_SHOWN:]
        for entry in recent:
            self.history_box.append(self._history_item(entry))
        self.history_scroll.set_visible(bool(recent))
        self.settings_box.set_visible(False)
        self.entry.set_text("")
        self.entry.grab_focus()

    def preferred_size(self) -> tuple[int, int]:
        _minimum, natural, _baseline_min, _baseline_nat = self.measure(Gtk.Orientation.VERTICAL, PANEL_WIDTH)
        return PANEL_WIDTH, natural

    def _toggle_settings(self) -> None:
        self.settings_box.set_visible(not self.settings_box.get_visible())
        self.on_setting("_resize", None)

    def _settings_view(self) -> Gtk.Grid:
        grid = Gtk.Grid(row_spacing=6, column_spacing=10)
        grid.add_css_class("clicky-settings")
        rows: list[tuple[str, Gtk.Widget]] = []

        countdown = Gtk.SpinButton.new_with_range(0, MAX_COUNTDOWN, 1)
        countdown.set_value(self.settings.get("countdown"))
        countdown.connect("value-changed", lambda spin: self.on_setting("countdown", spin.get_value_as_int()))
        rows.append(("Act after (s, 0 = ask me)", countdown))

        for key, label, options in (
            ("model", "Answer model", list(MODELS)),
            ("watch_model", "Watch model", list(MODELS)),
            ("watch_level", "Watch mode", list(WATCH_LEVELS)),
        ):
            dropdown = Gtk.DropDown.new_from_strings(options)
            dropdown.set_selected(options.index(self.settings.get(key)))
            dropdown.connect(
                "notify::selected", lambda d, _p, k=key, o=options: self.on_setting(k, o[d.get_selected()])
            )
            rows.append((label, dropdown))

        swatches = Gtk.Box(spacing=6)
        self.swatches: dict[str, Gtk.Button] = {}
        for name, rgb in COLORS.items():
            swatch = Gtk.Button()
            swatch.add_css_class("clicky-swatch")
            swatch.set_tooltip_text(name)
            provider = Gtk.CssProvider()
            provider.load_from_string(f"button {{ background: rgb({css_rgb(rgb)}); }}")
            swatch.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            swatch.connect("clicked", lambda _b, n=name: self._pick_color(n))
            swatches.append(swatch)
            self.swatches[name] = swatch
        self._mark_color(self.settings.get("color"))
        rows.append(("Color", swatches))

        for row, (label, widget) in enumerate(rows):
            text = Gtk.Label(label=label)
            text.set_xalign(0)
            grid.attach(text, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        return grid

    def _pick_color(self, name: str) -> None:
        self._mark_color(name)
        self.on_setting("color", name)

    def _mark_color(self, name: str) -> None:
        for swatch_name, swatch in self.swatches.items():
            if swatch_name == name:
                swatch.add_css_class("clicky-swatch-selected")
            else:
                swatch.remove_css_class("clicky-swatch-selected")

    def _history_item(self, entry: Entry) -> Gtk.Button:
        question = Gtk.Label(label=entry.question)
        question.add_css_class("clicky-history-question")
        answer = Gtk.Label(label=entry.reply.say or "(no answer)")
        for label in (question, answer):
            label.set_xalign(0)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(46)
            label.set_lines(2)
            label.set_ellipsize(Pango.EllipsizeMode.END)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        body.append(question)
        body.append(answer)
        button = Gtk.Button()
        button.set_child(body)
        button.add_css_class("clicky-history-item")
        button.set_focus_on_click(False)
        button.connect("clicked", lambda _button: self.on_pick(entry))
        return button

    def _activate(self, entry: Gtk.Entry) -> None:
        question = entry.get_text().strip()
        if question:
            self.on_submit(question)

    def _key_pressed(self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: int) -> bool:
        if keyval == 65307:  # Escape
            self.on_cancel()
            return True
        return False
