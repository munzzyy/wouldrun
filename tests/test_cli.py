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


class ExitFiresUndetermined(unittest.TestCase):
    """Under --exit-fires a workflow wouldrun cannot read is not a "no"."""

    TAB_BROKEN = "name: E2E\non:\n  pull_request:\n\tpaths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
    BAD_GLOB = "name: E2E\non:\n  pull_request:\n    paths: ['[z-a]']\njobs:\n  b:\n    runs-on: u\n"
    SRC_ONLY = "name: CI\non:\n  pull_request:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
    PR_ARGS = ["--event", "pull_request", "--base", "main", "--no-color"]

    def _repo(self, **workflows):
        return make_repo({f".github/workflows/{name}.yml": text for name, text in workflows.items()})

    def test_named_unparseable_workflow_exits_two_and_says_why(self):
        root = self._repo(e2e=self.TAB_BROKEN)
        code, _, err = _run(
            [str(root), *self.PR_ARGS, "--changed", "src/app.py", "--workflow", "e2e.yml", "--exit-fires"]
        )
        self.assertEqual(code, 2)
        self.assertIn(".github/workflows/e2e.yml", err)
        self.assertIn("tabs are not allowed", err)

    def test_named_workflow_with_a_bad_glob_exits_two(self):
        root = self._repo(e2e=self.BAD_GLOB)
        code, _, err = _run(
            [str(root), *self.PR_ARGS, "--changed", "src/app.py", "--workflow", "e2e.yml", "--exit-fires"]
        )
        self.assertEqual(code, 2)
        self.assertIn(".github/workflows/e2e.yml", err)
        self.assertIn("[z-a]", err)

    def test_an_unrelated_broken_workflow_does_not_change_the_named_answer(self):
        root = self._repo(broken=self.TAB_BROKEN, ci=self.SRC_ONLY)
        named = ["--workflow", "ci.yml", "--exit-fires"]
        code, _, err = _run([str(root), *self.PR_ARGS, "--changed", "src/app.py", *named])
        self.assertEqual((code, err), (0, ""))
        code, _, err = _run([str(root), *self.PR_ARGS, "--changed", "docs/x.md", *named])
        self.assertEqual((code, err), (1, ""))

    def test_repo_wide_broken_and_nothing_firing_exits_two(self):
        root = self._repo(broken=self.TAB_BROKEN, ci=self.SRC_ONLY)
        code, _, err = _run([str(root), *self.PR_ARGS, "--changed", "docs/x.md", "--exit-fires"])
        self.assertEqual(code, 2)
        self.assertIn(".github/workflows/broken.yml", err)

    def test_repo_wide_broken_but_another_fires_exits_zero(self):
        root = self._repo(broken=self.TAB_BROKEN, ci=self.SRC_ONLY)
        code, _, _ = _run([str(root), *self.PR_ARGS, "--changed", "src/app.py", "--exit-fires"])
        self.assertEqual(code, 0)

    def test_without_exit_fires_the_exit_stays_zero(self):
        root = self._repo(e2e=self.TAB_BROKEN)
        code, _, _ = _run([str(root), *self.PR_ARGS, "--changed", "src/app.py", "--workflow", "e2e.yml"])
        self.assertEqual(code, 0)

    def test_json_marks_undetermined_workflows(self):
        root = self._repo(broken=self.TAB_BROKEN, glob=self.BAD_GLOB, ci=self.SRC_ONLY)
        code, out, _ = _run([str(root), *self.PR_ARGS, "--changed", "src/app.py", "--json"])
        self.assertEqual(code, 0)
        flags = {w["path"]: w["undetermined"] for w in json.loads(out)["workflows"]}
        self.assertEqual(
            flags,
            {
                ".github/workflows/broken.yml": True,
                ".github/workflows/ci.yml": False,
                ".github/workflows/glob.yml": True,
            },
        )


