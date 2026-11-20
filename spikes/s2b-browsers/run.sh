#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)
mkdir -p "$here/out"
engine=$1
shift
docker run --rm --init --network none --platform linux/arm64 --cpus=1 \
    --shm-size=256m --cap-add SYS_PTRACE --entrypoint /usr/bin/python3 \
    -e PATH=/out:/usr/local/bin:/usr/bin:/bin -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$repo/spikes/s2-render:/bench/spikes/s2-render:ro" \
    -v "$here:/bench/spikes/s2b-browsers:ro" -v "$here/out:/out" \
    "s2b-$engine:bench" -u /bench/spikes/s2b-browsers/bench.py "$engine" "$@"
