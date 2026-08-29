"""End-to-end CLI tests."""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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


class WorkflowSelector(unittest.TestCase):
    """--exit-fires over every workflow in a repo is a constant 0, because
    essentially every repo has one unfiltered trigger. --workflow narrows the
    question to the workflow you actually care about."""

    def _repo(self):
        return make_repo(
            {
                ".github/workflows/ci.yml": "name: CI\non: push\njobs:\n  b:\n    runs-on: u\n",
                ".github/workflows/e2e.yml": (
                    "name: End to end\non:\n  push:\n    branches: [release/*]\n"
                    "jobs:\n  b:\n    runs-on: u\n"
                ),
            }
        )

    def test_matches_the_workflow_name(self):
        code, out, _ = _run([str(self._repo()), "--ref", "refs/heads/main", "--workflow", "End to end", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("End to end", out)
        self.assertNotIn("CI", out)
        self.assertIn("1 workflow(s)", out)

    def test_matches_the_file_name(self):
        code, out, _ = _run([str(self._repo()), "--ref", "refs/heads/main", "--workflow", "e2e.yml", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("End to end", out)

    def test_matches_the_file_stem_case_insensitively(self):
        code, out, _ = _run([str(self._repo()), "--ref", "refs/heads/main", "--workflow", "E2E", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("End to end", out)

    def test_repeatable(self):
        code, out, _ = _run(
            [str(self._repo()), "--ref", "refs/heads/main", "--workflow", "ci.yml",
             "--workflow", "e2e.yml", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("2 workflow(s)", out)

    def test_no_match_exits_two_and_says_so(self):
        code, _, err = _run([str(self._repo()), "--ref", "refs/heads/main", "--workflow", "nope", "--no-color"])
        self.assertEqual(code, 2)
        self.assertIn("no workflow matches --workflow", err)

    def test_no_match_exits_two_even_with_exit_fires(self):
        # The dangerous reading of a typo: nothing left to evaluate, so
        # --exit-fires would otherwise report a confident "nothing fires".
        code, _, err = _run(
            [str(self._repo()), "--ref", "refs/heads/main", "--workflow", "nope", "--exit-fires", "--no-color"]
        )
        self.assertEqual(code, 2)
        self.assertIn("no workflow matches", err)

    def test_exit_fires_is_scoped_to_the_named_workflow(self):
        root = self._repo()
        # Unscoped, ci.yml fires on any push, so --exit-fires is always 0.
        code, _, _ = _run([str(root), "--ref", "refs/heads/main", "--exit-fires", "--no-color"])
        self.assertEqual(code, 0)
        # Scoped to e2e, which is gated on release/*, the answer is a real no.
        code, _, _ = _run(
            [str(root), "--ref", "refs/heads/main", "--workflow", "e2e.yml", "--exit-fires", "--no-color"]
        )
        self.assertEqual(code, 1)
        code, _, _ = _run(
            [str(root), "--ref", "refs/heads/release/1", "--workflow", "e2e.yml", "--exit-fires", "--no-color"]
        )
        self.assertEqual(code, 0)

    def test_filters_json_output_too(self):
        code, out, _ = _run([str(self._repo()), "--ref", "refs/heads/main", "--workflow", "ci.yml", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual([w["name"] for w in payload["workflows"]], ["CI"])

    def test_narrows_list_mode(self):
        code, out, _ = _run([str(self._repo()), "--list", "--workflow", "ci.yml", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("CI", out)
        self.assertNotIn("End to end", out)

    def test_a_reusable_workflow_still_resolves_through_a_caller_you_did_not_name(self):
        # The filter runs after evaluation, so naming only the reusable
        # workflow still shows it as reached by its caller.
        root = make_repo(
            {
                ".github/workflows/release.yml": (
                    "name: Release\non: push\njobs:\n"
                    "  deploy:\n    uses: ./.github/workflows/reusable.yml\n"
                ),
                ".github/workflows/reusable.yml": (
                    "on: workflow_call\njobs:\n  d:\n    runs-on: u\n"
                ),
            }
        )
        code, out, _ = _run(
            [str(root), "--ref", "refs/heads/main", "--workflow", "reusable.yml", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)
        self.assertIn("not matched directly, but reached anyway", out)


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

    def test_changed_from_non_utf8_file_reports_cleanly(self):
        # A list written by a Windows editor or a PowerShell redirect is
        # cp1252, not UTF-8. UnicodeDecodeError is a ValueError, not an
        # OSError, so it used to sail past the handler as a raw traceback.
        root = workflow_repo("ci.yml", "on: push\njobs:\n  b:\n    runs-on: u\n")
        listfile = Path(tempfile.mkdtemp()) / "cp1252.txt"
        listfile.write_bytes(b"src/caf\xe9.py\n")
        code, out, err = _run([str(root), "--changed-from", str(listfile)])
        self.assertEqual(code, 2)
        self.assertIn("could not read changed files", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("FIRES", out)

    def test_changed_from_file_with_a_bom_does_not_glue_it_to_the_first_path(self):
        root = workflow_repo(
            "ci.yml", "on:\n  push:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
        )
        listfile = Path(tempfile.mkdtemp()) / "bom.txt"
        listfile.write_bytes(b"\xef\xbb\xbfsrc/app.py\n")
        code, out, _ = _run([str(root), "--ref", "refs/heads/main", "--changed-from", str(listfile), "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)


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


class TriggeringWorkflow(unittest.TestCase):
    """--triggering-workflow feeds `on.workflow_run`'s `workflows:` check."""

    def _repo(self):
        return workflow_repo(
            "deploy.yml",
            "on:\n  workflow_run:\n    workflows: ['CI']\n    types: [completed]\n"
            "jobs:\n  b:\n    runs-on: u\n",
        )

    def test_matching_name_fires(self):
        root = self._repo()
        code, out, _ = _run(
            [str(root), "--event", "workflow_run", "--ref", "refs/heads/main", "--type", "completed",
             "--triggering-workflow", "CI", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)

    def test_non_matching_name_skips(self):
        root = self._repo()
        code, out, _ = _run(
            [str(root), "--event", "workflow_run", "--ref", "refs/heads/main", "--type", "completed",
             "--triggering-workflow", "Lint", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED", out)
        self.assertIn("is not in `workflows:", out)

    def test_missing_name_skips_with_a_clear_reason(self):
        root = self._repo()
        code, out, _ = _run(
            [str(root), "--event", "workflow_run", "--ref", "refs/heads/main", "--type", "completed", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED", out)
        self.assertIn("no `--triggering-workflow` given", out)


class FiresOnly(unittest.TestCase):
    def _repo(self):
        return make_repo(
            {
                ".github/workflows/ci.yml": "name: CI\non: push\njobs:\n  b:\n    runs-on: u\n",
                ".github/workflows/dispatch.yml": "name: Manual\non: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n",
            }
        )

    def test_hides_skipped_workflows(self):
        root = self._repo()
        code, out, _ = _run([str(root), "--event", "push", "--ref", "refs/heads/main", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED", out)

        code, out, _ = _run(
            [str(root), "--event", "push", "--ref", "refs/heads/main", "--fires-only", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)
        self.assertNotIn("SKIPPED", out)
        self.assertNotIn("Manual", out)

    def test_filters_json_output_too(self):
        root = self._repo()
        code, out, _ = _run([str(root), "--event", "push", "--ref", "refs/heads/main", "--fires-only", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual([w["name"] for w in payload["workflows"]], ["CI"])

    def test_combines_with_exit_fires(self):
        root = make_repo(
            {".github/workflows/dispatch.yml": "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n"}
        )
        code, out, _ = _run(
            [str(root), "--event", "push", "--ref", "refs/heads/main", "--fires-only", "--exit-fires", "--no-color"]
        )
        self.assertEqual(code, 1)
        self.assertNotIn("SKIPPED", out)

    def test_nothing_firing_says_so_not_no_workflows_found(self):
        # Filtering every SKIPPED workflow out of the listing must not read as
        # "there are no workflow files" -- the header still has to count them.
        root = make_repo(
            {".github/workflows/dispatch.yml": "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n"}
        )
        code, out, _ = _run(
            [str(root), "--event", "push", "--ref", "refs/heads/main", "--fires-only", "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("1 workflow(s), 0 would fire", out)
        self.assertIn("No workflow would fire", out)
        self.assertNotIn("No workflow files found", out)


class PrMode(unittest.TestCase):
    """--pr NUMBER shells out to `gh pr view` for the base branch and changed
    files instead of making the caller transcribe them by hand."""

    def _repo(self):
        return workflow_repo(
            "ci.yml",
            "on:\n  pull_request:\n    branches: [main]\n    paths: ['src/**']\n"
            "jobs:\n  b:\n    runs-on: u\n",
        )

    def _mock_gh(self, stdout="", returncode=0, stderr=""):
        return unittest.mock.patch(
            "wouldrun.prlookup.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
            ),
        )

    def test_pr_populates_base_and_changed_files_and_defaults_the_event(self):
        payload = json.dumps({"baseRefName": "main", "files": [{"path": "src/a.py"}]})
        with self._mock_gh(stdout=payload):
            code, out, _ = _run([str(self._repo()), "--pr", "42", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("FIRES", out)
        self.assertIn("event=pull_request", out)

    def test_pr_respects_an_explicit_event_override(self):
        payload = json.dumps({"baseRefName": "main", "files": []})
        with self._mock_gh(stdout=payload):
            code, out, _ = _run([str(self._repo()), "--pr", "42", "--event", "push", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("event=push", out)

    def test_pr_respects_an_explicit_base_override(self):
        payload = json.dumps({"baseRefName": "main", "files": []})
        with self._mock_gh(stdout=payload):
            code, out, _ = _run([str(self._repo()), "--pr", "42", "--base", "develop", "--json"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["event"]["base_ref"], "develop")

    def test_gh_missing_reports_cleanly(self):
        with unittest.mock.patch(
            "wouldrun.prlookup.subprocess.run", side_effect=FileNotFoundError()
        ):
            code, out, err = _run([str(self._repo()), "--pr", "42"])
        self.assertEqual(code, 2)
        self.assertIn("gh was not found on PATH", err)
        self.assertNotIn("Traceback", err)

    def test_gh_failure_reports_cleanly(self):
        with self._mock_gh(returncode=1, stderr="no pull requests found"):
            code, _, err = _run([str(self._repo()), "--pr", "42"])
        self.assertEqual(code, 2)
        self.assertIn("gh pr view 42 failed", err)

    def test_pr_and_diff_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as ctx:
            _run([str(self._repo()), "--pr", "42", "--diff", "main"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
