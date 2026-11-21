# Mirror performance on the Home Assistant Green

**At 1920x1080, page audio is the largest cost that the app controls. A muted mirror uses about one core less, renders 38% more frames, and encodes each frame in 78 ms instead of 122 ms. With audio muted, `mirror_fps` 15 sends 9.1–9.7 fps against 6.7–7.3 fps at 10. With audio playing, `mirror_fps` 15 sends no more frames than 10. GPU rendering on the Mali-G52 works, but it renders fewer frames. The SoC has no hardware H.264 encoder in this kernel. No Chromium flag that was tested changed the frame rate.**

Measured on 21 November 2026 on the Green (Rockchip RK3566, 4× Cortex-A55 at 1.8 GHz, 4 GB, HAOS kernel 6.18.58) with app 0.1.2, tvpipe v0.2.0, HeadlessChrome 156 and the Living Room Apple TV 4K (2nd generation). Every cell ran at `mirror_size` 1920x1080. The user tested 1280x720 and rejected it: motion was not much smoother, and the picture looked worse.

## Kept changes

1. **tvpipe's reports are in the app log** (commit "Log tvpipe's sent rate, encoder report and receiver reports"). At debug level the app logs:
   - the sent video fps every 5 s, with the count of each one-second report;
   - the `capture` and `started` events (capture size, route, playout latency);
   - tvpipe's `log` events at their own level. tvpipe runs with `--debug` when the app logs at debug level. From the debug and trace lines the app keeps only the capture page's encoder report (`fps`, `sourceFps`, `encodeMs`, `dropped`, `repeated`), the encoded rate at tvpipe, AirPlay send timing, the TV's receiver reports and the viewport fit.

   Before this change the app kept only the sum of `receivers[].pushed`. That sum counts audio packets: about 130 per second at 7 video frames per second. The logging costs at most 0.1 core (see the control below).
2. **Muted pages mirror at 15 fps** (commit "Mirror muted pages at 15 fps and document the cost of audio"). The new app option `mirror_fps_muted` (default 15, `0` uses `mirror_fps`) sets tvpipe's `--fps` for a show with `audio: "mute"`. `mirror_fps` stays 10 for shows with audio. A change of `audio` already restarts the sender, so the option adds no restart.
3. **The cost of audio is documented** in the `tv_show` `audio` description, `app/DOCS.md` and `docs/SETUP.md`. The advice: set `audio` to `mute` for any page that plays no sound. The default stays `play` (see open decisions).

No Chromium flag, device mapping or package was kept.

## Method

A driver on a Mac changed app options through the Supervisor API (the HA websocket command `supervisor/api`), restarted the app and waited for `/api/health` to report `running`. A temporary app option passed extra Chromium switches to `chromium-wrapper`. A temporary sampler in the app logged per-process and per-thread CPU from `/proc` every 10 s. Both are removed again.

Each cell ran this protocol on the Living Room TV:

1. **Idle:** 15 s of Supervisor `stats` with nothing presenting (first cells only).
2. **Continuous page, cold show.** The first `tv_show` after a restart starts the sender. A CSS transform animation and a `requestAnimationFrame` loop repaint about a quarter of the screen on every frame. After a 6 s warm-up: 24 s of samples and two `tv_look` screenshots with a frame counter.
3. **Burst page, warm show.** Ten tiles. Every 400 ms a focus highlight moves with a 150 ms transition. The page counts frames per transition. 20 s of samples.
4. **Static page, warm show.** One line of text. 12 s of samples.

Measurements:

- **Render fps:** `requestAnimationFrame` callbacks per second.
- **Frames per transition** (burst page): mean, with minimum–maximum. A count of n means n−1 to n frames in 150 ms. At 60 Hz the count is 10.
- **Sent fps:** video frames per second in tvpipe's `stats` events, with the range of the one-second counts.
- **Encoder:** `encodeMs` from the capture page's report is the mean time from capture to encoded chunk. `dropped` counts frames that the page dropped because the encoder queue was full.
- **Lag:** tvpipe's capture-to-tvpipe time, mean and maximum.
- **TV delivery:** tvpipe's AirPlay timing report (lateness against a fixed schedule, minimum playout headroom) and the Apple TV's `rrmt` reports (lost and late packets).
- **CPU:** Supervisor `cpu_percent` × 4, as cores, with the maximum share of four cores. **Memory:** container maximum.
- **Show time:** the time of each `tv_show` call.

