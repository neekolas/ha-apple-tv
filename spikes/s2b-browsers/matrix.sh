#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p out
for engine in "$@"; do
    : > "out/$engine.jsonl"
    : > "out/$engine-errors.log"
    for page in static slides chart step probe; do
        if ./run.sh "$engine" "$page" --seconds "${S2B_SECONDS:-12}" ${S2B_CAPTURE:-} >> "out/$engine.jsonl" 2>> "out/$engine-errors.log"; then
            printf '%s %s finished\n' "$engine" "$page"
        else
            printf '%s %s failed\n' "$engine" "$page" >&2
        fi
    done
done
