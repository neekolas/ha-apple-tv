#!/bin/sh
# Run in series. Docker's guest CPU quota applies to the whole sender + receiver.
set -eu
cd "$(dirname "$0")"
image=${S2_IMAGE:-s2-bench}
docker build --platform linux/arm64 --target render -t s2-render .
docker build --platform linux/arm64 -t "$image" .
run() {
    docker run --rm --init --network none --cap-add SYS_PTRACE --platform linux/arm64 "$@"
}
run "$image" configs
for limit in unconstrained quota1; do
    set --
    if [ "$limit" = quota1 ]; then set -- --cpus=1; fi
    for size in 1280x720 1920x1080; do
        for page in static slides animation; do
            run "$@" "$image" share --size "$size" --page "$page" --label "$limit"
        done
    done
    run "$@" "$image" fallback --label "$limit"
    run "$@" "$image" share --fps 10 --label "$limit"
done
run --cpus=.4 --memory=768m --memory-swap=768m "$image" share \
    --page slides --fps 10 --label quota0.4-mem768
run --cpus=1 --memory=768m --memory-swap=768m "$image" share \
    --page slides --fps 10 --label quota1-mem768
