"""Command-line interface for wouldrun."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .discover import discover
from .event import REF_EVENTS, Event
from .evaluate import evaluate_all
from .gitdiff import GitDiffError, changed_files_from_diff, current_ref
from .prlookup import PrLookupError, pr_info
from .report import render_human, render_json, render_list

FALLBACK_REF = "refs/heads/main"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wouldrun",
        description="Work out which GitHub Actions workflows and jobs a change would "
        "trigger, without pushing or running act.",
    )
    p.add_argument("target", nargs="?", default=".", help="repo root to scan (default: .)")
    p.add_argument(
        "--event",
        default=None,
        help="event to simulate: push, pull_request, pull_request_target, "
        "workflow_dispatch, schedule, workflow_call, or any other GitHub event "
        "name (default: pull_request when --pr is given, push otherwise)",
    )
    p.add_argument(
        "--ref",
        default=None,
        help="ref for a push event: a branch (main), a full ref "
        "(refs/heads/main, refs/tags/v1.0.0); default: the branch checked out "
        "in the target repo, or refs/heads/main if that cannot be read",
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
    p.add_argument(
        "--triggering-workflow",
        dest="triggering_workflow",
        default=None,
        metavar="NAME",
        help="name of the upstream workflow whose completion is being simulated for "
        "a workflow_run event, matched against that trigger's `workflows:` list; "
        "without it, a `workflows:` filter cannot be confirmed and reports SKIPPED",
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
    changed.add_argument(
        "--pr",
        type=int,
        metavar="NUMBER",
        help="look up an open GitHub pull request's base branch and changed files with "
        "`gh pr view` (needs gh on PATH and repo access); sets --event to pull_request "
        "unless --event is also given",
    )
    p.add_argument(
        "--workflow",
        action="append",
        dest="workflow_names",
        metavar="NAME",
        help="only report this workflow. Matches its `name:`, its file name "
        "(ci.yml), or its path, case-insensitively. Repeatable, and it scopes "
        "--exit-fires to the workflows you named",
    )
    p.add_argument("--list", action="store_true", help="list workflows and their triggers; skip event evaluation")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    p.add_argument(
        "--fires-only",
        action="store_true",
        help="only show workflows that would fire; hide SKIPPED ones and their reasons",
    )
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
        # utf-8-sig, matching discover.py: a list written by a Windows editor
        # starts with a BOM, and plain utf-8 glues it onto the first path so
        # that path quietly matches nothing.
        with open(path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    return [line.strip() for line in text.splitlines() if line.strip()]


def _resolve_ref(args, event_name):
    """Return (ref, source) for the event under evaluation.

    A hardcoded refs/heads/main default answered for the wrong branch every
    time someone ran wouldrun from a feature branch, which is the most likely
    first run there is. Read the branch out of the target repo instead, and
    only fall back to main when there is nothing to read.
    """
    if args.ref is not None:
        return args.ref, "flag"
    if event_name in REF_EVENTS:
        ref = current_ref(args.target)
        if ref:
            return ref, "git"
    return FALLBACK_REF, "default"


def _workflow_keys(workflow) -> set:
    """Every string a --workflow value is allowed to match, lowercased."""
    path = workflow.path
    basename = path.rsplit("/", 1)[-1]
    keys = {path.lower(), basename.lower()}
    if "." in basename:
        keys.add(basename.rsplit(".", 1)[0].lower())
    if workflow.name:
        keys.add(workflow.name.lower())
    return keys


def _select_workflows(items, wanted, get_workflow=None):
    """Return (kept items, names that matched nothing).

    On the evaluation path this runs after evaluate_all, not before, so a
    reusable workflow reached through a caller you did not name still resolves
    and still shows up if you named the reusable one.
    """
    get_workflow = get_workflow or (lambda item: item)
    normalized = [w.strip().lower() for w in wanted if w.strip()]
    kept = []
    matched = set()
    for item in items:
        keys = _workflow_keys(get_workflow(item))
        hits = [w for w in normalized if w in keys]
        if hits:
            matched.update(hits)
            kept.append(item)
    return kept, [w for w in normalized if w not in matched]


def _no_match_message(unmatched) -> str:
    """A --workflow value that matches nothing has to fail loudly. Left alone
    it would leave nothing to evaluate, print "0 would fire", and exit 1 under
    --exit-fires as if the answer were a real no."""
    names = ", ".join(repr(u) for u in unmatched)
    return f"wouldrun: no workflow matches --workflow {names}"


def _build_changed_files(args) -> list:
    if args.changed:
        return [f.strip() for f in args.changed.split(",") if f.strip()]
    if args.changed_from:
        return _read_changed_from(args.changed_from)
    if args.diff:
        return changed_files_from_diff(args.diff, repo_root=args.target)
    return []


def _resolve_event_name(args) -> str:
    """--pr implies a pull_request event -- that is the only event a PR's base
    branch and changed files mean anything for -- unless the caller overrode
    --event explicitly."""
    if args.event is not None:
        return args.event
    return "pull_request" if args.pr else "push"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.isdir(args.target):
        print(f"wouldrun: no such directory: {args.target}", file=sys.stderr)
        return 2

    workflows = discover(args.target)

    if args.workflow_names and args.list:
        workflows, unmatched = _select_workflows(workflows, args.workflow_names)
        if unmatched:
            print(_no_match_message(unmatched), file=sys.stderr)
            return 2

    if args.list:
        print(render_list(workflows, as_json=args.json))
        return 0

    try:
        if args.pr:
            pr_base_ref, changed_files = pr_info(args.pr, repo_root=args.target)
            if args.base_ref is None:
                args.base_ref = pr_base_ref
        else:
            changed_files = _build_changed_files(args)
    except (GitDiffError, PrLookupError) as e:
        print(f"wouldrun: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"wouldrun: could not read changed files: {e}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as e:
        # UnicodeDecodeError is a ValueError, not an OSError, so a cp1252 list
        # from a PowerShell redirect used to escape the handler above as a raw
        # traceback.
        print(f"wouldrun: could not read changed files: {e}", file=sys.stderr)
        return 2

    event_name = _resolve_event_name(args)
    ref, ref_source = _resolve_ref(args, event_name)
    event = Event(
        name=event_name,
        ref=ref,
        base_ref=args.base_ref,
        changed_files=changed_files,
        activity_type=args.activity_type,
        triggering_workflow=args.triggering_workflow,
        ref_source=ref_source,
    )
    results = evaluate_all(workflows, event)

    if args.workflow_names:
        results, unmatched = _select_workflows(
            results, args.workflow_names, get_workflow=lambda r: r.workflow
        )
        if unmatched:
            print(_no_match_message(unmatched), file=sys.stderr)
            return 2

    display = [r for r in results if r.fires] if args.fires_only else results

    if args.json:
        print(render_json(display, event))
    else:
        color = not args.no_color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        print(render_human(display, event, color=color, total=results))

    if args.exit_fires:
        return 0 if any(r.fires for r in results) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
