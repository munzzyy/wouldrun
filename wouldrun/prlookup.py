"""Look up a real GitHub pull request's base branch and changed files.

Backs `--pr NUMBER`. Shells out to one fixed-argv `gh pr view` call, the same
read-only, no-shell approach `gitdiff.py` uses for `git`.
"""

from __future__ import annotations

import json
import os
import subprocess


class PrLookupError(RuntimeError):
    pass


def pr_info(pr_number, repo_root: str = ".") -> tuple:
    """Return (base_ref, changed_files) for an open GitHub PR, via `gh pr view`.

    Raises PrLookupError if `gh` is missing, the lookup fails, or the PR is
    outside this repo -- never a raw traceback or a guessed empty result.
    """
    try:
        number = int(pr_number)
    except (TypeError, ValueError):
        raise PrLookupError(f"--pr needs a PR number, got {pr_number!r}")
    if number <= 0:
        raise PrLookupError(f"--pr needs a positive PR number, got {number}")
    if not os.path.isdir(repo_root):
        raise PrLookupError(f"no such directory: {repo_root}")

    argv = ["gh", "pr", "view", str(number), "--json", "baseRefName,files"]
    try:
        proc = subprocess.run(
            argv,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except FileNotFoundError as e:
        raise PrLookupError("gh was not found on PATH; install the GitHub CLI to use --pr") from e
    except subprocess.TimeoutExpired as e:
        raise PrLookupError("gh pr view timed out after 30s") from e

    if proc.returncode != 0:
        raise PrLookupError(f"gh pr view {number} failed: {proc.stderr.strip() or proc.returncode}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PrLookupError(f"gh pr view {number} returned unparseable JSON: {e}") from e

    if not isinstance(data, dict):
        raise PrLookupError(f"gh pr view {number} returned unexpected JSON: {data!r}")

    base_ref = data.get("baseRefName")
    if not isinstance(base_ref, str) or not base_ref:
        raise PrLookupError(f"gh pr view {number} did not return a base branch")

    files = data.get("files")
    if not isinstance(files, list):
        raise PrLookupError(f"gh pr view {number} did not return a changed-files list")
    changed_files = [
        f["path"] for f in files if isinstance(f, dict) and isinstance(f.get("path"), str)
    ]

    return base_ref, changed_files
