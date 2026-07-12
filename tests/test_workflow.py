"""Tests for wouldrun.workflow: turning YAML into a Workflow object."""

import unittest

from wouldrun.workflow import Job, Workflow, _extract_on, parse_workflow


class OnShorthand(unittest.TestCase):
    def test_string_shorthand(self):
        wf = parse_workflow("x.yml", "on: push\njobs:\n  b:\n    runs-on: ubuntu-latest\n")
        self.assertIsNone(wf.parse_error)
        self.assertEqual(wf.triggers, {"push": None})

    def test_list_shorthand(self):
        wf = parse_workflow("x.yml", "on: [push, pull_request]\njobs:\n  b:\n    runs-on: u\n")
        self.assertEqual(set(wf.triggers), {"push", "pull_request"})

    def test_map_form(self):
        text = "on:\n  push:\n    branches: [main]\n  workflow_dispatch:\njobs:\n  b:\n    runs-on: u\n"
        wf = parse_workflow("x.yml", text)
        self.assertEqual(set(wf.triggers), {"push", "workflow_dispatch"})
        self.assertEqual(wf.triggers["push"], {"branches": ["main"]})
        self.assertIsNone(wf.triggers["workflow_dispatch"])

    def test_missing_on_is_a_parse_error(self):
        wf = parse_workflow("x.yml", "jobs:\n  b:\n    runs-on: u\n")
        self.assertIsNotNone(wf.parse_error)

    def test_on_list_with_non_string_entry_is_a_parse_error(self):
        wf = parse_workflow("x.yml", "on:\n  - push\n  - 5\n")
        self.assertIsNotNone(wf.parse_error)

    def test_non_mapping_top_level_is_a_parse_error(self):
        wf = parse_workflow("x.yml", "- a\n- b\n")
        self.assertIsNotNone(wf.parse_error)

    def test_malformed_yaml_is_a_parse_error_not_a_crash(self):
        wf = parse_workflow("x.yml", "on:\n\tpush:\n")
        self.assertIsNotNone(wf.parse_error)


class OnKeyBooleanGuard(unittest.TestCase):
    """wouldrun's own parser never produces a boolean `on` key (see
    yamlmini's docstring). This exercises the defensive fallback in
    _extract_on directly, simulating what a PyYAML-style loader would hand
    back, so the guard is proven live rather than merely present."""

    def test_extract_on_recovers_from_boolean_true_key(self):
        doc = {True: "push", "jobs": {}}
        value, guarded = _extract_on(doc)
        self.assertEqual(value, "push")
        self.assertTrue(guarded)

    def test_extract_on_prefers_real_string_key(self):
        doc = {"on": "push", True: "should not be used"}
        value, guarded = _extract_on(doc)
        self.assertEqual(value, "push")
        self.assertFalse(guarded)

    def test_extract_on_missing_entirely(self):
        from wouldrun.workflow import _MISSING

        doc = {"jobs": {}}
        value, guarded = _extract_on(doc)
        self.assertIs(value, _MISSING)


class Jobs(unittest.TestCase):
    def test_runs_on_and_needs(self):
        text = (
            "on: push\njobs:\n"
            "  build:\n    runs-on: ubuntu-latest\n"
            "  test:\n    needs: build\n    runs-on: ubuntu-latest\n"
        )
        wf = parse_workflow("x.yml", text)
        self.assertEqual(wf.jobs["build"].runs_on, "ubuntu-latest")
        self.assertEqual(wf.jobs["test"].needs, ["build"])

    def test_needs_list(self):
        text = "on: push\njobs:\n  c:\n    needs: [a, b]\n    runs-on: u\n"
        wf = parse_workflow("x.yml", text)
        self.assertEqual(wf.jobs["c"].needs, ["a", "b"])

    def test_uses_reusable_workflow(self):
        text = "on: push\njobs:\n  call:\n    uses: ./.github/workflows/b.yml\n"
        wf = parse_workflow("x.yml", text)
        self.assertEqual(wf.jobs["call"].uses, "./.github/workflows/b.yml")

    def test_condition_captured_raw(self):
        text = "on: push\njobs:\n  c:\n    if: github.ref == 'refs/heads/main'\n    runs-on: u\n"
        wf = parse_workflow("x.yml", text)
        self.assertIn("refs/heads/main", wf.jobs["c"].condition)

    def test_no_jobs_key_is_fine(self):
        wf = parse_workflow("x.yml", "on: push\n")
        self.assertEqual(wf.jobs, {})


if __name__ == "__main__":
    unittest.main()
