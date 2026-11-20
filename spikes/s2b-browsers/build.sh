#!/bin/sh
set -eu
cd "$(dirname "$0")"
engine=$1
target=packaged
base=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
case "$engine" in
    chromium) packages='chromium=156.0.8078.79-1~deb13u1' ;;
    shell) packages='chromium-headless-shell=156.0.8078.79-1~deb13u1' ;;
    geckodriver|firefox-av)
        target=downloaded; download=geckodriver
        packages='firefox-esr=153.7.0esr-1~deb13u1 libasound2t64'
        if [ "$engine" = firefox-av ]; then packages="$packages libavcodec61 pulseaudio"; fi ;;
    servo) target=downloaded; packages='libgl1-mesa-dri libegl1 libx11-6 libgstreamer-plugins-bad1.0-0 libgstreamer-gl1.0-0 libxkbcommon0 libwayland-client0 libdbus-1-3 libasound2t64 gstreamer1.0-plugins-base gstreamer1.0-plugins-good' ;;
    obscura) target=downloaded; packages='' ;;
    moli) target=downloaded; packages="libfontconfig1" ;;
    wpe|wpe54|wpe-audio)
        if [ "$engine" = wpe54 ]; then base=debian:sid-slim@sha256:07a3205e8583fdff12d2b05bd030f50437e9fd340ee616df0c05dc77af75767b; fi
        packages='cog gstreamer1.0-wpe gstreamer1.0-plugins-good gstreamer1.0-gl libgles2 libegl-mesa0 libgl1-mesa-dri python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0'
        if [ "$engine" = wpe-audio ]; then packages="$packages gstreamer1.0-plugins-bad"; fi ;;
    gtk) packages='gir1.2-webkit2-4.1 xvfb xauth libgl1-mesa-dri libegl-mesa0 gstreamer1.0-plugins-good python3-gi python3-gi-cairo' ;;
    qt) packages='python3-pyqt6.qtwebengine xvfb xauth libgl1-mesa-dri libegl-mesa0' ;;
    kitesurf|ladybird|ultralight) target=external ;;
    *) echo "Unknown engine: $engine" >&2; exit 2 ;;
esac
if [ "$target" != external ]; then
    docker build --platform linux/arm64 --target "$target" --build-arg "ENGINE=${download:-$engine}" \
        --build-arg "BASE=$base" --build-arg "PACKAGES=$packages" -t "s2b-$engine:runtime" .
fi
docker build --platform linux/arm64 -f Tools.Dockerfile \
    --build-arg "ENGINE_IMAGE=s2b-$engine:runtime" -t "s2b-$engine:bench" .
