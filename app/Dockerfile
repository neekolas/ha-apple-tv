ARG BUILD_FROM=python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
FROM ${BUILD_FROM}
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TVPIPE_BROWSER=/usr/local/bin/chromium-wrapper
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates curl tini ffmpeg tesseract-ocr tesseract-ocr-eng chromium fonts-noto-core \
    fonts-noto-color-emoji fonts-noto-cjk libusb-1.0-0 && rm -rf /var/lib/apt/lists/*
COPY build/install-tvpipe.sh /tmp/
RUN /tmp/install-tvpipe.sh
COPY build/chromium-wrapper /usr/local/bin/
COPY build/chromium-network-policy.json /etc/chromium/policies/managed/page-network.json
WORKDIR /opt/app
RUN --mount=type=bind,source=vendor,target=/mnt/vendor \
    pip install --no-cache-dir --require-hashes --no-deps -r /mnt/vendor/requirements.lock \
    && cp -a /mnt/vendor/pyatv /tmp/pyatv \
    && pip install --no-cache-dir --no-build-isolation --no-deps /tmp/pyatv \
    && pip check && rm -rf /tmp/pyatv
COPY atv_bridge_app atv_bridge_app
COPY atv_bridge_observer atv_bridge_observer
ENV ATV_BRIDGE_BROWSER_NAMESPACE=1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "atv_bridge_app"]
