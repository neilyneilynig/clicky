"""What the user is working on: the focused project and their highlighted text."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

PROJECT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod")
MAX_SELECTION_CHARS = 4000


def focused_project_dir() -> Path | None:
    """The project the focused window's innermost process is sitting in, if any."""
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"], check=True, capture_output=True, text=True, timeout=2
        )
        pid = int(json.loads(result.stdout)["pid"])
    except (FileNotFoundError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        return None
    leaf = deepest_descendant(pid, _children_by_parent())
    try:
        cwd = Path(f"/proc/{leaf}/cwd").resolve(strict=True)
    except OSError:
        return None
    return project_root(cwd)


def project_root(path: Path, stop: Path | None = None, home: Path | None = None) -> Path | None:
    """Nearest directory at or above path that looks like a project root (never home itself)."""
    home = home or Path.home()
    stop = stop or home.parent
    for candidate in (path, *path.parents):
        if candidate in (stop, home):
            return None
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return None


def deepest_descendant(pid: int, children: dict[int, list[int]]) -> int:
    """Follow the newest child at each level: terminal -> shell -> running program."""
    while children.get(pid):
        pid = max(children[pid])
    return pid


def _children_by_parent() -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            # The command name may contain spaces; fields after the closing paren are fixed.
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
    return children


def selection_text() -> str | None:
    """The currently highlighted text (Wayland primary selection), trimmed to a sane size."""
    try:
        result = subprocess.run(
            ["wl-paste", "--primary", "--no-newline"], capture_output=True, text=True, timeout=2
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    return text[:MAX_SELECTION_CHARS] or None


MAX_BACKUP_BYTES = 20 * 1024 * 1024


def _dirty_names(project: Path) -> list[str]:
    """Modified and untracked (non-ignored) files, relative to the project."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "ls-files", "--modified", "--others", "--exclude-standard", "-z"],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    return sorted(set(filter(None, result.stdout.decode(errors="replace").split("\0"))))


def project_snapshot(project: Path) -> dict[str, str]:
    """Content hashes of the project's modified and untracked files, to spot Claude's edits."""
    snapshot = {}
    for name in _dirty_names(project):
        try:
            snapshot[name] = hashlib.sha1((project / name).read_bytes()).hexdigest()
        except OSError:
            snapshot[name] = "missing"
    return snapshot


def dirty_contents(project: Path) -> dict[str, bytes]:
    """Copies of uncommitted work, so undoing Claude's edits can't lose the user's changes."""
    backup: dict[str, bytes] = {}
    total = 0
    for name in _dirty_names(project):
        try:
            data = (project / name).read_bytes()
        except OSError:
            continue
        total += len(data)
        if total > MAX_BACKUP_BYTES:
            break
        backup[name] = data
    return backup


def undo_edits(project: Path, changed: list[str], backup: dict[str, bytes]) -> list[str]:
    """Put changed files back: saved copy if it was dirty, last commit if clean, else delete."""
    restored = []
    for name in changed:
        path = project / name
        if name in backup:
            path.write_bytes(backup[name])
        elif _tracked(project, name):
            subprocess.run(["git", "-C", str(project), "checkout", "--", name], check=False, capture_output=True, timeout=10)
        else:
            path.unlink(missing_ok=True)
        restored.append(name)
    return restored


def _tracked(project: Path, name: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project), "ls-files", "--error-unmatch", "--", name], capture_output=True, timeout=5
    )
    return result.returncode == 0


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(name for name, digest in after.items() if before.get(name) != digest)
