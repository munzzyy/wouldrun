"""End-to-end CLI tests."""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wouldrun import cli
from tests._helpers import make_repo, workflow_repo

_HAVE_GIT = shutil.which("git") is not None


def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _run(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ListMode(unittest.TestCase):
    def test_list_shows_triggers(self):
        root = workflow_repo("ci.yml", "name: CI\non: [push, pull_request]\njobs:\n  b:\n    runs-on: u\n")
        code, out, _ = _run([str(root), "--list", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("push", out)
        self.assertIn("pull_request", out)

    def test_list_json(self):
        root = workflow_repo("ci.yml", "on: push\njobs:\n  b:\n    runs-on: u\n")
        code, out, _ = _run([str(root), "--list", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["workflows"]), 1)

    def test_list_no_workflows(self):
        root = make_repo({"README.md": "hi"})
        code, out, _ = _run([str(root), "--list", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("No workflow files found", out)


class EventMode(unittest.TestCase):
    def test_push_fires(self):
        root = workflow_repo("ci.yml", "on: push\njobs:\n  b:\n    runs-on: u\n")
        code, out, _ = _run([str(root), "--event", "push", "--ref", "refs/heads/main", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)

    def test_json_output_parses_and_has_reasons(self):
        root = workflow_repo("ci.yml", "on:\n  push:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n")
        code, out, _ = _run([str(root), "--event", "push", "--ref", "refs/heads/dev", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertFalse(payload["workflows"][0]["fires"])
        self.assertTrue(payload["workflows"][0]["reasons"])

    def test_changed_flag(self):
        root = workflow_repo(
            "ci.yml", "on:\n  push:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
        )
        code, out, _ = _run(
            [str(root), "--event", "push", "--ref", "refs/heads/main", "--changed", "src/a.py,docs/x.md", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)

    def test_changed_from_file(self):
        root = workflow_repo(
            "ci.yml", "on:\n  push:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
        )
        listfile = Path(tempfile.mkdtemp()) / "changed.txt"
        listfile.write_text("src/a.py\ndocs/x.md\n", encoding="utf-8")
        code, out, _ = _run(
            [str(root), "--event", "push", "--ref", "refs/heads/main", "--changed-from", str(listfile), "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)

    def test_changed_from_stdin(self):
        root = workflow_repo(
            "ci.yml", "on:\n  push:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
        )
        out = io.StringIO()
        err = io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("src/a.py\n")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(
                    [str(root), "--event", "push", "--ref", "refs/heads/main", "--changed-from", "-", "--no-color"]
                )
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out.getvalue())

    def test_exit_fires_zero_when_something_fires(self):
        root = workflow_repo("ci.yml", "on: push\njobs:\n  b:\n    runs-on: u\n")
        code, _, _ = _run([str(root), "--event", "push", "--ref", "refs/heads/main", "--exit-fires", "--no-color"])
        self.assertEqual(code, 0)

    def test_exit_fires_one_when_nothing_fires(self):
        root = workflow_repo("ci.yml", "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n")
        code, _, _ = _run([str(root), "--event", "push", "--ref", "refs/heads/main", "--exit-fires", "--no-color"])
        self.assertEqual(code, 1)

    def test_default_exit_is_always_zero_even_if_nothing_fires(self):
        root = workflow_repo("ci.yml", "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n")
        code, _, _ = _run([str(root), "--event", "push", "--ref", "refs/heads/main", "--no-color"])
        self.assertEqual(code, 0)


class RefResolution(unittest.TestCase):
    """--ref used to be a hardcoded refs/heads/main, so running wouldrun on a
    feature branch answered for main and said `branch main` while doing it."""

    def _repo_on_branch(self, branch):
        if not _HAVE_GIT:
            self.skipTest("git not available")
        root = workflow_repo(
            "ci.yml", "on:\n  push:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n"
        )
        _git(root, "init", "-q", "-b", branch)
        return root

    def test_explicit_ref_wins_and_is_not_annotated(self):
        root = self._repo_on_branch("feature/x")
        code, out, _ = _run([str(root), "--ref", "refs/heads/main", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)
        self.assertNotIn("no --ref given", out)

    def test_ref_comes_from_the_checked_out_branch(self):
        root = self._repo_on_branch("feature/x")
        code, out, _ = _run([str(root), "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED", out)
        self.assertIn("branch `feature/x`", out)
        self.assertIn("using the checked-out branch `refs/heads/feature/x`", out)

    def test_ref_falls_back_to_main_outside_a_git_repo(self):
        root = workflow_repo(
            "ci.yml", "on:\n  push:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n"
        )
        code, out, _ = _run([str(root), "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)
        self.assertIn("assuming `refs/heads/main`", out)

    def test_json_reports_where_the_ref_came_from(self):
        root = self._repo_on_branch("feature/x")
        code, out, _ = _run([str(root), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["event"]["ref"], "refs/heads/feature/x")
        self.assertEqual(payload["event"]["ref_source"], "git")

    def test_events_that_ignore_the_ref_get_no_note(self):
        root = self._repo_on_branch("feature/x")
        code, out, _ = _run([str(root), "--event", "pull_request", "--base", "main", "--no-color"])
        self.assertEqual(code, 0)
        self.assertNotIn("no --ref given", out)


class Errors(unittest.TestCase):
    def test_missing_target_directory(self):
        code, _, err = _run(["/no/such/path/xyz-wouldrun"])
        self.assertEqual(code, 2)
        self.assertIn("no such directory", err)

    def test_bad_diff_base_reports_clean_error(self):
        # `root` is a plain temp dir, not a git repo, so `git diff` fails
        # cleanly and cli.py should turn that into a message, not a traceback.
        root = workflow_repo("ci.yml", "on: push\njobs:\n  b:\n    runs-on: u\n")
        code, _, err = _run([str(root), "--diff", "main"])
        self.assertEqual(code, 2)
        self.assertIn("wouldrun:", err)

    def test_missing_changed_from_file(self):
        root = workflow_repo("ci.yml", "on: push\njobs:\n  b:\n    runs-on: u\n")
        code, _, err = _run([str(root), "--changed-from", "/no/such/file-xyz"])
        self.assertEqual(code, 2)


class MalformedWorkflowGlob(unittest.TestCase):
    def test_bad_char_range_degrades_gracefully_other_workflows_still_resolve(self):
        # `[z-a]` is a reversed character-class range: an invalid regex once
        # translated. SECURITY.md calls a workflow that crashes the whole
        # evaluator a vulnerability, not an ordinary bug, so this must not
        # take down workflows that have nothing to do with the bad one.
        root = make_repo(
            {
                ".github/workflows/broken.yml": (
                    "on:\n  push:\n    branches: ['[z-a]']\njobs:\n  b:\n    runs-on: u\n"
                ),
                ".github/workflows/ok.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
            }
        )
        code, out, err = _run([str(root), "--event", "push", "--ref", "refs/heads/main", "--no-color"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertNotIn("Traceback", out)
        self.assertIn("FIRES", out)
        self.assertIn("SKIPPED", out)


if __name__ == "__main__":
    unittest.main()
