# S2 · Render benchmark for Home Assistant Green

The spike tested full Debian Chromium with `tvpipe share` and WebCodecs. It proposed 1280×720, 10 fps, 2,500 kbit/s and a 768 MiB renderer budget for recipes and slides. The highest browser PSS in the 720p/30 trials was 561 MiB. tvpipe used about 4 MiB. The tests ran on an M4 Max with a loopback receiver.

The bridge now defaults to **1920×1080, 10 fps and 4,000 kbit/s**. Set `mirror_size` to `1280x720` for the spike's smaller profile. The 768 MiB figure was a benchmark budget. The app manifest sets no such memory limit. For measurements on a physical Green, see the [live lifecycle report](live-browser-lifecycle.md).

## Test environment

The sender ran in a native linux/arm64 OrbStack VM on an Apple M4 Max. The container used Debian 13 trixie-slim, full Debian Chromium 156.0.8078.79-1~deb13u1, Liberation fonts and the static aarch64 musl tvpipe 0.2.0 release. The test ran with loopback networking only. It had no display server, sound device, GPU device, PulseAudio daemon or PipeWire daemon. Debian installed the libpulse0 client library. The browser ran its own audio service.

The Mac exposes 12 performance cores and four efficiency cores. OrbStack exposes guest CPUs 0–15 with identical capacity (1024), maximum frequency (2 GHz) and CPU part ID. Those numbers describe the virtual CPUs. No physical efficiency-core mapping was available for a defensible `--cpuset-cpus` test. Docker's `--cpus=1` limits aggregate CPU time across the container. Threads can still run on several vCPUs during the quota period. [OrbStack architecture](https://docs.orbstack.dev/architecture)

## Codec and audio checks

The [CDP probe](../spikes/s2-render/bench.py) called `VideoEncoder.isConfigSupported` on a fully loaded loopback page in `--headless=new`. Every request used 30 fps, 2,500,000 bit/s, realtime latency and AVC length-prefixed output.

| Resolution | Requested codec | Supported |
|---|---|---|
| 1280×720 | avc1.42E01F | true |
| 1920×1080 | avc1.42E028 | true |
| 1920×1080 | avc1.4D4028 | true |
| 1920×1080 | avc1.640028 | true |

Each share used the release binary's own tab capture, WebCodecs encoder and local browser receiver. A lightweight client connected to its loopback WebSocket, acknowledged bytes and counted complete H.264 access units. It inspected the PCM sent to the receiver. This covers sender capture, encoding and delivery. Apple TV negotiation, AirPlay transport, receiver decoding and TV playback require a separate hardware test. [tvpipe v0.2.0 player protocol](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/docs/player-protocol.md)

tvpipe reported a browser capture surface with audio and sent stereo 48 kHz signed 16-bit PCM. In the 12-run 30 fps matrix, its mean audio bitrate was about 1,536 kbit/s. The receiver measured a peak of 0.25 and estimated frequency by counting positive zero crossings in an early audio sample. The 30 fps trials delivered over 8,100 audio packets each. In these 12 trials, the frequency estimates ranged from 436 to 440 Hz. The test checks signal activity. Perceived quality and A/V sync need a playback test.

## Measurement method

The [stage](../spikes/s2-render/stage.html) serves a static recipe, two recipe slides with a one-second CSS opacity crossfade every five seconds, or 12 continuously translating and rotating gradient tiles. All three play a 440 Hz Web Audio tone at 0.25 gain. Each row uses a fresh container and profile. The measurement starts after the receiver has accepted three H.264 frames and a further three-second warm-up. Runs are serial and each steady-state sample lasts 60 seconds.

Encoded fps comes from tvpipe's two-second browser encoder reports. Received fps counts H.264 records divided by elapsed wall time. The target sets a ceiling. The capture worker skips source frames that arrive before its pacing deadline and repeats the last frame after 250 ms idle. An unchanged recipe produces roughly three repeated frames per second. A slide's average includes four still seconds between transitions. [tvpipe v0.2.0 capture worker](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/crates/tvpipe/src/browser/capture.js)

CPU is the sum of user and system tick deltas from `/proc/PID/stat` across Chromium, its helpers and descendants, sampled once per second. 100% is one guest core. tvpipe and the Python harness are counted separately. Memory is the highest one-second sum of `Rss` or `Pss` from `smaps_rollup` across live processes. RSS counts shared pages once per mapping, so PSS is the better planning figure. Very short peaks or processes between samples can be missed. The benchmark adds `SYS_PTRACE` to read child PSS.

## Direct share at a 30 fps target

CPU percentages are measured on the M4. The final column projects Chromium plus tvpipe CPU for the observed frame rate onto the Green using the 6–10× assumption below. The JSON also contains source fps, encoder timing, audio statistics and receiver drop counts. [Raw samples](../spikes/s2-render/results/m4-max.jsonl)

