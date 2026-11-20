#!/bin/sh
set -eu
version=0.2.0
# GitHub release v0.2.0 publishes these hashes in SHA256SUMS.
case "$(dpkg --print-architecture)" in
  arm64) arch=aarch64; digest=a55fa1437d43dcf87ba388ccfb7aa70f371bbd27e1ea8a2293903149b668c108 ;;
  amd64) arch=x86_64; digest=c8a5d08ad1db67a566a39d7531c30e704817cbb944a772725521f44713613b1a ;;
  *) exit 1 ;;
esac
asset="tvpipe-$version-$arch-unknown-linux-musl"
curl --fail --location --retry 3 --proto '=https' --proto-redir '=https' \
  "https://github.com/mitsuhiko/tvpipe/releases/download/v$version/$asset.tar.gz" -o /tmp/tvpipe.tgz
echo "$digest  /tmp/tvpipe.tgz" | sha256sum -c -
mkdir -p /opt/tvpipe
tar xzf /tmp/tvpipe.tgz --strip-components=1 -C /opt/tvpipe
ln -s /opt/tvpipe/tvpipe /usr/local/bin/tvpipe
rm /tmp/tvpipe.tgz
