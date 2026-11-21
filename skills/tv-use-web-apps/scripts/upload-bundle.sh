#!/bin/sh
# Upload a TV app to the bridge once and print its bundle ID.
# Usage: upload-bundle.sh <app.html | directory with index.html>
# Needs HA_URL (for example http://homeassistant.local:8123) and HA_TOKEN.
# Then show it with: tv_show {"bundle":"<printed ID>"}
set -eu

src=${1:?usage: upload-bundle.sh <app.html | directory>}
: "${HA_URL:?Set HA_URL to your Home Assistant origin}"
: "${HA_TOKEN:?Set HA_TOKEN to a Home Assistant admin token}"

work=$(mktemp -d)
trap 'rm -r "$work"' EXIT

if [ -d "$src" ]; then
  [ -f "$src/index.html" ] || { echo "error: $src has no index.html" >&2; exit 1; }
  cp -R "$src"/. "$work/site"
else
  mkdir "$work/site"
  cp "$src" "$work/site/index.html"
  # Reference apps load their fonts from fonts/ beside the file.
  if [ -d "$(dirname "$src")/fonts" ]; then cp -R "$(dirname "$src")/fonts" "$work/site/fonts"; fi
fi

(cd "$work/site" && zip -qr ../site.zip .)

curl --fail-with-body -sS -X POST \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/zip" \
  --data-binary @"$work/site.zip" \
  "$HA_URL/api/atv_bridge/bundle" |
  sed -n 's/.*"bundle": *"\([0-9a-f]*\)".*/\1/p'
