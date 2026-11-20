#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p out
if [ ! -x out/tvpipe ]; then
    docker run --rm --network none --platform linux/arm64 --entrypoint cat \
        s2-render /usr/local/bin/tvpipe > out/tvpipe
    printf '%s  %s\n' 67d2785022fccdfb10915ad1347d00ebffeb4fa45ea6588cb7526060160fb4dd out/tvpipe | shasum -a 256 -c
    chmod +x out/tvpipe
fi
: > out/sender.jsonl
for page in static slides chart step; do
    ./run.sh chromium "$page" --share --seconds "${S2B_SECONDS:-12}" >> out/sender.jsonl
    for engine in chromium shell wpe; do
        ./run.sh "$engine" "$page" --raw --seconds "${S2B_SECONDS:-12}" >> out/sender.jsonl
    done
    printf '%s sender paths finished\n' "$page"
done
