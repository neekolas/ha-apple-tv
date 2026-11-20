# Troubleshooting

Start with `tv_status {"tv":"<room>"}`. Check `data.tv.reachable`, `data.pairings`, `data.capabilities` and `data.problems`. If you omit the room and get `data.tvs`, read each row’s `ok` and `status`. The outer `ok` describes the call.

## Tool failures

A failed tool result has `ok: false` and `error` with `code`, `message`, `retryable` and `fix`. Read the fix first. A failed action's screen can be stale or unknown. A retryable error means the call may work once the cause clears.

| Code | Retryable | Next step |
|---|---|---|
| `tv_unreachable` | yes | Check the TV's network and wake it with the remote if needed. Check any configured host or use discovery. |
| `busy` | yes | Wait for the active call. The per-TV lock waits up to 10 seconds. For a pairing conflict, finish or cancel the pending exchange. |
| `launch_refused` | no | For a refused link, inspect the screen and try an installed app or another confirmed link. For a media URL, check `data.capabilities.play_media` and the staged pyatv fork. |
| `page_unreachable` | yes | Check that the HA host can reach the page and its server is running. For a missing bundle, upload it again. For an update without a ready page, call `tv_show` first. |
| `stale_view` | no | The document changed during execution. Inspect its current state with `tv_page_run` before retrying a change. |
| `script_error` | no | Inspect the message and stack. A timed-out script may still have effects. |
| `scripts_disabled` | no | Enable the app option `page_scripts` and restart the app to allow caller scripts. |
| `target_missing` | no | Show a content page. The current document is a protected bridge listener or a browser-internal page. |
| `nothing_showing` | no | Use `tv_show` to present a page on the selected receiver. |
| `page_not_live` | no | The TV is showing a recording. Show new content with `tv_show`, or use mirror mode for interaction. |
| `screen_unavailable` | yes | Check screen pairing and whether the app publishes labels. If a picker lacks a clear choice, select the profile on the TV. Request an image only when needed. |
| `uncertain` | no | Read the evidence and take a fresh text look before continuing. For remote keys, `data.confirmed_keys` counts acknowledged keys. A key whose acknowledgment was lost may still have arrived. For menu paths, inspect `data.steps` when present. |
| `not_found` | no | Read `data.path` and choose a visible label from a new look. |
| `no_text_field` | no | Focus a text field. Check `screen.keyboard`. |
| `app_not_installed` | no | Use a name or ID from `tv://apps/<id>`. An ambiguous match includes candidates in `data.apps`. |
| `not_paired` / `pairing_removed` | no | Pair control on the affected receiver. If Companion credential reuse fails, finish the optional Companion PIN flow in HA. |
| `access_blocked` | no | Check **Settings → AirPlay and Apple Home → Allow Access → Anyone on the Same Network** on the TV. |
| `invalid_arguments` | no | Check the input schema, source URL, file paths, code length and pairing ID as applicable. |
| `tv_required` | no | There is no unique receiver. Use a stable ID from `data.tvs`. If the list is empty, register a receiver in HA. |
| `tv_not_found` | no | Choose a name or ID from `tv://tvs`. |
| `mirror_busy` | no | Check `data.holders`. Hide the other room's page, or use `replace: true` on a new show if the user wants to take over the slot. |

For a held key sequence, keep `play`, `pause`, `next` and `previous` out of the list. If one appears after a direction key, that earlier key can reach the TV before the bridge returns `invalid_arguments`. Inspect `data.confirmed_keys` and the screen.

If a new show returns `mirror_busy`, use `data.holders` to identify the occupied slots. Its `evidence` can include a prompt choice from the previous call.

## Status problems

- `screen_unavailable` indicates missing screen pairing. `screen_pairing_invalid` indicates an invalid saved record. Pair the screen to restore accessibility reads and screenshots.
- `address_changed` reports the old and new addresses after discovery. No fix is needed.
- `tailnet_dns_missing` means the host's tailnet DNS check failed. It matters when the host needs to load tailnet pages. A `page_unreachable` DNS problem can also name the last unresolved tailnet host. Check Tailscale routing and MagicDNS, then follow the returned DNS fix. The bridge suggests `ha dns options --servers dns://100.100.100.100` on the HA host.
- `data.pairings.control` and `data.pairings.screen` each report `ok`, `missing` or `invalid`. Check `data.capabilities.show`, `look_text`, `look_image` and `play_media` for the operation you need.

## HTTP errors

- HTTP 401 means HA rejected authentication or the user lacks administrator access.
- The HA bridge proxy returns HTTP 503 when no bridge integration entry is loaded. It returns HTTP 502 when it cannot reach the bridge app. Check the app state and HA integration.
- HTTP 413 means the request body exceeded the server limit. Keep the raw ZIP at or below 50 MiB. Invalid ZIPs within the HTTP limit and full-cache errors can return HTTP 200 with `ok: false`. Read `error.remediation`. For `invalid_bundle`, use a ZIP with relative paths and a root `index.html`. For `content_full`, end an active presentation or reduce the upload.

## Pair one room

Use the room's stable bridge ID or unique name. For `kind: "control"`, submit the four digits on the AirPlay prompt. For `kind: "screen"`, submit the six digits on the Remote Pairing prompt. Keep leading zeros. These examples follow a single **screen** exchange.

1. Start with `tv_pair {"tv":"<room>","kind":"screen"}`. Save `data.pairing_id`. `data.expires_in_s` is the remaining time in a 180-second window.
2. Relay `data.ask_user`. At `waiting_for_pairing_screen`, open **Settings → Remotes and Devices → Remote App and Devices** on that TV. Poll with `tv_pair {"tv":"<room>","kind":"screen","pairing_id":"<id>"}`.
3. At `code_on_screen`, ask for the digits. Replace the example digits in `tv_pair {"tv":"<room>","kind":"screen","pairing_id":"<id>","code":"012345"}` with the displayed code.
4. Check the result's evidence and then `tv_status {"tv":"<room>"}`. Control completion verifies a Companion app list. Screen completion verifies an accessibility read.

To cancel, use `tv_pair {"tv":"<room>","kind":"screen","pairing_id":"<id>","cancel":true}` for this exchange. Starting the same kind again while an agent exchange is pending returns its existing ID. A different kind returns `busy`. An expired or mismatched ID returns `invalid_arguments`. Correct a mistyped code length while the exchange is open. Start a new exchange if it expired or the PIN exchange failed.

For phone replies, select **Configure → Pairing notifications** in HA and choose a mobile app notify service. Set `notify: true` on the start call. For control pairing, `read_code_from_screen: true` can use an existing screen pairing to read an unambiguous AirPlay code. Use this only on the user's request.

Phone-reply pairing and `read_code_from_screen` are built and tested with fakes, not yet exercised on a real TV.

If a working screen record already exists, starting screen pairing can return `uncertain` with “The observer already has a verified pairing.” Check status first. To replace a saved record, use **Settings → Devices & services → Apple TV Agent Bridge → Configure**, choose the TV and **Re-pair control** or **Re-pair screen**. The app's **Apple TV Bridge** panel also offers pairing for registered rooms.
