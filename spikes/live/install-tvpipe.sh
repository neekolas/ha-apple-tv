#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p bin
stage=$(mktemp -d bin/install.XXXXXX)
trap 'rm -rf "$stage"' EXIT
archive=tvpipe-0.2.0-aarch64-apple-darwin.tar.gz
curl --fail --location --silent --show-error "https://github.com/mitsuhiko/tvpipe/releases/download/v0.2.0/$archive" -o "$stage/$archive"
(cd "$stage" && echo "bd28e334b70a6994ad6d5ec0289dd1ebf4777c4ce9fb529e115c1a76e3c7ed17  $archive" | shasum -a 256 -c - && tar -xzf "$archive")
binary=$(find "$stage" -type f -name tvpipe)
cp "$binary" bin/tvpipe.new
mv bin/tvpipe.new bin/tvpipe
bin/tvpipe --version
