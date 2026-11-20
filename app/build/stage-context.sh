#!/bin/sh
set -eu
app=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
test -f "$app/../vendor/pyatv/pyproject.toml" || { echo "Missing vendor/pyatv checkout" >&2; exit 1; }
mkdir -p "$app/vendor/pyatv"
rsync -a --delete --exclude=.git --exclude=.venv --exclude=__pycache__ "$app/../vendor/pyatv/" "$app/vendor/pyatv/"
