FROM debian:trixie-slim AS unpack
ADD --checksum=sha256:a55fa1437d43dcf87ba388ccfb7aa70f371bbd27e1ea8a2293903149b668c108 https://github.com/mitsuhiko/tvpipe/releases/download/v0.2.0/tvpipe-0.2.0-aarch64-unknown-linux-musl.tar.gz /tmp/tvpipe.tgz
RUN mkdir /release && tar xzf /tmp/tvpipe.tgz --strip-components=1 -C /release

FROM debian:trixie-slim AS render
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-liberation && rm -rf /var/lib/apt/lists/*
COPY --from=unpack /release/tvpipe /usr/local/bin/
COPY --from=unpack /release/LICENSE* /usr/share/doc/tvpipe/
COPY chromium-wrapper /usr/local/bin/s2-chromium

FROM render AS bench
RUN apt-get update && apt-get install -y --no-install-recommends python3-websocket python3-pil && rm -rf /var/lib/apt/lists/*
WORKDIR /bench
COPY *.py *.html /bench/
ENTRYPOINT ["python3", "-u", "bench.py"]
