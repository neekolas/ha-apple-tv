FROM debian:trixie-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sdk include /sdk/include
COPY --from=sdk bin /sdk/bin
COPY ultralight.cpp /src/frame.cpp
RUN g++ -O2 -std=c++17 /src/frame.cpp -I/sdk/include -L/sdk/bin \
    -Wl,-rpath,/opt/ultralight/lib -Wl,--allow-shlib-undefined \
    -lAppCore -lUltralight -lUltralightCore -lWebCore -o /frame

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates fonts-liberation libfontconfig1 libgl1 libxi6 libxrandr2 libxcursor1 \
    libxinerama1 libx11-6 libgtk-3-0t64 libasound2t64 libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sdk bin /opt/ultralight/lib
COPY --from=sdk resources /opt/ultralight/resources
COPY --from=sdk license /usr/share/doc/ultralight
COPY --from=build /frame /opt/ultralight/frame
ENV LD_LIBRARY_PATH=/opt/ultralight/lib
WORKDIR /opt/ultralight
