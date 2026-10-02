"""pyproject.toml and wouldrun.__version__ have to say the same version."""

import re
import sys
import unittest
from pathlib import Path

import wouldrun

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _version_by_regex(text):
    m = re.search(r'(?ms)^\[project\]$.*?^version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _pyproject_version():
    text = PYPROJECT.read_text(encoding="utf-8")
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib.loads(text)["project"]["version"]
    return _version_by_regex(text)


class VersionSync(unittest.TestCase):
    def test_pyproject_matches_the_package(self):
        self.assertEqual(_pyproject_version(), wouldrun.__version__)

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib is 3.11+")
    def test_the_3_9_regex_reads_what_tomllib_reads(self):
        import tomllib

        text = PYPROJECT.read_text(encoding="utf-8")
        self.assertEqual(_version_by_regex(text), tomllib.loads(text)["project"]["version"])


if __name__ == "__main__":
    unittest.main()
