"""Format wouldrun's --json output as a compact markdown table.

Used by action.yml: the composite Action runs `wouldrun ... --json` against a
pull request's base and changed files, then pipes that payload through this
module to get something readable in a job summary or a PR comment. Kept as a
plain function (`format_markdown`) so it's unit-testable without a live PR --
see tests/test_pr_format.py.
"""

from __future__ import annotations

import json
import sys

# The comment-update logic in action.yml searches every existing PR comment
# for this exact string to find the one it owns, so a later run updates it
# in place instead of posting a new comment on every push. It's emitted
# unconditionally, in both the job-summary and PR-comment output, since an
# HTML comment renders invisibly either way and it costs nothing to keep the
# two paths identical.
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
        f"**wouldrun** — event `{event.get('name', '?')}`, "
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


def main(argv=None) -> int:
    """Read a wouldrun --json payload from a file argument (or `-`/no
    argument for stdin) and print the markdown table to stdout."""
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] != "-":
        with open(argv[0], "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()
    payload = json.loads(text)
    sys.stdout.write(format_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
