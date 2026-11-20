FROM ubuntu:26.04
COPY ladybird.deb /tmp/ladybird.deb
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates fonts-liberation /tmp/ladybird.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/ladybird.deb
