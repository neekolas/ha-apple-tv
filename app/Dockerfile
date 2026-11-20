ARG BUILD_FROM=debian:trixie-slim
FROM ${BUILD_FROM}
ENV DEBIAN_FRONTEND=noninteractive PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TVPIPE_BROWSER=/usr/local/bin/chromium-wrapper
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tini python3.13 python3.13-venv ffmpeg tesseract-ocr tesseract-ocr-eng chromium fonts-noto-core \
    fonts-noto-color-emoji fonts-noto-cjk libusb-1.0-0 && rm -rf /var/lib/apt/lists/* \
    && python3.13 -m venv /opt/venv
COPY build/install-tvpipe.sh /tmp/
RUN /tmp/install-tvpipe.sh
COPY build/chromium-wrapper /usr/local/bin/
WORKDIR /opt/app
COPY requirements.txt ./
RUN --mount=type=bind,source=vendor,target=/mnt/vendor \
    if [ -f /mnt/vendor/pyatv/pyproject.toml ]; then \
      cp -a /mnt/vendor/pyatv /tmp/pyatv; \
      sed -i 's|^pyatv==0.18.0$|/tmp/pyatv|' requirements.txt; \
    fi && pip install --no-cache-dir -r requirements.txt && rm -rf /tmp/pyatv
COPY atv_bridge_app atv_bridge_app
COPY atv_bridge_observer atv_bridge_observer
ENV ATV_BRIDGE_BROWSER_NAMESPACE=1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "atv_bridge_app"]
