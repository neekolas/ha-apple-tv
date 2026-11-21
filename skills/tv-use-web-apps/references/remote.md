# Let someone use the Siri Remote on a page

Use this when a person at the TV should move through or pick things on a page you show.

| `remote` on `tv_show` | For | Effect |
|---|---|---|
| `"focus"` | Ordinary pages | Arrows move a visible focus ring between links and buttons; Select activates the focused one. A page that calls `preventDefault()` on a key keeps that key. |
| `"keys"` | Pages that handle `keydown` themselves | Trusted `ArrowLeft`, `ArrowRight`, `ArrowUp`, `ArrowDown` and `Enter` events. |
| `"off"` (default) | | Presses aren't forwarded. |

Example: `tv_show {"html":"<button onclick=\"next()\">Next</button>","remote":"focus"}`

## Check it

- `data.remote.state` is `"active"` when presses arrive.
- `"unavailable"` comes with a `reason`: `screen_not_paired` → `tv_pair {"kind":"screen"}`; `not_mirrored` → the page is a recording and gets no input. The page shows either way.
- Read presses from `screen.presenting.remote.presses` and `.last` in any tool result, or `tv_page_run {"script":"window.__tvRemote"}`.

## Limits

- Needs screen pairing and a mirror.
- Menu and Home stay with tvOS; Menu ends the mirror. Play/Pause and volume aren't forwarded.
- Your own `tv_remote` presses reach the page too.
- Clickpad Siri Remote: ring clicks become arrows, the center becomes Enter. 1st-gen touch remote: swipes become arrows (a fast flick moves several items), any click becomes Enter.
- Press to page takes about 150–250 ms.
- Verified: `tv_remote` presses on tvOS 26.6; clickpad Select and arrows on tvOS 27.2; touch-remote clicks on tvOS 26.6. Touch swipes are verified only by replaying a real capture. Clickpad swipes are untested.
