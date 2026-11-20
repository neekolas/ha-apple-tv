#!/bin/sh
set -eu
app=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
test -f "$app/../vendor/pyatv/pyproject.toml" || { echo "Missing vendor/pyatv checkout" >&2; exit 1; }
mkdir -p "$app/vendor/pyatv"
rsync -a --delete --exclude=.git --exclude=.venv --exclude=__pycache__ \
    --exclude=.pytest_cache --exclude='*.egg-info' --exclude=/build \
    "$app/../vendor/pyatv/" "$app/vendor/pyatv/"
uv export --project "$app" --locked --no-dev --group image-build \
    --no-emit-project --no-emit-package pyatv --no-header --quiet \
    --output-file "$app/vendor/requirements.lock"
