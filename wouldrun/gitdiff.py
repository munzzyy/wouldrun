"""A narrow, safe wrapper around `git diff --name-only` for --diff BASE."""

from __future__ import annotations

import os
import subprocess


class GitDiffError(RuntimeError):
    pass


def changed_files_from_diff(base: str, repo_root: str = ".") -> list:
    if not base or not isinstance(base, str):
        raise GitDiffError("--diff needs a non-empty base ref or commit")
    if base.startswith("-"):
        raise GitDiffError(f"refusing base ref {base!r}: looks like a flag, not a ref")
    if not os.path.isdir(repo_root):
        raise GitDiffError(f"no such directory: {repo_root}")

    # Diff against the merge base of BASE and the working tree, not against
    # BASE directly. A plain `git diff BASE` is a two-dot diff: once BASE has
    # advanced past where this branch forked, every commit added to BASE shows
    # up as a "changed file" it never touched, so a docs-only PR looks like it
    # changed source. GitHub evaluates pull_request path filters against the
    # PR's own changes (merge-base semantics), so match that. Diffing against
    # the merge-base *commit* (rather than the `BASE...HEAD` range) keeps
    # uncommitted working-tree changes in the result, which is half the point
    # of running wouldrun locally.
    merge_base = _merge_base(base, repo_root)
    argv = ["git", "-C", repo_root, "diff", "--name-only", "--no-color", merge_base, "--"]
    proc = _run_git(argv)
    if proc.returncode != 0:
        raise GitDiffError(f"git diff failed: {proc.stderr.strip() or proc.returncode}")

    return [line for line in proc.stdout.splitlines() if line.strip()]


def _merge_base(base: str, repo_root: str) -> str:
    proc = _run_git(["git", "-C", repo_root, "merge-base", base, "HEAD"])
    if proc.returncode != 0:
        raise GitDiffError(
            f"could not find a merge base for {base!r} and HEAD: "
            f"{proc.stderr.strip() or proc.returncode}"
        )
    return proc.stdout.strip()


def _run_git(argv):
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitDiffError("git was not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GitDiffError("git command timed out after 30s") from e
