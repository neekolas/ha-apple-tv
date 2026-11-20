---
name: apple-tv
description: Controls an Apple TV through the Apple TV Agent Bridge MCP server on Home Assistant. Use for presenting pages, playing media, opening apps, navigating menus or reading the TV screen.
---

# Apple TV

Use the bridge on Home Assistant OS with an Apple TV 4K running tvOS 27 or later (26.6 also works, less tested). The app holds the control session for each registered receiver. Its browser renders pages on the HA host and sends them to the TV. For a live page, `localhost` refers to the HA host. For AirPlay media playback, the TV fetches the URL.

## Start here

1. Install the app and integration, pair the TV, then connect to `/api/atv_bridge/mcp` using Streamable HTTP and an HA administrator's Bearer token. See [Install and connect](connect.md).
2. Call `tv_status {}` or read `tv://tvs`. Use the target room's stable ID if names collide. Every tool takes optional `tv`. If several TVs are paired, include it on every call for a particular room.
3. Check `tv_status {"tv":"<room>"}` for reachability, pairings and capabilities. If someone may be watching, use a text-only `tv_look` before changing their screen.

Use MCP discovery for tool choices, arguments and examples. For repetitive remote work, plan a `tv_sequence` using labels from the target TV. Put a checkpoint immediately before an action that depends on focus, app or row value. `tv_remote` sends keys in one fast burst and checks the screen once at the end; add `"check_each":true` only when each press must be confirmed. See [Drive native apps](apps.md).

On uncertain delivery, inspect the current screen before resuming. Ask before purchases, rentals or upgrades.

## Guides

- [Install, pair and connect](connect.md)
- [Present files, live URLs and updates](content.md)
- [Design a page for the TV](pages.md)
- [Drive native apps](apps.md)
- [Choose a service deep link](deeplinks.md)
- [Follow a worked example](examples.md)
- [Troubleshoot errors and pairing](troubleshooting.md)
- [Public API reference](../../docs/mcp-api.md)