| Docker CPU | Output | Page | Encoded fps | Received fps | Chromium CPU | tvpipe CPU | Chromium peak RSS / PSS (MiB) | Estimated A55 cores |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Uncapped | 720p | static | 3.08 | 3.08 | 12.4% | 1.9% | 1,110 / 524 | 0.9–1.4 |
| Uncapped | 720p | slides | 7.13 | 7.11 | 22.8% | 1.9% | 972 / 454 | 1.5–2.5 |
| Uncapped | 720p | animation | 22.67 | 22.63 | 53.6% | 1.4% | 1,148 / 560 | 3.3–5.5 |
| Uncapped | 1080p | static | 3.17 | 3.17 | 8.7% | 1.1% | 1,195 / 589 | 0.6–1.0 |
| Uncapped | 1080p | slides | 7.32 | 7.33 | 27.1% | 1.7% | 1,200 / 596 | 1.7–2.9 |
| Uncapped | 1080p | animation | 24.93 | 24.98 | 59.3% | 1.0% | 1,264 / 652 | 3.6–6.0 |
| 1 CPU | 720p | static | 3.10 | 3.08 | 5.0% | 0.7% | 1,092 / 519 | 0.3–0.6 |
| 1 CPU | 720p | slides | 7.45 | 7.45 | 19.6% | 1.6% | 1,116 / 534 | 1.3–2.1 |
| 1 CPU | 720p | animation | 23.38 | 23.35 | 44.2% | 1.3% | 1,157 / 561 | 2.7–4.5 |
| 1 CPU | 1080p | static | 3.15 | 3.17 | 15.7% | 2.3% | 1,198 / 594 | 1.1–1.8 |
| 1 CPU | 1080p | slides | 7.41 | 7.41 | 29.4% | 2.2% | 1,186 / 586 | 1.9–3.2 |
| 1 CPU | 1080p | animation | 24.29 | 24.28 | 56.5% | 0.8% | 1,277 / 655 | 3.4–5.7 |

At the 30 fps target, the continuous animation supplied about 30 source frames/s. tvpipe encoded 23–25 on average. All 12 receiver runs reported zero fanout drops. tvpipe supplied the receiver with a constrained-baseline AVC decoder config (avc1.42C01F or avc1.42C028). Every stream had the requested encoded dimensions. Some startup capture events reported a shorter tab, including 1280×662 and 1920×1022 across the trials. tvpipe refits the viewport after sharing starts. A live 720p CDP spot check reported a 1280×720 viewport and video track. [tvpipe viewport handling](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/crates/tvpipe/src/browser/headless.rs)

## JPEG and raw-video fallback

The fallback runs the same animation with full Chromium. CDP emits quality-75 JPEGs via `Page.startScreencast`. The harness paces frame acknowledgements to 10 Hz, base64-decodes each JPEG, decodes it with Pillow and writes RGB24 to tvpipe. A raw 720p frame is 2,764,800 bytes, about 27.6 MB/s at 10 fps. Stdin begins with `SLRW`. Each frame has a kind-1 header, a little-endian microsecond timestamp and payload length. The binary ran `tvpipe --machine play --to browser --raw-video 1280x720@10:rgb24 --encoder builtin -` with the same bitrate and output size. The receiver confirmed H.264 and the machine event reported the builtin route. CDP screencast supplies video only. [SLRW format](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/docs/slrw.md)

| Path, 720p animation at 10 fps target | Docker CPU | Received fps | Chromium CPU | tvpipe CPU | Python CPU | All three | Chromium peak RSS / PSS (MiB) |
|---|---|---:|---:|---:|---:|---:|---:|
| Direct share + tab audio | Uncapped | 7.90 | 38.7% | 1.1% | 1.7% | 41.5% | 1,124 / 546 |
| CDP JPEG + builtin H.264 | Uncapped | 10.02 | 40.8% | 27.8% | 13.1% | 81.7% | 1,030 / 470 |
| Direct share + tab audio | 1 CPU | 7.57 | 53.7% | 1.4% | 2.1% | 57.3% | 1,133 / 550 |
| CDP JPEG + builtin H.264 | 1 CPU | 10.01 | 33.0% | 18.4% | 11.7% | 63.1% | 1,028 / 462 |

For direct share, Python only serves the test page, receives packets and samples processes. For the fallback it also decodes JPEGs and feeds tvpipe. tvpipe itself peaked at about 4 MiB PSS on direct share and 23 MiB on the fallback. The fallback delivered the full 10 fps in this VM. Its measured aggregate CPU projects to 3.8–8.2 A55 cores across the two trials, before adding audio or AirPlay transport.

## Green estimate

