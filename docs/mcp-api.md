# tv-use public MCP API

Connect a Streamable HTTP client to `<HA origin>/api/atv_bridge/mcp` with the header `Authorization: Bearer <HA admin token>`. Use tvOS 27 or later (recommended; 26.6 also works, less tested). The bridge supports discovery without a static IP and requires no Apple Developer account. For installation, pairing, token creation and a read-only connection test, see [Install and connect](../skills/apple-tv/connect.md).

## Choose a receiver

Read `tv://tvs` to get registered receivers. Each tool accepts optional `tv`. Use a room name, stable bridge ID or advertised identifier. Matching ignores case. Use a stable ID if names collide.

- With exactly one saved AirPlay pairing, that TV is the default. A sole registered receiver is also selectable before pairing.
- Otherwise, `tv_status {}` and `tv_look {}` return `data.tvs`. Each row includes receiver details, `ok`, and either `status` or `screen`. A failed row includes `error`. The outer result can succeed even if a receiver failed. An empty registry gives an empty list.
- Name the room for an action when there is no default. When no default exists, specify a receiver to get an image or detailed labels. For pairing, supply the intended room even if it is the default.

## Read the result

MCP returns the result as JSON text and as `structuredContent`. A tool failure sets `isError: true`. The JSON has `ok`, `summary`, `verified`, `evidence` and `data`. Read `verified` and the evidence even when `ok` is true. For example, the TV can acknowledge Select while its visible effect remains unclear.

Single-TV operations other than `tv_status` and `tv_history` include `screen`. On a routed call it has `tv`, `tv_id`, `power` (`on`, `off`, `screensaver` or `unknown`), `keyboard` and four nullable objects:

| Field | Contents |
|---|---|
| `app` | `id`, `name`, `source` (accessibility or now-playing metadata) |
| `focus` | `label`, `role`, `value` |
| `playing` | `title`, `state`, `position_s`, `duration_s` |
| `presenting` | `page_id`, `title`, `url` |

Pairing uses an unknown screen snapshot. On failure the screen may be stale or unknown. Aggregate look returns top-level `screen: null`. A routing failure can have `screen.tv: null` with no `tv_id`.

For example, an unqualified status call with several paired TVs has this structure. This excerpt omits device addresses and other health fields.

```json
{
  "ok": true,
  "summary": "Apple TV summaries.",
  "verified": true,
  "evidence": [],
  "data": {
    "tvs": [
      {
        "id": "<bridge ID>",
        "name": "<room>",
        "ok": true,
        "status": {
          "tv": {"reachable": true},
          "pairings": {"control": "ok", "screen": "missing"},
          "capabilities": {"show": true, "look_text": false, "look_image": false, "play_media": true},
          "problems": [{"code": "screen_unavailable", "fix": "Run tv_pair with kind \"screen\" for this TV."}]
        }
      }
    ]
  }
}
```

## Tool reference

Arguments below are JSON objects. Add `"tv":"<room>"` to any example. Strings must be nonempty unless the field's schema says otherwise. MCP `tools/list` supplies the input schemas for all advertised tools.

### tv_status

`{}` checks bridge health. For a single receiver, inspect `data.tv` (including `id`, `name`, `model`, `tvos`, `address`, `address_source`, `reachable`), `data.pairings.control` and `data.pairings.screen` (`ok`, `missing`, `invalid`). `data.capabilities` reports `show`, `look_text`, `look_image` and `play_media`. Health also includes `browser`, `tailnet_dns` and `problems`. A successful check can report problems. An `address_changed` entry is informational and has no `fix`.

### tv_history

`{}` returns the watch history of every TV, newest first. It is read-only. It does not wake a TV, wait for the tool lock or read the screen, and the result has no `screen`. Add `tv` for one room.

| Argument | Meaning |
|---|---|
| `since`, `until` | Time range. An ISO 8601 date or date-time (UTC unless it has an offset), or an age such as `24h`, `7d` or `4w`. An entry matches when it overlaps the range. |
| `app` | Part of the app name, or the whole bundle ID. Matching ignores case. |
| `search` | Text in the title, episode title, series, artist or album. Matching ignores case. |
| `limit` | 0–500 entries, default 50. Use 0 for the summary alone. |

