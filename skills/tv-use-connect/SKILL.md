---
name: tv-use-connect
description: Connect to an Apple TV through the tv-use MCP server on Home Assistant and control it. Covers choosing a local or remote connection, registering the server, reading the screen, the remote, opening apps, deep links, playback, power and error recovery. Use when an agent needs to reach or operate the TV, or a tv_* call fails.
---

# Connect to and control the TV

tv-use (the **Apple TV Agent Bridge**) runs on Home Assistant. It gives agents one MCP endpoint for every paired Apple TV in the home: `<HA origin>/api/atv_bridge/mcp`, Streamable HTTP, header `Authorization: Bearer <HA admin token>`.

Related skills: **tv-use-setup** installs and pairs the bridge. **tv-use-web-apps** builds pages for the TV. **tv-use-realtime** runs live voice sessions.

## Connect

- **Local connection (recommended).** On the home network, connect to `http://homeassistant.local:<port>`, or HA's LAN IP address. No Tailscale. It is the fastest path.
- **Remote connection.** Outside the home, connect through Tailscale to HA's tailnet hostname.
- Both use the same token. HA's port is 8123 by default; some installs use 80.

If the tv-use tools are missing, register the server first. If you run at home but the server uses a tailnet URL, run the local probe, tell the user once if it answers, and offer to switch. See [connect.md](references/connect.md).

## First calls

1. `tv_status {}` or read `tv://tvs`. Every tool takes an optional `tv`: a name or stable ID. With several paired TVs, pass it on every call.
2. `tv_status {"tv":"<room>"}` for reachability, pairings and capabilities.
3. Before changing a screen someone may be watching, read it with a text-only `tv_look {"tv":"<room>","image":false}`.

## Pick the tool

| Goal | Tool |
|---|---|
| Read the screen: app, focus, labels, playback | `tv_look` (text by default; `"image":true` for a screenshot, which shows a toast on the TV) |
| Select a visible item by its label | `tv_navigate {"to":"General","select":true}` |
| Press keys, swipe or type | `tv_remote` (keys go in one fast burst; add `"check_each":true` only when each press must be confirmed) |
| Repeat a known route with checks | `tv_sequence` with `expect` checkpoints |
| Open an app, optionally search or follow a menu path | `tv_open` |
| Play a YouTube video, media URL or deep link | `tv_play` (the TV fetches media URLs itself, so they must be reachable from the TV) |
| Wake or sleep | `tv_power` |
| Show a web page | `tv_show` and `tv_page_run`; see **tv-use-web-apps** |
| Repair pairing | `tv_pair`; see [troubleshooting.md](references/troubleshooting.md) |

Every result has `ok`, `summary`, `verified`, `evidence` and usually `screen`. An acknowledged action can still be `verified: false`. Read the evidence and look before sending more input. A failure has `error.code`, `error.retryable` and `error.fix`. Read the fix first.

## Rules

- After a media or deep-link launch, make a text-only `tv_look` your next call, even if the launch reports success. Check for an **Open in …?** prompt before sending another key or launch. See [native-apps.md](references/native-apps.md#profiles-and-dialogs).
- Ask before purchases, rentals, upgrades and sign-in. The bridge has no purchase guard.
- On `mirror_busy`, ask before using `replace: true`. Another room may be watching.

## Load when needed

| File | Load it to |
|---|---|
| [connect.md](references/connect.md) | Register the MCP server, probe for a local connection, check rooms, or call tools from scripts over REST |
| [native-apps.md](references/native-apps.md) | Drive native apps: sequences, profile pickers, Open prompts, power and paid actions |
| [deeplinks.md](references/deeplinks.md) | Find a deep-link template for a streaming service |
| [examples.md](references/examples.md) | Follow worked examples: play a podcast, find which app has a show, visit a Settings menu |
| [troubleshooting.md](references/troubleshooting.md) | Handle an error code, a status problem, an HTTP error or a pairing exchange |
| [MCP API reference](https://github.com/neekolas/tv-use/blob/main/docs/mcp-api.md) | Check every tool argument and result field |
