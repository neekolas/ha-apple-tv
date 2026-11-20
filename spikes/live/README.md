# Apple TV live lab

This is a hardware test harness for an Apple silicon Mac. It uses Python 3.13, the editable vendored pyatv fork and the app's control and observer code. The production bridge supports tvOS 27 or later. For normal multi-TV operation use the Home Assistant app. [Operator guide](../../app/DOCS.md)

Set `ATV_BRIDGE_HOST` to the IPv4 address of an idle test receiver before running hardware commands. The lab defaults to a documentation-only address. Its `info` command scans the specified host and reads AirPlay information on port 7000. Its control adapter can fall back to Bonjour for a saved TV. The lab stores one controller and receiver in this checkout. Start in a fresh checkout when testing a different TV. The bridge app itself supports discovery without a fixed address.

Install `uv`. Mirroring uses Chrome and the pinned tvpipe binary. The canvas probe needs Cargo. The doubletake trial needs Docker with host networking for its Linux sender. The playback trial needs FFmpeg with H.264 and AAC encoders on the Mac. Pairing requires someone to read the TV. Control, mirroring and playback trials change its screen or power state.

## Run the hardware checks

Run each command individually and inspect its exit status and `result.json`. A failed capability in one spike can coexist with successful checks in the same report.

1. Prepare the environment and inspect the TV.

   ```sh
   cd spikes/live  # From the repository root.
   export ATV_BRIDGE_HOST='<test-tv-ip>'  # Replace with the receiver's IPv4 address.
   uv sync --locked
   ./install-tvpipe.sh
   uv run atvlab --help
   uv run atvlab info
   ```

   The download is the macOS arm64 tvpipe v0.2.0 release. Its archive SHA-256 is `bd28e334b70a6994ad6d5ec0289dd1ebf4777c4ce9fb529e115c1a76e3c7ed17`. The original hardware run saw AppleTV11,1, tvOS 27.2, build 24K122, Companion on 49153 and AirPlay/RAOP on 7000. Its pre-session plist omitted `displays`. [tvpipe release](https://github.com/mitsuhiko/tvpipe/releases/tag/v0.2.0)

2. Pair AirPlay and test reuse.

   ```sh
   uv run atvlab pair airplay
   uv run atvlab s3
   ```

   For each PIN exchange, wait for `PIN_REQUIRED airplay`, `PIN_REQUIRED companion` or `PIN_REQUIRED remote_pairing` on stdout. From another zsh terminal, publish the current on-screen digits with this command sequence. Leading zeros matter.

   ```zsh
   read -rs 'atvlab_pin?TV PIN: '
   echo
   (umask 077 && printf '%s\n' "$atvlab_pin" > /tmp/atvlab/pin.new && mv /tmp/atvlab/pin.new /tmp/atvlab/pin)
   unset atvlab_pin
   ```

   The command clears stale input before starting pairing, consumes the new file once and times out after 180 seconds. AirPlay uses the saved signing seed and controller UUID and sends `X-Apple-Client-Name: Agent Bridge`.

   S3 uses the AirPlay pairing for both Companion and RAOP. RAOP verifies the pairing, writes `silence.wav`, and streams that local file to the TV. Each sender export must round-trip through the fork's converter. Check `checks.companion.session_established` before choosing a separate pairing. If the TV rejected that pairing, run the following command **once**. Repeated failures can trigger a BackOff that requires a TV restart. The command never retries pairing automatically.

   ```sh
   uv run atvlab pair companion
   ```

3. Pair the observer to read the test TV. On the TV, open **Settings → Remotes and Devices → Remote App and Devices** for manual RemotePairing discovery. [Apple device pairing guide](https://developer.apple.com/documentation/xcode/managing-your-simulated-and-physical-devices-in-device-hub)

   ```sh
   uv run atvlab s5 pair
   uv run atvlab s5 screenshot --repeat 3
   uv run atvlab screen
   uv run atvlab s5 ax-probe
   ```

   `s5` alone also starts pairing. `screenshot` and `ax-probe` work as top-level commands. Each screenshot series reuses an observer and userspace tunnel for the selected TV. The first timing includes tunnel setup. The lab’s `ax-probe` walks elements using the legacy AccessibilityAudit inspector and may move accessibility focus. The production `tv_look` uses a passive text read. DVT screenshots can show a **Screenshot Taken** toast. Repeat `screen` during S1 or S6 for an independent visual check. Read `results/latest.png` after a successful exit. An unsuccessful capture leaves the previous image there.

4. Exercise control with the test TV in view.

   ```sh
   uv run atvlab status
   uv run atvlab s4 --wake-trials 3
   uv run atvlab s4 --launch 'youtube=youtube://www.youtube.com/watch?v=jNQXAC9IVRw' --times 3
   uv run atvlab s4 --text 'Agent Bridge keyboard test'
   ```

   Each wake trial sends Sleep, checks for Off and invokes the app's fresh-session wake ladder. Each launch trial presses Home, records a baseline, sends the link, and allows eight seconds for the expected bundle ID plus a change in app or now-playing metadata. Inspect `before`, `observed` and `companion_errors` if the app opens without a verifiable transition. An app match establishes which app handled the request. Check the title or screenshot when the specific content matters.

   Repeat `--launch` for additional services. The service name comes from the app's deeplink YAML, or use an expected bundle ID on the left of `=`. YouTube text opens a search link, then applies `left,up,up,up,select` until keyboard focus appears. Change `--search-keys` to match the installed YouTube layout. The result requires readback of the supplied text.

5. Probe the mirroring canvas, then run both senders.

   ```sh
   uv run atvlab s1 --canvas-only
   uv run atvlab s1 --minutes 5 --visible
   uv run atvlab s1 --sender doubletake --minutes 5
   ```

   Canvas-only performs HAP verification, the advertised mirroring handshake, control SETUP, display inspection and TEARDOWN. It sends zero audio/video stream descriptors. On first use it builds a small Cargo helper against tvpipe v0.2.0's pinned handshake library. A receiver that requires media-first SETUP can reject this probe. An absent display block is recorded as JSON null.

   The full tvpipe run captures the local clock page with Chrome. With `--visible`, click the tone button to hear 440 Hz and compare 18 pt, 24 pt and 32 pt text on the TV. Doubletake v0.6.0 builds from commit `be27776beb05beaa149a7096e4722f5cba60eb37` with Go in Docker. It captures a Chromium kiosk on a dedicated Linux X11 display. It uses PulseAudio and a 900 ms playout budget because the local monitor source lagged the sender by about 550 ms in the fake trial. The timer starts at sender readiness. [Doubletake v0.6.0](https://github.com/omarroth/doubletake/tree/v0.6.0)

6. Run the five-minute playback trials.

   ```sh
   uv run atvlab s6 --repeat 3
   ```

   The harness generates and caches a 300-second, 1920×1080 H.264/AAC MP4 with a running clock. It serves that file with HTTP Range on the Mac's interface toward the TV and an ephemeral port. Each trial calls `play_url`, samples the receiver's state every two seconds and records FCUP requests. The duration starts at receiver-reported playback. Less than 290 seconds sets `early_cut`. A startup failure has no measured duration. The per-trial watchdog is 350 seconds.

## Manual helpers

```sh
uv run atvlab power on
uv run atvlab press home
uv run atvlab launch com.google.ios.youtube
uv run atvlab press left up select
uv run atvlab status
uv run atvlab screen
uv run atvlab power off
```

`launch` also accepts a URL. It reports the Companion acknowledgement or rejection and a state snapshot. Use the S4 matrix for a timed verification against an expected app. A key sequence is delivered once.

## Results and state

Each lab operation prints the absolute result path. The directory is `results/<UTC timestamp>-<command>/`. Exit 0 indicates `ok: true`. Exit 1 indicates a failed check, protocol error, timeout or interrupted run. Result files and private state are gitignored.

| File or field | Meaning |
| --- | --- |
| `result.json` | Target, command, start, elapsed seconds, overall outcome and command-specific evidence. |
| `run.log` | Timestamped command and connection events. S6 logs media requests and their Range headers. |
| `info.plist` in the JSON | Every decoded plist key. Binary data is represented as a base64 object. `scan.devices` includes service identifiers, ports and TXT properties. |
| `checks` and `silence.wav` | S3 reuse and conversion results. The WAV is the two-second stereo PCM source, 44.1 kHz. |
| `trials` | S4 results by action and trial, including acknowledged Home, wake attempts, launch errors and text readback. S6 results include duration, cut time, state samples and FCUP message/request IDs. |
| `tvpipe.log` | Raw verbose machine events. The JSON summarizes canvas/session-ready lines, receiver losses, reconnects and dropped frames. |
| `doubletake-build.log`, `doubletake.log` | Pinned build output and sender output. The JSON records the negotiated capture, ready line, reported losses/reconnects and stream errors. |
| `screen-001.png` etc. | DVT captures with dimensions and per-capture elapsed seconds in `captures`. `screen` also atomically updates `results/latest.png`. Protected video can appear black. |
| `rsd_services`, `capabilities`, `list_items` | S5 AX probe. The methods report independently. The walk keeps partial items on failure and caps collection at 100 elements or 30 seconds. |

The lab's `state/` contains private controller and receiver credentials. It uses the legacy single-TV layout, including `observer/remote.plist` and separate tvpipe and doubletake exports. Use the integration's pairing flow on a deployed bridge. Replacing its shared controller identity would affect every paired receiver.

## Local verification

```sh
uv run --locked pytest -q
ATVLAB_SENDER_TEST=1 uv run --locked pytest -q
```

The default suite uses pyatv fake AirPlay, Companion, MRP and RAOP receivers, a loopback Range server, and observer service doubles. It covers both PIN pairings, client-name headers, reuse, received audio packets, sleep/wake, launch verification and rejection codes, text readback, URL playback cuts, FCUP event recording, screenshots and AX results.

The opt-in test runs the pinned macOS tvpipe and Linux doubletake binaries. It requires Chrome, Cargo, Go and Docker on the test workstation. A local doubletake diagnostic receiver verifies the encrypted control session, canvas-only teardown and arrival of audio/video packets from each sender. Its modern test profile requires RTSP request lines and pairing type 5. The fixture adapts those headers for this receiver. The hardware path uses the fork's native HAP exchange. A Go build overlay omits a Linux-only capture process flag when compiling the diagnostic receiver for macOS. The Linux sender builds from the upstream tag.
