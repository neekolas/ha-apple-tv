# Apple TV Agent Bridge app

Use the [operator guide](DOCS.md) to install the app and integration, pair a TV and connect an MCP client. The supported baseline is tvOS 27 or later. Pairing needs no Apple Developer account.

The Python 3.13 service owns a control engine per receiver. REST and MCP share loopback port 8765. The primary stage listens on port 8766. Additional stages use ephemeral loopback ports. Supervisor exposes the pairing panel through ingress on port 8099. The stage document and websocket accept loopback clients. The watchdog and stored content accept LAN clients. Supervisor checks the host address, so its watchdog uses the liveness route on 8766.

## Build and test

From `app/`, stage the vendored pyatv fork before building. You need `rsync`, `uv` and Docker with BuildKit. Use `linux/amd64` instead of `linux/arm64` for an amd64 host.

```sh
./build/stage-context.sh
docker build --platform linux/arm64 -t atv-bridge .
PYTHONPATH="$PWD/vendor/pyatv" uv run --locked pytest
```

The staging script copies the fork into the ignored `app/vendor/pyatv/` directory. Include this directory when packaging the context for a local Supervisor build. BuildKit requires the `vendor/` directory to exist in the context. If that directory exists without the fork, pip installs stock `pyatv==0.18.0` with `srptools<2`. Use the staged fork for AirPlay URL playback. The Dockerfile selects the staged path in place of the single pyatv requirement. The build downloads the matching tvpipe v0.2.0 musl archive and checks its SHA-256. Tini forwards shutdown to the service and reaps orphaned browser helpers. Full Debian Chromium and Noto fonts support tab capture, page audio, emoji and CJK text. The MP4 sender uses Playwright with that same system browser and FFmpeg for encoding.

Run the Home Assistant 2026.11.2 end-to-end test from `app/` with `tests/e2e/run.sh`. It runs HA in the integration test environment and the bridge in its Python 3.13 environment. The bridge pairs with the vendored fake TV over loopback. The test confirms a key press at the receiver after sending it through HA’s authenticated MCP proxy.

To test live framing, top-level fallback, page updates and returning to the stage in one real tvpipe session, use the network-isolated browser sink.

```sh
docker run --rm --network none -e PYTHONPATH=/opt/app \
  -v "$PWD/tests/e2e/live_page_check.py:/tmp/check.py:ro" atv-bridge python /tmp/check.py
```

To test actual page rendering, audio capture, faststart and codecs inside the image, run

```sh
docker run --rm --network none -e PYTHONPATH=/opt/app \
  -v "$PWD/tests/e2e/render_check.py:/tmp/render_check.py:ro" atv-bridge python /tmp/render_check.py
```

To smoke-test startup with no LAN access, run

```sh
smoke_dir=$(mktemp -d)
echo '{"apple_tv_host":"192.0.2.1","log_level":"info"}' > "$smoke_dir/options.json"
docker run -d --name atv-bridge-smoke --network none -v "$smoke_dir:/data" atv-bridge
docker exec atv-bridge-smoke curl --retry 20 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8766/api/health
docker exec atv-bridge-smoke sh -c 'curl -fsS -H "Authorization: Bearer $(cat /data/internal_token)" http://127.0.0.1:8765/api/health'
docker stop atv-bridge-smoke
docker rm atv-bridge-smoke
```

An unpaired installation reports `pairing_required` on 8765 and `running` on 8766.

## Interfaces

The module paths below are relative to `app/atv_bridge_app/` unless they start with `app/` or `/data/`.

`fleet.py` enrolls receivers, selects them by name or ID, starts their stages, and limits concurrent mirrors. `max_active_mirrors` defaults to one. Room names and IDs are available through `tv://tvs`.

