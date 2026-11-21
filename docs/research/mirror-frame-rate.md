# Mirror frame rate on the Home Assistant Green

**At 1920x1080 the Green renders a mirrored page at 8–28 frames per second, depending on how much of the page changes, and its software encoder manages about 15 fps. Raising `mirror_fps` above 10 at that size changed nothing that was measured. At 1280x720 the page renders at 15–46 fps and the encoder manages about 32 fps. For remote-driven pages, use `mirror_size` 1280x720 with `mirror_fps` 20.**

Measured on 20–21 November 2026 on the Green (4× Cortex-A55, 4 GB) with app 0.1.2, tvpipe v0.2.0, HeadlessChrome 156 and the Living Room Apple TV 4K (2nd generation, tvOS 27.2). Code read at bridge checkout `d635b83`. The deployed build was not compared with that checkout.

## Limits of this measurement

- **tvpipe's encoder reports were not readable.** The app reads tvpipe's machine events in `TvpipeSender._run` (`app/atv_bridge_app/present/tvpipe.py`). From a `stats` event it keeps only the sum of `receivers[].pushed` in `self.frames`. Only the test route `test_crash_tvpipe` returns that number, and the route exists only with `ATV_BRIDGE_LIFECYCLE_TEST=1`. The app does not log `stats` or `log` events. tvpipe itself logs the capture page's two-second report (`fps`, `sourceFps`, `encodeMs`, `dropped`, `repeated`) only at trace level (`share.rs`, "share: page stats"). The content iframe is sandboxed, so `tv_page_run` cannot reach `__tvpipe.capture` in the stage page.
- **So this report has no direct encoded fps and no dropped or repeated frame counts from the real sender.** It uses three other measurements: the page render rate, an encoder benchmark inside the page, and container CPU.
- **The received rate is an upper bound.** A frame counter in two screenshots gives the rate at which the page produced frames, not the rate at which the TV received them. The TV cannot receive more frames than the page renders or than `mirror_fps` allows.
- Each cell ran once, for 24 s per page. Treat differences of 10% as noise.

## Method

A driver on a Mac changed `mirror_size` and `mirror_fps` through the Supervisor API, restarted the app, and waited for `/api/health` to report `running`. The Supervisor `info`, `options`, `restart` and `stats` calls went through the HA websocket command `supervisor/api`. The REST proxy returns 401 for those paths with an admin token. Only `/logs` works over REST.

For each cell the driver showed two inline HTML pages with `tv_show` on the Living Room TV:

- **Continuous page.** A CSS transform animation moves a box across the screen. A `requestAnimationFrame` loop updates a frame counter and the page clock on every frame. Both values are also drawn as rows of black and white cells, so a script can read them from a screenshot. The rows cover about 24% of the screen and repaint on every frame. This is the heavy case.
- **Burst page.** Ten tiles. Every 400 ms a JS timer moves a scaled, shadowed focus highlight to another tile with a 150 ms transition. The page counts `requestAnimationFrame` callbacks for each transition. The count includes the first callback after the 150 ms end. So a count of n means between n−1 and n frames in 150 ms. A count of 1 means that no frame was drawn during the transition: the highlight jumped.

Measurements per page, after a 6 s warm-up:

- **Render rate:** `requestAnimationFrame` callbacks per second, read with `tv_page_run`. At 60 Hz the transition counter reads 10.
- **CPU and memory:** Supervisor `stats` every 3 s. `cpu_percent` is a share of all four cores. A check against the raw `cpu_usage` counter gave 3.3 cores at 87%.
- **Frames at the TV:** two `tv_look` image screenshots per continuous page, about 14 s apart. The driver decoded the frame counter and the page clock from each screenshot.
- **Log:** the last 100 lines of the app log after each cell.

Separately, a script in the content page ran WebCodecs `VideoEncoder` with tvpipe's own configuration (`avc1.42E0xx`, `latencyMode: 'realtime'`, `avc: {format: 'avc'}`, the app's bitrate, no `hardwareAcceleration` field). It encoded 24 prepared canvas frames as fast as possible. The mirror was showing a static page at the same time.

