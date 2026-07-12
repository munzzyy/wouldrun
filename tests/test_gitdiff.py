"""Tests for the --diff BASE convenience (a fixed-argv `git diff` wrapper)."""

import shutil
import subprocess
import unittest
from pathlib import Path

from wouldrun.gitdiff import GitDiffError, changed_files_from_diff
from tests._helpers import make_repo

_HAVE_GIT = shutil.which("git") is not None


def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _make_git_repo():
    root = make_repo({"a.txt": "one\n", "src/app.py": "print(1)\n"})
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


@unittest.skipUnless(_HAVE_GIT, "git not available")
class GitDiff(unittest.TestCase):
    def test_diff_against_head_with_no_changes_is_empty(self):
        root = _make_git_repo()
        self.assertEqual(changed_files_from_diff("HEAD", repo_root=str(root)), [])

    def test_diff_reports_modified_file(self):
        root = _make_git_repo()
        (Path(root) / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        changed = changed_files_from_diff("HEAD", repo_root=str(root))
        self.assertEqual(changed, ["src/app.py"])

    def test_diff_reports_new_untracked_staged_file(self):
        root = _make_git_repo()
        (Path(root) / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
        _git(root, "add", "-A")
        changed = changed_files_from_diff("HEAD", repo_root=str(root))
        self.assertIn("src/new.py", changed)

    def test_invalid_base_raises_clear_error_not_traceback(self):
        root = _make_git_repo()
        with self.assertRaises(GitDiffError):
            changed_files_from_diff("not-a-real-ref-xyz", repo_root=str(root))

    def test_flag_like_base_is_rejected(self):
        root = _make_git_repo()
        with self.assertRaises(GitDiffError):
            changed_files_from_diff("--upload-pack=x", repo_root=str(root))

    def test_empty_base_is_rejected(self):
        with self.assertRaises(GitDiffError):
            changed_files_from_diff("", repo_root=".")

    def test_missing_repo_root_is_rejected(self):
        with self.assertRaises(GitDiffError):
            changed_files_from_diff("HEAD", repo_root="/no/such/directory/xyz")


if __name__ == "__main__":
    unittest.main()
