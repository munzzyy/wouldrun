# Contributing

Thanks for looking at this. It's a small, single-purpose tool and contributions are welcome.

## Setup

```
git clone https://github.com/munzzyy/wouldrun
cd wouldrun
```

Nothing to install. wouldrun is pure standard library, and so is its test suite.

## Running the tests

```
python -m unittest discover -s tests -t .
```

That's the whole suite: the YAML reader, the glob translator, workflow parsing, the
trigger-matching engine, discovery, `git diff`, and the CLI. CI runs the same command
across Linux, macOS, and Windows on Python 3.9 through 3.13.

## Fixing a filter bug

The matching engine (`wouldrun/evaluate.py`) and the glob translator
(`wouldrun/globmatch.py`) are the whole point of this tool. If you find a case where
wouldrun's FIRES/SKIPPED verdict disagrees with what GitHub actually does, land the fix
with a test in `tests/test_evaluate.py` or `tests/test_globmatch.py` that fails before
the fix and passes after. Say where you confirmed the real behavior (a GitHub Actions
run log, the docs, a reproduction) in the PR description — this project would rather
cite a source than guess at GitHub's undocumented corners.

## Zero dependencies

wouldrun has no runtime dependencies and that's a feature, not an oversight — see the
README's "How it works" section for why a generic YAML library specifically was the
wrong call here. If a change needs one, that's a reason to reconsider the change.

## License

Contributions come in under the [Blue Oak Model License 1.0.0](https://blueoakcouncil.org/license/1.0.0). By opening a PR you agree your contribution is offered on those terms.
