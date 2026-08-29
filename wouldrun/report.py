"""Render evaluation results as human text, JSON, or the --list summary."""

from __future__ import annotations

import json

from . import __version__
from .event import REF_EVENTS

_GREEN = "\033[32m"
_GRAY = "\033[90m"
_RESET = "\033[0m"


def ref_note(event):
    """One line saying where a ref nobody typed came from, or None.

    Every branch and tag filter turns on this value, so a ref wouldrun picked
    for you has to be visible in the report. A ref the user passed needs no
    explanation, and neither does an event that ignores the ref entirely.
    """
    source = getattr(event, "ref_source", "flag")
    if source == "flag" or event.name not in REF_EVENTS:
        return None
    if source == "git":
        return f"no --ref given; using the checked-out branch `{event.ref}`"
    return f"no --ref given and no branch to read from git; assuming `{event.ref}`"


def render_human(results, event, color: bool = True, total=None) -> str:
    """`results` is what gets printed; `total` (defaults to `results`) is what
    the header counts against. They differ under `--fires-only`: the header
    still needs to say how many workflows exist and how many of them fire,
    even once the SKIPPED ones are dropped from the listing below it.
    """

    def c(code, s):
        return f"{code}{s}{_RESET}" if color else s

    if total is None:
        total = results

    lines = [""]
    fired = sum(1 for r in total if r.fires)
    lines.append(f"  wouldrun  event={event.name}  {len(total)} workflow(s), {fired} would fire")
    note = ref_note(event)
    if note:
        lines.append(f"  {note}")
    lines.append("")

    if not total:
        lines.append("  No workflow files found under .github/workflows/.")
        lines.append("")
        return "\n".join(lines)

    if not results:
        lines.append("  No workflow would fire. Drop --fires-only to see why.")
        lines.append("")
        return "\n".join(lines)

    for r in results:
        tag = c(_GREEN, " FIRES   ") if r.fires else c(_GRAY, " SKIPPED ")
        title = r.workflow.name or r.workflow.path
        lines.append(f"  {tag} {title}  [{r.workflow.path}]")
        for reason in r.reasons:
            lines.append(f"           {reason}")
        if r.fires and r.jobs:
            lines.append(f"           jobs: {', '.join(r.jobs)}")
        lines.append("")

    return "\n".join(lines)


def render_json(results, event) -> str:
    payload = {
        "tool": "wouldrun",
        "version": __version__,
        "event": {
            "name": event.name,
            "ref": event.ref,
            "ref_source": getattr(event, "ref_source", "flag"),
            "base_ref": event.base_ref,
            "activity_type": event.activity_type,
            "triggering_workflow": getattr(event, "triggering_workflow", None),
            "changed_files": event.changed_files,
        },
        "workflows": [
            {
                "path": r.workflow.path,
                "name": r.workflow.name,
                "fires": r.fires,
                "reasons": r.reasons,
                "jobs": r.jobs,
                "called_by": r.called_by,
                "parse_error": r.workflow.parse_error,
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2)


def render_list(workflows, as_json: bool = False) -> str:
    if as_json:
        payload = {
            "tool": "wouldrun",
            "version": __version__,
            "workflows": [
                {
                    "path": w.path,
                    "name": w.name,
                    "triggers": sorted(w.triggers) if not w.parse_error else [],
                    "jobs": sorted(w.jobs) if not w.parse_error else [],
                    "parse_error": w.parse_error,
                }
                for w in workflows
            ],
        }
        return json.dumps(payload, indent=2)

    lines = [""]
    if not workflows:
        lines.append("  No workflow files found under .github/workflows/.")
        lines.append("")
        return "\n".join(lines)
    for w in workflows:
        title = w.name or w.path
        lines.append(f"  {title}  [{w.path}]")
        if w.parse_error:
            lines.append(f"    parse error: {w.parse_error}")
        else:
            triggers = sorted(w.triggers) or ["(none)"]
            lines.append(f"    triggers: {', '.join(triggers)}")
            if w.jobs:
                lines.append(f"    jobs: {', '.join(sorted(w.jobs))}")
        lines.append("")
    return "\n".join(lines)
