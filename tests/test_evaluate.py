"""The core correctness matrix: (event, ref, changed files) x workflows."""

import unittest

from wouldrun.discover import discover
from wouldrun.event import Event
from wouldrun.evaluate import evaluate_all
from wouldrun.workflow import parse_workflow
from tests._helpers import make_repo


def _run(text, event):
    wf = parse_workflow(".github/workflows/x.yml", text)
    return evaluate_all([wf], event)[0]


class PushBranches(unittest.TestCase):
    TEXT = "on:\n  push:\n    branches: [main, 'releases/**']\njobs:\n  b:\n    runs-on: u\n"

    def test_matches_main(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertTrue(r.fires)

    def test_matches_glob_branch(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/releases/1.0"))
        self.assertTrue(r.fires)

    def test_rejects_other_branch(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/dev"))
        self.assertFalse(r.fires)

    def test_short_ref_treated_as_branch(self):
        r = _run(self.TEXT, Event(name="push", ref="main"))
        self.assertTrue(r.fires)


class ScalarFilterForms(unittest.TestCase):
    # GitHub accepts a bare string wherever a filter list is allowed:
    # `branches: main` means `branches: [main]`. The old code kept only lists,
    # so a scalar filter was dropped and read as "no filter, matches any ref".
    def test_scalar_branch_filter_skips_other_branch(self):
        text = "on:\n  push:\n    branches: main\njobs:\n  b:\n    runs-on: u\n"
        self.assertFalse(_run(text, Event(name="push", ref="refs/heads/feature")).fires)

    def test_scalar_branch_filter_matches_named_branch(self):
        text = "on:\n  push:\n    branches: main\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="push", ref="refs/heads/main")).fires)

    def test_scalar_paths_filter_skips_unmatched_file(self):
        text = "on:\n  push:\n    paths: 'src/**'\njobs:\n  b:\n    runs-on: u\n"
        e = Event(name="push", ref="refs/heads/main", changed_files=["docs/readme.md"])
        self.assertFalse(_run(text, e).fires)

    def test_scalar_pr_types_filter(self):
        text = "on:\n  pull_request:\n    types: opened\njobs:\n  b:\n    runs-on: u\n"
        opened = Event(name="pull_request", ref="refs/heads/x", activity_type="opened")
        closed = Event(name="pull_request", ref="refs/heads/x", activity_type="closed")
        self.assertTrue(_run(text, opened).fires)
        self.assertFalse(_run(text, closed).fires)


class MalformedTriggerDoesNotCrash(unittest.TestCase):
    # `on: push: main` is a common mistake -- a scalar where a mapping belongs.
    # It must degrade to an unfiltered trigger, not raise and abort the whole run.
    def test_string_push_trigger_degrades(self):
        text = "on:\n  push: main\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="push", ref="refs/heads/anything")).fires)

    def test_list_pull_request_trigger_degrades(self):
        text = "on:\n  pull_request: [opened]\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="pull_request", ref="refs/heads/x")).fires)


class PushBranchesIgnore(unittest.TestCase):
    TEXT = "on:\n  push:\n    branches-ignore: [dev]\njobs:\n  b:\n    runs-on: u\n"

    def test_excluded_branch_skips(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/dev"))
        self.assertFalse(r.fires)

    def test_other_branch_fires(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertTrue(r.fires)


class PushNoRefFilter(unittest.TestCase):
    TEXT = "on: push\njobs:\n  b:\n    runs-on: u\n"

    def test_any_branch_matches(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/whatever"))
        self.assertTrue(r.fires)

    def test_any_tag_matches_too(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/tags/v9.9.9"))
        self.assertTrue(r.fires)


class TagsOnlyExcludesBranchPushes(unittest.TestCase):
    """Subtle, documented case: if only tags/tags-ignore is set, branch
    pushes never match (and vice versa) even though neither key alone
    reads like an exclusion of the other ref kind."""

    TEXT = "on:\n  push:\n    tags: ['v*']\njobs:\n  b:\n    runs-on: u\n"

    def test_tag_push_matches(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/tags/v1.0.0"))
        self.assertTrue(r.fires)

    def test_branch_push_does_not_match(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertFalse(r.fires)
        self.assertTrue(any("tags" in reason for reason in r.reasons))


class BranchesOnlyExcludesTagPushes(unittest.TestCase):
    TEXT = "on:\n  push:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n"

    def test_branch_push_matches(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertTrue(r.fires)

    def test_tag_push_does_not_match(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/tags/v1.0.0"))
        self.assertFalse(r.fires)


class CombinedBranchesAndTags(unittest.TestCase):
    """A push trigger can define branches AND tags together; either ref
    kind satisfies it independently (the well-known combined CI+release
    pattern)."""

    TEXT = "on:\n  push:\n    branches: [main]\n    tags: ['v*']\njobs:\n  b:\n    runs-on: u\n"

    def test_branch_matches(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertTrue(r.fires)

    def test_tag_matches(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/tags/v2.0.0"))
        self.assertTrue(r.fires)

    def test_other_branch_fails(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/dev"))
        self.assertFalse(r.fires)


class Paths(unittest.TestCase):
    TEXT = "on:\n  push:\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"

    def test_matching_file_fires(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main", changed_files=["src/app.py"]))
        self.assertTrue(r.fires)

    def test_non_matching_file_skips(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main", changed_files=["docs/x.md"]))
        self.assertFalse(r.fires)

    def test_one_of_many_matching_is_enough(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/heads/main", changed_files=["docs/x.md", "src/app.py"]),
        )
        self.assertTrue(r.fires)

    def test_no_changed_files_given_skips(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main", changed_files=[]))
        self.assertFalse(r.fires)


class PathsIgnore(unittest.TestCase):
    TEXT = "on:\n  push:\n    paths-ignore: ['docs/**']\njobs:\n  b:\n    runs-on: u\n"

    def test_only_ignored_file_skips(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main", changed_files=["docs/x.md"]))
        self.assertFalse(r.fires)

    def test_non_ignored_file_fires(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main", changed_files=["src/app.py"]))
        self.assertTrue(r.fires)

    def test_mixed_files_fires_because_one_survives(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/heads/main", changed_files=["docs/x.md", "src/app.py"]),
        )
        self.assertTrue(r.fires)


class PathsNegationWithinList(unittest.TestCase):
    TEXT = "on:\n  push:\n    paths:\n      - '**'\n      - '!docs/**'\njobs:\n  b:\n    runs-on: u\n"

    def test_non_docs_file_matches(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main", changed_files=["src/app.py"]))
        self.assertTrue(r.fires)

    def test_docs_file_excluded(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main", changed_files=["docs/x.md"]))
        self.assertFalse(r.fires)

    def test_mixed_fires_on_the_surviving_file(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/heads/main", changed_files=["docs/x.md", "src/app.py"]),
        )
        self.assertTrue(r.fires)


class PathsVsPathsIgnorePrecedence(unittest.TestCase):
    """GitHub rejects a workflow that sets both; wouldrun evaluates with
    `paths` only and says so, rather than silently picking one."""

    TEXT = (
        "on:\n  push:\n    paths: ['src/**']\n    paths-ignore: ['src/vendor/**']\n"
        "jobs:\n  b:\n    runs-on: u\n"
    )

    def test_paths_wins_and_vendor_file_still_matches(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/heads/main", changed_files=["src/vendor/lib.py"]),
        )
        self.assertTrue(r.fires)
        self.assertTrue(any("invalid" in reason for reason in r.reasons))


class BranchAndPathBothRequired(unittest.TestCase):
    """The task's headline subtle case: push with both branches and paths
    needs both to pass."""

    TEXT = (
        "on:\n  push:\n    branches: [main]\n    paths: ['src/**']\n"
        "jobs:\n  b:\n    runs-on: u\n"
    )

    def test_both_match_fires(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/heads/main", changed_files=["src/app.py"]),
        )
        self.assertTrue(r.fires)

    def test_branch_matches_but_path_does_not_skips(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/heads/main", changed_files=["docs/x.md"]),
        )
        self.assertFalse(r.fires)

    def test_path_matches_but_branch_does_not_skips(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/heads/dev", changed_files=["src/app.py"]),
        )
        self.assertFalse(r.fires)


class GlobEdgeCases(unittest.TestCase):
    def test_paths_double_star_matches_nested(self):
        text = "on:\n  push:\n    paths: ['**/*.py']\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="push", ref="refs/heads/main", changed_files=["a/b/c.py"]))
        self.assertTrue(r.fires)

    def test_paths_single_star_does_not_match_nested(self):
        text = "on:\n  push:\n    paths: ['*.py']\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="push", ref="refs/heads/main", changed_files=["a/b.py"]))
        self.assertFalse(r.fires)

    def test_paths_single_star_matches_root(self):
        text = "on:\n  push:\n    paths: ['*.py']\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="push", ref="refs/heads/main", changed_files=["b.py"]))
        self.assertTrue(r.fires)


class PullRequest(unittest.TestCase):
    TEXT = "on:\n  pull_request:\n    branches: [main]\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"

    def test_base_matches_and_path_matches_fires(self):
        r = _run(
            self.TEXT,
            Event(name="pull_request", base_ref="main", changed_files=["src/app.py"]),
        )
        self.assertTrue(r.fires)

    def test_base_does_not_match_skips(self):
        r = _run(
            self.TEXT,
            Event(name="pull_request", base_ref="dev", changed_files=["src/app.py"]),
        )
        self.assertFalse(r.fires)

    def test_default_base_is_main_when_unspecified(self):
        r = _run(self.TEXT, Event(name="pull_request", changed_files=["src/app.py"]))
        self.assertTrue(r.fires)


class PullRequestTypes(unittest.TestCase):
    def test_default_types_assumes_opened(self):
        text = "on: pull_request\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="pull_request"))
        self.assertTrue(r.fires)

    def test_explicit_types_filters_out_unlisted_activity(self):
        text = "on:\n  pull_request:\n    types: [labeled]\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="pull_request", activity_type="opened"))
        self.assertFalse(r.fires)

    def test_explicit_types_matches_listed_activity(self):
        text = "on:\n  pull_request:\n    types: [labeled]\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="pull_request", activity_type="labeled"))
        self.assertTrue(r.fires)

    def test_non_default_activity_without_types_filter_skips(self):
        # A workflow with no `types:` runs only on GitHub's default PR
        # activity types (opened, synchronize, reopened). A `--type labeled`
        # event must SKIP it, not FIRE -- the no-types branch used to ignore
        # the activity type entirely.
        text = "on:\n  pull_request:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="pull_request", base_ref="main", activity_type="labeled"))
        self.assertFalse(r.fires)
        r2 = _run(text, Event(name="pull_request", base_ref="main", activity_type="closed"))
        self.assertFalse(r2.fires)

    def test_default_activity_without_types_filter_still_fires(self):
        text = "on:\n  pull_request:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="pull_request", base_ref="main", activity_type="synchronize"))
        self.assertTrue(r.fires)


class PullRequestFullRefBase(unittest.TestCase):
    # `--ref` accepts a full `refs/heads/...`; `--base` must normalize it the
    # same way instead of matching the whole ref literally against a branch
    # filter (which never matches -> a false SKIP).
    TEXT = "on:\n  pull_request:\n    branches: [main]\njobs:\n  b:\n    runs-on: u\n"

    def test_full_ref_base_matches_branch_filter(self):
        r = _run(self.TEXT, Event(name="pull_request", base_ref="refs/heads/main"))
        self.assertTrue(r.fires)

    def test_short_base_still_matches(self):
        r = _run(self.TEXT, Event(name="pull_request", base_ref="main"))
        self.assertTrue(r.fires)


class TagPushIgnoresPathFilter(unittest.TestCase):
    # GitHub does not evaluate `paths`/`paths-ignore` for tag pushes, so a tag
    # whose ref filter matches fires regardless of the changed files. Applying
    # the path filter would falsely SKIP release workflows.
    TEXT = "on:\n  push:\n    tags: ['v*']\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"

    def test_tag_push_fires_even_when_no_path_matches(self):
        r = _run(
            self.TEXT,
            Event(name="push", ref="refs/tags/v1.0.0", changed_files=["README.md"]),
        )
        self.assertTrue(r.fires)

    def test_tag_push_fires_with_no_changed_files_given(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/tags/v1.0.0", changed_files=[]))
        self.assertTrue(r.fires)

    def test_tag_that_fails_the_ref_filter_still_skips(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/tags/rc-1", changed_files=["README.md"]))
        self.assertFalse(r.fires)

    def test_branch_push_still_applies_path_filter(self):
        text = "on:\n  push:\n    branches: [main]\n    paths: ['src/**']\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="push", ref="refs/heads/main", changed_files=["README.md"]))
        self.assertFalse(r.fires)


class IndentlessBranchSequenceEvaluates(unittest.TestCase):
    # End-to-end: a flush (indentless) block sequence for `branches:` must
    # parse and drive the ref filter, not report the workflow as unparseable.
    TEXT = "on:\n  push:\n    branches:\n    - main\n    - dev\njobs:\n  b:\n    runs-on: u\n"

    def test_listed_branch_fires(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertTrue(r.fires)

    def test_unlisted_branch_skips(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/feature"))
        self.assertFalse(r.fires)


class MultilineFlowBranchFilterEvaluates(unittest.TestCase):
    # End-to-end: a branch filter written as a multi-line flow list must be
    # applied, not silently dropped (which read as "matches any ref" and
    # truncated the rest of the file).
    TEXT = "on:\n  push:\n    branches: [\n      main,\n      release\n    ]\njobs:\n  b:\n    runs-on: u\n"

    def test_listed_branch_fires(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertTrue(r.fires)

    def test_unlisted_branch_skips(self):
        r = _run(self.TEXT, Event(name="push", ref="refs/heads/feature-x"))
        self.assertFalse(r.fires)


class NoMatchingTrigger(unittest.TestCase):
    def test_event_not_in_triggers_skips(self):
        text = "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="push", ref="refs/heads/main"))
        self.assertFalse(r.fires)
        self.assertIn("no `push` trigger", r.reasons[0])


class WorkflowDispatchAndSchedule(unittest.TestCase):
    def test_workflow_dispatch_always_fires(self):
        text = "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="workflow_dispatch"))
        self.assertTrue(r.fires)

    def test_schedule_fires_and_reports_cron(self):
        text = "on:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="schedule"))
        self.assertTrue(r.fires)
        self.assertTrue(any("0 3 * * *" in reason for reason in r.reasons))


class WorkflowCallResolution(unittest.TestCase):
    def _repo(self):
        return make_repo(
            {
                ".github/workflows/caller.yml": (
                    "on:\n  push:\n    branches: [main]\n"
                    "jobs:\n"
                    "  call:\n    uses: ./.github/workflows/reused.yml\n"
                    "  other:\n    runs-on: u\n"
                ),
                ".github/workflows/reused.yml": (
                    "on:\n  workflow_call:\njobs:\n  build:\n    runs-on: u\n"
                ),
                ".github/workflows/unrelated.yml": "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n",
            }
        )

    def test_called_workflow_fires_when_caller_fires(self):
        root = self._repo()
        workflows = discover(str(root))
        results = evaluate_all(workflows, Event(name="push", ref="refs/heads/main"))
        by_path = {r.workflow.path for r in results if r.fires}
        self.assertIn(".github/workflows/caller.yml", by_path)
        self.assertIn(".github/workflows/reused.yml", by_path)
        self.assertNotIn(".github/workflows/unrelated.yml", by_path)

    def test_called_workflow_not_reached_when_caller_does_not_fire(self):
        root = self._repo()
        workflows = discover(str(root))
        results = evaluate_all(workflows, Event(name="push", ref="refs/heads/dev"))
        fired = {r.workflow.path for r in results if r.fires}
        self.assertEqual(fired, set())

    def test_missing_reusable_target_is_noted_not_crashed(self):
        root = make_repo(
            {
                ".github/workflows/caller.yml": (
                    "on: push\njobs:\n  call:\n    uses: ./.github/workflows/missing.yml\n"
                ),
            }
        )
        workflows = discover(str(root))
        results = evaluate_all(workflows, Event(name="push", ref="refs/heads/main"))
        self.assertTrue(results[0].fires)
        self.assertTrue(any("missing.yml" in r for r in results[0].reasons))

    def test_called_workflow_without_workflow_call_trigger_is_flagged(self):
        root = make_repo(
            {
                ".github/workflows/caller.yml": (
                    "on: push\njobs:\n  call:\n    uses: ./.github/workflows/oops.yml\n"
                ),
                ".github/workflows/oops.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
            }
        )
        workflows = discover(str(root))
        results = evaluate_all(workflows, Event(name="push", ref="refs/heads/main"))
        oops = next(r for r in results if r.workflow.path == ".github/workflows/oops.yml")
        self.assertTrue(any("no `workflow_call` trigger" in reason for reason in oops.reasons))

    def test_called_workflow_without_workflow_call_trigger_does_not_fire(self):
        # Real GitHub behavior: calling a workflow that never declared
        # `workflow_call:` is rejected at dispatch time, so the target never
        # runs. Unlike the fixture above, `oops.yml` here has no trigger of
        # its own that matches this event either, so a bug that force-fires
        # it just because something called it is actually observable.
        root = make_repo(
            {
                ".github/workflows/caller.yml": (
                    "on: push\njobs:\n  call:\n    uses: ./.github/workflows/oops.yml\n"
                ),
                ".github/workflows/oops.yml": "on: workflow_dispatch\njobs:\n  b:\n    runs-on: u\n",
            }
        )
        workflows = discover(str(root))
        results = evaluate_all(workflows, Event(name="push", ref="refs/heads/main"))
        caller = next(r for r in results if r.workflow.path == ".github/workflows/caller.yml")
        oops = next(r for r in results if r.workflow.path == ".github/workflows/oops.yml")
        # The caller's own push trigger still matches...
        self.assertTrue(caller.fires)
        # ...but GitHub would reject the `uses:` call before either job
        # starts, so the target must not be reported as firing...
        self.assertFalse(oops.fires)
        self.assertTrue(any("no `workflow_call` trigger" in reason for reason in oops.reasons))
        # ...and the caller needs to know its own job graph is broken too,
        # not just the target buried in its own reasons.
        self.assertTrue(any("no `workflow_call` trigger" in reason for reason in caller.reasons))


class TypedEventTypesFilter(unittest.TestCase):
    """`types:` is honored for every event that supports it, not just
    pull_request. An activity type the workflow's `types:` list leaves out
    must SKIP; an event with no `types:` fires on any activity type."""

    def test_release_default_types_fires_for_any_activity(self):
        # No `types:` -- GitHub runs `release` on all of its activity types by
        # default, so a published (or any other) release fires.
        text = "on: release\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="release", activity_type="published")).fires)
        self.assertTrue(_run(text, Event(name="release", activity_type="prereleased")).fires)

    def test_release_explicit_type_matches(self):
        text = "on:\n  release:\n    types: [published]\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="release", activity_type="published"))
        self.assertTrue(r.fires)

    def test_release_excluded_type_skips(self):
        text = "on:\n  release:\n    types: [published]\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="release", activity_type="created"))
        self.assertFalse(r.fires)
        self.assertTrue(any("not in `types:" in reason for reason in r.reasons))

    def test_issues_excluded_type_skips(self):
        text = "on:\n  issues:\n    types: [opened, reopened]\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="issues", activity_type="opened")).fires)
        self.assertFalse(_run(text, Event(name="issues", activity_type="closed")).fires)

    def test_issue_comment_scalar_type_form(self):
        # A scalar `types: created` means the same as `types: [created]`.
        text = "on:\n  issue_comment:\n    types: created\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="issue_comment", activity_type="created")).fires)
        self.assertFalse(_run(text, Event(name="issue_comment", activity_type="deleted")).fires)

    def test_label_edited_only(self):
        text = "on:\n  label:\n    types: [created]\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="label", activity_type="created")).fires)
        self.assertFalse(_run(text, Event(name="label", activity_type="edited")).fires)

    def test_discussion_type_filter(self):
        text = "on:\n  discussion:\n    types: [created, answered]\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="discussion", activity_type="answered")).fires)
        self.assertFalse(_run(text, Event(name="discussion", activity_type="labeled")).fires)

    def test_watch_default_started(self):
        text = "on: watch\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="watch", activity_type="started")).fires)

    def test_registry_package_excluded_type_skips(self):
        text = "on:\n  registry_package:\n    types: [published]\njobs:\n  b:\n    runs-on: u\n"
        self.assertFalse(_run(text, Event(name="registry_package", activity_type="updated")).fires)

    def test_no_activity_type_given_still_fires_with_filter(self):
        # Without --type there is no activity to test against the filter, so
        # the trigger is reported as firing rather than guessing and SKIPping.
        text = "on:\n  issues:\n    types: [labeled]\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="issues"))
        self.assertTrue(r.fires)

    def test_pull_request_target_types_still_filtered(self):
        # pull_request_target keeps its own handler (branches/paths + subset
        # default types); make sure its `types:` filtering did not regress.
        text = "on:\n  pull_request_target:\n    types: [labeled]\njobs:\n  b:\n    runs-on: u\n"
        self.assertTrue(_run(text, Event(name="pull_request_target", activity_type="labeled")).fires)
        self.assertFalse(_run(text, Event(name="pull_request_target", activity_type="opened")).fires)


class UnknownEventPassesThrough(unittest.TestCase):
    def test_create_trigger_present_fires(self):
        # `create` takes no types/branches/paths filters, so a matching
        # trigger always fires.
        text = "on: create\njobs:\n  b:\n    runs-on: u\n"
        r = _run(text, Event(name="create"))
        self.assertTrue(r.fires)


class MalformedGlobDoesNotCrashEvaluation(unittest.TestCase):
    """A workflow with an invalid filter pattern (e.g. a reversed character
    range) must degrade to a SKIPPED verdict with a reason, not blow up
    evaluate_all -- wouldrun's own SECURITY.md calls a workflow that crashes
    the evaluator a vulnerability."""

    BAD_TEXT = "on:\n  push:\n    branches: ['[z-a]']\njobs:\n  b:\n    runs-on: u\n"

    def test_single_malformed_workflow_reports_a_reason_not_a_crash(self):
        r = _run(self.BAD_TEXT, Event(name="push", ref="refs/heads/main"))
        self.assertFalse(r.fires)
        self.assertTrue(any("[z-a]" in reason for reason in r.reasons))

    def test_other_workflows_still_evaluate(self):
        root = make_repo(
            {
                ".github/workflows/broken.yml": self.BAD_TEXT,
                ".github/workflows/ok.yml": "on: push\njobs:\n  b:\n    runs-on: u\n",
            }
        )
        workflows = discover(str(root))
        results = evaluate_all(workflows, Event(name="push", ref="refs/heads/main"))
        by_path = {r.workflow.path: r for r in results}
        self.assertFalse(by_path[".github/workflows/broken.yml"].fires)
        self.assertTrue(by_path[".github/workflows/ok.yml"].fires)


if __name__ == "__main__":
    unittest.main()
