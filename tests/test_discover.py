"""Tests for wouldrun.discover: finding and loading workflow files."""

import os
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

    def test_bom_prefixed_workflow_parses(self):
        # Windows editors emit a UTF-8 BOM. Opening with plain utf-8 leaves the
        # BOM glued to the first key so `on:` reads as "\ufeffon" and the
        # trigger vanishes (a false SKIP). GitHub parses BOM'd files fine.
        root = make_repo({"README.md": "x"})
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        (wf_dir / "ci.yml").write_bytes(
            b"\xef\xbb\xbfon: push\njobs:\n  b:\n    runs-on: u\n"
        )
        workflows = discover(str(root))
        self.assertEqual(len(workflows), 1)
        wf = workflows[0]
        self.assertIsNone(wf.parse_error)
        self.assertIn("push", wf.triggers)

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


class Symlinks(unittest.TestCase):
    def test_a_symlinked_workflow_is_reported_and_its_target_never_quoted(self):
        import tempfile
        from wouldrun.discover import discover
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            secret = os.path.join(outside, "environ")
            with open(secret, "w", encoding="utf-8") as fh:
                fh.write("DEMO_TOKEN=visible-if-leaked\n")
            wf = os.path.join(root, ".github", "workflows")
            os.makedirs(wf)
            try:
                os.symlink(secret, os.path.join(wf, "leak.yml"))
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable here")
            found = discover(root)
            self.assertEqual([w.path for w in found], [".github/workflows/leak.yml"])
            self.assertIn("symbolic link", found[0].parse_error or "")
            self.assertNotIn("visible-if-leaked", repr(vars(found[0])))
