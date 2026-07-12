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

    argv = ["git", "-C", repo_root, "diff", "--name-only", "--no-color", base, "--"]
    try:
        proc = subprocess.run(
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
        raise GitDiffError("git diff timed out after 30s") from e

    if proc.returncode != 0:
        raise GitDiffError(f"git diff failed: {proc.stderr.strip() or proc.returncode}")

    return [line for line in proc.stdout.splitlines() if line.strip()]
