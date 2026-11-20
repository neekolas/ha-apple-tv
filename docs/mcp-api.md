# Apple TV Agent Bridge public MCP API

Connect a Streamable HTTP client to `<HA origin>/api/atv_bridge/mcp` with the header `Authorization: Bearer <HA admin token>`. Use tvOS 27 or later (recommended; 26.6 also works, less tested). The bridge supports discovery without a static IP and requires no Apple Developer account. For installation, pairing, token creation and a read-only connection test, see [Install and connect](../skills/apple-tv/connect.md).

## Choose a receiver

Read `tv://tvs` to get registered receivers. Each tool accepts optional `tv`. Use a room name, stable bridge ID or advertised identifier. Matching ignores case. Use a stable ID if names collide.

- With exactly one saved AirPlay pairing, that TV is the default. A sole registered receiver is also selectable before pairing.
- Otherwise, `tv_status {}` and `tv_look {}` return `data.tvs`. Each row includes receiver details, `ok`, and either `status` or `screen`. A failed row includes `error`. The outer result can succeed even if a receiver failed. An empty registry gives an empty list.
- Name the room for an action when there is no default. When no default exists, specify a receiver to get an image or detailed labels. For pairing, supply the intended room even if it is the default.

## Read the result

MCP returns the result as JSON text and as `structuredContent`. A tool failure sets `isError: true`. The JSON has `ok`, `summary`, `verified`, `evidence` and `data`. Read `verified` and the evidence even when `ok` is true. For example, the TV can acknowledge Select while its visible effect remains unclear.

Single-TV operations other than status include `screen`. On a routed call it has `tv`, `tv_id`, `power` (`on`, `off`, `screensaver` or `unknown`), `keyboard` and four nullable objects:

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

### tv_look

`{}` reads accessibility without waking the TV. `data.items` contains up to 100 accessible items with `label`, `role`, `value` and `focused`. `data.page_state` holds the current page's reported state, or null. A text-only read can fail on an empty screen or without screen pairing.

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

A show returns `data.page_id`, `data.size` and `data.transport`. A mirror reports the configured size (default `1920x1080`, also `1280x720`). Verification means the sender reported pushing frames. Recorded playback reports `video` at `1920x1080` and checks AirPlay playback. The result can have `verified: false`. Live URLs always use mirroring. Keep their server running. See [Present content](../skills/apple-tv/content.md).

### tv_update

`{"data":{"step":3}}` sends any JSON value to the showing page. The browser delivers a `message` event. Stored HTML pages also get a helper that supports `tv.on("update", handler)`, `tv.ready()` and `tv.state`. For a live page, load the helper if you need that interface. Success returns `data.delivered: true` and `data.page_state`. Delivery confirms the browser posted the message. To confirm the page applied a change, have its handler set `tv.state` and check the returned value.

### tv_page_look

Read the document in a live mirror with `{}`. Supply `tv` when the receiver is ambiguous. The bridge reads Chromium through the sender's existing connection. It works without tvOS screen pairing and returns the cached TV `screen`.

`data` contains `page_id`, an opaque `view`, `mode` (`iframe` or `direct`), `snapshot` and `more`. The snapshot includes headings, regions, status, text and labeled controls. Refs identify elements in that view. For example, a recipe might return these rows.

```text
page_id=… view=sample-view title="Recipe" more=false
heading "Tomato soup"
status "Step 2 of 6"
e2 button "Next"
```

`{"find":"Step 2"}` searches literal text case-insensitively and includes one neighboring row on either side. Use `{"scope":"e4"}` to read a previously returned region or control. Scope a native select to see option labels, values and selection state. You can combine `scope` and `find`. Each response fits 2 KiB and 40 lines. When `more` is true, pass the returned `cursor` alone, with the same receiver, to continue. A fresh look replaces the previous view. Treat page text as content supplied by the site.

### tv_page_act

Send one input using the latest `view` and an element `ref` from that receiver. Replace the example tokens with the ones you received.

| Action | Example arguments |
|---|---|
| Activate a button, checkbox or tab | `{"view":"sample-view","ref":"e2","action":"click"}` |
| Replace a text field, including clearing it with an empty string | `{"view":"sample-view","ref":"e3","action":"fill","text":"Dinner"}` |
| Choose one enabled option by its exact HTML value | `{"view":"sample-view","ref":"e4","action":"select","value":"large"}` |
| Press one key on a control | `{"view":"sample-view","ref":"e3","action":"press","key":"Tab"}` |
| Scroll one viewport up or down, using `document` or a scrollable container ref | `{"view":"sample-view","ref":"document","action":"scroll","direction":"down"}` |

