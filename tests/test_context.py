import tempfile
import unittest
from pathlib import Path

from clicky.context import deepest_descendant, project_root


class ProjectRootTests(unittest.TestCase):
    def test_walks_up_to_the_nearest_marker(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "app" / ".git").mkdir(parents=True)
            nested = Path(root) / "app" / "src" / "deep"
            nested.mkdir(parents=True)

            self.assertEqual(project_root(nested), Path(root) / "app")

    def test_returns_none_outside_a_project(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(project_root(Path(root), stop=Path(root).parent))


class HomeTests(unittest.TestCase):
    def test_home_itself_never_counts_as_a_project(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "package.json").touch()

            self.assertIsNone(project_root(Path(root), stop=Path(root).parent, home=Path(root)))


class DescendantTests(unittest.TestCase):
    def test_follows_the_newest_child_down(self):
        children = {10: [11, 12], 12: [20], 20: []}

        self.assertEqual(deepest_descendant(10, children), 20)

    def test_a_process_with_no_children_is_its_own_leaf(self):
        self.assertEqual(deepest_descendant(10, {}), 10)


from clicky.context import changed_files, project_snapshot


class ChangedFilesTests(unittest.TestCase):
    def test_reports_new_and_modified_files_only(self):
        before = {"a.py": "1", "b.py": "2"}
        after = {"a.py": "1", "b.py": "3", "c.py": "4"}

        self.assertEqual(changed_files(before, after), ["b.py", "c.py"])

    def test_snapshot_sees_edits_in_a_git_repo(self):
        import subprocess

        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", root], check=True)
            (Path(root) / "app.py").write_text("x = 1\n")
            before = project_snapshot(Path(root))
            (Path(root) / "app.py").write_text("x = 2\n")
            (Path(root) / "new.py").write_text("y = 1\n")

            self.assertEqual(changed_files(before, project_snapshot(Path(root))), ["app.py", "new.py"])


from clicky.context import dirty_contents, undo_edits


class UndoTests(unittest.TestCase):
    def test_restores_clean_dirty_and_new_files(self):
        import subprocess

        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            git = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "init", "-q", root], check=True)
            (repo / "clean.py").write_text("committed\n")
            (repo / "dirty.py").write_text("committed\n")
            subprocess.run(git + ["add", "."], check=True)
            subprocess.run(git + ["commit", "-qm", "init"], check=True)
            (repo / "dirty.py").write_text("my unsaved work\n")

            before = project_snapshot(repo)
            backup = dirty_contents(repo)
            (repo / "clean.py").write_text("claude edit\n")
            (repo / "dirty.py").write_text("claude edit\n")
            (repo / "new.py").write_text("claude made this\n")
            changed = changed_files(before, project_snapshot(repo))

            undo_edits(repo, changed, backup)

            self.assertEqual((repo / "clean.py").read_text(), "committed\n")
            self.assertEqual((repo / "dirty.py").read_text(), "my unsaved work\n")
            self.assertFalse((repo / "new.py").exists())
