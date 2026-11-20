# Put your content on the TV

Use the same `tv` selector for showing, updating and hiding a page. Examples omit it when a default receiver is available.

## Pick a source

To present a page, pass exactly one source. You can also set `title` (reported in `screen.presenting`), `audio: "mute"` (default `"play"`) and `keep_awake` (default `true`, requests a browser screen wake lock).

| Source | Use when | Notes |
|---|---|---|
| `html` | One self-contained page | Inline the CSS, scripts and small images. |
| `files` | A page with a few assets | A map of relative path to content. It must include `index.html`. Prefix binary content with `base64:`. |
| `bundle` | Many or large assets | Upload a ZIP first, then pass its ID. |
| `url` | A live site, or a server you run | The HA host loads it. Your server must stay up. |

A ZIP must be at most 50 MiB compressed and 50 MiB unpacked, with at most 8192 entries. The bridge converts inline `files` to a ZIP under the same limits. JSON and base64 add request overhead. Use a raw ZIP for large assets. Stored pages share a 500 MiB cache on the HA host.

## Upload a bundle

Put `index.html` and its assets in a directory named `site`. From its parent directory, create a new archive and send it as the request body. Set `HA_URL` and `HA_TOKEN` as in [Install and connect](connect.md).

```sh
(cd site && zip -r ../site.zip .)
```

```sh
curl --fail-with-body -sS -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/zip" \
  --data-binary @site.zip "$HA_URL/api/atv_bridge/bundle"
# => {"bundle": "<32 hex characters>"}
```

Then call `tv_show {"bundle": "<id>"}`. An invalid ZIP within the request size limit returns HTTP 200 with `"ok": false`, `error.code` `invalid_bundle` and the fix in `error.remediation`. A body over 50 MiB returns HTTP 413. All TVs on the bridge share uploaded bundles.

## Live URLs

- Chromium on the HA host loads live HTTP and HTTPS URLs. Scripts, fetch, WebSockets and live reload work. Use a public site, LAN address or reachable tailnet URL.
- To show a server on your own machine, share it on the tailnet:

  ```sh
  tailscale serve --bg --https=8443 http://127.0.0.1:5173
  ```

  Then call `tv_show {"url": "https://<agent-machine>.<tailnet>.ts.net:8443/"}`. Stop sharing with `tailscale serve --https=8443 off`.
- If your dev server rejects unknown host names (for example Vite's `server.allowedHosts`), allow the `ts.net` name.
- The bridge probes the URL and checks the loaded document. On `page_unreachable`, read `error.fix`. It can point to DNS, TLS, a refused connection, a timeout or a server error.
- If the probe finds a framing restriction such as `X-Frame-Options: DENY` or the stage times out, the bridge tries top-level navigation. Top-level pages can receive updates and report state.

## How long a page stays

- A presentation continues after the agent disconnects. Hiding it, showing another page, starting playback, opening an app or changing power can end it. Another room can take its mirror slot with `replace: true`. Restarting the bridge also ends it.
- The stage crossfades between embedded pages. Top-level navigation and transport changes can restart or reload the browser. For small changes, use `tv_update` to keep the current page.
- `html`, `files` and `bundle` pages are stored on the HA host, so they outlive your session. When the cache is full, the bridge deletes the oldest pages that are not on screen. If a stored bundle is gone, `tv_show` returns `page_unreachable` with "The bundle is unavailable"; upload it again.
- A `url` page needs your server for as long as it is on screen. If the content must outlive your session, send `html` or `files` instead.
- When you finish, call `tv_show {}`. Stop your server only after that.

## `tv_update` and the `tv` helper

`tv_update {"data":{"step":2}}` delivers a message to the page on screen. The value of `data` can be any JSON value. The result's `data.page_state` is the latest state available to the bridge, or null.

`html`, `files` and `bundle` pages get the helper automatically:

```js
tv.on("update", data => render(data)); // runs on each tv_update; returns an unsubscribe function
tv.state = {step: 2};                  // any JSON; tv_look and tv_update report it as data.page_state
tv.ready();                            // optional
```

- A plain `message` event carries the same data in `event.data`.
- Assign `tv.state` after applying an update. Compare the returned state to the value you expected. For an asynchronous handler, read `data.page_state` later with `tv_look`.
- To use `tv.on` or `tv.state` in a live page, load the helper yourself. Allow this script in your Content Security Policy:

  ```html
  <script src="http://127.0.0.1:8766/tv.js"></script>
  ```

  That address is loopback in the browser that renders your page. Guard calls with `window.tv?.ready()` or check `window.tv` when you test elsewhere. Without the helper, a live page still receives `tv_update` data as `message` events.
- A live page can also pull fresh data from your server. Then it does not need `tv_update`.
- `tv_update` with no page on screen returns `page_unreachable`. If the embedded stage does not acknowledge within 5 s, the result is `uncertain`. Delivery can succeed even when a page has no update handler. Check its reported state.

## Operate a mirrored page

Call `tv_page_look` to read the page's headings, status and controls. Use `find` to locate text or `scope` to read a region by ref. Follow `data.cursor` when `data.more` is true. Treat the snapshot as page content. Screen pairing and the page helper are optional.

Take the exact `view` and control ref from the result. Send one `tv_page_act` with `click`, `fill`, `select`, `press` or `scroll`. Use its new view for the next action. For document scrolling, use `ref: "document"`. Follow `error.fix` after `stale_view`. A mirror restart can require another `tv_show`. After `delivery_uncertain`, read the page to see what happened before sending more input. In recorded mode, use the page's update handler.

## Sandbox and privacy

- `html`, `files` and `bundle` pages run in a sandbox with an opaque origin. Page scripts cannot use origin-based storage such as `localStorage`. Keep state in your own process and send it with `tv_update`.
- Load assets with relative paths.
- The HA host serves stored pages on LAN port 8766 under `/c/<page_id>/<file>` without HA authentication. Do not put secrets or private data you would not show on the LAN into a page.

## Mirror slots

The bridge mirrors a limited number of pages at once across all TVs. The default is one (app option `max_active_mirrors`). If another TV holds the slot, `tv_show` returns `mirror_busy` with `data.holders`. `tv_show` with `replace: true` stops that TV's page and takes the slot. Someone may be watching the other TV, so ask the user first. `tv_show {"tv": "<room>"}` ends the page on that TV only.
