# tv-use architecture

Home Assistant exposes one bridge endpoint for all registered Apple TVs. The app owns each receiver's control session, screen pairing and presentation. The recommended and tested baseline is tvOS 27 or later; tvOS 26.6 also works but is less tested. Hardware testing included Apple TV 4K (2nd generation), AppleTV11,1, on tvOS 27.2 (24K122). Pairing requires access to the TV and its on-screen codes. It requires no Apple Developer account. [Install, pair and operate the bridge](../app/DOCS.md)

## Request path

| Caller | Destination | Purpose |
| --- | --- | --- |
| MCP client or script | Home Assistant's configured HTTP origin | Admin bearer token. POST to `/api/atv_bridge/mcp`, `/api/atv_bridge/tool/<name>` or `/api/atv_bridge/bundle`. |
| HA integration | `127.0.0.1:8765` | Internal bearer token. Forwards requests to `/mcp`, `/api/tool/<name>` and `/api/bundle`. |
| Supervisor | Ingress port `8099` | Serves the pairing panel through **Open Web UI**. |
| Chromium | Receiver's loopback stage origin | Loads `/stage` and connects to `/stage/ws`. The websocket requires a matching Origin. |
| Supervisor and LAN devices | Host port `8766` over HTTP | Liveness at `/api/health`, stored content at `/c/<page_id>/<file>`, and generated media at `/m/<receiver_id>/<file>`. |

The app uses host networking. The primary stage listens on all interfaces at port 8766. Additional stages bind ephemeral loopback ports. Stage documents and websockets accept only local clients. The watchdog reports `running` before any TV is paired. The authenticated API on 8765 reports receiver health.

## Processes and storage

| Source | Responsibility |
| --- | --- |
| `app/atv_bridge_app/` | Python 3.13 service. Runs aiohttp, the MCP SDK, the fleet registry and a control engine per receiver. Tini starts the service. |
| `vendor/pyatv/` and `vendor/pyatv-patches/` | Installable fork and ordered diffs. The deployer stages the fork in the Docker build context. |
| `app/atv_bridge_app/present/` | Supervises tvpipe and Chromium for mirroring. The recording path uses Playwright, Chromium and FFmpeg. |
| `app/atv_bridge_observer/` | Separate Python process using pymobiledevice3. Exchanges JSON lines over pipes. Reads accessibility over a paired tunnel and captures images through DVT. |
| `custom_components/atv_bridge/` | HA config flow, receiver entities, repairs, pairing notifications, LLM tools and admin HTTP views. |
| `tools/deploy/deploy.py` | Stages and deploys the app and custom integration to a Supervisor installation. |

Supervisor writes `/data/options.json`. The service creates a shared `/data/identity.json` with a controller UUID, 32-byte Ed25519 seed and the name **Agent Bridge**. It creates `/data/internal_token` and advertises the internal host, port and token through Supervisor discovery.

Under `/data/tvs/<id>/`, `receiver.json` identifies the receiver and `pairings.json` records protocol pairings and the last authenticated TV identity. Screen pairing uses `observer/remote.plist`. Mirroring uses `tvpipe/credentials.txt`. Generated clips use `media/`. Tool audit records contain argument names, outcome and time. Each `audit.jsonl` rotates at 10 MiB and keeps one previous file. Upgrades from the legacy single-TV layout copy its records to `/data/single-tv-backup/` before migration.

Stored pages share `/data/content/` and a 500 MiB budget. The cache protects pages in use. ZIP upload limits are 50 MiB compressed, 50 MiB unpacked and 8,192 entries.

## Receiver discovery and control

Leave `apple_tv_host` and `apple_tv_id` empty to select an advertised TV during setup. A static IP is optional. Resolution tries a configured IPv4 address and the last known address before Bonjour discovery. It matches advertised identifiers and can fall back to a unique exact saved name. Discovery filters for Apple TV models advertising Companion. Pairing or a successful authenticated connection saves the resolved identity and address.

