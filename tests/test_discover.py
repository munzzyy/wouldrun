"""Tests for wouldrun.discover: finding and loading workflow files."""

import unittest

from wouldrun.discover import discover
from tests._helpers import make_repo


class Discover(unittest.TestCase):
    def test_finds_yml_and_yaml(self):
        root = make_repo(
            {
                ".github/workflows/a.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
                ".github/workflows/b.yaml": "on: push\njobs:\n  b:\n    runs-on: u\n",
            }
        )
        workflows = discover(str(root))
        self.assertEqual({w.path for w in workflows}, {".github/workflows/a.yml", ".github/workflows/b.yaml"})

    def test_ignores_non_workflow_files(self):
        root = make_repo(
            {
                ".github/workflows/a.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
                ".github/workflows/README.md": "not a workflow",
                ".github/workflows/notes.txt": "not a workflow",
            }
        )
        workflows = discover(str(root))
        self.assertEqual([w.path for w in workflows], [".github/workflows/a.yml"])

    def test_no_workflows_dir_returns_empty(self):
        root = make_repo({"README.md": "hello"})
        self.assertEqual(discover(str(root)), [])

    def test_broken_workflow_does_not_crash_discovery(self):
        root = make_repo(
            {
                ".github/workflows/ok.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
                ".github/workflows/broken.yml": "on:\n\tpush:\n",
            }
        )
        workflows = discover(str(root))
        self.assertEqual(len(workflows), 2)
        broken = next(w for w in workflows if w.path.endswith("broken.yml"))
        self.assertIsNotNone(broken.parse_error)

    def test_results_are_sorted_by_filename(self):
        root = make_repo(
            {
                ".github/workflows/z.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
                ".github/workflows/a.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
            }
        )
        workflows = discover(str(root))
        self.assertEqual([w.path for w in workflows], [".github/workflows/a.yml", ".github/workflows/z.yml"])


if __name__ == "__main__":
    unittest.main()