`data.entries` holds one entry per viewing of an item. Fields that the app did not report are absent.

| Field | Contents |
|---|---|
| `tv`, `tv_id` | The room and its bridge ID |
| `start`, `end` | First and last time the item was playing, in UTC |
| `watched_s` | Seconds actually played. Pauses do not count. |
| `app`, `app_id` | The app that owned the media |
| `title`, `artist`, `album`, `genre`, `kind` | As the app reported them. `kind` is `video`, `music` or `tv`. On YouTube the artist is the channel. |
| `series`, `season`, `episode`, `episode_title` | From the app, or parsed from the title formats of Hulu, Disney+ and HBO Max |
| `content_id`, `store_id`, `item_id`, `url` | The app's content identifier, the iTunes Store ID, the queue item, and a YouTube link. A content identifier is stored only when it is a plain token: 1 to 200 letters, digits, `.`, `_`, `:` and `-`. A link or any other text is not stored. On YouTube it is stored only as the 11-character video ID (letters, digits, `_` and `-`), from a bare ID or from a link that holds one. `url` is built from that video ID alone. |
| `position_s`, `duration_s` | Last position and length. The position is the last one read. When playback stops or the item changes, the playing time counted after that reading is added, up to the length. Compare them to see whether the item was finished. |
| `meta` | Other descriptive fields from a fixed list, such as `subtitle`, `directorName`, `localizedContentRating` or `serviceIdentifier`. Links, profile and subscription identifiers and binary fields are never stored. |

`data.summary` covers every matching entry, not only the returned page: `entries`, `watched_s`, `first`, `last`, and the ten largest `top_apps`, `top_series` and `top_artists` by watched time. `data.now` lists items that are playing or paused at the time of the call. It leaves out the bridge's own pages and clips and TVs that are off. `data.recording` is false when the `watch_history` app option is off. Then the bridge does not read the stored file, and `data.entries` is empty.

The bridge builds the history from now-playing state that the TV pushes. `watched_s` counts only time between two readings that were both playing, measured on a monotonic clock, plus the time up to the moment the engine saw playback stop. It never counts a pause or a scrub. It can be low. When a sample reads a stop before the engine reports it, the time since the previous sample is not counted. That loss is at most one `history_interval` (default 300 seconds) for each stop. It is known and accepted.

An item continues its entry when it plays again within four hours, also after another item played in between. It starts a new entry after four hours, or on a rewatch. A rewatch is a reading in the first tenth of the item's duration when the entry's `position_s` was far enough. After a break, far enough is at least half of the duration. Without a break it is the last tenth. So a scrub back to the start from before the last tenth continues the entry, and an item that repeats without a break can continue one entry. A break is a reading that was not this item in the state playing, paused, seeking or loading: another item, an idle TV or a lost session. A bridge restart is also a break. The rewatch rule needs the duration and both positions. Without them the item continues its entry.

Playback without a title gets its own entry. It never extends a titled entry. When a title arrives, the entry takes it only in one of three cases: both readings carry the same queue item, the titled reading has no position, or its position is at least the time the untitled entry has played, less 10 seconds. That time is the entry's `watched_s` plus the time since the previous reading, if that reading was playing. In any other case the titled item started later. Then the untitled entry stays untitled, and the titled item gets its own entry. Untitled playback continues only after a break of at most 15 minutes. Plays shorter than 10 seconds, the bridge's own pages and clips, and system apps are left out. Netflix reports no title, so its entries carry the app and the time only.

The history is private household data. It is stored only on the Home Assistant host, in the app's data, with file mode 0600, up to `history_max_entries` (default 2,000) entries per TV. Home Assistant backups include it. Only this endpoint and the REST tool route serve it, and both require an HA administrator token. The Home Assistant LLM tools and Assist do not expose it. [What each app reports](research/watch-history.md)