class EmptyChangeSet(unittest.TestCase):
    TEXT = "on:\n  pull_request_target:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"

    def test_no_source_given_asks_for_one(self):
        root = workflow_repo("t.yml", self.TEXT)
        code, out, _ = _run([str(root), "--event", "pull_request_target", "--base", "main", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("no changed files given (use --changed/--changed-from/--diff)", out)

    def test_an_empty_source_says_nothing_changed(self):
        root = workflow_repo("t.yml", self.TEXT)
        empty = Path(tempfile.mkdtemp()) / "none.txt"
        empty.write_text("", encoding="utf-8")
        code, out, _ = _run(
            [str(root), "--event", "pull_request_target", "--base", "main", "--changed-from", str(empty), "--no-color"]
        )
        self.assertEqual(code, 0)
        self.assertIn("no files changed", out)
        self.assertNotIn("use --changed", out)

    def test_head_without_diff_is_a_usage_error(self):
        root = workflow_repo("t.yml", self.TEXT)
        code, _, err = _run([str(root), "--event", "pull_request_target", "--head", "feature"])
        self.assertEqual(code, 2)
        self.assertIn("--head only works together with --diff", err)


@unittest.skipUnless(_HAVE_GIT, "git not available")
class HeadOnABaseCheckout(unittest.TestCase):
    """pull_request_target checks out the base branch, so the PR's files only
    show up when the head is diffed by ref."""

    def test_pull_request_target_fires_with_head(self):
        origin = make_repo(
            {
                ".github/workflows/t.yml": (
                    "on:\n  pull_request_target:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
                ),
                "README.md": "hi\n",
            }
        )
        _git(origin, "init", "-q", "-b", "main")
        _git(origin, "config", "user.email", "test@example.com")
        _git(origin, "config", "user.name", "Test")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "base")
        _git(origin, "checkout", "-q", "-b", "feature")
        (origin / "src").mkdir()
        (origin / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "add src/a.py")
        _git(origin, "checkout", "-q", "main")
        clone = Path(tempfile.mkdtemp()) / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)

        args = [str(clone), "--event", "pull_request_target", "--base", "main", "--diff", "origin/main", "--json"]
        code, out, _ = _run(args)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["event"]["changed_files"], [])
        self.assertFalse(payload["workflows"][0]["fires"])
        self.assertIn("no files changed", payload["workflows"][0]["reasons"][-1])

        code, out, _ = _run(args + ["--head", "origin/feature"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["event"]["changed_files"], ["src/a.py"])
        self.assertTrue(payload["workflows"][0]["fires"])


_EXAMPLE_REPO = str(Path(__file__).resolve().parent.parent / "examples" / "example-repo")


class ChangedPathNormalization(unittest.TestCase):
    PUSH = [_EXAMPLE_REPO, "--event", "push", "--ref", "main", "--workflow", "ci", "--json"]

    def _fires(self, argv):
        code, out, err = _run(argv)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        return payload["workflows"][0]["fires"], payload["event"]["changed_files"], err

    def test_dot_slash_prefix_is_stripped_from_changed(self):
        fires, files, _ = self._fires(self.PUSH + ["--changed", "./src/app.py"])
        self.assertTrue(fires)
        self.assertEqual(files, ["src/app.py"])

    def test_dot_slash_prefix_is_stripped_from_changed_from(self):
        listing = Path(tempfile.mkdtemp()) / "changed.txt"
        listing.write_text("./src/app.py\n././docs/x.md\n", encoding="utf-8")
        fires, files, _ = self._fires(self.PUSH + ["--changed-from", str(listing)])
        self.assertTrue(fires)
        self.assertEqual(files, ["src/app.py", "docs/x.md"])

    def test_leading_slash_is_stripped_with_a_note(self):
        fires, files, err = self._fires(self.PUSH + ["--changed", "/src/app.py,/docs/x.md"])
        self.assertTrue(fires)
        self.assertEqual(files, ["src/app.py", "docs/x.md"])
        self.assertIn("dropped the leading '/' from '/src/app.py' and 1 more", err)

    def test_clean_paths_get_no_note(self):
        _, _, err = self._fires(self.PUSH + ["--changed", "src/app.py"])
        self.assertEqual(err, "")

    def test_backslashes_become_slashes_only_on_windows(self):
        self.assertEqual(cli.normalize_changed_path("src\\app.py", sep="\\"), ("src/app.py", False))
        self.assertEqual(cli.normalize_changed_path(".\\src\\app.py", sep="\\"), ("src/app.py", False))
        self.assertEqual(cli.normalize_changed_path("src\\app.py", sep="/"), ("src\\app.py", False))
        self.assertEqual(cli.normalize_changed_path("/src/app.py", sep="/"), ("src/app.py", True))


class BaseBranchSource(unittest.TestCase):
    TEXT = "on:\n  pull_request:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n"

    def test_missing_base_is_assumed_and_said(self):
        root = workflow_repo("ci.yml", self.TEXT)
        code, out, _ = _run([str(root), "--event", "pull_request", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("no --base given; assuming `main`", out)
        code, out, _ = _run([str(root), "--event", "pull_request", "--json"])
        event = json.loads(out)["event"]
        self.assertEqual((event["base_ref"], event["base_ref_source"]), ("main", "default"))

    def test_given_base_is_not_annotated(self):
        root = workflow_repo("ci.yml", self.TEXT)
        code, out, _ = _run([str(root), "--event", "pull_request", "--base", "dev", "--no-color"])
        self.assertNotIn("no --base given", out)
        code, out, _ = _run([str(root), "--event", "pull_request", "--base", "dev", "--json"])
        event = json.loads(out)["event"]
        self.assertEqual((event["base_ref"], event["base_ref_source"]), ("dev", "flag"))

    def test_push_gets_no_base_note(self):
        root = workflow_repo("ci.yml", self.TEXT)
        _, out, _ = _run([str(root), "--event", "push", "--ref", "main", "--no-color"])
        self.assertNotIn("no --base given", out)


class EventNameWarnings(unittest.TestCase):
    def _root(self):
        return workflow_repo("ci.yml", "on: [push, pull_request]\njobs:\n  b:\n    runs-on: u\n")

    def test_typo_suggests_the_close_match(self):
        code, _, err = _run([str(self._root()), "--event", "pul_request", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("`pul_request` is not a GitHub event wouldrun knows; did you mean `pull_request`?", err)

    def test_type_is_ignored_for_push(self):
        code, _, err = _run([str(self._root()), "--event", "push", "--ref", "main", "--type", "opened"])
        self.assertEqual(code, 0)
        self.assertIn("--type is ignored for `push`", err)

    def test_known_events_get_no_warning(self):
        for argv in (["--event", "pull_request", "--type", "labeled"], ["--event", "repository_dispatch", "--type", "x"],
                     ["--event", "workflow_dispatch"], ["--event", "push", "--ref", "main"]):
            _, _, err = _run([str(self._root()), *argv, "--no-color"])
            self.assertEqual(err, "", argv)


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
