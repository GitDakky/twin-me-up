#!/usr/bin/env bash
# Run every check CI runs, in the same order. Usage: ./scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
# A PYTHONPATH exported for another project can shadow this project's dependencies.
unset PYTHONPATH
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"

run() { printf '\n==> %s\n' "$*"; "$@"; }

run "$PY" -m ruff check src tests scripts
run "$PY" -m ruff format --check src tests scripts
run "$PY" -m mypy
run "$PY" -m pytest -q
run "$PY" scripts/gen_integrations_doc.py
if ! git diff --quiet -- docs/integrations.md; then
  echo "docs/integrations.md is out of date. Commit the regenerated file." >&2
  exit 1
fi
printf '\nAll checks passed.\n'
