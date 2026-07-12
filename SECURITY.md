# Security

wouldrun statically evaluates GitHub Actions workflow files: which workflows
would fire for a given push, PR, or set of changed paths. It parses YAML with
a safe loader, resolves triggers and filters, and prints its verdicts. It
never executes a workflow, never shells out, and never talks to the network.

Workflow files are still input someone else may have written. A workflow
crafted to crash the evaluator, to hang it (pathological globs or YAML), or to
make wouldrun report SKIPPED for a workflow GitHub would actually run - that
last one matters if you use wouldrun to decide what needs review - is a
vulnerability here. Plain wrong answers on well-formed workflows are ordinary
bugs; an issue with the workflow file attached is perfect.

## Reporting a vulnerability

Please don't open a public issue for security problems. Use GitHub's private
reporting instead:

https://github.com/munzzyy/wouldrun/security/advisories/new

Include what you found, how to reproduce it, and the impact you'd expect.

## Supported versions

Fixes land on the latest tagged version; there's no backport policy.