- `atv/` contains the pyatv adapter. It tries the configured IPv4 address, the paired TV’s last known address and then multicast Bonjour. It feature-detects Companion keepalive, CompanionExtras and the fork's credential converter. Stock 0.18 compatibility includes complete reply checks, TCP keepalive and injecting the saved Ed25519 signing material into the HAP pairing procedure before PIN submission.
- `engine.py` serializes pairing, control and reconnect operations. Wake first creates a Companion-only session. After power-on it creates the full session for app state and metadata. An observed transition from off to on or screensaver also invalidates an established session. A sequence of key presses runs once.
- `tools/` owns the tool names, input schemas, validation and result construction. `tools/public.py` serializes REST and MCP calls and checks readiness for mutations. `tools/contract.py` contains the advertised tool schemas and their routing fields. `tools/navigation.py` drives labeled focus navigation. `data/apps.yaml` defines catalog links and in-app search recipes. The SDK runs stateless streamable HTTP with JSON responses. An MCP screenshot carries structured metadata and a PNG ImageContent block.
- `stage.py` extracts ZIPs into temporary directories before publishing them. It protects pages in use by any receiver from eviction. The stage websocket sends `show` and `send` messages. The browser replies with `ready` after iframe load and `sent` after calling postMessage. The stage injects /tv.js into local HTML. Live sites load it explicitly. Framing failures switch the captured tab to the top-level URL through Chromium DevTools. Static assets and generated MP4s use file responses with HTTP Range support. Stored HTML receives the helper when served.
- `present/sender.py` defines the sender interface. State changes update `GET /api/health` sender status and publish events. The default `mirror` backend supervises tvpipe. A machine-mode stop requests AirPlay teardown. In `auto`, two failed mirror starts switch the active presentation to `play_url`. Hiding resets the selection.
- `present/play_url.py` publishes a completed MP4 atomically in the receiver's media directory and serves it at `/m/<id>/<file>`. It uses the fork’s AirPlay stream and resumes from elapsed playback time after a receiver cut. A successful GET of the generated file from the receiver with the session token is the evidence for `showing`. Encoding and playback run outside the control lock. A new page or message replaces the clip. Stopping cancels recording and playback, closes Chromium and removes the file.
- `processes.py` supervises the observer and provides process cleanup. The service removes `SUPERVISOR_TOKEN` from the environment it passes to the observer, tvpipe, recording Chromium and FFmpeg.
- `app/atv_bridge_observer/` is a GPL-3.0-or-later program in its own Python process. It implements newline-delimited JSON requests with a `method` field. Supported methods are `pair_start`, `pair_finish`, `screenshot`, `ax_items`, `ax_focused`, `ax_running_apps`, `ax_release` and `health`. In discovery mode, the provider matches RemotePairing Bonjour advertisements to the saved pairing record. It uses directed DNS-SD at the resolved IP for fixed hosts and as a fallback. Observations share one UserspaceRsdTunnel and reuse the native channel between text reads. The legacy audit channel opens on demand. On TVs with the native service, display hit tests identify the process to snapshot. The snapshot supplies its labels, frames and native focus. A legacy audit hierarchy provides a fallback with unknown focus. The observer logs driver errors to stderr, which the bridge forwards to the Home Assistant app log. The main service communicates through pipes.
- `/data/internal_token` and `/data/identity.json` are shared. Each receiver keeps `pairings.json`, its observer record, tvpipe credentials and audit log under `/data/tvs/<id>/`. These files survive restarts. Supervisor discovery sends the loopback address, port and internal token to the custom integration. Tool auditing records argument names, outcome and time. It omits argument values, rotates at 10 MiB and retains one previous file.

The tests use fake TV sessions and child processes. They exercise the real MCP client and server over HTTP. Hardware checks for pairing, wake from sleep, deep links, DVT screenshots and sustained AirPlay on the Green require a separate authorized run.

The service uses the MIT license. The observer uses GPL-3.0-or-later and includes an MIT-licensed archive codec. tvpipe's release license and third-party notices are installed under `/opt/tvpipe/`.

### Schedule an overnight wake check

From the repository root on a machine that can reach Home Assistant, choose an idle receiver and a future time. This test sends both wake and sleep. Keep the process running until it finishes. Set `HA_TOKEN` or pass `--token-file` for the HA long-lived access token. The script uses the public MCP endpoint and appends one JSON line per action with the result, timestamp, elapsed seconds and errno when available.

```sh
uv run app/tests/live/overnight_wake.py --url "$HA_URL" \
  --tv "$TV_NAME" --at "$WAKE_AT" --output overnight.jsonl
```

Set `HA_URL` to your HA origin, `TV_NAME` to a name or ID from `tv://tvs`, and `WAKE_AT` to a future ISO 8601 timestamp with an explicit UTC offset. The default interval between wake and sleep is three seconds. The script exits successfully when both transitions verify.