Driver, pages and raw results are in the session scratch folder under `fps/` (`bench.py`, `synth.js`, `results.jsonl`, `decomp.jsonl`, `synth.jsonl`). That folder is temporary.

## Results

"Render fps" is the page's `requestAnimationFrame` rate. "Received fps, upper bound" is the smaller of `mirror_fps` and the render rate. "Frames per transition" is the transition counter described above (mean, minimum–maximum). CPU is the mean in cores, with the maximum share of four cores in brackets. Memory is the container maximum.

### 1920x1080 (4,000 kbit/s)

| `mirror_fps` | Page | Render fps | Received fps, upper bound | Frames per transition | CPU cores | Memory MiB | Errors in log |
|---|---|---|---|---|---|---|---|
| 10 | continuous | 8.2 | 8.2 | | 3.3 (90%) | 640 | none |
| 10 | burst | | | 2.4 (1–5) | 3.3 (87%) | 695 | none |
| 15 | continuous | 7.9 | 7.9 | | 3.4 (92%) | 653 | none |
| 15 | burst | | | 2.1 (1–4) | 3.6 (94%) | 713 | none |
| 20 | continuous | 8.0 | 8.0 | | 3.4 (94%) | 641 | none |
| 20 | burst | | | 2.3 (1–4) | 3.5 (92%) | 680 | none |
| 30 | continuous | 7.9 | 7.9 | | 3.4 (89%) | 623 | none |
| 30 | burst | | | 2.2 (1–3) | 3.3 (89%) | 636 | none |

60 fps was not run at this size, because 30 fps already rendered at 8 fps.

A counter of 2.1–2.4 is 9–15 fps during the transition. Every 1080p burst cell had transitions with a count of 1.

### 1280x720 (2,500 kbit/s)

| `mirror_fps` | Page | Render fps | Received fps, upper bound | Frames per transition | CPU cores | Memory MiB | Errors in log |
|---|---|---|---|---|---|---|---|
| 10 | continuous | 20.0 | 10 | | 3.0 (84%) | 603 | none |
| 10 | burst | | | 5.8 (5–7) | 2.8 (81%) | 609 | none |
| 15 | continuous | 18.0 | 15 | | 3.1 (85%) | 556 | none |
| 15 | burst | | | 5.2 (4–6) | 2.9 (89%) | 569 | none |
| 20 | continuous | 15.7 | 15.7 | | 3.4 (90%) | 561 | none |
| 20 | burst | | | 5.2 (3–7) | 2.7 (74%) | 574 | none |
| 30 | continuous | 15.4 | 15.4 | | 3.4 (87%) | 566 | none |
| 30 | burst | | | 4.9 (2–6) | 3.2 (89%) | 579 | none |
| 60 | continuous | 14.8 | 14.8 | | 3.4 (89%) | 566 | none |
| 60 | burst | | | 4.8 (3–6) | 3.1 (87%) | 584 | none |

A counter of 4.8–5.8 is 27–35 fps during the transition. No 720p transition had a count of 1.

### Frames at the TV

- Every screenshot showed the page, with a clean picture and a readable frame counter. Frames reach the TV in every cell.
- The frame counter advanced between the two screenshots at the render rate (for example 7.8 fps at 1080p and 30 fps, 20.4 fps at 720p and 10 fps). This confirms the render rate. It does not measure the received rate.
- The page clock in the screenshot was 2.1–2.7 s behind `captured_at`. This includes the delay of the screenshot itself (`tv_look` took about 3.8 s), so it is an upper bound on latency. The early and late values in each cell differed by at most 0.2 s, so no backlog grew.
- The app log had no warnings or errors. The log carries no tvpipe output, so this shows only that the sender did not fail or restart.

### What costs CPU

Short runs (15 s each) of simpler pages:

| Size, `mirror_fps` | Page | Render fps | Frames per transition | CPU cores |
|---|---|---|---|---|
| 1080p, 10 | static, no script | | | 1.2 |
| 1080p, 10 | static with an empty `requestAnimationFrame` loop | 59.9 | | 1.7 |
| 1080p, 10 | one small box, CSS transform only | 27.6 | | 3.5 |
| 1080p, 10 | burst without box shadow | | 2.5 (2–4) | 3.3 |
| 1080p, 10 | burst | | 2.3 (1–4) | 3.4 |
| 720p, 20 | static, no script | | | 1.4 |
| 720p, 20 | static with an empty `requestAnimationFrame` loop | 60.0 | | 1.0 |
| 720p, 20 | one small box, CSS transform only | 45.7 | | 3.2 |
| 720p, 20 | burst without box shadow | | 4.9 (3–7) | 2.9 |
| 720p, 20 | burst | | 4.6 (3–6) | 2.8 |

- A page with no change still uses 1.0–1.4 cores.
- Any visible change saturates the Green. One small moving box takes 3.2–3.5 cores.
- The render rate depends on the output size and on how much of the page repaints. At 1080p the small box rendered 27.6 fps and the continuous page 8.2 fps. At 720p the values are 45.7 fps and 15–20 fps.
- Removing the box shadow from the burst page did not help.
- The container used 153–243 MiB with no mirror and 556–769 MiB with one.

## Encoder type

The encoder is software.

- `VideoEncoder.isConfigSupported` with tvpipe's configuration returned `true` for `hardwareAcceleration: 'no-preference'` and `'prefer-software'`, and `false` for `'prefer-hardware'`, at both sizes.
- tvpipe's `capture.js` does not set `hardwareAcceleration`, so it uses `'no-preference'`. It tries Constrained Baseline first (`42E0`), then Main and High.
- Rendering is also software. WebGL reports `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (LLVM 16.0.0)), SwiftShader driver)`.
- `app/build/chromium-wrapper` has no GPU or video-encode flags. It sets `--no-sandbox`, `--disable-dev-shm-usage`, the proxy and resolver rules, and network restrictions. tvpipe adds `--headless=new`, `--auto-accept-this-tab-capture`, `--force-device-scale-factor=1` and the window size.

Encoder benchmark in the page (P-frames only, two runs each, mirror idle beside it):

| Size | Content | ms per frame | Maximum fps |
|---|---|---|---|
| 1920x1080 | focus highlight moves, rest static | 64, 65 | 15.5 |
| 1920x1080 | whole frame changes | 116, 119 | 8.5 |
| 1280x720 | focus highlight moves, rest static | 29, 31 | 32–34 |
| 1280x720 | whole frame changes | 49, 53 | 19–21 |

The benchmark encodes RGBA canvas frames, so it includes a colour conversion that the real capture path may not need. Keyframes cost more than these P-frames. The focus content needed only 190–290 kbit/s at 30 fps.

## Where the limit comes from

The sent rate is at most the smallest of three rates: `mirror_fps`, the render rate and the encoder rate.

1. **Rendering and tab capture, in software.** Chromium composites with SwiftShader on the CPU and copies each frame for tab capture. At 1080p the render rate was 8 fps for the heavy continuous page, 9–15 fps during focus transitions and 28 fps for one small moving box. At 720p it was 15–20 fps, 27–35 fps and 46 fps. The 1080p values did not change with `mirror_fps` from 10 to 30.
2. **The software H.264 encoder.** At 1080p it needs about 64 ms per frame for focus content, which caps the rate near 15 fps. For light content at 1080p this is below the render rate, so the encoder is the likely limit there. At 720p it needs about 30 ms, which caps the rate near 32 fps.
3. **Four cores shared by both.** Every animated cell used 2.7–3.6 of the 4 cores. At 720p the continuous render rate fell from 20 fps to 15 fps when `mirror_fps` rose from 10 to 30. The likely cause is that the encoder takes CPU time from the renderer. This was not measured directly.