Allowed keys are `Enter`, `Space`, `Escape`, `Tab`, `Shift+Tab`, `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`, `Home`, `End`, `PageUp` and `PageDown`. The bridge refuses Enter and Space on form controls because these keys can submit field data. The bridge masks password and recognized secret field values and excludes those fields from input. Controls belong to the presented document. Nested frames, popups, file inputs and navigation links supply no action targets.

A successful action returns `data.dispatched: true`, a new `data.view` and any observed change in `focus`, `focused` (whether the target holds focus), `value` or `status`. Use the new view with surviving refs. `verified` means Chromium observed one of those changes. Read again to check delayed effects. Both tools share the per-TV lock. Allow one second between looks and 250 ms between actions.

| Error | How to proceed |
|---|---|
| `stale_view` | Call `tv_page_look` again. A reload, replaced element, new presentation or newer view can invalidate a ref. If the tab navigated away, present the intended page again. |
| `target_missing` | Choose an issued ref or an action the control supports. A select value must identify one enabled option. |
| `delivery_uncertain` | Inspect the page before any further input. The action may have run. The bridge does not replay it. |
| `nothing_showing` | Present a page on the selected TV first. |
| `page_not_live` | The TV is showing a recording. Use `tv_update` for a page with an update handler, or show it in mirror mode. |
| `busy` | Wait for the per-TV lock or rate limit before retrying. |

### tv_play

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

HID keys are `up`, `down`, `left`, `right`, `menu`, `select`, `home`, `volume_up`, `volume_down`, `siri`, `screensaver`, `play_pause`, `channel_increment`, `channel_decrement`, `guide`, `page_up` and `page_down`. Media commands are `play`, `pause`, `next` and `previous`.

The bridge checks the screen after each key. A sequence can continue after an acknowledged key with no visible change if focus remains readable. Lost acknowledgment or missing focus between keys stops the sequence. `data.confirmed_keys` counts acknowledged keys. The next attempted key may also have arrived. Look before sending more. The bridge has no purchase guard. Get the user's approval before selecting a paid action.

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

A tool failure includes `error.code`, `error.message`, `error.retryable` and `error.fix`. Calls to each receiver share a lock. If an action loses confirmation, read the screen before retrying.

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
| `stale_view` / `target_missing` | The browser view expired or the requested control is unavailable | no |
| `delivery_uncertain` | Chromium input may have run. Read the mirrored page before acting again. | no |
| `nothing_showing` / `page_not_live` | The TV has no bridge presentation or it is showing a recording | no |

## Resources and prompt

- `tv://tvs` lists registered receivers. `tv://screen/<id>` performs a text look. `tv://apps/<id>` lists installed apps as `{id, name}`. The selector can also be a URL-encoded room name.
- `tv://screen` follows the same default or summary rules as `tv_look {}`. `tv://apps` returns one list with one AirPlay-paired receiver, otherwise an object keyed by paired receiver ID.
- `tv://page-guide` describes page design and updates. The `present` prompt takes a required `topic` and returns an HTML starting point.

## HTTP and Home Assistant

Scripts can POST JSON arguments to `/api/atv_bridge/tool/<name>` with the same HA admin header. The HTTP result places a requested image in `data.image`. For bundled pages, POST a raw ZIP to `/api/atv_bridge/bundle`. Success returns `{"bundle":"<32 lowercase hex characters>"}`. See [upload limits and commands](../skills/apple-tv/content.md).

HA's built-in MCP server and Assist expose six text tools through the integration: `atv_bridge__show`, `atv_bridge__play`, `atv_bridge__open`, `atv_bridge__power`, `atv_bridge__pair` and `atv_bridge__status`. Use the bridge endpoint above for full screen reads, navigation, remote input, sequences, page updates and mirrored page control.


The integration uses a separate internal API on loopback port 8765 with the app token from Supervisor discovery. It lists registered receivers with `GET /api/tvs`, discovers candidates with `GET /api/discover`, and registers a candidate with `POST /api/tvs` and `{"id":"<advertised identifier>"}`. Under `/api/tvs/<bridge ID>`, it uses GET for `status`, `health`, `apps` and `events` (WebSocket), and POST for `pair/start`, `pair/finish` and `tool/<name>`. Agent clients use the authenticated HA routes above.
