# Security

wouldrun statically evaluates GitHub Actions workflow files: which workflows
would fire for a given push, PR, or set of changed paths. It parses YAML with
a safe loader, resolves triggers and filters, and prints its verdicts. It
never executes a workflow. The only time it talks to the network is `--pr`,
which asks `gh pr view` for a pull request's base branch and changed files.

It does shell out, always read-only and always with a fixed `argv` list rather
than a shell string: `git merge-base`, `git diff --name-only` and
`git ls-files --others` when you pass `--diff`, `git symbolic-ref HEAD` to read
the current branch when you leave `--ref` off, and `gh pr view` when you pass
`--pr`. A base ref starting with
`-` is rejected before it reaches git, so a crafted `--diff` value cannot
smuggle in a flag, and every call times out after 30s.

Workflow files are still input someone else may have written. A workflow
crafted to crash the evaluator, to hang it (pathological globs or YAML), or to
make wouldrun report SKIPPED for a workflow GitHub would actually run - that
last one matters if you use wouldrun to decide what needs review - is a
vulnerability here. Plain wrong answers on well-formed workflows are ordinary
bugs; an issue with the workflow file attached is perfect.

The composite Action (`action.yml`) is a different trust boundary from the CLI
above. It runs wouldrun from its own action directory, the copy GitHub already
fetched to run the action, with no pip install. Nothing from the PR checkout is
executed: the Python steps run in the runner's temp directory, so a `wouldrun/`
or `pip/` package in the PR can't stand in for the real one. Only when
`post-comment: "true"` is set does it call the GitHub API to read and write a
PR comment. That comment path is the one part of this project that needs a
write-scoped token (`pull-requests: write`); see the README's GitHub Action
section for why it's opt-in rather than the default. On `pull_request_target`
the Action also fetches the PR's head commit, authenticated with the
`github-token` input, so it can diff it. That commit is never checked out, and
the only thing read from it is the list of paths it changed.

## Reporting a vulnerability

Please don't open a public issue for security problems. Use GitHub's private
reporting instead:

https://github.com/munzzyy/wouldrun/security/advisories/new

Include what you found, how to reproduce it, and the impact you'd expect.

## Supported versions

v0.1.0 is the only tagged release so far. Fixes land on `main` and go out with
the next tag, so run `main` if you need a fix before then.
