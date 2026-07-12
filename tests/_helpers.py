"""Shared test helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path


def make_repo(files: dict) -> Path:
    """Write {relpath: text} under a fresh temp dir and return its root."""
    root = Path(tempfile.mkdtemp(prefix="wouldrun-test-"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def workflow_repo(name: str, text: str) -> Path:
    """A repo with a single workflow file under .github/workflows/<name>."""
    return make_repo({f".github/workflows/{name}": text})
