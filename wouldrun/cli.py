"""Command-line interface for wouldrun."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .discover import discover
from .event import Event
from .evaluate import evaluate_all
from .gitdiff import GitDiffError, changed_files_from_diff
from .report import render_human, render_json, render_list


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wouldrun",
        description="Work out which GitHub Actions workflows and jobs a change would "
        "trigger, without pushing or running act.",
    )
    p.add_argument("target", nargs="?", default=".", help="repo root to scan (default: .)")
    p.add_argument(
        "--event",
        default="push",
        help="event to simulate: push, pull_request, pull_request_target, "
        "workflow_dispatch, schedule, workflow_call, or any other GitHub event "
        "name (default: push)",
    )
    p.add_argument(
        "--ref",
        default="refs/heads/main",
        help="ref for a push event: a branch (main), a full ref "
        "(refs/heads/main, refs/tags/v1.0.0); default: refs/heads/main",
    )
    p.add_argument(
        "--base",
        dest="base_ref",
        default=None,
        help="base branch for pull_request/pull_request_target (default: main)",
    )
    p.add_argument(
        "--type",
        dest="activity_type",
        default=None,
        help="activity type for pull_request-like events (opened, synchronize, "
        "reopened, ...); default: GitHub's default types for the event",
    )
    changed = p.add_mutually_exclusive_group()
    changed.add_argument("--changed", metavar="FILES", help="comma-separated changed file paths")
    changed.add_argument(
        "--changed-from",
        metavar="PATH",
        help="read changed file paths, one per line, from PATH (use - for stdin)",
    )
    changed.add_argument(
        "--diff",
        metavar="BASE",
        help="run `git diff --name-only BASE --` in the target repo to get changed files",
    )
    p.add_argument("--list", action="store_true", help="list workflows and their triggers; skip event evaluation")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    p.add_argument("--no-color", action="store_true", help="disable ANSI color")
    p.add_argument(
        "--exit-fires",
        action="store_true",
        help="exit 0 if at least one workflow would fire, 1 otherwise (default: always exit 0)",
    )
    p.add_argument("--version", action="version", version=f"wouldrun {__version__}")
    return p


def _read_changed_from(path: str) -> list:
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    return [line.strip() for line in text.splitlines() if line.strip()]


def _build_changed_files(args) -> list:
    if args.changed:
        return [f.strip() for f in args.changed.split(",") if f.strip()]
    if args.changed_from:
        return _read_changed_from(args.changed_from)
    if args.diff:
        return changed_files_from_diff(args.diff, repo_root=args.target)
    return []


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.isdir(args.target):
        print(f"wouldrun: no such directory: {args.target}", file=sys.stderr)
        return 2

    workflows = discover(args.target)

    if args.list:
        print(render_list(workflows, as_json=args.json))
        return 0

    try:
        changed_files = _build_changed_files(args)
    except GitDiffError as e:
        print(f"wouldrun: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"wouldrun: could not read changed files: {e}", file=sys.stderr)
        return 2

    event = Event(
        name=args.event,
        ref=args.ref,
        base_ref=args.base_ref,
        changed_files=changed_files,
        activity_type=args.activity_type,
    )
    results = evaluate_all(workflows, event)

    if args.json:
        print(render_json(results, event))
    else:
        color = not args.no_color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        print(render_human(results, event, color=color))

    if args.exit_fires:
        return 0 if any(r.fires for r in results) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
