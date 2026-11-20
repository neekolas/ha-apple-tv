# Apple TV Agent Bridge

Show a web page on an Apple TV, play media, drive apps with the remote, or read the screen from an MCP client. The bridge runs on Home Assistant. One app handles the TVs. One custom integration handles pairing, entities and agent access. Nothing needs to be installed on the TV.

The bridge targets **Apple TV 4K on tvOS 27 or later**, the recommended and tested baseline. tvOS 26.6 also works but is less tested: pairing, control and screen reading all worked on an Apple TV 4K (1st generation), which cannot run tvOS 27. Testing included an Apple TV 4K (2nd generation) on tvOS 27.2 (24K122) and a Home Assistant Green. Discovery works without a static IP. No Apple Developer account is required.

An agent can show a recipe and advance it one step at a time, make a slideshow from uploaded photos, or put a live dashboard from your laptop on the TV. For installed apps it can open Settings, find a visible menu item by label, or start and check playback.

## Install and connect

1. Put Home Assistant and the TVs on the same LAN. Install the local app **Apple TV Agent Bridge** and copy the custom integration into Home Assistant. The setup guide covers both a manual install and a workstation deploy. [Setup guide](docs/SETUP.md) · [Workstation deploy](docs/deploy.md)
2. Restart Home Assistant Core. If the TV already has a built-in Apple TV integration entry, disable that entry before pairing. Accept the bridge discovery in **Settings → Devices & services**. Choose the TV if prompted. Enter its AirPlay PIN. For screen reading, open **Settings → Remotes and Devices → Remote App and Devices** on the TV before requesting the screen PIN. Use **Add Apple TV** on the same integration entry for another TV.
3. Create a long-lived access token in a Home Assistant administrator’s profile. Give your MCP client the Streamable HTTP endpoint at `/api/atv_bridge/mcp` on your Home Assistant URL and the header `Authorization: Bearer <token>`. For Claude Code, set `HA_TOKEN` to the token in your shell and use your own HA origin in this example. [Client setup](docs/SETUP.md#connect-an-agent)

   ```sh
   HA_URL='http://homeassistant.local:8123'
   claude mcp add --transport http apple-tv "$HA_URL/api/atv_bridge/mcp" \
     --header "Authorization: Bearer $HA_TOKEN"
   ```

4. Call `tv_status {}`. With one paired TV, check `data.tv.reachable`, `data.pairings` and `data.problems`. With several paired TVs, check each row in `data.tvs`, or call `tv_status {"tv":"<TV name>"}` for one TV.

## Operate a TV

Every tool accepts an optional `tv`. Use the name or stable bridge ID from `tv://tvs`. One paired TV is the default, even if others await pairing. With several paired TVs, name the TV for actions. An untargeted `tv_status` or `tv_look` returns summaries. Use an ID when names overlap.

| Task | MCP call | What to expect |
| --- | --- | --- |
| Show a page | `tv_show {"tv":"<TV name>","html":"<h1>Dinner is ready</h1>"}` | Chromium renders on the HA host. tvpipe mirrors the tab with audio. A live HTTP(S) URL, inline files or an uploaded ZIP can supply the page instead. |
| Change the page | `tv_update {"tv":"<TV name>","data":{"step":3}}` | A page with an update handler receives the data. Dismiss it with `tv_show {"tv":"<TV name>"}`. |
| Start playback | `tv_play {"tv":"<TV name>","youtube":"jNQXAC9IVRw"}` | Accepts a YouTube ID or URL, media URL, deep link, or an app and title. Playback and title search depend on the installed app. |
| Open an app | `tv_open {"tv":"<TV name>","app":"Settings"}` | Optionally supply a search or a menu path. Read `tv://apps/<TV ID>` for installed apps. |
| Read the screen | `tv_look {"tv":"<TV name>"}` | Reads accessibility labels and focus. Some apps expose little text. Set `image` to `true` for a screenshot. tvOS can show a screenshot notice. |
| Navigate by label | `tv_navigate {"tv":"<TV name>","to":"General","select":true}` | Targets a visible label. Requires screen pairing. |
| Use the remote | `tv_remote {"tv":"<TV name>","keys":["down"]}` | Sends keys, a swipe or text. Text entry requires a focused keyboard field. |
| Set power | `tv_power {"tv":"<TV name>","on":false}` | Checks the resulting Apple TV power state. |
| Repair pairing | `tv_pair {"tv":"<TV name>","kind":"screen"}` | Starts a PIN exchange for a registered TV. Follow `data.ask_user` and use the returned pairing ID. Use `kind:"control"` for AirPlay. |
| Check health | `tv_status {"tv":"<TV name>"}` | Reports reachability, pairing state, capabilities and problems with suggested fixes. |

Tool results include `ok`, `summary`, `verified`, `evidence` and `data`. An acknowledged action can have `verified: false`. Read the evidence and check the screen before sending another key. Failures include `error.code`, `error.message`, `error.retryable` and `error.fix`. Most tools return a compact `screen`. Pairing uses unknown screen values, and status omits the screen.

Scripts can POST the same arguments to `/api/atv_bridge/tool/<name>` with the admin token. The bridge also supplies six text tools to Home Assistant’s LLM APIs. For Assist, expose each intended bridge media player. [Agent skill and examples](skills/apple-tv/SKILL.md) · [MCP API](docs/mcp-api.md)

## Using the bridge with Home Assistant

Home Assistant’s built-in Apple TV integration provides media player and remote entities. The bridge provides its own entities, a live Chromium presentation, screen observation, and a room-aware MCP endpoint. Disable the built-in entry only for a TV you move to the bridge. To switch back, stop the bridge app and re-enable that entry. [Setup guide](docs/SETUP.md#prepare-each-tv)

## How it works

- **Control.** The app keeps a separate pyatv session for each registered TV. The staged fork includes 18 patches for AirPlay pairing and URL playback, Companion errors and keepalive, and other protocol fixes. [Patch series](docs/pyatv-fork.md)
- **Presentation.** The default is mirroring at 1920×1080 and 10 fps. One TV can mirror at a time by default. An agent can serve a live web app at a URL reachable from Home Assistant, including over Tailscale. For HTML, files and bundles, `auto` mode can switch to MP4 playback after two failed mirror starts. Live URLs use mirroring. Page input comes from `tv_update`. [Presentation options](app/DOCS.md)
- **Observation.** A separate observer process uses RemotePairing for accessibility text and screenshots. Results depend on what the current app exposes. Protected video may appear black in screenshots. [Screen-reading measurements](docs/live-accessibility.md)
- **Access.** HA authenticates agent requests and forwards them to the app on loopback port 8765. LAN port 8766 serves the watchdog, uploaded content and rendered media without authentication. The bridge has no purchase guard. Use the agent’s confirmation flow and tvOS purchase settings if you want approval before spending.

## Hardware results and limits

On the tested Home Assistant Green, two inline pages shared one mirror sender. Across 12 show/hide cycles, every hide returned the browser process count to zero. Two sender crashes recovered during that run. It was a single-TV test. Recheck each TV with `tv_status` after a tvOS update. [Hardware test results](docs/live-browser-lifecycle.md)

- Wake after a long overnight sleep is not yet measured. Multi-hour mirroring is also unverified.
- Built and tested with fakes, not yet exercised on a real TV: phone-reply pairing through a Home Assistant notification; `tv_pair` with `read_code_from_screen`; automatic handling of in-app profile pickers, App Tracking Transparency prompts and tvOS "Open in" dialogs; discovery after the TV's address changes; removing a TV; finishing Add Apple TV with Skip screen pairing; taking over another room's mirror with `replace: true`.

## Further reading

- [App development](app/README.md) and [integration development](custom_components/atv_bridge/README.md)
- [Architecture](docs/ARCHITECTURE.md) and [browser lifecycle measurements](docs/live-browser-lifecycle.md)
- [Render cost](docs/spike-s2.md) and [browser comparison](docs/spike-s2b.md)

## Acknowledgements

This project stands on other people's protocol work, reverse engineering and open-source software. Thank you.

### Protocol and device work

- [pyatv](https://github.com/postlund/pyatv) by Pierre Ståhl (postlund), MIT. The control layer for Companion, MRP, AirPlay and RAOP. The bridge ships a fork in `vendor/pyatv/`; see [docs/pyatv-fork.md](docs/pyatv-fork.md).
- The authors of the upstream pyatv pull requests carried in the fork: JaviSoto ([#2894](https://github.com/postlund/pyatv/pull/2894), AirPlay pairing on tvOS 27), jlacivita ([#2846](https://github.com/postlund/pyatv/pull/2846), URL playback on modern tvOS), robkochman (the [playback rework](https://github.com/postlund/pyatv/commit/8144c77c6cecbed4f9ba2adb5a350ad86a8f6604) it builds on), jullduxster ([#2948](https://github.com/postlund/pyatv/pull/2948), [#2947](https://github.com/postlund/pyatv/pull/2947)), pedro-f20 ([#2939](https://github.com/postlund/pyatv/pull/2939)), zmiyajan ([#2953](https://github.com/postlund/pyatv/pull/2953)), hkoeck ([#2935](https://github.com/postlund/pyatv/pull/2935)), MarvinSchenkel ([#2945](https://github.com/postlund/pyatv/pull/2945)), SeoFood ([#2925](https://github.com/postlund/pyatv/pull/2925), [#2924](https://github.com/postlund/pyatv/pull/2924)) and marlow-systems ([#2920](https://github.com/postlund/pyatv/pull/2920)).
- [tvpipe](https://github.com/mitsuhiko/tvpipe) by Armin Ronacher (mitsuhiko). The AirPlay mirroring sender. Its main library is Apache-2.0. The v0.2.0 release binary includes the LGPL-3.0-or-later FairPlay module and is distributed under LGPL-3.0-or-later terms. The app unpacks the release under `/opt/tvpipe/`. [Release licensing](https://github.com/mitsuhiko/tvpipe/blob/v0.2.0/README.md#license)
- [doubletake](https://github.com/omarroth/doubletake) by omarroth, LGPL-3.0-or-later. The reference open-source AirPlay mirroring sender and the source of tvpipe's FairPlay port; used for tvOS 27 protocol details and one of the credential formats the fork converts. It is not shipped.
- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) by doronz88, GPL-3.0-or-later. The RemotePairing tunnel, screenshots and accessibility access. Pinned, and run only in the observer's separate process.
- [ipb](https://github.com/ipbtools/ipb), MIT. The observer's accessibility archive codec is adapted from it. The license is in `app/atv_bridge_observer/licenses/`.
- [apple-protocols](https://github.com/basmilius/apple-protocols) by basmilius. A reference for the Companion session, extra commands, and reusing AirPlay credentials for Companion.
- [Unfolded Circle integration-appletv](https://github.com/unfoldedcircle/integration-appletv). Its runtime patches for tvOS 27 and its connection handling informed the fork and the wake logic.
- [appletv-mcp](https://github.com/trevor-nichols/appletv-mcp) by trevor-nichols. Prior art for an Apple TV MCP server; its rule never to replay an uncertain key press is used here.
- [UxPlay](https://github.com/FDH2/UxPlay) and [atvloadly](https://github.com/bitxeno/atvloadly), for their documentation of AirPlay mirroring and of Apple TV developer pairing from Linux.

### Software the app runs on

- [Home Assistant](https://www.home-assistant.io/), Apache-2.0, which hosts all of it, and its [Tailscale app](https://github.com/hassio-addons/app-tailscale) by Franck Nijhof, which carries agents' traffic and tailnet URLs.
- [Chromium](https://www.chromium.org/), which renders every page, and [Playwright](https://playwright.dev/), which drives it for URL-playback rendering.
- [FFmpeg](https://ffmpeg.org/), for the MP4 fallback.
- [Debian](https://www.debian.org/), the app's base image, and the [Noto fonts](https://fonts.google.com/noto).
- Python libraries: [aiohttp](https://github.com/aio-libs/aiohttp), the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf), [srptools](https://github.com/idlesign/srptools), [Pillow](https://github.com/python-pillow/Pillow), [PyYAML](https://github.com/yaml/pyyaml) and [jsonschema](https://github.com/python-jsonschema/jsonschema).

## Licenses

The bridge service is MIT. The observer (`app/atv_bridge_observer/`) is GPL-3.0-or-later and runs as its own process. The vendored pyatv fork keeps pyatv's MIT license. The installed tvpipe binary is under LGPL-3.0-or-later terms, as described above. Each other component keeps its own license.
