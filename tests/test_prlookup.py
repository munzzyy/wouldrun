"""Tests for the --pr convenience (a fixed-argv `gh pr view` wrapper)."""

import json
import subprocess
import unittest
import unittest.mock

from wouldrun.prlookup import PrLookupError, pr_info


def _mock_gh(stdout="", returncode=0, stderr=""):
    return unittest.mock.patch(
        "wouldrun.prlookup.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
        ),
    )


class PrInfo(unittest.TestCase):
    def test_returns_base_ref_and_changed_files(self):
        payload = json.dumps(
            {"baseRefName": "main", "files": [{"path": "src/a.py"}, {"path": "docs/x.md"}], "changedFiles": 2}
        )
        with _mock_gh(stdout=payload):
            base_ref, changed = pr_info(42, repo_root=".")
        self.assertEqual(base_ref, "main")
        self.assertEqual(changed, ["src/a.py", "docs/x.md"])

    def test_empty_files_list_is_fine(self):
        payload = json.dumps({"baseRefName": "main", "files": [], "changedFiles": 0})
        with _mock_gh(stdout=payload):
            base_ref, changed = pr_info(1, repo_root=".")
        self.assertEqual(base_ref, "main")
        self.assertEqual(changed, [])

    def test_non_numeric_pr_is_rejected(self):
        with self.assertRaises(PrLookupError):
            pr_info("not-a-number", repo_root=".")

    def test_zero_or_negative_pr_is_rejected(self):
        with self.assertRaises(PrLookupError):
            pr_info(0, repo_root=".")
        with self.assertRaises(PrLookupError):
            pr_info(-3, repo_root=".")

    def test_missing_repo_root_is_rejected(self):
        with self.assertRaises(PrLookupError):
            pr_info(1, repo_root="/no/such/directory/xyz")

    def test_gh_not_on_path_reports_cleanly(self):
        with unittest.mock.patch(
            "wouldrun.prlookup.subprocess.run", side_effect=FileNotFoundError()
        ):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_gh_timeout_reports_cleanly(self):
        with unittest.mock.patch(
            "wouldrun.prlookup.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["gh"], timeout=30),
        ):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_nonzero_exit_reports_stderr(self):
        with _mock_gh(returncode=1, stderr="no pull requests found for branch"):
            with self.assertRaises(PrLookupError) as ctx:
                pr_info(999, repo_root=".")
        self.assertIn("no pull requests found", str(ctx.exception))

    def test_unparseable_json_reports_cleanly(self):
        with _mock_gh(stdout="not json"):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_missing_base_ref_is_rejected(self):
        payload = json.dumps({"files": [], "changedFiles": 0})
        with _mock_gh(stdout=payload):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_files_that_are_not_a_list_is_rejected(self):
        payload = json.dumps({"baseRefName": "main", "files": "oops", "changedFiles": 0})
        with _mock_gh(stdout=payload):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_non_dict_json_is_rejected(self):
        with _mock_gh(stdout=json.dumps(["not", "a", "dict"])):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_malformed_file_entries_are_skipped_not_crashed_on(self):
        payload = json.dumps(
            {"baseRefName": "main", "files": [{"path": "src/a.py"}, {"no_path": True}, "oops"], "changedFiles": 1}
        )
        with _mock_gh(stdout=payload):
            base_ref, changed = pr_info(1, repo_root=".")
        self.assertEqual(changed, ["src/a.py"])

    def test_missing_changed_file_count_is_rejected(self):
        payload = json.dumps({"baseRefName": "main", "files": []})
        with _mock_gh(stdout=payload):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")


VIEW_ARGV = ["gh", "pr", "view", "7", "--json", "baseRefName,files,changedFiles"]
API_ARGV = ["gh", "api", "--paginate", "--slurp", "repos/{owner}/{repo}/pulls/7/files?per_page=100"]


def _done(stdout):
    return subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=stdout, stderr="")


def _view(paths, count):
    return _done(json.dumps({"baseRefName": "main", "files": [{"path": p} for p in paths], "changedFiles": count}))


def _pages(paths, size=100):
    return _done(json.dumps([[{"filename": p} for p in paths[i : i + size]] for i in range(0, len(paths), size)]))


class BigPullRequests(unittest.TestCase):
    """gh pr view stops at 100 files; the REST file list has the rest."""

    def _run(self, *results):
        with unittest.mock.patch("wouldrun.prlookup.subprocess.run", side_effect=list(results)) as run:
            try:
                return pr_info(7, repo_root="."), run
            except PrLookupError as e:
                return e, run

    def _argvs(self, run):
        for c in run.call_args_list:
            self.assertEqual(c.kwargs["cwd"], ".")
            self.assertEqual(c.kwargs["timeout"], 30)
            self.assertNotIn("shell", c.kwargs)
        return [c.args[0] for c in run.call_args_list]

    def test_complete_list_makes_one_call(self):
        paths = [f"f{i}" for i in range(100)]
        (base, changed), run = self._run(_view(paths, 100))
        self.assertEqual(changed, paths)
        self.assertEqual(self._argvs(run), [VIEW_ARGV])

    def test_truncated_list_is_read_again_from_the_api(self):
        paths = [f"src/f{i}.py" for i in range(178)]
        (base, changed), run = self._run(_view(paths[:100], 178), _pages(paths))
        self.assertEqual(base, "main")
        self.assertEqual(changed, paths)
        self.assertEqual(self._argvs(run), [VIEW_ARGV, API_ARGV])

    def test_api_list_still_short_raises_with_both_counts(self):
        paths = [f"f{i}" for i in range(3000)]
        err, run = self._run(_view(paths[:100], 3500), _pages(paths))
        self.assertIsInstance(err, PrLookupError)
        self.assertIn("3500", str(err))
        self.assertIn("3000", str(err))
        self.assertEqual(self._argvs(run), [VIEW_ARGV, API_ARGV])

    def test_api_failure_is_a_lookup_error(self):
        failed = subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="HTTP 404")
        err, run = self._run(_view(["a"], 2), failed)
        self.assertIsInstance(err, PrLookupError)
        self.assertIn("HTTP 404", str(err))

    def test_api_pages_that_are_not_lists_raise(self):
        err, run = self._run(_view(["a"], 2), _done(json.dumps({"message": "nope"})))
        self.assertIsInstance(err, PrLookupError)


if __name__ == "__main__":
    unittest.main()
