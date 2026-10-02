"""Tests for the action.yml markdown formatter."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._helpers import make_repo
from wouldrun import cli
from wouldrun.pr_format import MARKER, format_markdown, format_merged_markdown, github_outputs, main


def _payload(event=None, workflows=None):
    return {
        "tool": "wouldrun",
        "version": "0.1.0",
        "event": event or {"name": "pull_request", "base_ref": "main"},
        "workflows": workflows or [],
    }


class FormatMarkdown(unittest.TestCase):
    def test_includes_marker_for_sticky_comment_lookup(self):
        out = format_markdown(_payload())
        self.assertTrue(out.startswith(MARKER))

    def test_no_workflows_says_so_without_a_table(self):
        out = format_markdown(_payload())
        self.assertIn("No workflow files found", out)
        self.assertNotIn("| Workflow |", out)

    def test_fires_and_skipped_render_as_a_table_row_each(self):
        workflows = [
            {
                "path": ".github/workflows/ci.yml",
                "name": "CI",
                "fires": True,
                "reasons": [
                    "branch `main`: matches `branches: ['main']`",
                    "`paths: ['src/**']` matches changed file `src/app.py`",
                ],
                "jobs": ["test"],
                "called_by": [],
                "parse_error": None,
            },
            {
                "path": ".github/workflows/docs.yml",
                "name": "Docs",
                "fires": False,
                "reasons": [
                    "branch `main`: matches `branches: ['main']`",
                    "`paths-ignore: ['src/**']` covers every changed file (['src/app.py'])",
                ],
                "jobs": [],
                "called_by": [],
                "parse_error": None,
            },
        ]
        out = format_markdown(_payload(workflows=workflows))
        self.assertIn("2 workflow(s), 1 would fire", out)
        self.assertIn("| CI | FIRES | `paths: ['src/**']` matches changed file `src/app.py` |", out)
        self.assertIn(
            "| Docs | SKIPPED | `paths-ignore: ['src/**']` covers every changed file (['src/app.py']) |",
            out,
        )

    def test_last_reason_wins_over_earlier_ones(self):
        # Mirrors evaluate.py: the deciding filter is always the last reason
        # appended, so the table's Reason column should show that one, not
        # whichever filter happened to be checked first.
        workflows = [
            {
                "path": ".github/workflows/x.yml",
                "name": "X",
                "fires": False,
                "reasons": ["first check passed", "second check failed, so this is why it's skipped"],
                "jobs": [],
                "called_by": [],
                "parse_error": None,
            }
        ]
        out = format_markdown(_payload(workflows=workflows))
        self.assertIn("second check failed, so this is why it's skipped", out)
        self.assertNotIn("| X | SKIPPED | first check passed |", out)

    def test_no_reasons_gives_an_empty_cell_not_a_crash(self):
        workflows = [
            {
                "path": ".github/workflows/x.yml",
                "name": None,
                "fires": True,
                "reasons": [],
                "jobs": [],
                "called_by": [],
                "parse_error": None,
            }
        ]
        out = format_markdown(_payload(workflows=workflows))
        self.assertIn("| .github/workflows/x.yml | FIRES |  |", out)

    def test_pipe_and_newline_in_a_reason_do_not_break_the_table(self):
        workflows = [
            {
                "path": ".github/workflows/x.yml",
                "name": "X | Y",
                "fires": False,
                "reasons": ["contains a | pipe\nand a newline"],
                "jobs": [],
                "called_by": [],
                "parse_error": None,
            }
        ]
        out = format_markdown(_payload(workflows=workflows))
        # Exactly one table row for this workflow -- a stray `|` or a real
        # newline from a reason would otherwise split it into extra rows or
        # break the table layout.
        rows = [line for line in out.splitlines() if line.startswith("| X")]
        self.assertEqual(len(rows), 1)
        self.assertIn("contains a \\| pipe and a newline", rows[0])

    def test_reports_a_parse_error_as_the_reason(self):
        workflows = [
            {
                "path": ".github/workflows/broken.yml",
                "name": ".github/workflows/broken.yml",
                "fires": False,
                "reasons": ["could not parse this workflow: bad indent"],
                "jobs": [],
                "called_by": [],
                "parse_error": "bad indent",
            }
        ]
        out = format_markdown(_payload(workflows=workflows))
        self.assertIn("could not parse this workflow: bad indent", out)


class Main(unittest.TestCase):
    def _run_main(self, argv, stdin_text=None):
        out = io.StringIO()
        old_stdin = sys.stdin
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        try:
            with contextlib.redirect_stdout(out):
                code = main(argv)
        finally:
            sys.stdin = old_stdin
        return code, out.getvalue()

    def test_reads_json_file_argument(self):
        path = Path(tempfile.mkdtemp()) / "wouldrun.json"
        path.write_text(json.dumps(_payload()), encoding="utf-8")
        code, out = self._run_main([str(path)])
        self.assertEqual(code, 0)
        self.assertIn(MARKER, out)

    def test_reads_stdin_when_given_dash(self):
        code, out = self._run_main(["-"], stdin_text=json.dumps(_payload()))
        self.assertEqual(code, 0)
        self.assertIn(MARKER, out)

    def test_reads_stdin_when_given_nothing(self):
        code, out = self._run_main([], stdin_text=json.dumps(_payload()))
        self.assertEqual(code, 0)
        self.assertIn(MARKER, out)


class GithubOutputs(unittest.TestCase):
    def _wf(self, path, fires):
        return {"path": path, "name": None, "fires": fires, "reasons": [], "jobs": [], "called_by": [], "parse_error": None}

    def test_something_fires(self):
        payload = _payload(
            workflows=[
                self._wf(".github/workflows/ci.yml", True),
                self._wf(".github/workflows/docs.yml", False),
                self._wf(".github/workflows/e2e.yml", True),
            ]
        )
        self.assertEqual(
            github_outputs(payload),
            "fires=true\n"
            "fired-count=2\n"
            'fired-workflows=[".github/workflows/ci.yml",".github/workflows/e2e.yml"]\n',
        )

    def test_nothing_fires(self):
        payload = _payload(workflows=[self._wf(".github/workflows/docs.yml", False)])
        self.assertEqual(github_outputs(payload), "fires=false\nfired-count=0\nfired-workflows=[]\n")

    def test_odd_path_stays_on_one_line(self):
        payload = _payload(workflows=[self._wf('.github/workflows/we\nird "x".yml', True)])
        lines = github_outputs(payload).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2], 'fired-workflows=[".github/workflows/we\\nird \\"x\\".yml"]')

    def test_main_appends_to_the_output_file_and_still_prints_markdown(self):
        tmp = Path(tempfile.mkdtemp())
        report = tmp / "wouldrun.json"
        report.write_text(json.dumps(_payload(workflows=[self._wf(".github/workflows/ci.yml", True)])), encoding="utf-8")
        output = tmp / "github_output"
        output.write_text("skip=false\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--github-output", str(output), str(report)])
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith(MARKER))
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            'skip=false\nfires=true\nfired-count=1\nfired-workflows=[".github/workflows/ci.yml"]\n',
        )


SINGLE = {
    "tool": "wouldrun",
    "version": "0.1.0",
    "event": {"name": "pull_request", "base_ref": "main", "changed_files": ["src/a.py"]},
    "workflows": [
        {
            "path": ".github/workflows/ci.yml",
            "name": "CI",
            "fires": True,
            "reasons": ["branch `main`: matches `branches: ['main']`", "`paths: ['src/**']` matches changed file `src/a.py`"],
            "jobs": ["test"],
            "called_by": [],
            "parse_error": None,
            "undetermined": False,
        },
        {
            "path": ".github/workflows/label.yml",
            "name": "Label | triage",
            "fires": False,
            "reasons": ["no `pull_request` trigger (this workflow listens for: pull_request_target)"],
            "jobs": [],
            "called_by": [],
            "parse_error": None,
            "undetermined": False,
        },
        {
            "path": ".github/workflows/x.yml",
            "name": None,
            "fires": False,
            "reasons": [],
            "jobs": [],
            "called_by": [],
            "parse_error": None,
            "undetermined": False,
        },
    ],
}

# What main() printed for SINGLE before reports could be merged.
SINGLE_MARKDOWN = (
    "<!-- wouldrun -->\n"
    "**wouldrun**: event `pull_request`, 3 workflow(s), 1 would fire\n"
    "\n"
    "| Workflow | Verdict | Reason |\n"
    "|---|---|---|\n"
    "| CI | FIRES | `paths: ['src/**']` matches changed file `src/a.py` |\n"
    "| Label \\| triage | SKIPPED | no `pull_request` trigger (this workflow listens for: pull_request_target) |\n"
    "| .github/workflows/x.yml | SKIPPED |  |\n"
)


def _cli_json(root, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main([str(root), *args, "--json"])
    assert code == 0, code
    return json.loads(out.getvalue())


class MergedReports(unittest.TestCase):
    """The Action's two passes on a pull_request run: the PR's workflows as
    pull_request, the base branch's as pull_request_target."""

    @classmethod
    def setUpClass(cls):
        pr_tree = make_repo(
            {
                ".github/workflows/ci.yml": "name: CI\non:\n  pull_request:\n    paths: ['src/**']\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/both.yml": "name: Both\non: [pull_request, pull_request_target]\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/label.yml": "name: Label\non:\n  pull_request_target:\n    paths: ['src/**']\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/docs.yml": "name: Docs\non:\n  pull_request_target:\n    paths: ['docs/**']\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/push.yml": "name: Push\non: push\njobs:\n  t:\n    runs-on: u\n",
            }
        )
        base_tree = make_repo(
            {
                ".github/workflows/both.yml": "name: Both\non: [pull_request, pull_request_target]\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/label.yml": "name: Label\non:\n  pull_request_target:\n    paths: ['src/**']\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/docs.yml": "name: Docs\non:\n  pull_request_target:\n    paths: ['docs/**']\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/push.yml": "name: Push\non: push\njobs:\n  t:\n    runs-on: u\n",
                ".github/workflows/gone.yml": "name: Gone\non: pull_request_target\njobs:\n  t:\n    runs-on: u\n",
            }
        )
        common = ["--base", "main", "--type", "synchronize", "--changed", "src/a.py"]
        cls.pr = _cli_json(pr_tree, "--event", "pull_request", *common)
        cls.target = _cli_json(base_tree, "--event", "pull_request_target", *common)
        cls.md = format_merged_markdown([cls.pr, cls.target])
        cls.rows = {line.split(" | ")[0][2:]: line for line in cls.md.splitlines() if line.startswith("| ")}

    def test_header_names_both_events(self):
        self.assertTrue(self.md.startswith(MARKER + "\n"))
        self.assertIn("events `pull_request` and `pull_request_target`, 6 workflow(s), 4 would fire", self.md)
        self.assertIn("| Workflow | Event | Verdict | Reason |", self.md)

    def test_pull_request_target_only_workflow_fires_and_says_so(self):
        self.assertEqual(
            self.rows["Label"],
            "| Label | pull_request_target | FIRES | `paths: ['src/**']` matches changed file `src/a.py` |",
        )

    def test_workflow_with_both_triggers_names_both(self):
        self.assertTrue(self.rows["Both"].startswith("| Both | pull_request, pull_request_target | FIRES |"))

    def test_pull_request_workflow_keeps_its_own_row(self):
        self.assertTrue(self.rows["CI"].startswith("| CI | pull_request | FIRES |"))

    def test_skipped_row_shows_the_event_it_listens_to(self):
        self.assertTrue(self.rows["Docs"].startswith("| Docs | pull_request_target | SKIPPED | `paths: ['docs/**']`"))

    def test_skipped_under_both_falls_back_to_the_first_event(self):
        self.assertTrue(self.rows["Push"].startswith("| Push | pull_request | SKIPPED | no `pull_request` trigger"))

    def test_base_only_workflow_still_shows(self):
        self.assertTrue(self.rows["Gone"].startswith("| Gone | pull_request_target | FIRES |"))


