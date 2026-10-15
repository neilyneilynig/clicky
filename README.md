# Hey Clicky

Hey Clicky is an on-screen coding assistant for Hyprland (one or more monitors; it follows your mouse). Ask
it a question with a hotkey; it screenshots your current screen, asks your signed-in
Claude Code CLI for help, then a blue pointer flies to each relevant spot on screen
and walks you through the steps.

## Keys

Clicky replaces your mouse cursor with a blue pointer (Hyprland's own cursor is hidden
while Clicky runs and comes back whenever Clicky flies off to point at something).

- `Super+Shift+C` or `Super+Shift+H`: open the ask panel: recent questions and answers
  (click one to see it again), quick actions (Fix this error, Explain screen, What next?,
  Commit message, Summarize) and the question box
- `Super+Shift+G`: next step (after the last step, Clicky returns to your cursor), or
  cancel a pending action
- `Super+Shift+V`: drag a box around part of the screen, then ask about just that area
- `Super+Shift+Z`: undo Clicky's last code edit
- `Super+Shift+I`: explain or fix the text you have highlighted
- `Super+Shift+S`: copy the answer's code snippet or command
- `Super+Shift+R`: do the current step right now: click there, type its text, or run its
  command (the exact command is always shown in the bubble first)

Questions can be whole tasks ("open YouTube and play lofi"): Clicky acts, looks at the
screen again and keeps going until Claude says it's done (at most 10 rounds). Press
`Super+Shift+G` while it's thinking to stop. When asked from inside a project, Claude may
also edit that project's files; the bubble lists what changed.

Questions can search the web and read pages when the answer needs current information.
The panel's ⚙ Settings button sets the countdown (0 = always wait for `Super+Shift+R`),
the answer and watch models, how chatty watch mode is, and Clicky's colour. Settings live
in `~/.local/state/clicky/settings.json`.

Clicky bounces when an answer arrives, wiggles on errors, shows little emoji reactions,
leaves a trail when it flies, and dozes off after five idle minutes.

Steps with an action do themselves after a 3-second countdown ("Running in 3…") unless
you press `Super+Shift+G` to cancel.
- `Super+Shift+W`: toggle watch mode

When the focused terminal is inside a project, Claude may read its files and run
`git -C <project> status/diff/log/show`, `ls` and its test command to answer better.
Nothing else is allowed.

Watch mode checks for screen changes every 10 seconds and asks Claude (Sonnet) at most
every 20 seconds, quietly in the background. It also looks at the focused project's diff
and tests. Errors show immediately and point at the problem, with fix steps that run
after the countdown when Claude is confident; if the same error is still there 3 minutes
later Clicky offers help; tips appear at most every 2 minutes; and when you pause for 30
seconds it suggests your next step.

Clicking and typing need `wtype` and `wlrctl` (currently built from source into
`~/.local/bin`; `yay -S wtype wlrctl` gives packaged versions).

## Commands

Install the user-level Linux app:

```sh
cd ~/Projects/clicky
./install.sh
```

Then start the overlay once per desktop session:

```sh
heycliky run
```

From another terminal or a Hyprland binding:

```sh
heycliky ask
heycliky next
heycliky watch toggle
heycliky hide
heycliky quit
```

Clicky sends screenshots to your locally installed Claude Code CLI. If Claude Code
fails, it retries with the signed-in GitHub Copilot CLI (with no tools allowed) and the
same screenshot. Requests are logged to `~/.local/state/clicky/clicky.log`. Walkthroughs stay on screen until you
step through them; plain answers fade after 30 seconds and watch tips after 12.

Run `CLICKY_SIMULATE=1 heycliky run` to see a canned walkthrough without calling Claude.

## Tests

```sh
cd ~/Projects/clicky
/usr/bin/python3 -m unittest discover -s tests -v
```
