ARG ENGINE_IMAGE=debian:trixie-slim
FROM ${ENGINE_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends     python3-websocket python3-pil && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
