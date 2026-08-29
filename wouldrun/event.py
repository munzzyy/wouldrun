"""The hypothetical event wouldrun evaluates workflows against."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Events whose verdict turns on `ref`. Everything else either filters on a base
# branch (pull_request), or ignores the ref entirely, so there is no point
# reading a branch out of git for them and no point explaining the one we used.
REF_EVENTS = frozenset({"push", "workflow_run"})


@dataclass
class Event:
    name: str
    ref: Optional[str] = None  # push: refs/heads/main or refs/tags/v1.0.0
    base_ref: Optional[str] = None  # pull_request: base branch, e.g. "main"
    changed_files: List[str] = field(default_factory=list)
    activity_type: Optional[str] = None  # pull_request: opened, synchronize, ...
    # workflow_run: the name of the upstream workflow whose completion is being
    # simulated, matched against `on.workflow_run.workflows:`. None means the
    # caller did not say, which is not the same as "matches anything" -- see
    # evaluate.py's `_evaluate_workflow_run_extras`.
    triggering_workflow: Optional[str] = None
    # Where `ref` came from: "flag" (the user passed --ref), "git" (read from
    # the target repo's HEAD), or "default" (nothing to read it from, so it was
    # assumed). A wrong ref flips every branch filter, so the report says which.
    ref_source: str = "flag"


def classify_ref(ref: Optional[str]):
    """Return (is_tag, short_name) for a ref, defaulting to a branch."""
    if not ref:
        return False, ""
    if ref.startswith("refs/tags/"):
        return True, ref[len("refs/tags/") :]
    if ref.startswith("refs/heads/"):
        return False, ref[len("refs/heads/") :]
    return False, ref
