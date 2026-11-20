# S2b · A lighter browser for the Home Assistant Green

Tested November 19, 2026 on an M4 Max in native ARM64 Linux containers. The deployment target was Home Assistant Green (RK3566, four Cortex-A55 cores and 4 GB shared with HA). The trials used an in-container receiver on loopback.

**The bridge ships full Debian Chromium with `tvpipe share`.** Headless shell preserves Blink rendering and saves about 235 MiB on the chart's complete raw path. It loses tab audio and spends more CPU feeding tvpipe's encoder. The chart delivered 8.66 fps at 21.45% of one M4 guest core with direct share, versus 9.17 fps at 37.00% with shell raw. WPE, Kitesurf and the other smaller engines did not match that combination of page fidelity, audio and CPU.

## What has to work

The bridge keeps a top-level stage open. A sandboxed iframe shows the current bundle. The stage receives updates over a WebSocket and forwards `tv_update` data to the iframe via `window.postMessage`. The mirror sender starts full Chromium through `tvpipe share`. Live tailnet URLs also need browser networking and WebSockets. Pages that refuse framing can use the captured top-level tab, and the sender uses Playwright over CDP to navigate it and deliver updates. The MP4 fallback uses Playwright and Chromium tab capture before FFmpeg builds the clip. Replacing just the mirror leaves that fallback's Chromium dependency in the image. [Architecture](ARCHITECTURE.md), [stage page](../app/atv_bridge_app/data/stage.html), [mirror sender](../app/atv_bridge_app/present/tvpipe.py), [MP4 renderer](../app/atv_bridge_app/present/render.py)

tvpipe 0.2.0 can capture a Chromium tab and pass WebCodecs H.264 and captured audio to the same receiver. Its Firefox backend uses WebDriver BiDi and Firefox's privileged tab-capture path when it prefers the current tab. The other integration is `tvpipe --machine play --raw-video 1920x1080@10:<format> --encoder builtin --raw-audio 48000:2:s16le -`. Write the `SLRW` magic, kind-1 video and kind-2 PCM records with timestamps on one clock. RGB24 is 6.22 MB/frame or 62.2 MB/s at 10 fps. BGRA is 8.29 MB/frame or 82.9 MB/s. An exporter can hold the last frame and repeat it at 1–3 fps while idle. An app-owned TTS track can be mixed into PCM separately. That would require a contract for the audio asset, its start and stop, and sync with the page. It would leave arbitrary page media and Web Audio outside the mix. [tvpipe capture implementation](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/crates/tvpipe/src/browser/capture.js), [SLRW format](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/docs/slrw.md)

## Browser survey

This survey records the versions and vendor descriptions used to choose candidates at the time of the spike. The fixture results below check quality and timing separately. License names are for the project or SDK. Bundled libraries carry their own notices.

