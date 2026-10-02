"""Look up a real GitHub pull request's base branch and changed files.

Backs `--pr NUMBER`. Shells out to a fixed-argv `gh pr view` call, the same
read-only, no-shell approach `gitdiff.py` uses for `git`. `gh pr view` lists
at most 100 files, so a bigger pull request takes one more call, to the
REST API's paginated file list through `gh api`.
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

    argv = ["gh", "pr", "view", str(number), "--json", "baseRefName,files,changedFiles"]
    data = _run_gh_json(argv, repo_root, f"gh pr view {number}")

    if not isinstance(data, dict):
        raise PrLookupError(f"gh pr view {number} returned unexpected JSON: {data!r}")

    base_ref = data.get("baseRefName")
    if not isinstance(base_ref, str) or not base_ref:
        raise PrLookupError(f"gh pr view {number} did not return a base branch")

    files = data.get("files")
    if not isinstance(files, list):
        raise PrLookupError(f"gh pr view {number} did not return a changed-files list")
    count = data.get("changedFiles")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise PrLookupError(f"gh pr view {number} did not return a changed-file count")
    changed_files = [
        f["path"] for f in files if isinstance(f, dict) and isinstance(f.get("path"), str)
    ]

    if len(changed_files) < count:
        changed_files = _all_files(number, repo_root)
        if len(changed_files) < count:
            raise PrLookupError(
                f"PR {number} changes {count} files but GitHub listed only "
                f"{len(changed_files)} of them, so path filters cannot be checked against all of them"
            )

    return base_ref, changed_files


def _all_files(number, repo_root):
    """Every changed path of PR `number`, read page by page from the REST API."""
    endpoint = f"repos/{{owner}}/{{repo}}/pulls/{number}/files?per_page=100"
    argv = ["gh", "api", "--paginate", "--slurp", endpoint]
    pages = _run_gh_json(argv, repo_root, f"gh api {endpoint}")
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise PrLookupError(f"gh api {endpoint} returned unexpected JSON")
    return [
        f["filename"]
        for page in pages
        for f in page
        if isinstance(f, dict) and isinstance(f.get("filename"), str)
    ]


def _run_gh_json(argv, repo_root, label):
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
        raise PrLookupError(f"{label} timed out after 30s") from e

    if proc.returncode != 0:
        raise PrLookupError(f"{label} failed: {proc.stderr.strip() or proc.returncode}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PrLookupError(f"{label} returned unparseable JSON: {e}") from e