Each cell ran once. Treat differences under 10–15% as noise. Static-page CPU varied from 0.30 to 0.82 cores between identical muted cells.

## Results

All cells at 1920x1080. "Audio" is the `tv_show` default (`play`). "Muted" is `audio: "mute"`, which starts tvpipe with `--no-audio`.

### Continuous page

| Cell | Render fps | Sent fps (1 s range) | `encodeMs` | Lag mean / max ms | Cores (max share) |
|---|---|---|---|---|---|
| Audio, fps 10 (before) | 7.8 | 6.8 (4–9) | 122 | 116 / 329 | 3.50 (94%) |
| Audio, fps 15 | 7.6 | 7.0 (4–9) | 117 | 94 / 219 | 3.29 (88%) |
| Muted, fps 10 | 10.8 | 7.3 (5–9) | 78 | 70 / 233 | 2.42 (69%) |
| **Muted, fps 15** | 10.0 | **9.2 (6–11)** | 82 | 71 / 403 | 2.69 (80%) |
| Muted, fps 20 | 10.0 | 9.2 (6–12) | 79 | 68 / 293 | 2.61 (84%) |
| GPU, audio, fps 10 | 5.4 | 5.2 (4–6), 24 dropped | 95 | 84 / 367 | 2.76 (79%) |
| GPU, muted, fps 15 | 6.3 | 6.2 (5–7), 34 dropped | 63 | 59 / 132 | 1.95 (65%) |

### Burst page (focus moves)

| Cell | Frames per transition | Sent fps (1 s range) | `encodeMs` | AirPlay late max ms | Cores |
|---|---|---|---|---|---|
| Audio, fps 10 (before) | 2.3 (1–4) | 6.7 (2–10) | 125 | 447 | 3.25 |
| Audio, fps 15 | 2.2 (1–3) | 7.1 (3–9) | 151 | 759 | 3.30 |
| Muted, fps 10 | 2.9 (2–4) | 6.7 (5–9) | 75 | 9 | 2.39 |
| **Muted, fps 15** | 2.7 (2–5) | **9.1 (6–11)** | 73 | −8 | 2.71 |
| Muted, fps 20 | 2.8 (1–4) | 9.7 (6–11) | 74 | 32 | 2.82 |
| GPU, audio, fps 10 | 1.7 (1–2) | 5.1 (4–6) | 87 | −168 | 2.53 |
| GPU, muted, fps 15 | 2.0 (2–2) | 6.0 (5–7) | 61 | 76 | 1.89 |

### Static page and show times

| Cell | Static cores | Cold show s | Warm show s |
|---|---|---|---|
| Audio, fps 10 (before) | 1.34 | 16.9 | 3.0–3.5 |
| Audio, fps 15 | 1.44 | 25.6 | 3.8–4.3 |
| Muted, fps 10 | 0.51 | 22.7 | 2.9–3.0 |
| Muted, fps 15 (four runs) | 0.30–0.82 | 21.5–23.3 | 2.5–3.9 |
| GPU, audio, fps 10 | 1.25 | 17.2 | 2.7–3.0 |

The cold show time (17–26 s) did not change with any variant. With nothing presenting, the container used 0.0–0.6 cores.

### Where the cores go

Per-thread samples on the continuous page, as a percentage of one core:

| Process (thread) | Audio, fps 10 | Muted, fps 10 | GPU, audio, fps 10 |
|---|---|---|---|
| Stage renderer (raster, `capture.js`, openh264 threads) | 125 + 12 | 89 + 15 | 92 + 7 |
| GPU process `VizCompositorTh` (software compositing and capture copy) | 79 | 94 | 13 |
| GPU process main thread (GL commands) | | | 70 |
| Browser process | 30 | 8 | 24 |
| App (Python) | 25 | 6 | 20 |
| Playwright driver (`node`) | 19 | 2 | 15 |
| tvpipe | 14 | 2 | 11 |

The observer used 13–20% only while `tv_look` took screenshots.

## Hypotheses

### Kept or confirmed

