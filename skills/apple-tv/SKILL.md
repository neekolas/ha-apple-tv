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

## Pick the tool

| Request | First call | What to check |
|---|---|---|
| Show your own page | `tv_show` with one of `html`, `files`, `bundle` or `url` | `data.transport`, `verified`, `screen.presenting` |
| Change the current page | `tv_update {"data":{"step":2}}` | `data.page_state` if the page reports it |
| Watch something | `tv_play` with `youtube`, `media`, `link`, or both `app` and `title` | For a link, check `data.landed` and the foreground app |
| Open an app or search | `tv_open` with `app`, optionally `search` or `path` | Check the app, focus and evidence |
| Read the screen | `tv_look {}` | Read labels first. An explicit `image: true` requests a screenshot and shows a toast. |
| Move to an item | `tv_navigate` with its full label in `to` | Add `select: true` only when the user wants to activate it |
| Send other input | `tv_remote` with `keys`, `text` or `swipe` | Check acknowledged keys and whether their effect was verified |
| Wake or sleep | `tv_power` with `on` | Read the reported power state |
| Pair a registered receiver | `tv_pair` with the room and `kind` | Relay `data.ask_user` and use the same exchange for the reply |

Actions usually prepare the receiver first. They can wake it, dismiss the screen saver and choose a visible profile. App launches can also answer recognized privacy prompts. Read `evidence` for those choices. `tv_open`, `tv_play` and `tv_power` take over the current presentation. To stop showing a page, call `tv_show` with just the room selector.

Wait for each action before deciding on the next. On `uncertain` or `verified: false`, look at the screen before sending more input. Ask before purchases, rentals or upgrades. The bridge has no purchase guard.

## Guides

- [Install, pair and connect](connect.md)
- [Present files, live URLs and updates](content.md)
- [Design a page for the TV](pages.md)
- [Drive native apps](apps.md)
- [Choose a service deep link](deeplinks.md)
- [Follow a worked example](examples.md)
- [Troubleshoot errors and pairing](troubleshooting.md)
- [Public API reference](../../docs/mcp-api.md)
