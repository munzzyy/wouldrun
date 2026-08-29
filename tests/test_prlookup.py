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
            {"baseRefName": "main", "files": [{"path": "src/a.py"}, {"path": "docs/x.md"}]}
        )
        with _mock_gh(stdout=payload):
            base_ref, changed = pr_info(42, repo_root=".")
        self.assertEqual(base_ref, "main")
        self.assertEqual(changed, ["src/a.py", "docs/x.md"])

    def test_empty_files_list_is_fine(self):
        payload = json.dumps({"baseRefName": "main", "files": []})
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
        payload = json.dumps({"files": []})
        with _mock_gh(stdout=payload):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_files_that_are_not_a_list_is_rejected(self):
        payload = json.dumps({"baseRefName": "main", "files": "oops"})
        with _mock_gh(stdout=payload):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_non_dict_json_is_rejected(self):
        with _mock_gh(stdout=json.dumps(["not", "a", "dict"])):
            with self.assertRaises(PrLookupError):
                pr_info(1, repo_root=".")

    def test_malformed_file_entries_are_skipped_not_crashed_on(self):
        payload = json.dumps(
            {"baseRefName": "main", "files": [{"path": "src/a.py"}, {"no_path": True}, "oops"]}
        )
        with _mock_gh(stdout=payload):
            base_ref, changed = pr_info(1, repo_root=".")
        self.assertEqual(changed, ["src/a.py"])


if __name__ == "__main__":
    unittest.main()
