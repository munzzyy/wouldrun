"""Decide, for one hypothetical event, which workflows fire and why.

This module intentionally does not evaluate `if:` step/job conditions or
GitHub's `${{ }}` expression language, does not check cron schedules against
a real clock, and does not model `types:` filters for events other than
`pull_request` / `pull_request_target`. Those are all documented as explicit
limits in the README; getting them wrong quietly would be worse than not
having them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import globmatch
from .event import Event, classify_ref

MAX_CALL_DEPTH = 20

_PR_DEFAULT_TYPES = {
    "pull_request": ["opened", "synchronize", "reopened"],
    "pull_request_target": ["opened", "synchronize", "reopened"],
}


@dataclass
class WorkflowResult:
    workflow: object
    fires: bool
    reasons: List[str] = field(default_factory=list)
    jobs: List[str] = field(default_factory=list)
    called_by: List[str] = field(default_factory=list)  # "<path> (job <id>)" entries


def evaluate_all(workflows, event: Event) -> List[WorkflowResult]:
    by_path = {w.path: w for w in workflows}
    results = {}

    for wf in workflows:
        results[wf.path] = _evaluate_direct(wf, event)

    called = _resolve_workflow_calls(workflows, by_path, results)
    for path, entries in called.items():
        r = results.get(path)
        if r is None:
            continue
        if not r.fires:
            r.fires = True
            r.reasons.append(
                "not matched directly, but reached anyway: " + "; ".join(entries)
            )
            r.jobs = sorted(r.workflow.jobs) if r.workflow.jobs else r.jobs
        else:
            r.reasons.append("also reachable as a called workflow: " + "; ".join(entries))
        r.called_by.extend(entries)

    return [results[wf.path] for wf in workflows]


def _evaluate_direct(workflow, event: Event) -> WorkflowResult:
    if workflow.parse_error:
        return WorkflowResult(
            workflow=workflow,
            fires=False,
            reasons=[f"could not parse this workflow: {workflow.parse_error}"],
        )

    if event.name not in workflow.triggers:
        known = ", ".join(sorted(workflow.triggers)) or "none"
        return WorkflowResult(
            workflow=workflow,
            fires=False,
            reasons=[f"no `{event.name}` trigger (this workflow listens for: {known})"],
        )

    spec = workflow.triggers[event.name]
    try:
        fires, reasons = _evaluate_trigger(event.name, spec, event)
    except globmatch.GlobError as e:
        # A malformed filter pattern (e.g. a reversed character-class range
        # like `[z-a]`) is bad input in one workflow's YAML, not a reason to
        # take down evaluation for every other workflow in the repo -- see
        # SECURITY.md on crashing the evaluator.
        return WorkflowResult(
            workflow=workflow,
            fires=False,
            reasons=[f"could not evaluate this workflow's filter patterns: {e}"],
        )
    jobs = sorted(workflow.jobs) if fires else []
    return WorkflowResult(workflow=workflow, fires=fires, reasons=reasons, jobs=jobs)


def _evaluate_trigger(event_name, spec, event: Event):
    if event_name == "push":
        return _evaluate_push(spec, event)
    if event_name in _PR_DEFAULT_TYPES:
        return _evaluate_pull_request(event_name, spec, event)
    if event_name == "workflow_dispatch":
        return True, ["`workflow_dispatch` trigger present; manual runs are not filtered by ref or changed files"]
    if event_name == "schedule":
        crons = _cron_list(spec)
        detail = ", ".join(f"`{c}`" for c in crons) if crons else "(no cron entries found)"
        return True, [
            f"`schedule` trigger present ({detail}); wouldrun does not check the cron "
            "expression against a clock, so this only confirms the trigger exists"
        ]
    if event_name == "workflow_call":
        return True, ["`workflow_call` trigger present; this workflow can be called as a reusable workflow"]
    return True, [
        f"`{event_name}` trigger present; wouldrun does not model filters for this event beyond "
        "push and pull_request, so this only confirms the trigger exists"
    ]


def _cron_list(spec):
    if not isinstance(spec, list):
        return []
    out = []
    for item in spec:
        if isinstance(item, dict) and isinstance(item.get("cron"), str):
            out.append(item["cron"])
    return out


def _as_filter_list(value):
    """A branches/tags/paths/types filter may be written as a single string or
    a list of strings -- GitHub accepts both (`branches: main` means the same
    as `branches: [main]`). Normalize to a non-empty list of strings, or None
    when there is no usable filter. Dropping the scalar form (the old
    `isinstance(v, list)` check) silently turned a real one-branch filter into
    "no filter, matches any ref"."""
    if isinstance(value, str):
        return [value] if value else None
    if isinstance(value, list):
        items = [v for v in value if isinstance(v, str) and v]
        return items or None
    return None


def _evaluate_push(spec, event: Event):
    # `on.push` should be a mapping (or null). A scalar or list here -- e.g. the
    # common `on: push: main` mistake -- must not crash the run and take every
    # other workflow down with it; treat it as an unfiltered push.
    spec = spec if isinstance(spec, dict) else {}
    reasons = []
    branches, branches_ignore = _resolve_pair(spec, "branches", "branches-ignore", reasons)
    tags, tags_ignore = _resolve_pair(spec, "tags", "tags-ignore", reasons)
    paths, paths_ignore = _resolve_pair(spec, "paths", "paths-ignore", reasons)

    ok, why = _match_ref(event.ref, branches, branches_ignore, tags, tags_ignore)
    reasons.append(why)
    if not ok:
        return False, reasons

    ok, why = _match_paths(event.changed_files, paths, paths_ignore)
    reasons.append(why)
    if not ok:
        return False, reasons

    return True, reasons


def _evaluate_pull_request(event_name, spec, event: Event):
    spec = spec if isinstance(spec, dict) else {}
    reasons = []

    types = _as_filter_list(spec.get("types"))
    default_types = _PR_DEFAULT_TYPES[event_name]
    activity = event.activity_type or default_types[0]
    if types:
        if activity not in types:
            reasons.append(f"activity type `{activity}` is not in `types: {types}`")
            return False, reasons
        reasons.append(f"activity type `{activity}` matches `types: {types}`")
    else:
        reasons.append(
            f"no `types` filter; GitHub's default types for {event_name} are "
            f"{default_types}, assuming `{activity}`"
        )

    branches, branches_ignore = _resolve_pair(spec, "branches", "branches-ignore", reasons)
    paths, paths_ignore = _resolve_pair(spec, "paths", "paths-ignore", reasons)

    if branches or branches_ignore:
        base = event.base_ref or "main"
        ok, why = _match_glob_list(base, branches, branches_ignore, "branches", "branches-ignore")
        reasons.append(f"base branch `{base}`: {why}")
        if not ok:
            return False, reasons
    else:
        reasons.append("no `branches`/`branches-ignore` filter; matches any base branch")

    ok, why = _match_paths(event.changed_files, paths, paths_ignore)
    reasons.append(why)
    if not ok:
        return False, reasons

    return True, reasons


def _resolve_pair(spec, include_key, exclude_key, reasons):
    include = _as_filter_list(spec.get(include_key))
    exclude = _as_filter_list(spec.get(exclude_key))
    if include and exclude:
        reasons.append(
            f"workflow declares both `{include_key}` and `{exclude_key}`, which GitHub "
            f"rejects as an invalid workflow; evaluating with `{include_key}` only"
        )
        exclude = None
    return include, exclude


def _match_ref(ref, branches, branches_ignore, tags, tags_ignore):
    is_tag, short = classify_ref(ref)
    have_branch_filter = bool(branches or branches_ignore)
    have_tag_filter = bool(tags or tags_ignore)

    if not have_branch_filter and not have_tag_filter:
        kind = "tag" if is_tag else "branch"
        return True, f"{kind} `{short}`: no branch/tag filter on this push trigger; matches any ref"

    if is_tag:
        if not have_tag_filter:
            return False, (
                f"tag `{short}`: this push trigger only filters `branches`/`branches-ignore`, "
                "so tag pushes never match it"
            )
        ok, why = _match_glob_list(short, tags, tags_ignore, "tags", "tags-ignore")
        return ok, f"tag `{short}`: {why}"

    if not have_branch_filter:
        return False, (
            f"branch `{short}`: this push trigger only filters `tags`/`tags-ignore`, "
            "so branch pushes never match it"
        )
    ok, why = _match_glob_list(short, branches, branches_ignore, "branches", "branches-ignore")
    return ok, f"branch `{short}`: {why}"


def _match_glob_list(value, include, exclude, include_name, exclude_name):
    if include:
        if _match_pattern_list(value, include):
            return True, f"matches `{include_name}: {include}`"
        return False, f"does not match `{include_name}: {include}`"
    if exclude:
        if _match_pattern_list(value, exclude):
            return False, f"matches `{exclude_name}: {exclude}` (excluded)"
        return True, f"does not match `{exclude_name}: {exclude}`"
    return True, "no filter"


def _match_pattern_list(value, patterns):
    matched = False
    for raw in patterns:
        if not isinstance(raw, str):
            continue
        if raw.startswith("\\!"):
            neg, pattern = False, raw[1:]
        elif raw.startswith("!"):
            neg, pattern = True, raw[1:]
        else:
            neg, pattern = False, raw
        if globmatch.match(pattern, value):
            matched = not neg
    return matched


def _match_paths(changed_files, paths, paths_ignore):
    if not paths and not paths_ignore:
        return True, "no `paths`/`paths-ignore` filter; matches regardless of changed files"
    if not changed_files:
        which = "paths" if paths else "paths-ignore"
        return False, (
            f"no changed files given (use --changed/--changed-from/--diff) to evaluate "
            f"the `{which}` filter against"
        )
    if paths:
        for f in changed_files:
            if _match_pattern_list(f, paths):
                return True, f"`paths: {paths}` matches changed file `{f}`"
        return False, f"`paths: {paths}` matches none of the changed files ({changed_files})"
    surviving = [f for f in changed_files if not _match_pattern_list(f, paths_ignore)]
    if surviving:
        return True, f"`paths-ignore: {paths_ignore}` does not cover changed file `{surviving[0]}`"
    return False, f"`paths-ignore: {paths_ignore}` covers every changed file ({changed_files})"


def _resolve_workflow_calls(workflows, by_path, results):
    """Map called-workflow path -> list of "<caller> (job <id>)" explanations.

    A workflow reached only via `workflow_call` from a directly-fired
    workflow still runs, regardless of whether it has its own trigger that
    matches this event, so this closure runs independent of `_evaluate_direct`.
    """
    called = {}
    queue = [wf.path for wf in workflows if results[wf.path].fires]
    seen = set(queue)
    depth = 0
    while queue and depth < MAX_CALL_DEPTH:
        depth += 1
        next_queue = []
        for caller_path in queue:
            caller = by_path.get(caller_path)
            if caller is None or caller.parse_error:
                continue
            for job_id, job in caller.jobs.items():
                target_path = _resolve_local_uses(job.uses)
                if target_path is None:
                    continue
                entry = f"called by `{caller_path}` job `{job_id}`"
                if target_path not in by_path:
                    # Points somewhere wouldrun didn't discover; note it once
                    # against the caller instead of silently dropping it.
                    results[caller_path].reasons.append(
                        f"job `{job_id}` uses `{job.uses}`, which does not match any "
                        "discovered workflow file"
                    )
                    continue
                target = by_path[target_path]
                if not target.parse_error and "workflow_call" not in target.triggers:
                    # GitHub rejects this at dispatch time: calling a workflow
                    # that never declared `workflow_call:` fails the run
                    # before either side's jobs start, so the target must not
                    # be marked as reached, and the caller needs to know its
                    # own job is the one that's broken -- not just have a
                    # warning buried in the unreachable target's reasons.
                    warning = (
                        f"job `{job_id}` calls `{target_path}` via `uses:`, but "
                        f"`{target_path}` has no `workflow_call` trigger; GitHub "
                        "would reject this call and the run fails before it starts"
                    )
                    results[caller_path].reasons.append(warning)
                    results[target_path].reasons.append(warning)
                    continue
                called.setdefault(target_path, []).append(entry)
                if target_path not in seen:
                    seen.add(target_path)
                    next_queue.append(target_path)
        queue = next_queue
    return called


def _resolve_local_uses(uses):
    """Normalize a job's `uses:` to a repo-relative path, or None if it is
    not a same-repo reusable-workflow reference (an action, or an external
    repo's workflow)."""
    if not uses or not isinstance(uses, str):
        return None
    if not uses.startswith("./"):
        return None
    path = uses[2:]
    parts = [p for p in path.split("/") if p not in ("", ".")]
    normalized = []
    for part in parts:
        if part == "..":
            if normalized:
                normalized.pop()
            continue
        normalized.append(part)
    return "/".join(normalized)
