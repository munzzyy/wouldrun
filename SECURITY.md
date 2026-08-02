# Security

wouldrun statically evaluates GitHub Actions workflow files: which workflows
would fire for a given push, PR, or set of changed paths. It parses YAML with
a safe loader, resolves triggers and filters, and prints its verdicts. It
never executes a workflow and never talks to the network.

It does shell out, in two places, both read-only and both a fixed `argv` list
rather than a shell string: `git diff --name-only` when you pass `--diff`, and
`git symbolic-ref HEAD` to read the current branch when you leave `--ref` off.
A base ref starting with `-` is rejected before it reaches git, so a crafted
`--diff` value cannot smuggle in a flag, and both calls time out after 30s.

Workflow files are still input someone else may have written. A workflow
crafted to crash the evaluator, to hang it (pathological globs or YAML), or to
make wouldrun report SKIPPED for a workflow GitHub would actually run - that
last one matters if you use wouldrun to decide what needs review - is a
vulnerability here. Plain wrong answers on well-formed workflows are ordinary
bugs; an issue with the workflow file attached is perfect.

The composite Action (`action.yml`) is a different trust boundary from the CLI
above: it pip-installs wouldrun out of the action directory GitHub already
checked out, never from PyPI, and only when `post-comment: "true"` is set does
it call the GitHub API to read and write a PR comment. That comment path is the
one part of this project that needs a write-scoped token
(`pull-requests: write`) and talks to the network at all; see the README's
GitHub Action section for why it's opt-in rather than the default.

## Reporting a vulnerability

Please don't open a public issue for security problems. Use GitHub's private
reporting instead:

https://github.com/munzzyy/wouldrun/security/advisories/new

Include what you found, how to reproduce it, and the impact you'd expect.

## Supported versions

There is no tagged release yet. Fixes land on `main`, and that is the only
version anyone should be running.