- **Make tvpipe's sent rate observable.** Kept (above). It showed that the earlier frame counts mixed audio and video.
- **Audio costs about one core (new).** tvpipe's `capture.js` turns tab audio into 16-bit PCM in an AudioWorklet and sends a WebSocket message about every 10 ms. Each message passes Chromium's DevTools network observer (the Playwright driver reads every frame), the app's Python page proxy, and tvpipe, which sends AirPlay audio. Muting removed 1.1 cores on the continuous page, 0.9 on the burst page and 0.8 on the static page. Render fps rose from 7.8 to 10.8 and `encodeMs` fell from 122 to 78 ms. A muted transition never dropped to one frame. The `tv_show` default stays `play`, because changing it changes the public contract.
- **Raise `mirror_fps` above the render rate when muted.** At fps 10 the muted page renders 10.8 fps but sends only 7.3. Two gates drop frames: Chromium's tab capture keeps at least 100 ms between frames, and `capture.js` skips a frame that arrives less than 98 ms after the last one. At fps 15 both gates fall to about 65 ms, and the sent rate (9.1–9.7) is close to the render rate. A tvpipe fork that changes the gate would give the same result. fps 20 is no better than 15. With audio, fps 15 sent the same 7.0–7.1 fps as fps 10. The CPU was saturated (3.3 cores), and the worst burst frame was 759 ms late against 447 ms.

### Rejected

- **GPU rendering (Mali-G52, Panfrost).** With `video: true`, Mesa 25.0.7 and `--use-gl=angle --use-angle=gles-egl --ignore-gpu-blocklist --enable-gpu-rasterization`, Chromium composites and rasters on the GPU (`ANGLE (Mesa, Mali-G52 r1 (Panfrost), OpenGL ES 3.1)`). `gl-egl` works too. It used 0.6–0.8 cores less, but rendered 31% fewer frames, and transitions fell to 1.7 frames. The GPU process main thread sat at 70%, most likely on the 1080p read-back that tab capture needs. ANGLE on Vulkan fell back to llvmpipe, because Mesa 25.0 has no Vulkan driver for this GPU.
- **Hardware H.264 encode.** The container's V4L2 nodes are the RGA scaler, the VPU decoder and the VEPU encoder (`rockchip,rk3568-vepu-enc`). The VEPU outputs only JPEG. The kernel has no driver for the RK3566's H.264 encoder (`rkvenc`). `ffmpeg -c:v h264_v4l2m2m` has nothing to use.
- **Encoder settings.** In-page `VideoEncoder` benchmark with tvpipe's configuration at 1080p (15 frames, queued at once, mirror showing a static page):

  | Variant | Focus content ms per frame | Whole frame changes ms per frame |
  |---|---|---|
  | Keyframe every 5 frames (tvpipe for AirPlay) | 64.8 | 132.3 |
  | Keyframe every 30 frames | 68.0 | 127.3 |
  | `contentHint: 'text'` | 126.1 | |
  | `contentHint: 'detail'` | 135.6 | |
  | `contentHint: 'motion'` | 70.7 | |
  | `bitrateMode: 'constant'` | 70.4 | |
  | `latencyMode: 'quality'` | 68.1 | |
  | I420 input, two runs (RGBA input) | 56.8, 81.9 (69.8, 82.7) | |

  tvpipe asks for a keyframe every `fps / 2` frames for AirPlay. Keyframes cost no more than other frames here. No option made the encoder faster, so a tvpipe fork for encoder settings is not worth it. The encoder alone caps 1080p near 12–15 fps.
