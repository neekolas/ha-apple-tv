#!/bin/sh
set -eu
cd "$(dirname "$0")/../../.."
export PYTHONDONTWRITEBYTECODE=1
uv sync --locked --project app
exec uv run --locked --project tests/integration pytest -c tests/integration/pyproject.toml --confcutdir=app/tests/e2e -p no:cacheprovider app/tests/e2e/ha_flow.py "$@"