| Candidate, maturity and license | Linux ARM64 availability | JavaScript, Web APIs, CSS and fonts | Pixels and graphics | Page audio and TV frame export |
| --- | --- | --- | --- | --- |
| **Debian Chromium 156**, mature, BSD-3-Clause and third-party licenses | Debian stable security package | V8/Blink. Broad modern Web APIs, CSS, font shaping and web fonts. | Canvas 2D, WebGL, SVG and CSS animation. | Audio output and tab audio. Prefer tvpipe share via getDisplayMedia and WebCodecs H.264. CDP screenshot and screencast work. [Debian Chromium](https://packages.debian.org/trixie/chromium) |
| **chrome-headless-shell**, mature Chromium test shell, same Chromium licenses | Debian calls it `chromium-headless-shell`. Tested the same version as full Chromium. | V8/Blink and the same core CSS, shaping and graphics APIs. Omits the full browser and its tab capture UI. | Canvas 2D, WebGL subject to the software/GPU stack, SVG and CSS animation. | HTML media can play to a configured sink. CDP screencast has no audio and the shell cannot provide the tab capture required by tvpipe share. Export JPEG/PNG, decode and feed SLRW, or add a separate audio sink. [Chrome headless modes](https://developer.chrome.com/docs/chromium/headless), [Debian shell](https://packages.debian.org/trixie/chromium-headless-shell) |
| **Firefox ESR 153.7**, mature, MPL-2.0 | Debian stable security package and ARM64 geckodriver 0.37.1 | SpiderMonkey/Gecko. Broad Web APIs, CSS, web fonts and text shaping. | Canvas 2D, WebGL with a compatible GL stack, SVG and CSS animation. | HTML media output through the configured system sink. Headless screenshots via WebDriver. tvpipe has a separate Firefox share path with browser-specific prefs and WebCodecs. [Debian Firefox ESR](https://packages.debian.org/trixie/firefox-esr), [geckodriver](https://github.com/mozilla/geckodriver/releases/tag/v0.37.1), [tvpipe Firefox launch](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/crates/tvpipe/src/browser/launch.rs) |
| **WPE WebKit / Cog**, mature embedded WebKit, BSD/LGPL WebKit and MIT Cog | Debian stable has WebKit 2.48.3 + Cog 0.18.4. Debian sid has 2.54.3. | JavaScriptCore, broad DOM/CSS and WebKit font shaping. Current WPEPlatform has shared memory and DMA-BUF. | Canvas 2D, SVG, CSS animations, WebGL with suitable EGL/GPU. Cog's headless platform renders offscreen and does not provide a consumer API. | GStreamer handles HTML media. The `wpesrc` plugin offers BGRA or GL frames and dynamic PCM audio pads. A custom WPEPlatform view, legacy FDO export, or Wayland compositor can also consume frames. [WPE FAQ](https://wpewebkit.org/about/faq.html), [Cog](https://github.com/Igalia/cog), [GStreamer wpesrc](https://gstreamer.freedesktop.org/documentation/wpe/wpesrc.html) |
| **WebKitGTK 2.54**, mature, BSD/LGPL | Debian stable ARM64 security package | JavaScriptCore and modern WebKit layout/fonts. Useful maintained successor to QtWebKit for an embedded app. | Canvas 2D, SVG, CSS animations and driver-dependent WebGL. | GStreamer audio output depends on the installed plugins and sink. Tested visible-region snapshot via GTK under Xvfb. A compositor is another path. [WebKitGTK API](https://webkitgtk.org/reference/webkit2gtk/stable/), [Debian package](https://packages.debian.org/trixie/libwebkit2gtk-4.1-0) |
| **Kitesurf**, experimental, Apache-2.0, open source since Nov 13 | ARM64 Workerd container from upstream Dockerfile. Local build tested. | Boa handles page JS in WASM under Workerd. Local Docker mode also uses V8 for CDP evaluation, page `eval` and `new Function`. Partial DOM/CDP and web APIs. Stylo/Blitz CSS, software paint and web fonts. | Paints real pixels. Canvas 2D, SVG and some animations. No WebGL or full multimedia stack. | No usable page audio track or tab capture. CDP screenshot/screencast or scene streaming. Cloud Browser Run would introduce a remote service and does not exercise local Green memory. [Upstream code](https://github.com/cloudflare/kitesurf/tree/a58b15dfabc0a238a4ab37c9ba9c1813c865bf21), [Cloudflare engine design](https://blog.cloudflare.com/kitesurf/), [open-source announcement](https://blog.cloudflare.com/kitesurf-open-source/) |
| **Obscura 0.2.7**, experimental, Apache-2.0 | Upstream render-enabled ARM64 tarball | V8, evolving DOM/CDP. Native CSS block/flex/grid, font shaping and software paint. | Default release paints. Canvas, SVG and animation support are evolving. No production WebGL/compositor path. A separate build omits rendering. | Native media playback incomplete. CDP screenshot and activity-driven screencast. [Obscura release](https://github.com/h4ckf0r0day/obscura/releases/tag/v0.2.7), [renderer introduction](https://github.com/h4ckf0r0day/obscura/releases/tag/v0.2.0) |
| **Moli 1.1.26**, active agent browser, MIT or Apache-2.0 | Upstream ARM64 Linux tarball | V8, DOM and automation APIs. Taffy layout and Parley text. `--layout` enables actual on-demand paint. Default automation geometry is synthetic. | Real screenshots with layout enabled. SVG/CSS coverage varies. Upstream excludes high-fidelity Canvas/WebGL and retained compositing. | Optional audio resources do not imply media playback. Activity-driven CDP cast defaults to 1 Hz. Tested with a 100 ms poll interval. [Moli source and limits](https://github.com/lexmount/moli/tree/v1.1.26) |
| **Lightpanda 1.0.4**, active automation engine, AGPL-3.0 | Upstream ARM64 Linux binary and .deb | V8, Zig DOM and automation APIs. Web API coverage is independent of layout or paint. | No page layout or paint. No visual canvas, SVG, WebGL, animated pixels, or rendered fonts for a TV. | No page audio/export pipeline. CDP automation and DOM extraction only. No frame benchmark. [Release](https://github.com/lightpanda-io/browser/releases/tag/1.0.4), [Lightpanda's layout and paint explanation](https://lightpanda.io/blog/posts/what-is-a-true-headless-browser) |
| **Servo 0.8.0**, pre-1.0 embeddable engine, MPL-2.0 | Upstream Linux AArch64 `servoshell` tarball | SpiderMonkey, evolving DOM, Stylo and WebRender. Modern CSS and text layout with compatibility gaps. | Paints. Canvas 2D, SVG and CSS animation are supported to varying degrees. WebGL depends on GL and preferences. | GStreamer media, with incomplete playback coverage. `servoshell` headless uses WebDriver PNG or one-shot CLI output. Embedding could export from the renderer. [Servo release](https://github.com/servo/servo/releases/tag/v0.8.0), [Servo Book](https://book.servo.org/) |
| **Ladybird**, pre-alpha, BSD-2-Clause | Upstream ARM64 Ubuntu 26.04 CI package 0.1.0. Public release index was empty. | LibJS with an ARM64 JIT, LibWeb layout, independent CSS and shaping. Broadening web API coverage. | Headless pixels, Canvas 2D, SVG and CSS animations. WebGL coverage and available backend vary. | LibMedia can play supported content with a configured platform output. WebDriver PNG. No production tab capture interface. [Ladybird October update](https://ladybird.org/newsletter/2026-10-31/), [WebDriver instructions](https://github.com/LadybirdBrowser/ladybird/blob/master/Documentation/WebDriver.md), [tested CI run](https://github.com/LadybirdBrowser/ladybird/actions/runs/44428304638) |
| **Ultralight 1.4.0**, embeddable SDK, proprietary runtime with BSD/LGPL WebKit | Public free 1.4.0 Linux ARM64 archive. Public 2.0 beta download list lacks Linux ARM64. | JavaScriptCore/WebKit fork. CSS and fonts via AppCore. SDK feature set differs from current WebKit. | Offscreen BGRA bitmap or GPU texture. Canvas, SVG and CSS animation are possible. WebGL depends on SDK version and rendering backend. | SDK/version dependent media. Free 1.4 software bitmap path tested. The 2.0 docs describe AppCore audio output integration. Check license terms before distribution. [SDK downloads](https://ultralig.ht/download), [SDK source overview](https://github.com/ultralight-ux/Ultralight), [2.0 audio and graphics libraries](https://ultralig.ht/docs/2.0/linking-to-the-library) |
| **Qt WebEngine 6.8.2**, mature Chromium embedder, Qt LGPL/GPL/commercial terms plus Chromium licenses | Debian ARM64 Python Qt WebEngine package | Blink/V8, Chromium layout, fonts and general JS APIs at Qt's pinned engine version. | Canvas, SVG, CSS animation and GPU-dependent WebGL. Tested widget capture under Xvfb. | Media output with a configured sink and supported codecs. Qt widget snapshot or a display backend. Chrome's full tab capture is absent. [Qt licenses and engine](https://doc.qt.io/qt-6.8/qtwebengine-licensing.html), [Debian package](https://packages.debian.org/trixie/python3-pyqt6.qtwebengine) |
| **wkhtmltox and print replacements** | Old wkhtmltopdf has ARM64 builds. Debian trixie has no package. Chromium-based Gotenberg/Browsershot reuse the Chromium route. | wkhtmltox is LGPL-3.0 and bundles obsolete QtWebKit/JavaScriptCore. Modern JS, CSS Grid and site compatibility are insufficient. WeasyPrint (BSD) implements print CSS without page JavaScript. | Legacy static raster/PDF and basic SVG/Canvas in QtWebKit. WeasyPrint paints static PDF. Neither provides a maintained interactive canvas/WebGL/animation stream. | One-shot file output. No page PCM/live stage transport. Qt WebEngine and WebKitGTK are measured as embeddable successors. [wkhtmltopdf project status](https://wkhtmltopdf.org/status), [Gotenberg Chromium routes](https://gotenberg.dev/docs/routes#chromium), [WeasyPrint source](https://github.com/Kozea/WeasyPrint) |
| **Ekioh Flow 7.0.1**, proprietary embedded browser and Raspberry Pi technology preview | Public 64-bit Raspberry Pi OS preview under vendor terms. Generic Green SDK is vendor-supplied. | SpiderMonkey JIT, parallel layout, web fonts and bidi text. | GPU-rendered HTML, Canvas, SVG, WebGL and CSS transforms/animations. | Media tags via platform player, WebDriver or compositor capture. Excluded from the CPU-only container matrix because vendor preview targets Raspberry Pi graphics and we had no generic ARM64 SDK. [Flow capabilities](https://www.ekioh.com/flow-browser/), [preview build](https://support.ekioh.com/download/) |

## Test method

All benchmark containers used `--platform linux/arm64 --cpus=1 --network none` on an Apple M4 Max via OrbStack. No GPU or sound device was passed through. Each cell starts a fresh container and measures a blank page followed by a single full-screen page. Quota limits aggregate CPU time. It does not reproduce Cortex-A55 instruction speed or memory bandwidth. These numbers exclude Home Assistant, the app and AirPlay transport. [S2 hardware estimate and limitations](spike-s2.md)

The two S2 fixtures are the static recipe and two slides with a one-second opacity crossfade every five seconds. S2 has animated tiles and no dashboard, so S2b adds a Canvas/SVG dashboard with JavaScript updating the first bar at 10 Hz. The fourth page is a button-driven recipe step view. The stage sends a message every two seconds. The frame performs the button action, changes its HTML, attempts to play a one-second WAV via `<audio>` and sends a measurement callback. The stage uses the bridge's iframe sandbox flags. The harness replaces the production WebSocket with a local timer. The original S2 continuous tone is omitted to isolate browser paint. [S2 fixtures](../spikes/s2-render/stage.html), [S2b stage](../spikes/s2b-browsers/stage.html), [S2b fixtures](../spikes/s2b-browsers/apps.html)

CPU uses the cgroup's total core time, with process stats for the renderer, harness and tvpipe. Peak RSS and PSS sum `smaps_rollup` across the browser, subprocesses and engine-specific driver. 100% CPU is one M4 guest core. Some browser groups ran concurrently in the VM under separate quotas. The sender matrix ran its own cases in series. RSS and PSS can vary across containers as the VM shares library pages. The image table uses compressed image content and total OrbStack disk usage from the runtime tags. Those tags include the engine's required launcher and dependencies. Benchmark-only tools and tvpipe are separate.

All capture runs requested 1920×1080 at 1×. CDP used quality-75 JPEG and paced frame acknowledgements at 10 Hz. Screenshot routes requested up to 10 PNGs per second. Raw surface paths pulled BGRA at up to 10 Hz. The harness checks dimensions and counts both returned and changed frames, so 10 polls of one still frame do not count as movement. Changed means a different payload or pixel hash. This can include antialiasing or control changes. For visual diffs, a separate PNG fixes the slide at its second endpoint, the chart bar at its initial value and the step view at step two. Different font rasterization can make equivalent text differ at pixel level. [Harness](../spikes/s2b-browsers/bench.py), [diff script](../spikes/s2b-browsers/compare.py)

## ARM64 browser measurements

Each page cell is **peak engine PSS (MiB) · total CPU (%) · returned/changed fps**. Idle RSS/PSS is the median of the four blank samples. A low CPU value beside a blank or unchanging app frame means the browser skipped required work. The raw data includes renderer-only CPU, peak RSS, timing, throttle counts and the step callbacks. [Browser results](../spikes/s2b-browsers/results/arm64-browser.jsonl)

| Engine | Capture | Image compressed / disk | Idle RSS / PSS MiB | Recipe | Slides | Chart | Step |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Chromium | CDP screencast | 312 MB / 1.15 GB | 911 / 418 | 461 · 1.3 · 0.08/0.08 | 486 · 11.6 · 2.42/2.42 | 485 · 22.2 · 10.00/9.16 | 503 · 4.7 · 0.58/0.58 |
| Headless shell | CDP screencast | 256 MB / 920 MB | 408 / 202 | 250 · 1.0 · 0.08/0.08 | 284 · 12.1 · 2.33/2.33 | 278 · 21.9 · 10.00/9.17 | 278 · 2.4 · 0.58/0.58 |
| Firefox ESR | WebDriver PNG | 175 MB / 701 MB | 1,102 / 516 | 607 · 48.2 · 10.00/0.08 | 614 · 50.0 · 9.99/1.83 | 618 · 45.2 · 10.00/9.17 | 609 · 42.5 · 10.00/0.58 |
| WPE 2.48 | GStreamer BGRA | 243 MB / 952 MB | 451 / 308 | 322 · 14.3 · 10.00/0.08 | 338 · 24.8 · 10.00/2.00 | 404 · 67.4 · 10.00/7.75 | 261 · 15.9 · 9.91/0.58 |
| WPE 2.54 | GStreamer BGRA | 262 MB / 1.05 GB | 635 / 490 | 522 · 12.7 · 10.00/0.08 | 533 · 23.7 · 10.00/2.00 | 584 · 28.4 · 10.00/8.08 | 549 · 13.7 · 10.00/0.58 |
| WebKitGTK | WebKit GTK PNG | 254 MB / 1.02 GB | 653 / 424 | 456 · 54.0 · 9.83/0.08 | 471 · 63.3 · 9.41/1.33 | 497 · 66.8 · 10.00/7.66 | 474 · 53.8 · 10.00/0.58 |
| Servo | WebDriver PNG | 188 MB / 764 MB | 261 / 257 | 305 · 52.8 · 10.00/0.08 | 364 · 76.6 · 8.00/0.25 | 341 · 92.9 · 10.00/9.25 | 334 · 51.8 · 10.00/0.58 |
| Ladybird CI | WebDriver PNG | 359 MB / 1.21 GB | 185 / 161 | 4,108 · 59.1 · 9.99/0.08 | 3,781 · 73.8 · 8.75/1.50 | 4,125 · 53.9 · 10.00/9.25 | 4,129 · 50.2 · 10.00/0.58 |
| Qt WebEngine | Qt widget PNG | 291 MB / 1.10 GB | 650 / 459 | 479 · 69.1 · 10.00/0.08 | 489 · 86.5 · 7.39/1.16 | 495 · 100.1 · 8.40/7.91 | 512 · 73.3 · 9.99/0.58 |
| Kitesurf | CDP screencast | 113 MB / 480 MB | 402 / 400 | 414 · 3.3 · 0.08/0.08 | 434 · 3.1 · 0.08/0.08 | 437 · 2.1 · 0.08/0.08 | 438 · 2.3 · 0.08/0.08 |
| Moli | CDP screencast | 82 MB / 315 MB | 57 / 54 | 71 · 1.1 · 0.08/0.08 | 79 · 1.4 · 0.25/0.25 | 89 · 26.0 · 8.50/8.08 | 73 · 2.6 · 0.58/0.58 |
| Obscura | CDP screencast | 106 MB / 377 MB | 32 / 30 | 42 · 0.8 · 0.08/0.08 | 42 · 0.8 · 0.08/0.08 | 50 · 2.3 · 0.08/0.08 | 43 · 1.0 · 0.08/0.08 |
| Ultralight | BitmapSurface BGRA | 158 MB / 677 MB | 63 / 59 | 68 · 13.1 · 10.00/0.08 | 76 · 15.9 · 10.00/1.92 | 69 · 13.7 · 10.00/0.08 | 69 · 12.9 · 10.00/0.08 |

The table counts the percentage of pixels where any RGB channel differs from Chromium by more than 16 levels. The companion data also records mean absolute RGB error. The harness writes full-size captures to the local `out/` directory. Running `compare.py` adds the amplified differences. The compact [general engines](../spikes/s2b-browsers/samples/general-engines.png) and [lighter engines](../spikes/s2b-browsers/samples/lighter-engines.png) contact sheets show the same captures. [Pixel-diff results](../spikes/s2b-browsers/results/pixel-diff.jsonl)

| Engine | Recipe | Slides | Chart | Step |
| --- | ---: | ---: | ---: | ---: |
| Chromium | 0.00% | 0.00% | 0.00% | 0.00% |
| Headless shell | 0.00% | 0.00% | 0.00% | 0.00% |
| Firefox ESR | 1.07% | 0.93% | 1.80% | 1.39% |
| WPE 2.48 | 0.54% | 0.58% | 0.53% | 0.66% |
| WPE 2.54 | 0.45% | 0.44% | 0.11% | 0.60% |
| WebKitGTK | 1.70% | 1.41% | 0.79% | 1.23% |
| Servo | 1.86% | 1.46% | 1.34% | 1.39% |
| Ladybird CI | 1.65% | 1.38% | 2.05% | 1.83% |
| Qt WebEngine | 0.15% | 0.14% | 0.09% | 0.08% |
| Kitesurf | 4.24% | 3.20% | 38.05% | 17.83% |
| Moli | 0.94% | 0.77% | 0.98% | 0.73% |
| Obscura | 2.33% | 99.95% | 38.05% | 17.83% |
| Ultralight | 1.96% | 1.68% | 5.81% | 17.54% |

## What the frames show

- **Chromium and headless shell** matched pixel for pixel for all four frozen PNGs. Both drew the Canvas bars and SVG ring, ran the slide transition and advanced the step with `postMessage`. Both created a WebGL context in the software-only probe. The shell returned `NotSupportedError` when tvpipe tried to capture the tab. The presence of a `getDisplayMedia` property did not predict capture success.
- **Kitesurf** rendered both recipe endpoints. It used fallback sans-serif for the heading and packed the list tighter. The live slide stream emitted one image. The frame was blank on dashboard and step, and the step callback never reached the server. Loading the chart directly gave 9.59 changed frames/s at 27.0% total CPU and 517 MiB engine PSS. Its memory numbers include Workerd. Upstream also describes its local sandbox as unsuitable for untrusted sites. [Cloudflare benchmark design](https://blog.cloudflare.com/kitesurf/), [local security model](https://github.com/cloudflare/kitesurf/tree/a58b15dfabc0a238a4ab37c9ba9c1813c865bf21)
- **Obscura** sent a blank iframe rectangle for all four pages even though the step callbacks reached the server. Its direct, top-level chart gave 9.48 changed frames/s, 34.7% total CPU and 57 MiB engine PSS. [Top-level chart data](../spikes/s2b-browsers/results/top-level-chart.jsonl)
- **Moli** was the lightest engine that painted and updated the sandboxed dashboard and step. The chart delivered 8.08 changed frames/s at 89 MiB engine PSS before encoding. The day labels were malformed, the meter fill was missing, and the slideshow jumped between endpoints. Its media probe exposed WAV capability without advancing the playback clock.
- **Firefox, WPE, WebKitGTK, Qt and Ladybird** drew the chart and advanced the step. Text, native meters and gradient rasterization differ. Qt's frozen PNGs were close to Chromium, but the widget capture saturated the one-core quota on the chart. Servo missed the SVG ring's center text and native meter fill. It mostly jumped between the slide endpoints, and PNG captures used nearly a full M4 core on the chart. Ladybird's headless WebDriver runs grew from a small blank process set to about 4 GiB PSS within the 12-second polling windows on most pages. That observed growth alone rules out this CI build and capture path on the Green.
- **Ultralight 1.4** painted the recipes and the CSS crossfade at about 70–76 MiB PSS. The chart's Canvas region was empty, the step text did not initialize and there were no step callbacks. Its standalone 2D API pixel probe passed. That combination is a compatibility failure of the tested app/SDK path.

The capability page checks API behavior with a 1× viewport. It attempts a Canvas 2D draw/readback, WebGL context creation, SVG DOM method, active CSS animation and a WAV playback clock. A true clock says the media element advanced. The **nonzero PCM verified at the tvpipe receiver** comes from the separate full Chromium share test. WebGL context creation alone says little about shader performance on the Green. [Probe source](../spikes/s2b-browsers/probe.html)

## Sender measurements and Green estimates

Chromium direct share carried the sandboxed step page's WAV. The receiver counted 1,632 PCM records in 12 seconds, and tvpipe reported a peak sample of 0.244. CDP JPEG supplied pixels without an audio track, so the raw runs sent video alone. WPE's audio pad yielded no nonzero samples in this setup, even with the additional GStreamer plugins. The audio pad or a separately captured browser audio sink needs verification before use.

Firefox ESR's direct share first failed WebCodecs H.264 without a system encoder. With Debian `libavcodec61` and a local PulseAudio null sink, Firefox started H.264 and advertised an audio track, but tvpipe 0.2.0 aborted parsing its AVC config with `h264 sps: cropping exceeds picture size`. The logged SPS includes a duplicate leading NAL byte, consistent with Mozilla's reported AVCC defect. Firefox screenshots remain usable. [Mozilla AVCC issue](https://bugzilla.mozilla.org/show_bug.cgi?id=2031056)

The receiver was tvpipe's loopback browser-player endpoint. It counted H.264 access units and PCM records. There was no hardware receiver or AirPlay. CPU includes the exporting harness, decoder, encoder and loopback receiver. PSS in the table covers the browser and its engine-specific driver. [Sender results](../spikes/s2b-browsers/results/arm64-sender.jsonl)

| Page | Sender path | Received H.264 fps | Total CPU | Peak engine PSS |
| --- | --- | ---: | ---: | ---: |
| static | Chromium direct share | 3.25 | 11.1% | 580 MiB |
| static | Chromium → SLRW | 2.50 | 7.4% | 460 MiB |
| static | Headless shell → SLRW | 2.50 | 5.1% | 247 MiB |
| static | WPE 2.48 → SLRW | 2.50 | 18.5% | 250 MiB |
| slides | Chromium direct share | 4.00 | 24.1% | 596 MiB |
| slides | Chromium → SLRW | 4.17 | 24.0% | 496 MiB |
| slides | Headless shell → SLRW | 4.25 | 23.4% | 276 MiB |
| slides | WPE 2.48 → SLRW | 4.00 | 32.1% | 338 MiB |
| chart | Chromium direct share | 8.66 | 21.4% | 602 MiB |
| chart | Chromium → SLRW | 9.17 | 37.8% | 484 MiB |
| chart | Headless shell → SLRW | 9.17 | 37.0% | 268 MiB |
| chart | WPE 2.48 → SLRW | 7.83 | 85.4% | 406 MiB |
| step | Chromium direct share | 3.50 | 13.1% | 600 MiB |
| step | Chromium → SLRW | 2.75 | 7.9% | 502 MiB |
| step | Headless shell → SLRW | 2.67 | 7.3% | 276 MiB |
| step | WPE 2.48 → SLRW | 2.58 | 27.6% | 336 MiB |
| chart | WPE 2.54 → SLRW | 8.33 | 39.2% | 588 MiB |
| chart | Moli → SLRW | 7.99 | 39.5% | 82 MiB |
| static (2 Hz polling) | WPE 2.48 → SLRW | 2.00 | 6.8% | 322 MiB |

On the chart, full Chromium plus tvpipe used about 607 MiB PSS. The shell's raw route used about 372 MiB including its connector and built-in encoder, a **235 MiB** saving. The two estimates add process-group samples. They exclude the bridge and the full-share test server. The shell used **15.6 percentage points more M4 CPU** on the same workload. Applying S2's rough 6–10× multiplier, the measured chart corresponds to **1.3–2.1 A55 cores** for direct share and **2.2–3.7** for shell raw. That is an estimated increase of **0.9–1.6 Green cores**, with neither figure validated on the device. The shell saves about 56 MB compressed and 230 MB of OrbStack disk in isolated runtime images. The app still packages Chromium until its MP4 fallback changes too.

The shell is a sound renderer if memory becomes the binding constraint and audio can be supplied separately. Its static raw path used 5.1% CPU at 2.5 fps, and the step raw path used 7.3% with silent output. WPE 2.48's BGRA route used 85.4% CPU at 7.83 fps on the chart. WPE 2.54 used 39.25% at 8.33 fps, with 588 MiB engine PSS before counting the connector and encoder. Lowering WPE 2.48 still-page polling to 2 Hz reduced that static path to 6.8% CPU while delivering 2.0 fps.

Moli's raw chart used about 188 MiB PSS across engine, connector and encoder. It used 39.5% CPU, received about 8 fps and retained the visual and media limits above. Full Chromium is the best fit for the mixed workload. S2 proposed 720p/10 and a 768 MiB budget. The shipping app defaults to 1080p/10 at 4,000 kbit/s. Set `mirror_size` to `1280x720` for the smaller profile. [App options](../app/config.yaml)

## Reproduce

Start in the repository root on an ARM64 Docker host. Keep the same shell in `spikes/s2b-browsers` for the later commands. The sender script requires `shasum`. The packaged builds use the pinned Debian base and the package versions listed where specified in the build script. Debian package indexes can move over time. The benchmarks use only in-container loopback.

```sh
cd spikes/s2b-browsers
docker build --platform linux/arm64 --target render -t s2-render ../s2-render
for engine in chromium shell geckodriver wpe wpe54 gtk servo obscura moli qt; do
  ./build.sh "$engine" || exit 1
done
./matrix.sh chromium shell geckodriver wpe wpe54 gtk servo obscura moli qt
./sender-matrix.sh
./run.sh wpe54 chart --raw
./run.sh moli chart --raw
./run.sh wpe static --rate 2 --raw
```

`sender-matrix.sh` takes the pinned ARM64 tvpipe 0.2.0 binary from the S2 `s2-render` image. Build its `render` target first if that tag is unavailable. The binary SHA-256 is `67d2785022fccdfb10915ad1347d00ebffeb4fa45ea6588cb7526060160fb4dd`. The suite writes logs, matrix JSONL and 1080p screenshots to `out/` in the spike directory. Single `run.sh` invocations print JSON to stdout. Inspect `out/<engine>-errors.log` if a matrix cell fails. `matrix.sh` continues to the next cell. The smaller samples can be viewed without rebuilding.

Kitesurf's registry returned an anonymous pull error on this date. Building the upstream Dockerfile at `a58b15dfabc0a238a4ab37c9ba9c1813c865bf21` took under five minutes in the ARM64 VM. Run its image locally with Workerd. No cloud account is used.

```sh
mkdir -p out
git clone https://github.com/cloudflare/kitesurf.git out/kitesurf-source
git -C out/kitesurf-source checkout a58b15dfabc0a238a4ab37c9ba9c1813c865bf21
docker build --platform linux/arm64 -f out/kitesurf-source/docker/Dockerfile \
  --build-arg BAZEL_FLAGS=--jobs=8 -t s2b-kitesurf:runtime out/kitesurf-source
./build.sh kitesurf
./matrix.sh kitesurf
```

Ladybird used the ARM64 Ubuntu 26.04 package from [CI run 44428304638](https://github.com/LadybirdBrowser/ladybird/actions/runs/44428304638). It has SHA-256 `5079f283c708cf947c2d7d8fef527f83a4a2f913c442e9b927ef86752f1c607d`. The run's aggregate status was failed because an unrelated sanitizer job failed. The Ubuntu ARM64 package job succeeded. The download needs authenticated `gh` access to Actions artifacts. If the artifact has expired, use a saved copy with the same checksum. Ultralight used the 1.4.0 Free SDK archive with SHA-256 `7c577948493f52d7f647d3fd5fde6bee8c102b0448c74c460ccf695eab01a742`. Only the small exporter source is included here.

```sh
mkdir -p out/ladybird out/ultralight
gh api repos/LadybirdBrowser/ladybird/actions/artifacts/14486859210/zip > out/ladybird.download
# GitHub can return a ZIP wrapper. Locate the tested Debian package inside it.
if unzip -tq out/ladybird.download >/dev/null 2>&1; then
  unzip -p out/ladybird.download '*.deb' > out/ladybird/ladybird.deb
else
  cp out/ladybird.download out/ladybird/ladybird.deb
fi
echo '5079f283c708cf947c2d7d8fef527f83a4a2f913c442e9b927ef86752f1c607d  out/ladybird/ladybird.deb' | shasum -a 256 -c - || exit 1
docker build --platform linux/arm64 -f Ladybird.Dockerfile \
  -t s2b-ladybird:runtime out/ladybird
curl -fL 'https://ultralig.ht/api/v1/sdk/download?platform=linux-arm64&version=1.4.0' \
  -o out/ultralight.7z
echo '7c577948493f52d7f647d3fd5fde6bee8c102b0448c74c460ccf695eab01a742  out/ultralight.7z' | shasum -a 256 -c - || exit 1
bsdtar -xf out/ultralight.7z -C out/ultralight
docker build --platform linux/arm64 -f Ultralight.Dockerfile --build-context sdk=out/ultralight \
  -t s2b-ultralight:runtime .
./build.sh ladybird
./build.sh ultralight
./matrix.sh ladybird ultralight
```

To regenerate a contact sheet and compute the PNG diffs, use Pillow in the benchmark image.

```sh
docker run --rm --network none --platform linux/arm64 --entrypoint python3 \
  -v "$PWD:/work" s2b-chromium:bench /work/compare.py /work/out \
  chromium shell geckodriver wpe wpe54 gtk servo ladybird qt kitesurf moli obscura ultralight \
  --sheet /work/out/engines.png
```