- **Software paths without SwiftShader** (`--disable-gpu --disable-software-rasterizer`, muted, fps 15): 9.2 / 9.6 fps sent, 2.7 frames per transition. Same as without. Headless Chromium already composites in software: `gpu_compositing: disabled_software`.
- **Unneeded services** (`--disable-component-update --disable-sync --disable-extensions --disable-default-apps --disable-breakpad --disable-domain-reliability --metrics-recording-only --disable-client-side-phishing-detection --disable-hang-monitor --disable-ipc-flooding-protection` and Translate, OptimizationHints, OptimizationGuideModelDownloading, AutofillServerCommunication and CertificateTransparencyComponentUpdater disabled): 9.4 / 9.7 fps, 2.7 frames, static 0.42 cores, cold show 23.3 s. Within noise.
- **Compositor and raster work** (`--disable-partial-raster --num-raster-threads=1`): 9.3 / 9.6 fps, 2.7 frames. Within noise.
- **Throttling.** tvpipe already passes `--disable-background-timer-throttling`, `--disable-renderer-backgrounding` and `--disable-backgrounding-occluded-windows`. The stage page runs at full rate.
- **Rendering at the target size.** `devicePixelRatio` is 1, and the stage viewport and the capture track are 1920x1080 at the set fps. tvpipe's viewport fit races the slow window at start (`the page is 1920x1024, not 1920x1080`, then three more resizes), but the window settles at 1920x1280 with a 1920x1080 viewport. So no frame is scaled in steady state.
- **CPU limits and priority.** The container has no cgroup CPU limit (`cpu.max` is `max`). With audio, every core is busy, and the Python app, Playwright and tvpipe use about 0.6 core of it, mostly for audio. Muted, the cores are not saturated (65–84% at most). The limit is then the single `VizCompositorTh` thread at 93–95%, which priorities cannot speed up. Priorities were not changed.
- **Delivery to the TV.** TV delivery does not limit the frame rate. Every cell kept positive playout headroom, and the TV reported 0 late packets and at most 2 lost packets per minute. The uneven motion comes from uneven frame production.

### Check on the final build

The cleaned build (no probes, no flags, `mirror_fps` 10, `mirror_fps_muted` 15, with watch history from `main`):

| Show | Continuous render / sent fps | Burst frames, sent fps | Cores (continuous, burst, static) |
|---|---|---|---|
| Audio | 7.9 / 6.9 (5–9) | 2.2 (1–4), 6.7 | 3.48, 3.43, 1.95 |
| Muted (plan `1920x1080@15, no audio`) | 10.2 / 9.4 (7–11) | 2.8 (2–4), 9.2 | 2.63, 2.71, 0.70 |

With nothing presenting, the container used 0.03 cores.

### Control: logging cost

Muted, fps 15, `log_level` info (tvpipe without `--debug`, no sampler): render 9.9 fps, 2.59 cores, burst 2.5 frames, against 10.0 fps, 2.69 cores and 2.7 frames at debug. The logging costs at most 0.1 core.

## Recommendation

- **Use `audio: "mute"` for every page that plays no sound.** At 1080p this is the largest gain available.
- **For muted 1080p pages, 15 fps is the highest rate that stays steady.** It sends 9.1–9.7 fps, uses at most 84% of the CPU and keeps positive playout headroom. 20 fps sends no more. `mirror_fps_muted` now defaults to 15.
- **With audio, keep `mirror_fps` 10.** 15 fps sends no more frames and makes delivery more uneven.
- The app options are back at 1920x1080, `mirror_fps` 10 and `log_level` debug. `mirror_fps_muted` is at its default of 15.

## Open decisions and next steps

1. **Make `tv_show` mute by default** (sound becomes opt-in with `audio: "play"`). Two lines: the default in `PublicTools.tv_show` and in the contract. Expected gain for every page that does not opt in: about one core, and 9.1–9.7 instead of 6.7–7.3 sent fps (with `mirror_fps_muted` 15).
2. **Make audio cheaper in a tvpipe fork:** send PCM in larger chunks, send nothing while the tab is silent, or start the AirPlay audio stream only when sound starts. This keeps sound for pages that need it. Expected gain: most of the one core when a page is silent.
3. **Lift the compositor limit.** Muted, the software compositor thread in Chromium's GPU process is at 93–95% and caps the continuous page near 10 fps. GPU compositing moves that work off the CPU, but the read-back for tab capture is slower than the gain on Panfrost. A newer Mesa or a Chromium capture path without the full read-back could change this. Expected gain: up to the encoder's limit of 12–15 fps.
4. **Remove the DevTools observer from the capture socket.** Playwright enables the Network domain on the stage page, so Chromium and the Playwright driver process every capture WebSocket frame. A raw CDP connection with only the needed domains would save about 0.2 core with audio. It saves little when muted.
5. **Press-to-screen latency.** Muting cut the capture-to-tvpipe time from 116 to 70 ms on average. tvpipe's playout budget (`--latency`, 300 ms in the plan, AirPlay video lead 100 ms) was not changed. A glass-to-glass measurement with a camera is needed to tune it.
6. **Hardware encode** needs a kernel with an `rkvenc` driver. HAOS does not ship one, so this is not practical.