AirPlay PIN pairing supplies the controller credentials. The bridge tests reuse for Companion and can request a separate Companion PIN. Remote Pairing is a separate six-digit exchange for screen access. The Companion session uses a 15-second NoOp timer. The engine rebuilds its session after an observed off-to-on transition. Wake uses a fresh Companion session and a HID wake command. Each of up to two attempts waits up to five seconds for on or screensaver after the command, then opens the full session. Background reconnect delays grow from one to 60 seconds.

## Tool routing and results

Each advertised tool accepts an optional `tv` selector. Use a name or ID from `tv://tvs`. With one paired receiver, it is the default. With only one registered receiver, it can also be selected before pairing. When the choice is ambiguous, changes return `tv_required`. Unscoped `tv_status` and `tv_look` return per-TV summaries in `data.tvs`. The latter uses text reads. An unknown selector returns `tv_not_found`.

| Tool | Operation |
| --- | --- |
| `tv_status` | Check reachability, pairing, capabilities, browser use and problems. |
| `tv_pair` | Start, poll, finish or cancel a control or screen exchange on a registered TV. |
| `tv_look` | Read accessible labels and focus. Request an image for a DVT capture. |
| `tv_open` | Launch an installed app by name or ID. Optionally search or follow a labeled path. |
| `tv_navigate` | Focus a label and optionally select it. |
| `tv_remote` | Send keys, a swipe or text. Keys go out in one burst with one screen read at the end and after Home; `check_each` reads after every key. |
| `tv_sequence` | Execute bounded, optionally repeated steps while holding the receiver lock. Raw keys check delivery and explicit checkpoints read accessibility. |
| `tv_power` | Request on or off and check the receiver's power state. |
| `tv_play` | Play YouTube, a media URL or an app title, or open a deep link and report where it landed. |
| `tv_show` | Show a URL, HTML, inline files or a stored bundle. With only the selector, dismiss that TV's page. |
| `tv_page_run` | Await caller JavaScript in the presented document and return bounded JSON. The `page_scripts` app option controls this tool. |

Tool calls wait up to ten seconds for the selected TV's tool lock. Results contain `ok`, `summary`, `verified`, `evidence` and `data`. All tools except `tv_status` include `screen`. Errors add `code`, `message`, `retryable` and `fix` under `error`. In a fleet summary, inspect the individual receiver results. The bridge has no purchase guard.

MCP exposes `tv://tvs`, `tv://screen`, `tv://apps`, receiver-specific screen and app resources, and `tv://page-guide`. The `present` prompt requires `topic`. The unlisted `tv_hide` alias also dismisses a page. [Implemented schemas](../app/atv_bridge_app/tools/contract.py)

## Presenting a page

The default mirror uses 1920×1080 at a target of 10 fps and 4,000 kbit/s. The 1280×720 option uses 2,500 kbit/s. Chromium loads the page and tvpipe sends the tab over AirPlay. The stage swaps iframes on a page change. When a live site blocks framing, the sender opens it in the captured tab. The next show can return to the stage. A live URL must be reachable from the HA host. A media URL supplied to `tv_play` must be reachable from the Apple TV.

Page control follows the captured tab or the stage iframe identified by the current presentation revision. One evaluation checks the committed document URI and executes the script in that frame. The caller supplies plain JavaScript and optional JSON input. Chromium awaits the result and caps its serialized size before sending it to the app. Each call follows the current content document after navigation. The capture browser starts with `--autoplay-policy=no-user-gesture-required`. The stage iframe allows autoplay. The admin-only script tool runs in the current content document. Its code runs with page privileges. Bridge listeners and browser-internal documents are excluded.

For stored pages, `play_url` renders an MP4 for the receiver to fetch. In `auto` mode two failed mirror starts trigger recording. Live URLs always use the mirror. The default mirror limit is one. A full fleet returns `mirror_busy`. A show with `replace: true` can stop another receiver's presentation.

## Page network security

