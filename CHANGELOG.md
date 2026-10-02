# Changelog

## Unreleased

- `workflow_run` triggers check their `workflows:` list against the new
  `--triggering-workflow NAME`. A missing list or no name to check reports SKIPPED
  instead of a guessed FIRES.
- `--pr NUMBER` takes a pull request base branch and its changed files from `gh pr view`.
- `--fires-only` drops SKIPPED workflows from the report.
- The license is now GPL-3.0-or-later. Releases up to 0.1.0 stay under MIT.
- YAML anchors and aliases are resolved instead of read as text, which used to drop
  triggers and jobs. A `<<` merge key is a parse error, as it is on GitHub. So is a
  file whose aliases would add more than two million characters once copied out.
- `--exit-fires` exits 2 when nothing fires but a workflow in scope failed to parse or
  evaluate, and names that workflow on stderr. JSON workflows get an `undetermined` field.
- `repository_dispatch` honors `types:` with `--type` standing in for the `event_type`.
- `--head REF` next to `--diff` lists what REF changed without checking it out. An
  empty changed-files source now says no files changed.
- `--changed` and `--changed-from` drop a leading `./` or `/` from each path. On Windows
  they also turn backslashes into `/`.
- A `pull_request` run without `--base` says it assumed `main`. JSON `event.base_ref` is
  the base that was used and the new `event.base_ref_source` says where it came from.
- An unknown `--event` gets a warning naming the closest real event. So does `--type`
  on an event that has no activity types.
- `--diff` also counts untracked files that `.gitignore` does not cover.
- Action: runs wouldrun from its own directory with no pip install, so nothing in the
  PR checkout gets imported in its place. Event values reach the shell through `env:`.
- Action: on `pull_request_target` it fetches the PR head commit and diffs that, so
  path filters see the files the PR touched.
- Action: the sticky comment only edits a comment the action account wrote. On a fork
  PR with a read-only token the table goes to the job summary instead of failing.
- Action: new `workflow` input plus `fires`, `fired-count` and `fired-workflows`
  outputs for gating a later job.
- SECURITY.md and the README list every subprocess and the one network path. The In CI
  snippets check out full history and pass the base through `env:`.
- CI adds Python 3.10 and 3.14, tests the comment script with Node and runs the Action
  on pull requests to this repo.

## 0.1.0 (2026-08-02)

First tagged release, under the MIT license.

- Reads every workflow under `.github/workflows/` with its own small YAML reader and
  says FIRES or SKIPPED with the reason for a push, a pull request or another event.
- `push` and `pull_request` branch, tag and path filters, `types:` on every typed event
  and the `workflow_run` branch filter.
- Follows same-repo `workflow_call` chains written with `./` or `$/`.
- `--changed`, `--changed-from`, `--diff`, `--ref` (defaulting to the checked-out
  branch), `--workflow`, `--exit-fires`, `--json` and `--list`.
- A composite GitHub Action that writes the table to the job summary or, opt-in, to a
  sticky PR comment.