The earlier projection of 12–21 fps (`docs/spike-s2.md`) fits 720p. At 1080p only light content can reach its lower end.

Pixel count is the main lever. 720p has 44% of the pixels of 1080p and gave about twice the render rate and twice the encoder rate.

## Recommendation

**For interactive remote pages: `mirror_size` 1280x720, `mirror_fps` 20.**

- At 720p a 150 ms focus transition renders 4–5 frames. `mirror_fps` 20 can send 3 of them. `mirror_fps` 10 can send 1 or 2.
- `mirror_fps` 30 could send 4, but the encoder then has no spare time (30 ms per frame against a 33 ms budget), and burst CPU rose from 2.7 to 3.2 cores with peaks at 89%. That is not reliable.
- `mirror_fps` 60 gave the same result as 30.
- At 1080p a focus transition renders 1–2 frames and sometimes none. No `mirror_fps` value changed that. Keep `mirror_fps` 10 there.
- The current default (1080p, 10 fps) is right for static pages, where sharp text matters more than motion.

This is not "buttery smooth". The Green delivers 3–4 frames for a 150 ms transition at best, against 9 at 60 Hz. A page can hide this: move focus with no transition, or use a 250–300 ms transition so that more frames fall inside it.

The sent rate at 720p and 20 fps is an estimate from the render rate and the encoder benchmark. Confirm it with tvpipe's own report once the app logs it (first item below).

## Per-show override

A per-show fps override alone is not worth adding. At 1080p a higher rate changed nothing that was measured.

A per-show pair of size and fps is worth considering. For example, a show with `remote` set to `keys` or `focus` could use 1280x720 at 20 fps, and other shows could keep 1920x1080 at 10 fps.

The cost is a sender restart, because size and fps are tvpipe start arguments. In the app log, a `tv_show` that started the sender took 19.7–26.5 s (11 shows). A `tv_show` that replaced the page in a running sender took 2.7–3.9 s. So a change of size between two shows adds about 20 s. A show after a dismiss already pays this cost, because the dismiss stops the sender.

## Cheap wins

No code was changed. In order of value:

1. **Log tvpipe's reports.** The app already parses every machine event. Logging the `stats` event (pushed frames per receiver) at debug level gives a sent rate. Logging `log` events gives the codec line. The encoder report with `fps`, `sourceFps`, `encodeMs` and `dropped` needs tvpipe trace logging or a small tvpipe change that puts the page report into the machine `stats` event.
2. **Use 720p for moving pages.** See the recommendation.
3. **Tell page authors what is cheap.** Keep pages still between key presses. Avoid `requestAnimationFrame` loops and looping CSS animations. A box shadow costs nothing extra.
4. **Try the software compositor without SwiftShader** (`--disable-gpu`). It may render faster than SwiftShader. It may also break tab capture. Not tested.
5. **Hardware path.** If the Green's SoC offers a GPU and a hardware H.264 encoder to Linux, Chromium could use them. That needs device access in the container and a Chromium build that supports them. This run did not check either. It is not cheap.

Not useful:

- **Lower bitrate.** Focus content used under 300 kbit/s of the 2,500–4,000 kbit/s target. The bitrate is not the limit.
- **Keyframe interval.** tvpipe sends a keyframe every 30 frames (`capture.js`, `plan.keyInterval || 30`) and has no CLI option for it. A longer interval needs a tvpipe fork. Its effect was not measured.

## Notes on the run

- The app was restarted 13 times, about 15 s each. The original options were restored and compared with the saved copy. They match (`1920x1080`, 10 fps, log level debug).
- The first call of the session was `tv_status {}` without a `tv` field. It returned a status row for both TVs, including the Bedroom TV. It was read-only. Every later call named the Living Room TV.
- Six early `tv_page_run` calls returned `script_error`. They tried to read `parent.__tvpipe` from the sandboxed iframe.
