"""Narrow, safe wrappers around the two `git` reads wouldrun needs.

`changed_files_from_diff` backs `--diff BASE`; `current_ref` backs the `--ref`
default. Both build a fixed argv list, never a shell string, and both run with
`core.quotepath=false` so git hands back real path bytes instead of its
C-escaped rendering of them.
"""

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
    # `-z` (NUL-separated, never quoted) instead of one path per line. Without
    # it git applies core.quotepath and returns `"src/caf\303\251.py"` for any
    # path with a non-ASCII byte in it -- quotes, backslashes and all -- which
    # matches no filter pattern and turns a workflow GitHub would run into a
    # confident SKIPPED. `-z` also survives a path with a newline in it.
    argv = _git_argv(repo_root, "diff", "--name-only", "--no-color", "-z", merge_base, "--")
    proc = _run_git(argv)
    if proc.returncode != 0:
        raise GitDiffError(f"git diff failed: {proc.stderr.strip() or proc.returncode}")

    return [path for path in proc.stdout.split("\0") if path.strip()]


def current_ref(repo_root: str = ".") -> str:
    """Return the checked-out branch as a full ref, or "" if there isn't one.

    Empty means: not a git work tree, HEAD is detached, or git is missing. The
    caller decides what to assume in that case -- this function will not guess
    a branch, because guessing is what made `--ref` wrong in the first place.
    """
    if not os.path.isdir(repo_root):
        return ""
    try:
        proc = _run_git(_git_argv(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD"))
    except GitDiffError:
        return ""
    if proc.returncode != 0:
        return ""
    branch = proc.stdout.strip()
    return "refs/heads/" + branch if branch else ""


def _merge_base(base: str, repo_root: str) -> str:
    proc = _run_git(_git_argv(repo_root, "merge-base", base, "HEAD"))
    if proc.returncode != 0:
        raise GitDiffError(
            f"could not find a merge base for {base!r} and HEAD: "
            f"{proc.stderr.strip() or proc.returncode}"
        )
    return proc.stdout.strip()


def _git_argv(repo_root: str, *args) -> list:
    return ["git", "-C", repo_root, "-c", "core.quotepath=false", *args]


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
    except UnicodeDecodeError as e:
        # A path that is not valid UTF-8 (legal on Linux) would otherwise
        # escape as a bare traceback. Say so and stop, rather than reporting a
        # verdict built from a mangled file list.
        raise GitDiffError(f"git printed a path that is not valid UTF-8: {e}") from e