### tv_look

`{}` reads accessibility without waking the TV. `data.items` contains up to 100 accessible items with `label`, `role`, `value` and `focused`. A text-only read can fail on an empty screen or without screen pairing.

`{"image":true,"max_width":1280}` adds a PNG as a separate MCP image. Width defaults to 1280 and accepts integers from 1 to 4096. The result includes `data.captured_at` and `data.toast_visible`. tvOS displays a screenshot toast. Protected video can appear black. Image mode can still succeed if labels are missing.

### tv_show

Supply one source to present, or just the room selector (or `{}` with a default) to stop. `tv_hide` is an unlisted compatibility alias for stopping.

| Source example | What to supply |
|---|---|
| `{"html":"<h1>Dinner</h1>"}` | One HTML page |
| `{"files":{"index.html":"<h1>Dinner</h1>","menu.txt":"Soup"}}` | Relative paths and string contents. Prefix binary files with `base64:`. |
| `{"bundle":"<bundle ID>"}` | The ID from a ZIP upload |
| `{"url":"https://example.org/"}` | A live HTTP or HTTPS page reachable from the HA host |

Optional fields are `title`, `audio` (`play` by default, or `mute`), `keep_awake` (defaults to true and requests a browser wake lock) and `replace` (defaults to false). The default mirror capacity is one. If `mirror_busy` names other rooms in `data.holders`, `replace: true` can stop the oldest holder before starting the new presentation.

The bridge checks the resolved address before connecting, including after redirects. Public sites, reachable tailnet sites and the bridge’s active stage origins work by default. A blocked URL returns `blocked_address` with the address class and a fix. Operators can grant additional LAN destinations with `page_network_allow`.

A show returns `data.page_id`, `data.size` and `data.transport`. A mirror reports the configured size (default `1920x1080`, also `1280x720`). Verification means the sender reported pushing frames. Recorded playback reports `video` at `1920x1080` and checks AirPlay playback. The result can have `verified: false`. Live URLs always use mirroring. Keep their server running. See [Present content](../skills/apple-tv/content.md).

### tv_page_run

