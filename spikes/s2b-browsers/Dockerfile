ARG BASE=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
FROM ${BASE} AS base
ARG PACKAGES
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates fonts-liberation ${PACKAGES} && rm -rf /var/lib/apt/lists/*

FROM base AS release
ARG ENGINE
ADD --checksum=sha256:82ccd34067269a628e7ab79cde88b540172aec71467dfa682d5053c5a14d7738 https://github.com/servo/servo/releases/download/v0.8.0/servo-aarch64-linux-gnu.tar.gz /tmp/servo.tar.gz
ADD https://github.com/mozilla/geckodriver/releases/download/v0.37.1/geckodriver-v0.37.1-linux-aarch64.tar.gz /tmp/geckodriver.tar.gz
ADD https://github.com/h4ckf0r0day/obscura/releases/download/v0.2.7/obscura-aarch64-linux.tar.gz /tmp/obscura.tar.gz
ADD https://github.com/lexmount/moli/releases/download/v1.1.26/moli-aarch64-unknown-linux-gnu.tar.gz /tmp/moli.tar.gz
RUN mkdir /release && tar -xzf "/tmp/$ENGINE.tar.gz" -C /release

FROM base AS packaged

FROM base AS downloaded
COPY --from=release /release/ /opt/engine/
