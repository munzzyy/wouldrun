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

    def test_diff_uses_merge_base_not_two_dot(self):
        # `git diff BASE` is a two-dot diff: once BASE advances past where this
        # branch forked, commits added to BASE show up as "changed files" the
        # branch never touched. GitHub evaluates PR path filters against the
        # PR's own changes (merge-base semantics), so wouldrun must too.
        root = _make_git_repo()  # main has a.txt + src/app.py
        _git(root, "checkout", "-q", "-b", "feature")
        (Path(root) / "src" / "app.py").write_text("print(3)\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "feature edits src")
        # main advances with an unrelated file after the fork point.
        _git(root, "checkout", "-q", "main")
        (Path(root) / "a.txt").write_text("changed on main\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "main drifts")
        _git(root, "checkout", "-q", "feature")
        changed = changed_files_from_diff("main", repo_root=str(root))
        # The branch only touched src/app.py; base drift on a.txt must not leak.
        self.assertEqual(changed, ["src/app.py"])

    def test_diff_still_counts_uncommitted_tracked_changes(self):
        # Diffing against the merge-base *commit* (not the BASE...HEAD range)
        # keeps uncommitted working-tree edits in the result -- seeing those is
        # half the point of running wouldrun locally.
        root = _make_git_repo()
        _git(root, "checkout", "-q", "-b", "feature")
        (Path(root) / "a.txt").write_text("uncommitted edit\n", encoding="utf-8")
        changed = changed_files_from_diff("main", repo_root=str(root))
        self.assertIn("a.txt", changed)

    def test_non_ascii_path_comes_back_decoded_not_c_quoted(self):
        # git's default core.quotepath renders any non-ASCII byte as an octal
        # escape inside literal double quotes, e.g. "src/caf\303\251.py". That
        # string matches no filter pattern, so a workflow GitHub would run gets
        # reported SKIPPED.
        root = _make_git_repo()
        (Path(root) / "src" / "café.py").write_text("x = 1\n", encoding="utf-8")
        _git(root, "add", "-A")
        changed = changed_files_from_diff("HEAD", repo_root=str(root))
        self.assertIn("src/café.py", changed)
        self.assertFalse([p for p in changed if p.startswith('"')])
        self.assertFalse([p for p in changed if "\\" in p])

    def test_path_containing_a_newline_stays_one_entry(self):
        root = _make_git_repo()
        try:
            (Path(root) / "src" / "we\nird.py").write_text("x = 1\n", encoding="utf-8")
        except OSError:  # pragma: no cover - Windows rejects newlines in names
            self.skipTest("this filesystem does not allow a newline in a filename")
        _git(root, "add", "-A")
        changed = changed_files_from_diff("HEAD", repo_root=str(root))
        self.assertIn("src/we\nird.py", changed)

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