Run plain JavaScript in the presented content document. Supply `script` as an expression, function expression or async function body. Select a receiver with the optional `tv`. For a page you authored, call its API directly, for example `{"script":"window.setStep(arg)","arg":2}`. For a known field, read `{"script":"document.querySelector('#note').value"}`. For discovery, scope `querySelectorAll` to the controls you need and return a small array. [Tested DOM recipes](../skills/apple-tv/examples.md#read-and-operate-a-page)

The optional JSON input is available as `arg`. The bridge awaits promises and returns the JSON value in `data.result`. An empty return becomes `null`. If the serialized result exceeds 65,536 UTF-8 bytes, `data.truncated` is true and `data.preview` contains the beginning of the JSON text. `data.bytes` gives the original size.

`timeout_ms` defaults to 5,000 and accepts 1 through 30,000. A deadline ends the wait. Code may still have effects in the page. Exceptions return `script_error` with a short message and stack excerpt when available.

Click with `document.querySelector('#next').click()`. Submit with `document.querySelector('form').requestSubmit()`. A link or form may replace the document. The next call runs in the new document. A navigation during a script may return `stale_view` after the script has taken effect.

Scripts generate synthetic events. A dispatched key reaches JavaScript handlers but does not insert text or perform a key's default browser action. Chromium allows media autoplay with sound, and the stage iframe delegates autoplay. APIs such as fullscreen and the file picker can still require permissions or transient user activation. Synthetic events do not grant trusted input. Allow 250 ms between scripts. [Interaction recipes](../skills/apple-tv/examples.md)

Page control requires HA administrator access through the bridge endpoint on the private network. The bridge selects the presented content iframe or captured top-level tab and excludes the stage wrapper, browser error pages and bridge listeners. Scripts run with that page's browser privileges. They can read field values, make requests, submit forms and navigate. Every script uses the same destination policy as the page. A blocked fetch rejects with a network error. Bundle-relative requests, links and anchors work on the stage origin. Set the app option `page_scripts: false` to disable the whole tool, including read-only calls. The option applies to caller scripts and leaves normal site JavaScript enabled.


| Error | How to proceed |
|---|---|
| `blocked_address` | The presented document attempted to navigate to a protected address. Show an allowed URL or bundle. |
| `stale_view` | Follow `error.fix`. Run a read-only script on the current document before retrying a change. |
| `target_missing` | Present a content document instead of a bridge listener or browser error page. |
| `scripts_disabled` | Enable `page_scripts` in the app options to allow caller scripts. |
| `script_error` | Inspect the message and optional stack. After a timeout check for effects before retrying. |
| `nothing_showing` | Present a page on the selected TV first. |
| `page_not_live` | The TV is showing a recording. Show new content with `tv_show`, or use mirror mode for interaction. |
| `busy` | Wait for the per-TV lock or rate limit before retrying. |

### tv_play

The Apple TV fetches media URLs over its own network connection. Browser egress rules apply to presented pages.

Use one of these forms. Starting playback takes over the room's presentation.

| Arguments | Behavior |
|---|---|
| `{"youtube":"jNQXAC9IVRw"}` | An 11-character video ID or a recognized YouTube video URL. Checks YouTube playback. |
| `{"media":"https://example.org/clip.mp4"}` | AirPlay URL playback. The TV must reach the HTTP(S) URL. Requires `play_media` capability. |
| `{"app":"HBO Max","title":"Succession"}` | Uses a known title recipe or searches the app, selects a matching label and checks playback and title. |
| `{"link":"https://music.apple.com/us/album/rumours/594061854"}` | Opens a web or app deep link and checks the foreground destination. |

`data.path` on success identifies `youtube`, `airplay`, `title_link`, `search_link`, `in_app_search` or `deep_link`. For `link`, `data.landed` is `playing`, `title_page` (recognized title actions) or `app_home` (target app observed). Check the title and app before taking another action. The bridge can use a screenshot to confirm the focused Open button for a recognized custom scheme. Catalog handoffs can reach other apps. See [Deep links by service](deeplinks.md).

### tv_open

Use `{"app":"Netflix"}` to open an installed app by name or bundle ID. Add **one** of `"search":"<query>"` or `"path":["Remotes and Devices"]`. Paths take at least one label. For Settings paths, the bridge first checks the root menu. Opening an app takes over the room's presentation.

Success returns `data.app`. Search adds `data.path` with the search method. Menu paths add the key sequence in `data.path` and per-step results in `data.steps`. If the installed-app match is ambiguous, `app_not_installed` includes candidates in `data.apps`.

### tv_navigate

`{"to":"Remotes and Devices","select":true}` moves focus to an accessible label, then selects it. Omit `select` or set it false to focus only. Optional `within` is a container hint. Matching ignores case and allows prefixes. Prefer the full label from a recent look. Navigation stops at 40 presses including Select, or after three direction presses leave focus unchanged. Inspect `data.path`, `screen.focus` and `verified`. A changed setting value on the same row can verify a selection.

### tv_remote

Choose one form:

- `{"keys":["down","select"]}` sends 1–50 ordered keys. Optional `hold_ms` is an integer from 0 to 10000. Use a nonzero hold only for HID keys.
- `{"text":"search terms","submit":true}` replaces text in a focused field and checks readback. Optional `submit` sends Select afterwards.
- `{"swipe":"left"}` accepts `up`, `down`, `left` or `right`.

Optional `check_each` (keys only, default false) switches to the slower per-key mode described below.

HID keys are `up`, `down`, `left`, `right`, `menu`, `select`, `home`, `volume_up`, `volume_down`, `siri`, `screensaver`, `play_pause`, `channel_increment`, `channel_decrement`, `guide`, `page_up` and `page_down`. Media commands are `play`, `pause`, `next` and `previous`.

By default the bridge sends the keys in one burst, 100 ms apart, waits 300 ms and reads the screen once. `verified` is true when power, focus, keyboard, app or playback differs from the start. After `home`, it reads the screen before sending the remaining keys and stops if focus is unreadable. With `check_each: true` it reads the screen after every key, so `verified` is false if any key showed no change, and missing focus between keys stops the run. Lost acknowledgment always stops the keys. `data.confirmed_keys` counts acknowledged keys. The next attempted key may also have arrived. Look before sending more. The bridge has no purchase guard. Get the user's approval before selecting a paid action.

### tv_sequence

Run remote work on a single receiver in one MCP call. Supply `steps`, optional `for_each` and the usual `tv` selector. The bridge validates the request before waking the TV. It holds that receiver's tool lock through preparation, all iterations and the final screen read. The execution deadline is 90 seconds including preparation. A separate final read, when needed, has a five-second timeout. Cancellation can take additional time to close an active connection.

| Step | Behavior |
| --- | --- |
| `{"key":"menu"}` | Send one `tv_remote` key and wait for acknowledgment. Skip the accessibility read between raw keys. |
| `{"pause_ms":300}` | Wait 1–5000 ms for a transition. |
| `{"to":"General","select":false}` | Navigate using accessible focus and an exact full label. Select is optional. An unverified selection stops the run. |
| `{"text":"search terms"}` | Replace a focused keyboard field and verify readback. Use a separate key step to submit. |
| `{"app":"Settings","path":["Remotes and Devices"]}` | Take over the presentation, verify the launch and optionally follow up to ten menu labels using the `tv_open` path checks. |
| `{"expect":{"app":"Settings","focus":"General","value":"On","visible":"Network"}}` | Require every supplied field to match the same fresh accessibility observation. Supply at least one field. |

Checkpoint comparisons ignore case and normalize whitespace. `focus` and `value` refer to the focused row. `visible` checks the focused label and up to 100 accessible items. `app` accepts the accessible foreground name or bundle ID. A mismatch returns `not_found` with the failing field, expected value and observed value in the step's `data`.

For repetition, supply an ordered list of labels. The bridge runs the complete step list for each label. Use the whole string `"$item"` in `to` or any `expect` field. Other strings are literal. This example focuses two Settings rows and checks each without selecting it.

```json
{
  "for_each": ["Accessibility", "Network"],
  "steps": [{"to":"$item"}, {"expect":{"focus":"$item","app":"Settings"}}]
}
```

The limit is 50 steps after repetition. `data.total_steps` counts the planned steps. `data.steps` contains each attempted step's zero-based `iteration` and `step`, operation, outcome, evidence and operation-specific data. `data.stopped_at` is null on completion, the first failed location on a step error, or `{"phase":"prepare"}` or `{"phase":"screen"}` on a setup or final-read error. Validation and routing errors use the normal tool envelope before execution.

Read the final `screen` and individual outcomes. With raw keys, `ok: true` means the run completed and `verified: false` records that individual key effects were unchecked. A passing checkpoint independently verifies the requested state. On lost acknowledgment the attempted key may have arrived. Earlier steps stay applied. Inspect the screen before resuming at a known step.

### tv_power

`{"on":true}` wakes the receiver on a fresh connection. `{"on":false}` requests sleep and checks for Off. It can retry sleep on a fresh session. Both forms take over a presentation. An already-off receiver skips wake for a sleep request.

### tv_pair

Start an exchange on a registered receiver with `{"tv":"<room>","kind":"control"}` (four-digit AirPlay code) or `kind: "screen"` (six-digit Remote Pairing code). Relay `data.ask_user`. Start returns `data.kind`, `pairing_id`, `state`, `code_length` and remaining `expires_in_s`. The window is 180 seconds.

At `waiting_for_pairing_screen`, open **Settings → Remotes and Devices → Remote App and Devices** on the target. The bridge retries discovery every two seconds. Poll with the same room, kind and returned ID. At `code_on_screen`, add `"code":"0123"` for a four-digit exchange or the actual six digits for screen pairing. Preserve leading zeros. To cancel, send `"cancel":true` with the same room, kind and ID. Submit or cancel may omit the ID to use the current exchange for that kind. Completion checks the Companion app list or an accessibility read.

Optional `notify: true` requests a reply notification through the phone configured in HA. Use `read_code_from_screen: true` only if the user requests it for control pairing and screen pairing already works. If the code is ambiguous, relay the open prompt. See [Pairing and recovery](../skills/apple-tv/troubleshooting.md).

## Failures

A tool failure includes `error.code`, `error.message`, `error.retryable` and `error.fix`. Calls to each receiver share a lock. If an action loses confirmation, read the screen before retrying. A call that waits more than 10 seconds for the lock returns `busy`. The bridge stops any call that runs longer than 150 seconds and returns `uncertain`. The Home Assistant proxy answers 504 if the app sends nothing for 200 seconds. The app log records each MCP request and tool call with its duration, and warns when its event loop stalls.

| Public code | Meaning | Retryable |
|---|---|---|
| `tv_unreachable` | Connection or wake failed | yes |
| `busy` | Receiver or pairing exchange is busy | yes |
| `page_unreachable` | URL, bundle, store, stage or sender unavailable | yes |
| `screen_unavailable` | Screen read, capture or focus unavailable | yes |
| `not_paired` / `pairing_removed` | Missing or rejected credentials | no |
| `access_blocked` | The TV denied access | no |
| `app_not_installed` | No unique installed app matches | no |
| `launch_refused` | The launch was refused or the build lacks the capability | no |
| `not_found` / `no_text_field` | Navigation or sequence checkpoint failed, or text entry lacks keyboard focus | no |
| `invalid_arguments` | Invalid arguments, content or pairing exchange | no |
| `tv_required` / `tv_not_found` | Ambiguous selection or unknown receiver. Read `data.tvs`. | no |
| `mirror_busy` | All mirror slots are held. Read `data.holders`. | no |
| `uncertain` | Delivery, effect or destination lacks confirmation | no |
| `stale_view` / `target_missing` | The document changed during execution or the browser is on a protected page | no |
| `scripts_disabled` / `script_error` | Caller scripts are disabled, or JavaScript failed or exceeded its deadline | no |
| `nothing_showing` / `page_not_live` | The TV has no bridge presentation or it is showing a recording | no |

## Resources and prompt

- `tv://tvs` lists registered receivers. `tv://screen/<id>` performs a text look. `tv://apps/<id>` lists installed apps as `{id, name}`. The selector can also be a URL-encoded room name.
- `tv://screen` follows the same default or summary rules as `tv_look {}`. `tv://apps` returns one list with one AirPlay-paired receiver, otherwise an object keyed by paired receiver ID.
- `tv://page-guide` describes page design and updates. The `present` prompt takes a required `topic` and returns an HTML starting point.

## HTTP and Home Assistant

Scripts can POST JSON arguments to `/api/atv_bridge/tool/<name>` with the same HA admin header. The HTTP result places a requested image in `data.image`. For bundled pages, POST a raw ZIP to `/api/atv_bridge/bundle`. Success returns `{"bundle":"<32 lowercase hex characters>"}`. See [upload limits and commands](../skills/apple-tv/content.md).

HA's built-in MCP server and Assist expose six text tools through the integration: `atv_bridge__show`, `atv_bridge__play`, `atv_bridge__open`, `atv_bridge__power`, `atv_bridge__pair` and `atv_bridge__status`. Watch history is not among them. Use the bridge endpoint above for full screen reads, navigation, remote input, sequences and mirrored page control.


The integration uses a separate internal API on loopback port 8765 with the app token from Supervisor discovery. It lists registered receivers with `GET /api/tvs`, discovers candidates with `GET /api/discover`, and registers a candidate with `POST /api/tvs` and `{"id":"<advertised identifier>"}`. Under `/api/tvs/<bridge ID>`, it uses GET for `status`, `health`, `apps` and `events` (WebSocket), and POST for `pair/start`, `pair/finish` and `tool/<name>`. Agent clients use the authenticated HA routes above.
