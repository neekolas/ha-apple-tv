FROM golang:1.25-trixie AS build
RUN sed -i 's/^Components: main$/Components: main non-free/' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libfdk-aac-dev
RUN git clone --depth=1 --branch v0.6.0 https://github.com/omarroth/doubletake /src
WORKDIR /src
RUN test "$(git rev-parse HEAD)" = be27776beb05beaa149a7096e4722f5cba60eb37 && make doubletake doubletake-test-receiver
FROM debian:trixie-slim
RUN sed -i 's/^Components: main$/Components: main non-free/' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends chromium xvfb x11-utils fonts-dejavu-core pulseaudio pulseaudio-utils libfdk-aac2 gstreamer1.0-x gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-pulseaudio gstreamer1.0-tools \
 && rm -rf /var/lib/apt/lists/* && useradd -m lab
RUN install -d -m 1777 /tmp/.X11-unix
COPY --from=build /src/bin/* /usr/local/bin/
COPY window.sh /usr/local/bin/
USER lab
ENV XDG_SESSION_TYPE=x11 XDG_RUNTIME_DIR=/tmp/lab-runtime
ENTRYPOINT ["window.sh"]
