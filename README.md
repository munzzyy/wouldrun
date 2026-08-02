# wouldrun

[![CI](https://github.com/munzzyy/wouldrun/actions/workflows/ci.yml/badge.svg)](https://github.com/munzzyy/wouldrun/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

![wouldrun evaluating a push to src/app.py: CI fires on the src/** path filter while Docs, Release, and the reusable deploy workflow each skip for a stated reason](docs/media/demo.svg)

Answers "given this push, PR, or set of changed files, which of my GitHub Actions
workflows would actually run, and why?" wouldrun reads every workflow under
`.github/workflows/`, resolves the `on:` triggers, branch/tag filters, and path filters
the same way GitHub does, follows `workflow_call` into any reusable workflows it
triggers, and tells you FIRES or SKIPPED with the specific reason for each one. No push,
no `act`, no container.

## Install

Pure standard library, Python 3.9+, no runtime dependencies.

```bash
pipx install git+https://github.com/munzzyy/wouldrun
```

Or clone it and run it in place, no install step at all:

```bash
git clone https://github.com/munzzyy/wouldrun
cd wouldrun
python -m wouldrun --list      # run it directly
pip install -e .               # or install the `wouldrun` command
```

wouldrun is not published on PyPI, so `pip install wouldrun` does not get you
this project. Install it from git.

## Usage

The repo ships a small example under `examples/example-repo/.github/workflows/`: a CI
workflow gated on `src/**` (but not markdown files in it), a docs workflow that runs on
everything *except* `src/**`, and a release workflow on version tags that calls a
reusable deploy workflow. `--list` shows what each one listens for:

```
$ wouldrun examples/example-repo --list

  CI  [.github/workflows/ci.yml]
    triggers: pull_request, push
    jobs: test

  Docs  [.github/workflows/docs.yml]
    triggers: push
    jobs: build-docs

  Release  [.github/workflows/release.yml]
    triggers: push
    jobs: build, deploy

  .github/workflows/reusable-deploy.yml  [.github/workflows/reusable-deploy.yml]
    triggers: workflow_call
    jobs: deploy
```

A push to `main` that only touches `src/app.py`:

```
$ wouldrun examples/example-repo --event push --ref refs/heads/main --changed src/app.py

  wouldrun  event=push  4 workflow(s), 1 would fire

   FIRES    CI  [.github/workflows/ci.yml]
           branch `main`: matches `branches: ['main']`
           `paths: ['src/**', '!src/**/*.md']` matches changed file `src/app.py`
           jobs: test

   SKIPPED  Docs  [.github/workflows/docs.yml]
           branch `main`: matches `branches: ['main']`
           `paths-ignore: ['src/**']` covers every changed file (['src/app.py'])

   SKIPPED  Release  [.github/workflows/release.yml]
           branch `main`: this push trigger only filters `tags`/`tags-ignore`, so branch pushes never match it

   SKIPPED  .github/workflows/reusable-deploy.yml  [.github/workflows/reusable-deploy.yml]
           no `push` trigger (this workflow listens for: workflow_call)
```

A tag push follows the `workflow_call` chain into the reusable workflow it triggers:

```
$ wouldrun examples/example-repo --event push --ref refs/tags/v1.2.3

  wouldrun  event=push  4 workflow(s), 2 would fire

   FIRES    Release  [.github/workflows/release.yml]
           tag `v1.2.3`: matches `tags: ['v[0-9]+.[0-9]+.[0-9]+']`
           no `paths`/`paths-ignore` filter; matches regardless of changed files
           jobs: build, deploy

   FIRES    .github/workflows/reusable-deploy.yml  [.github/workflows/reusable-deploy.yml]
           no `push` trigger (this workflow listens for: workflow_call)
           not matched directly, but reached anyway: called by `.github/workflows/release.yml` job `deploy`
           jobs: deploy
```

### Which branch it assumes

Leave `--ref` off and wouldrun uses the branch checked out in the target repo,
and says so in the report header:

```
$ wouldrun

  wouldrun  event=push  2 workflow(s), 0 would fire
  no --ref given; using the checked-out branch `refs/heads/feature/x`
```

If there's no branch to read (not a git repo, detached HEAD, no git on PATH) it
falls back to `refs/heads/main` and says that instead. `--json` carries the same
information as `event.ref_source`: `flag`, `git`, or `default`.

### Feeding it changed files

```bash
wouldrun --changed "src/app.py,docs/x.md"        # inline list
wouldrun --changed-from changed-files.txt        # one path per line
wouldrun --changed-from -                        # read the list from stdin
wouldrun --diff main                             # git diff --name-only main -- , in the target repo
```

### Other events

```bash
wouldrun --event pull_request --base main --changed src/app.py
wouldrun --event pull_request --type labeled --base main
wouldrun --event workflow_dispatch
wouldrun --event schedule
```

### In CI

The composite Action further down is the shortest path. To run the CLI yourself,
install it from git at a commit you chose:

```yaml
- run: |
    pipx run --spec "git+https://github.com/munzzyy/wouldrun@<commit-sha>" \
      wouldrun --diff "origin/${{ github.base_ref }}" --exit-fires
```

`--exit-fires` makes the exit code reflect the verdict (0 if at least one workflow would
fire, 1 if none would) instead of the default, which is always 0 so you can pipe the
report into something else without tripping `set -e`.

Across a whole repo that answer is almost always yes, since one unfiltered
`push:` or `pull_request:` is enough. The question worth asking in CI is about one
workflow: is the expensive end-to-end suite going to fire, so is it worth building
the environment for it? `--workflow` asks that, and scopes `--exit-fires` to it:

```yaml
- run: |
    pipx run --spec "git+https://github.com/munzzyy/wouldrun@<commit-sha>" \
      wouldrun --diff "origin/${{ github.base_ref }}" --workflow e2e.yml --exit-fires
```

It matches a workflow's `name:`, its file name (`e2e.yml`), its stem (`e2e`), or its
path, case-insensitively, and it's repeatable. A value that matches nothing exits 2
rather than reporting that nothing fires.

### Output formats

- default: plain-text report, one block per workflow
- `--json`: the same verdicts and reasons, machine-readable
- `--list`: just workflow names and triggers, no event needed

Full flag reference: `wouldrun --help`.

## GitHub Action

`action.yml` at the repo root wraps the CLI as a composite Action for any repo's
own pull requests: it checks out the PR, runs wouldrun against the PR's base and
changed files, and reports the FIRES/SKIPPED table.

By default that report only goes to the job summary, nothing posted anywhere,
no permission beyond the default `contents: read`:

```yaml
on:
  pull_request:

jobs:
  wouldrun:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: munzzyy/wouldrun@v0.1.0
```

Posting that same table as a PR comment is opt-in, `post-comment: "true"`, and
needs `pull-requests: write` on the job:

```yaml
on:
  pull_request:

jobs:
  wouldrun:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: munzzyy/wouldrun@v0.1.0
        with:
          post-comment: "true"
```

Opt-in instead of default-on because a bot that comments on every push is a
common reason people end up muting or removing an Action, and
`pull-requests: write` is a real trust bar above a job that only reads. The
comment path finds and updates a single existing comment (by a hidden
`<!-- wouldrun -->` marker, checked on every run) instead of posting a new one
each time, so a PR carries at most one wouldrun comment no matter how many
times it's pushed to.

The `@v0.1.0` pin above is the current tagged release. Pin to a commit SHA
instead if you want even tags to stop moving.

## What it checks

- `on:` triggers in every shorthand: bare string, list, and mapping form.
- `push`: `branches`, `branches-ignore`, `tags`, `tags-ignore`, `paths`, `paths-ignore`,
  including that a ref filter and a path filter are ANDed together, that
  `branches`/`branches-ignore` and `tags`/`tags-ignore` are independent (a branch-only
  filter excludes tag pushes, and a tags-only filter excludes branch pushes, even
  though neither key looks like it should touch the other ref kind), and that
  declaring `paths` and `paths-ignore` together is what GitHub itself rejects, so
  wouldrun evaluates with `paths` and says so instead of guessing.
- `pull_request` / `pull_request_target`: `branches`/`branches-ignore` against the PR
  base, `paths`/`paths-ignore` against changed files, and `types` against an activity
  type you pass with `--type` (falling back to GitHub's default types when you don't).
- `types` on every other typed event too (`issues`, `issue_comment`, `label`, `milestone`,
  `release`, `discussion`, `discussion_comment`, `registry_package`, `watch`, `project`,
  `project_card`, and the rest): a `--type` that the workflow's `types:` list leaves out is
  reported as SKIPPED. These events fire on all of their activity types by default, so a
  bare trigger with no `types:` matches any `--type` you pass.
- `workflow_run`: `types` plus the `branches`/`branches-ignore` filter on the branch
  of the run that finished, checked against `--ref` the same way a push is.
- GitHub's filter-pattern glob syntax: `*` (never crosses `/`), `**` (crosses `/`, and
  folds its adjoining `/` so `**/README.md` also matches a root-level `README.md`), `?`
  (zero or one of the character before it), `+` (one or more of the character or
  `[...]` class before it), `[...]` classes with ranges and negation, and `!` negation
  within a `paths`/`branches`/etc. list, processed in order the way GitHub processes it.
  `tests/test_globmatch.py` includes GitHub's own semver tag example,
  `v[12].[0-9]+.[0-9]+`, as a regression case.
- `workflow_call`: if workflow A's job calls `./.github/workflows/b.yml` and A fires, B
  is reported as reached even if B has no trigger of its own that would have matched
  this event. Chains resolve transitively with cycle protection. `$/.github/workflows/b.yml`,
  the self-repository form GitHub added in July 2026, resolves to the same file.
- The `on:` boolean-coercion trap: PyYAML's default loader resolves an unquoted `on`
  key to the Python boolean `True` under YAML 1.1 rules, so a workflow's trigger
  silently vanishes the moment you `yaml.safe_load` it. wouldrun doesn't use PyYAML
  (see "How it works" below), and `tests/test_workflow.py` exercises the fallback guard
  directly in case that ever changes.

## What it does not do

- FIRES means the trigger matches, not that the run starts. GitHub decides that
  second part on the server, after the match, and several things can veto it:
  workflow-execution rulesets that allowlist which actors and events may trigger
  a workflow, the approval hold GitHub puts on runs it flags as potentially
  malicious, the approval gate on pull requests opened by bots, a workflow
  disabled in the Actions tab, and org or repo Actions policy. None of that is in
  the workflow file, so no static reader can see it. Read FIRES as "nothing in the
  YAML stops this".
- It does not evaluate `if:` conditions or GitHub's `${{ }}` expression language. A job
  gated by `if: github.event_name == 'push'` is reported as part of the workflow's job
  list whenever the workflow fires, regardless of what the condition would actually
  decide at runtime.
- It does not evaluate `on.workflow_run`'s `workflows:` list, because nothing in the
  repo says which upstream workflow finished. The other `workflow_run` filters are
  checked, and the report names the `workflows:` list it had to leave alone.
- It does not check a `schedule:` cron expression against a clock. It confirms the
  trigger exists and shows you the cron string; whether "now" matches it is out of
  scope.
- It only reads a job's own `uses:` (the reusable-workflow call). It does not parse
  `steps:`, so step-level `uses:` (an action reference) and `if:` are invisible to it.
- It resolves `workflow_call` only for same-repo paths, written either way:
  `./.github/workflows/*` or the newer `$/.github/workflows/*`. A call into another
  repo's reusable workflow is reported by name but not followed.
- It is a static tool. It never pushes, opens a PR, or runs anything. Its only
  subprocesses are two read-only git commands, each with a fixed argument list:
  `git diff --name-only` for `--diff`, and `git symbolic-ref HEAD` for the `--ref`
  default.

## How it works

wouldrun does not use PyYAML. `wouldrun/yamlmini.py` is a small, from-scratch reader
for the subset of YAML that workflow files use (block and flow mappings/sequences,
quoted and plain scalars, `|`/`>` block scalars, comments) with one deliberate
difference from PyYAML's default behavior: it resolves booleans the way YAML 1.2's core
schema does (only `true`/`false`), not YAML 1.1's (which also turns `on`, `off`, `yes`,
and `no` into booleans). That difference is the entire reason this project doesn't take
a YAML dependency: the field this tool cares about most, `on:`, is exactly the field
PyYAML's default loader gets wrong. `wouldrun/globmatch.py` matches GitHub's
filter-pattern glob syntax with a linear reach-set sweep over a compiled token list,
not a translated regex: a regex where every `*` becomes `[^/]*` is ambiguous enough
that a pattern a workflow file is allowed to contain sends Python's engine into
catastrophic backtracking. `wouldrun/evaluate.py` is the trigger-matching engine
described above. Nothing here calls a model, makes a network request, or writes
anything. It shells out in exactly two places, both a fixed `argv` list and never a
shell string: `git diff --name-only` for `--diff`, and `git symbolic-ref HEAD` to
read the current branch when you don't pass `--ref`.

## Contributing

Found a case where wouldrun's verdict disagrees with what GitHub actually did? Open an
issue with the workflow snippet and the event that exposed it. Bug fixes land with a
test in `tests/test_evaluate.py` or `tests/test_globmatch.py` so a fixed case stays
fixed; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. Free to use, change, and ship, commercial or not. See [LICENSE](LICENSE).

## Support

If wouldrun saved you a push just to see what fires, [sponsoring](https://github.com/sponsors/munzzyy) is what keeps it maintained.