class MainWithSeveralReports(unittest.TestCase):
    def _files(self, *payloads):
        tmp = Path(tempfile.mkdtemp())
        paths = []
        for i, payload in enumerate(payloads):
            path = tmp / f"r{i}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(str(path))
        return tmp, paths

    def _main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_one_report_prints_what_it_always_did(self):
        tmp, paths = self._files(SINGLE)
        output = tmp / "github_output"
        code, out = self._main(["--github-output", str(output), *paths])
        self.assertEqual(code, 0)
        self.assertEqual(out, SINGLE_MARKDOWN)
        self.assertEqual(format_markdown(SINGLE), SINGLE_MARKDOWN)
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            'fires=true\nfired-count=1\nfired-workflows=[".github/workflows/ci.yml"]\n',
        )

    def test_two_reports_merge_and_count_a_workflow_once(self):
        target = {
            "event": {"name": "pull_request_target"},
            "workflows": [
                {"path": ".github/workflows/ci.yml", "name": "CI", "fires": True, "reasons": ["matched"]},
                {"path": ".github/workflows/label.yml", "name": "Label | triage", "fires": True, "reasons": ["matched"]},
            ],
        }
        tmp, paths = self._files(SINGLE, target)
        output = tmp / "github_output"
        code, out = self._main(["--github-output", str(output), *paths])
        self.assertEqual(code, 0)
        self.assertIn("3 workflow(s), 2 would fire", out)
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            "fires=true\nfired-count=2\n"
            'fired-workflows=[".github/workflows/ci.yml",".github/workflows/label.yml"]\n',
        )

    def test_workflow_names_filter_every_report(self):
        target = {
            "event": {"name": "pull_request_target"},
            "workflows": [{"path": ".github/workflows/label.yml", "name": "Label | triage", "fires": True, "reasons": ["matched"]}],
        }
        tmp, paths = self._files(SINGLE, target)
        output = tmp / "github_output"
        code, out = self._main(["--github-output", str(output), "--workflow", "label", *paths])
        self.assertEqual(code, 0)
        self.assertIn("1 workflow(s), 1 would fire", out)
        self.assertNotIn("| CI |", out)
        self.assertIn('fired-workflows=[".github/workflows/label.yml"]', output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