The mirror and MP4 renderer each start a loopback HTTP proxy before launching Chromium. A fixed proxy and the `<-loopback>` bypass rule route documents, subresources, fetches, WebSockets, EventSource, workers and navigations through the proxy. Removing Chromium’s implicit loopback bypass matters on this host-networked app. The bridge checks every DNS answer and opens TCP to an approved numeric address. HTTP keeps the original Host header. HTTPS uses a CONNECT tunnel, so Chromium still checks the certificate against the original hostname. The live URL probe applies the same check on each redirect and retains the hostname for TLS. [Chromium proxy rules](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)

| Destination | Page access |
| --- | --- |
| Public internet | Allowed. Directly attached networks require an explicit grant. |
| Tailnet | Allowed on `100.64.0.0/10` and `fd7a:115c:a1e0::/48`. A MagicDNS name goes through the same address check. Network routes and Tailscale ACLs still apply. |
| Stage | Allowed on literal `127.0.0.1` at active stage ports. Bundles can load relative assets and navigate to another file or anchor. |
| Bridge host and HA Supervisor | Protected on every host interface, including its tailnet address. The Supervisor subnet `172.30.32.0/23` stays blocked even with a broader LAN grant. The HA API, ingress and Chromium debugging socket are unavailable to pages on these addresses. |
| Other private, link-local, multicast, unspecified, reserved and IPv6 unique-local addresses | Blocked by default. The policy checks IPv4-mapped addresses as IPv4. It rejects loopback aliases and mDNS names. |
| Operator grants | `page_network_allow` accepts exact hostnames, IPs, CIDRs and host/port pairs. A grant applies to every presented page and the URL probe. Loopback, host interfaces, multicast, unspecified, scoped IPv6, transition ranges and mDNS remain protected. |

The wrapper disables QUIC, direct socket APIs and non-proxied WebRTC UDP. Managed Chromium policies enforce the WebRTC setting and turn off DNS-over-HTTPS and network prediction. The proxy owns DNS resolution. A denied in-page request fails at the network layer. A denied presentation or observed document navigation reports `blocked_address`. [Browser launcher](../app/build/chromium-wrapper) · [Managed policies](../app/build/chromium-network-policy.json)

tvpipe v0.2.0 injects a capture script that streams frames to a token-protected loopback WebSocket. The mirror verifies that its own running tvpipe child owns the listener and identifies the pinned release’s capture service before forwarding a WebSocket upgrade. The renderer uses tab capture and MediaRecorder. Native AirPlay connects from tvpipe to the receiver over the LAN. The Apple TV fetches `tv_play` media URLs itself. [tvpipe capture implementation](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/crates/tvpipe/src/browser/share.rs)

## Internal routes

| Method and path | Result or inputs |
| --- | --- |
| `GET /api/discover` | Bonjour candidates in `devices` and the discovery runtime's saved selection in `paired_tv`. |
| `GET /api/tvs`, `POST /api/tvs` | List registered TVs or enroll a discovered advertised identifier with `{"id":"<advertised-id>"}`. |
| `GET /api/tvs/<id>/health`, `status`, `apps` | Receiver health and pending pairing, control state, or installed apps. |
| `GET /api/tvs/<id>/events` | Websocket for receiver state changes. |
| `POST /api/tvs/<id>/pair/start` | Supply `protocol` as `airplay`, `companion` or `remote_pairing`. Use the returned `pairing_id` to finish the exchange. |
| `POST /api/tvs/<id>/pair/finish` | Supply `pairing_id` and the on-screen `pin` as text. |
| `POST /api/tvs/<id>/tool/<name>` | Call a tool for this receiver. |

The API also supports unscoped health, status, apps, events, pairing and tool routes under `/api/`. Use scoped routes for receiver entities. Internal health and pairing responses use `state` and internal errors use `remediation`. The tool and bundle routes have their own result shapes. [Internal handlers](../app/atv_bridge_app/server.py) · [HA integration](../custom_components/atv_bridge/README.md)
