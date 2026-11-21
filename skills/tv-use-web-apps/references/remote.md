# Let someone use the Siri Remote on a page

Use this when a person at the TV should move through or pick things on a page you show.

| `remote` on `tv_show` | For | Effect |
|---|---|---|
| `"tvos"` | Pages you write, including every reference app | `focus` with the tvOS look: the focused item lifts 6% with a ring and shadow, arrows search straight ahead first, and Left and Right stay inside a `data-tv-group` row. Follow the layout rules in [app-contract.md](app-contract.md#remote-input). |
| `"focus"` | Ordinary pages you did not write | Arrows move a visible focus ring between links and buttons; Select activates the focused one. A page that calls `preventDefault()` on a key keeps that key. |
| `"keys"` | Pages that handle `keydown` themselves | Trusted `ArrowLeft`, `ArrowRight`, `ArrowUp`, `ArrowDown` and `Enter` events. |
| `"off"` (default) | | Presses aren't forwarded. |

Example: `tv_show {"bundle":"<pick-one ID>","remote":"tvos"}`

To see what the person did, read `tv_page_run {"script":"tv.state().input"}`: `count`, `last` (`{n, action, detail, at}`) and the five most recent actions. Each app's header lists the actions it reports.

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
