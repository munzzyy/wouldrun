"""Tests for the action.yml markdown formatter."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from wouldrun.pr_format import MARKER, format_markdown, github_outputs, main


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


if __name__ == "__main__":
    unittest.main()
