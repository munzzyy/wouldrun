"""Turn a workflow file's text into a normalized Workflow object."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import yamlmini


class WorkflowParseError(ValueError):
    pass


@dataclass
class Job:
    job_id: str
    runs_on: object = None
    needs: list = field(default_factory=list)
    uses: Optional[str] = None
    condition: Optional[str] = None


@dataclass
class Workflow:
    path: str  # e.g. .github/workflows/ci.yml, relative to the scanned repo
    name: Optional[str] = None
    triggers: dict = field(default_factory=dict)  # event name -> filter spec or None
    jobs: dict = field(default_factory=dict)  # job_id -> Job
    on_key_guarded: bool = False  # True if the boolean-`on` guard actually fired
    parse_error: Optional[str] = None

    @classmethod
    def broken(cls, path, error):
        return cls(path=path, parse_error=str(error))


def parse_workflow(path: str, text: str) -> Workflow:
    try:
        doc = yamlmini.load(text)
    except yamlmini.YamlError as e:
        return Workflow.broken(path, e)

    if not isinstance(doc, dict):
        return Workflow.broken(path, "top level of the workflow is not a mapping")

    on_value, guarded = _extract_on(doc)
    if on_value is _MISSING:
        return Workflow.broken(path, "workflow has no `on:` trigger")

    try:
        triggers = _normalize_on(on_value)
    except WorkflowParseError as e:
        return Workflow.broken(path, e)

    jobs = {}
    raw_jobs = doc.get("jobs")
    if isinstance(raw_jobs, dict):
        for job_id, spec in raw_jobs.items():
            jobs[str(job_id)] = _parse_job(str(job_id), spec)

    name = doc.get("name")
    return Workflow(
        path=path,
        name=name if isinstance(name, str) else None,
        triggers=triggers,
        jobs=jobs,
        on_key_guarded=guarded,
    )


_MISSING = object()


def _extract_on(doc):
    """Return (value, guarded) for the `on:` key.

    wouldrun's own YAML reader never turns the `on` key into a boolean (see
    yamlmini's module docstring), so this guard should be structurally
    unreachable in normal operation. It stays in as a second line of
    defense: if `doc` ever arrives from a different source (a future
    refactor, a test fixture built by hand, a swapped-in parser), a stray
    boolean key still gets recovered instead of silently producing "no
    triggers at all".
    """
    if "on" in doc:
        return doc["on"], False
    if True in doc and "on" not in doc:
        return doc[True], True
    return _MISSING, False


def _normalize_on(raw):
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        out = {}
        for item in raw:
            if isinstance(item, str):
                out[item] = None
            else:
                raise WorkflowParseError(f"on: list contains a non-string entry: {item!r}")
        return out
    if isinstance(raw, dict):
        return dict(raw)
    raise WorkflowParseError(f"on: has an unsupported shape ({type(raw).__name__})")


def _parse_job(job_id, spec):
    if not isinstance(spec, dict):
        return Job(job_id=job_id)
    needs = spec.get("needs")
    if isinstance(needs, str):
        needs = [needs]
    elif not isinstance(needs, list):
        needs = []
    uses = spec.get("uses")
    condition = spec.get("if")
    return Job(
        job_id=job_id,
        runs_on=spec.get("runs-on"),
        needs=[str(x) for x in needs],
        uses=uses if isinstance(uses, str) else None,
        condition=condition if isinstance(condition, str) else None,
    )
