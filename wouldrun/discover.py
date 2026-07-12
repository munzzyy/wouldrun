"""Find and load workflow files under a repo's .github/workflows/."""

from __future__ import annotations

import os

from .workflow import Workflow, parse_workflow

MAX_FILE_BYTES = 2 * 1024 * 1024


def discover(root: str) -> list:
    """Return every .github/workflows/*.yml|*.yaml under `root`, parsed.

    Paths on the returned Workflow objects are POSIX-style and relative to
    `root`, e.g. ".github/workflows/ci.yml", regardless of platform.
    """
    workflows_dir = os.path.join(root, ".github", "workflows")
    if not os.path.isdir(workflows_dir):
        return []
    out = []
    for entry in sorted(os.listdir(workflows_dir)):
        if not (entry.endswith(".yml") or entry.endswith(".yaml")):
            continue
        full = os.path.join(workflows_dir, entry)
        if not os.path.isfile(full):
            continue
        rel = "/".join([".github", "workflows", entry])
        try:
            size = os.path.getsize(full)
            if size > MAX_FILE_BYTES:
                out.append(Workflow.broken(rel, f"file exceeds {MAX_FILE_BYTES} byte cap"))
                continue
            with open(full, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            out.append(Workflow.broken(rel, f"could not read file: {e}"))
            continue
        except UnicodeDecodeError as e:
            out.append(Workflow.broken(rel, f"not valid UTF-8: {e}"))
            continue
        out.append(parse_workflow(rel, text))
    return out
