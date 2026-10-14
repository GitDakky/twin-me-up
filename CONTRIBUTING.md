# Contributing

Thank you for helping. Every kind of contribution counts: bug reports, verification reports for
agent systems, documentation, tests and code.

## Ways to help

- **Verify an integration.** Systems marked "Documented, not yet run" in the README need someone
  to try them and report back with the
  [integration form](https://github.com/GitDakky/twin-me-up/issues/new?template=integration.yml). A good report is worth as much
  as code.
- **Pick up a good first issue.** They are
  [labelled](https://github.com/GitDakky/twin-me-up/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22) and
  scoped so you can finish one in an evening.
- **Improve the docs.** Plain, short and accurate beats long and clever.
- **Share how you use your twin** in [Discussions](https://github.com/GitDakky/twin-me-up/discussions).

## Development setup

```bash
git clone https://github.com/GitDakky/twin-me-up
cd twin-me-up
uv venv && uv pip install -e '.[dev]'
./scripts/check.sh
```

`scripts/check.sh` runs exactly what CI runs: ruff lint and formatting, strict mypy, the test
suite and a check that generated docs are current. Tests need no network and no API keys.
Optionally, `pre-commit install` formats and lints on every commit.

## Principles

These are design commitments, not preferences. Pull requests that weaken them will not be
merged.

1. **Learning is ratified by the human.** Nothing an agent proposes gains authority without
   the principal's confirmation.
2. **Authority is granted, never inferred.** No prediction, score or stage grants an action.
3. **Enforcement fails closed.** If a check cannot reach a decision, the action does not
   happen.
4. **No emotion, personality or sentiment modelling.** Of anyone.
5. **Local-first, no telemetry.** The twin sends nothing anywhere on its own.
6. **Claims need evidence.** Integrations are listed as verified only with a reproducible
   check.

## Pull requests

1. For anything bigger than a small fix, open or find an issue first so we can agree the
   approach before you spend time on it.
2. Fork the repository, branch from `main` and keep each pull request focused on one change.
3. Fill in the pull request template, including how you verified the change.
4. CI must pass: tests on Linux with Python 3.11 to 3.13 and on macOS, packaging checks, CodeQL
   and dependency review.
5. A maintainer reviews every change. `main` accepts reviewed pull requests through squash
   merges only.

Write commit subjects in the imperative ("Add a Goose hook dialect") and use the body to
explain why. AI-assisted contributions are welcome: say which tool you used and review the
result yourself before asking anyone else to.

## Issue triage

New issues arrive labelled `needs triage`. A maintainer then labels them (`bug`,
`enhancement`, `integration`, `documentation`, `good first issue`, `help wanted`), asks for
anything missing, or closes them with a reason. Questions are better asked in Discussions,
where answers help everyone.

## Adding an agent system

1. Add a guide to `HOSTS` in `src/twinmeup/connect.py`.
2. If the system supports pre-action hooks, add its tool names to `HOOK_TOOLS`, the file that
   registers its hooks to `_HOST_CONFIG`, and a dialect in `src/twinmeup/hooks.py`.
3. Regenerate the integration docs: `python scripts/gen_integrations_doc.py`.
4. Verify it for real and say how in the pull request: the command you ran and what it
   returned. Update the table in the README.

## Releases

Maintainers set the new version in `pyproject.toml`, `src/twinmeup/__init__.py`,
`server.json` and `CITATION.cff`, move the "Unreleased" notes in `CHANGELOG.md` under the new
version, then publish a GitHub release tagged `vX.Y.Z`. The release workflow tests and builds
the package, attaches it to the release, publishes it to PyPI through trusted publishing once a
maintainer approves, and updates the MCP Registry listing. `tests/test_release_metadata.py`
fails if the versions drift apart.

## Licence of contributions

Twin Me Up is licensed under the [Apache License 2.0](LICENSE). Unless you say otherwise, what
you contribute is licensed under the same terms, as section 5 of the licence describes. There
is no contributor licence agreement to sign.

## Reporting bugs

Use the bug form. Include your operating system, Python version, `twinmeup version` and the
output of `twinmeup doctor`. Remove anything personal from logs and records before sharing
them.

## Conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