The target has four Cortex-A55 cores at 1.8 GHz and 4 GB RAM. For planning, assume an A55 needs 6–10 times as much core time as an M4 performance core for the same work. Treat the measured M4 guest core time as performance-core time and multiply it by 6–10. For example, 720p animation uncapped used 0.5505 M4 cores for Chromium and tvpipe together at 22.63 received fps. That implies 3.3–5.5 A55 cores at that cadence. If the renderer had three Green cores and all work scaled linearly with frames, the corresponding ceiling would be roughly 12–21 fps, capped at the observed sender cadence. Fixed page-rendering cost and serial work can lower it.

The quota run still executes each instruction on Apple silicon. The host schedules work dynamically, the experiment ran each cell once, and the estimate excludes HA Core, other apps and the real AirPlay transport. Frame pacing, memory bandwidth, page content and the Green's actual core speeds can move the result. The table gives average CPU demand for the observed content. A crossfade can demand more CPU during its one active second.

### Memory limit and audio under CPU pressure

Two additional 60-second runs used the proposed 720p/10 slide setting, a 768 MiB cgroup memory limit and no swap. Both completed. The visual average includes idle time between crossfades. The 0.4 guest-core quota is a stress case that permits 40 ms of container CPU per 100 ms period. Quota throttling can stall the audio callback even when average CPU appears to fit.

| Docker CPU | Received fps | Chromium + tvpipe CPU | Chromium peak PSS | Cgroup peak | PCM packets / 60 s | Mean source audio | Mean encode latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 CPU | 4.01 | 35.2% | 535 MiB | 359 MiB | 8,190 | 1,536 kbit/s | 5.8 ms |
| 0.4 CPU | 3.80 | 30.7% | 538 MiB | 358 MiB | 5,757 | 1,097 kbit/s | 71 ms |

The tight quota delivered 30% fewer PCM records with zero receiver fanout drops. The one-CPU control recovered the expected audio data rate. Peak PCM remained nonzero in both, but the brief zero-crossing checks in these extra runs read 479 and 499 Hz for the 440 Hz source. Inspect audio continuity on the Green before relying on it for sustained A/V. Cgroup peak is Linux's charged memory for this container. PSS includes its share of mapped pages that can already be charged elsewhere in the VM. The spike’s 768 MiB budget allowed roughly 200 MiB over the worst 720p Chromium + tvpipe PSS seen here. Complex real pages can use more.

## Container and launch

| Image, linux/arm64 | Compressed content | Unpacked layers, approx. | OrbStack Docker disk usage |
|---|---:|---:|---:|
| Debian trixie-slim | 30.2 MB | 109 MB | 139 MB |
| Render runtime | 315 MB | 843 MB | 1.16 GB |
| Benchmark with Python, WebSocket client and Pillow | 328 MB | 887 MB | 1.21 GB |

Sizes use decimal units from `docker image ls --tree` and `docker history`. The Chromium + fonts + dependency layer is 727 MB. The tvpipe binary is 6,932,160 bytes, from a 2,977,538-byte archive. The base index digest was `sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2`. The [Dockerfile](../spikes/s2-render/Dockerfile) checks the release archive SHA-256. A future build of the trixie tag can pick up a newer Chromium.

The [wrapper](../spikes/s2-render/chromium-wrapper) adds `--no-sandbox --disable-dev-shm-usage` for this root container. tvpipe sets `--headless=new`, `--auto-accept-this-tab-capture`, `--autoplay-policy=no-user-gesture-required`, `--screen-info={0,0 WxH}`, `--window-size=W,H`, `--force-device-scale-factor=1`, a temporary profile, an ephemeral debugging port and background-throttling switches. It injects the capture script with a user gesture. Use a loopback stage URL so the page has a secure context for WebCodecs and tab capture. [tvpipe v0.2.0 launch code](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/crates/tvpipe/src/browser/launch.rs)

The benchmark targets tvpipe's local browser receiver with `--to browser --port 0`. It uses `/usr/local/bin/s2-chromium` inside the benchmark image. The deployed app instead starts its own sender with the receiver's resolved address, per-TV credentials and stage URL. It writes the `SLRW` magic to machine-mode stdin. Use `tv_show` through Home Assistant to run that path. [Benchmark sender](../spikes/s2-render/bench.py) · [App sender](../app/atv_bridge_app/present/tvpipe.py)

From the repository root on an arm64 Docker host, build both images and run the loopback matrix.

```sh
spikes/s2-render/run.sh > s2-results.jsonl
```

For one workload after building, use the default `s2-bench` tag (or the tag you set in `S2_IMAGE`).

```sh
docker run --rm --init --network none --cap-add SYS_PTRACE \
  s2-bench share --page slides --size 1280x720 --fps 10 --seconds 60
```
