"""The hypothetical event wouldrun evaluates workflows against."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Event:
    name: str
    ref: Optional[str] = None  # push: refs/heads/main or refs/tags/v1.0.0
    base_ref: Optional[str] = None  # pull_request: base branch, e.g. "main"
    changed_files: List[str] = field(default_factory=list)
    activity_type: Optional[str] = None  # pull_request: opened, synchronize, ...


def classify_ref(ref: Optional[str]):
    """Return (is_tag, short_name) for a ref, defaulting to a branch."""
    if not ref:
        return False, ""
    if ref.startswith("refs/tags/"):
        return True, ref[len("refs/tags/") :]
    if ref.startswith("refs/heads/"):
        return False, ref[len("refs/heads/") :]
    return False, ref
