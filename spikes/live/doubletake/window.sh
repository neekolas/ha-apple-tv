#!/bin/sh
set -eu
umask 077
cat > /tmp/credentials.json

mkdir -m 700 -p "$XDG_RUNTIME_DIR"
Xvfb -displayfd 3 -screen 0 1920x1080x24 -nolisten tcp 3>/tmp/display &
i=0
until [ -s /tmp/display ]; do
    i=$((i+1))
    [ "$i" -lt 100 ] || exit 1
    sleep .1
done
DISPLAY=:$(cat /tmp/display)
export DISPLAY
xdpyinfo >/dev/null
pulseaudio --start --exit-idle-time=-1
chromium --no-sandbox --disable-dev-shm-usage --disable-background-networking --kiosk file:///tmp/test.html >/tmp/chromium.log 2>&1 &
i=0
until xwininfo -root -tree | grep -q 'ATV Lab'; do
    i=$((i+1))
    [ "$i" -lt 150 ] || { cat /tmp/chromium.log >&2; exit 1; }
    sleep .2
done
exec doubletake -target-latency-ms 900 -audio-source monitor -hwaccel none "$@"
