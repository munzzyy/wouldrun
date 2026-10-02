"""Format wouldrun's --json output as a compact markdown table.

Used by action.yml: the composite Action runs `wouldrun ... --json` against a
pull request's base and changed files, then pipes that payload through this
module to get something readable in a job summary or a PR comment. Kept as a
plain function (`format_markdown`) so it's unit-testable without a live PR --
see tests/test_pr_format.py.

On a `pull_request` run the Action also evaluates the base branch's workflows
as `pull_request_target`, since GitHub runs both for the same pull request.
Given more than one report, `format_merged_markdown` folds them into one table
with an Event column.
"""

from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace

from .cli import _select_workflows

# scripts/sticky-comment.js finds the comment it owns by a body that starts
# with this exact string, so a later run updates it in place instead of
# posting a new comment on every push. Changing it would orphan every existing
# comment. It's emitted unconditionally, in both the job-summary and
# PR-comment output, since an HTML comment renders invisibly either way and it
# costs nothing to keep the two paths identical.
MARKER = "<!-- wouldrun -->"


def _decisive_reason(reasons) -> str:
    """Pick one reason out of a workflow's full list for the table's Reason
    column. evaluate.py appends a reason per filter it checks and returns as
    soon as one of them fails, so the last entry is always the filter that
    actually decided the verdict -- not just the first thing it looked at.
    render_human() prints the whole list for this reason; a table cell only
    has room for the punch line.
    """
    if not reasons:
        return ""
    return reasons[-1]


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def format_markdown(payload: dict) -> str:
    """Render a wouldrun --json payload (see report.render_json) as a
    marker comment plus a markdown table: workflow, FIRES/SKIPPED, reason."""
    event = payload.get("event") or {}
    workflows = payload.get("workflows") or []
    fired = sum(1 for w in workflows if w.get("fires"))

    lines = [
        MARKER,
        f"**wouldrun**: event `{event.get('name', '?')}`, "
        f"{len(workflows)} workflow(s), {fired} would fire",
        "",
    ]

    if not workflows:
        lines.append("No workflow files found under `.github/workflows/`.")
        return "\n".join(lines) + "\n"

    lines.append("| Workflow | Verdict | Reason |")
    lines.append("|---|---|---|")
    for w in workflows:
        title = _escape_cell(w.get("name") or w.get("path", "?"))
        verdict = "FIRES" if w.get("fires") else "SKIPPED"
        reason = _escape_cell(_decisive_reason(w.get("reasons")))
        lines.append(f"| {title} | {verdict} | {reason} |")

    return "\n".join(lines) + "\n"


def _event_name(payload) -> str:
    return (payload.get("event") or {}).get("name") or "?"


def _no_trigger(event, workflow) -> bool:
    """True when evaluate.py skipped `workflow` only because it has no `event` trigger."""
    reasons = workflow.get("reasons") or []
    return len(reasons) == 1 and reasons[0].startswith(f"no `{event}` trigger")


def merge_rows(payloads) -> list:
    """One row per workflow path across several reports of the same pull
    request, one report per event. A workflow fires if it fires under any of
    them. A row that fires names every event it fires under and shows the
    first one's reason; a skipped row shows the first event the workflow
    listens to, or the first event at all."""
    by_path = {}
    for payload in payloads:
        event = _event_name(payload)
        for w in payload.get("workflows") or []:
            by_path.setdefault(w.get("path", "?"), []).append((event, w))
    rows = []
    for path in sorted(by_path):
        entries = by_path[path]
        fired = [(e, w) for e, w in entries if w.get("fires")]
        if fired:
            shown = fired
        else:
            listening = [(e, w) for e, w in entries if not _no_trigger(e, w)]
            shown = (listening or entries)[:1]
        workflow = shown[0][1]
        rows.append(
            {
                "path": path,
                "title": workflow.get("name") or path,
                "event": ", ".join(e for e, _ in shown),
                "fires": bool(fired),
                "reason": _decisive_reason(workflow.get("reasons")),
            }
        )
    return rows


def format_merged_markdown(payloads) -> str:
    """The table for several reports at once: workflow, event, verdict, reason."""
    rows = merge_rows(payloads)
    fired = sum(1 for r in rows if r["fires"])
    events = " and ".join(f"`{_event_name(p)}`" for p in payloads)
    lines = [
        MARKER,
        f"**wouldrun**: events {events}, {len(rows)} workflow(s), {fired} would fire",
        "",
    ]
    if not rows:
        lines.append("No workflow files found under `.github/workflows/`.")
        return "\n".join(lines) + "\n"
    lines.append("| Workflow | Event | Verdict | Reason |")
    lines.append("|---|---|---|---|")
    for r in rows:
        verdict = "FIRES" if r["fires"] else "SKIPPED"
        lines.append(
            f"| {_escape_cell(r['title'])} | {_escape_cell(r['event'])} | {verdict} | {_escape_cell(r['reason'])} |"
        )
    return "\n".join(lines) + "\n"


def select(payload: dict, names) -> dict:
    """`payload` with only the workflows a --workflow NAME list matches."""
    kept, _ = _select_workflows(
        payload.get("workflows") or [],
        names,
        get_workflow=lambda w: SimpleNamespace(path=w.get("path", ""), name=w.get("name")),
    )
    return {**payload, "workflows": kept}


def github_outputs(payload: dict) -> str:
    """The Action's step outputs, as the `key=value` lines $GITHUB_OUTPUT takes."""
    fired = [w.get("path", "") for w in payload.get("workflows") or [] if w.get("fires")]
    return _outputs(fired)


def merged_github_outputs(payloads) -> str:
    return _outputs([r["path"] for r in merge_rows(payloads) if r["fires"]])


def _outputs(fired) -> str:
    return (
        f"fires={'true' if fired else 'false'}\n"
        f"fired-count={len(fired)}\n"
        f"fired-workflows={json.dumps(fired, separators=(',', ':'))}\n"
    )


def _read(path):
    if path == "-":
        return json.loads(sys.stdin.read())
    with open(path, "r", encoding="utf-8") as fh:
        return json.loads(fh.read())


def main(argv=None) -> int:
    """Read one or more wouldrun --json payloads (a file argument each, or
    `-`/no argument for stdin) and print the markdown table to stdout."""
    parser = argparse.ArgumentParser(prog="python -m wouldrun.pr_format")
    parser.add_argument(
        "reports",
        nargs="*",
        metavar="report",
        help="wouldrun --json output, one per event, merged into one table (default: stdin)",
    )
    parser.add_argument(
        "--github-output",
        metavar="PATH",
        help="also append fires, fired-count and fired-workflows to PATH ($GITHUB_OUTPUT)",
    )
    parser.add_argument(
        "--workflow",
        action="append",
        default=[],
        metavar="NAME",
        help="only keep workflows NAME matches, the way wouldrun --workflow matches (repeatable)",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    payloads = [_read(path) for path in args.reports or ["-"]]
    if args.workflow:
        payloads = [select(p, args.workflow) for p in payloads]
    if len(payloads) == 1:
        outputs, markdown = github_outputs(payloads[0]), format_markdown(payloads[0])
    else:
        outputs, markdown = merged_github_outputs(payloads), format_merged_markdown(payloads)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(outputs)
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
