#!/bin/bash
# Grab one webcam frame of the TV. Usage: tools/cam.sh [out.jpg]
# Needs macOS camera permission for the calling app.
out=${1:-/tmp/cam/tv.jpg}; mkdir -p "$(dirname "$out")"
ffmpeg -hide_banner -loglevel error -f avfoundation -pixel_format uyvy422 \
  -framerate 30 -video_size 1920x1080 -i "1" -vf "select=gte(n\,15)" \
  -frames:v 1 -update 1 -y "$out" </dev/null && echo "$out"
