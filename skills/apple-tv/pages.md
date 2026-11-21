# Build good TV content

How to build pages that read well from the sofa and show your work while you do it. The usual session is a live voice conversation where the agent rarely speaks. The TV is the reply.

## The TV is the reply

- Put the answer on the screen. Do not read it aloud as well.
- Show progress on the screen, not in speech. Do not say "I'm working on it" or "Putting it up now."
- Show something within a second or two of the request, even if it is only a title and empty places. Then fill it in.
- One page per task. Update it in place instead of showing a new page for each change.
- When you finish, the page holds the result. Clear the status line and stop.

## Show work in progress

Start from [pending-page.html](pending-page.html). It defines four functions on `window.tv`. Call them with `tv_page_run`. Each returns `{working, pending}`, so you can check the page state without a screenshot.

| Call | Effect |
|---|---|
| `tv.working(text)` | Shows a status line at the bottom, such as "Finding a recipe". Use short present-tense text. |
| `tv.pending(name, label)` | Reserves a place for a result that is still coming. The label says what will appear there. |
| `tv.fill(name, html)` | Puts a result in its place and removes the placeholder. Plain text works too. |
| `tv.done()` | Hides the status line. |

The flow:

1. Edit the last lines of the starter for this request: the title, one `tv.pending` per expected result and the first `tv.working` text. Send it with `tv_show {"html": "..."}` before you start the real work. Starting the mirror takes a few seconds, and this way it overlaps with your work.
2. As each result arrives, fill it and say what comes next, in one call:
   `tv_page_run {"script":"tv.fill('summary', arg); return tv.working('Writing the steps')","arg":"Sweet, savory pork in about 30 minutes."}`
3. When all places are filled, call `tv_page_run {"script":"tv.done()"}`.

Combine updates into one script. The bridge refuses a second `tv_page_run` that arrives within 0.25 seconds with `busy`. On the test TV, `tv_show` took 2.7 to 5.2 seconds to start the mirror, and each later `tv_page_run` update took 0.03 to 0.7 seconds.

You can add the four functions to your own page instead. Keep the names, so any agent can drive it the same way.

### When the screen cannot show progress

- **Someone is watching.** If `tv_look` shows playback or another app in use, do not replace the screen just to show progress. Ask first, or answer by voice.
- **Recorded mode.** If `tv_show` returns `data.transport: "video"`, `tv_page_run` cannot change the page. Show the final page with a new `tv_show`.
- **Native apps.** While a native app is on screen there is no page to update. Let the app's own screen show the result, and check it with `tv_look`.
- **Mirror slot taken.** On `mirror_busy`, ask before using `replace: true`, because another room may be watching.

In these cases, a short spoken line is the right fallback.

## Canvas and type

- Design for the configured output. Mirroring defaults to 1920×1080 and also supports 1280×720. Read `data.size` after `tv_show`. Use a responsive layout and fit each step on one screen.
- Use body text of at least 28 px and headings of 56 px or more. People read from several metres away.
- Use high contrast and few words per screen. Dark backgrounds suit a living room in the evening.
- Keep a margin of about 60 px at the top and bottom and 80 px at the sides, on a 1080p layout.
- If you need to inspect the layout, `tv_look {"image":true,"tv":"<room>"}` takes a screenshot and shows a toast.

## Motion

- The TV receives the page as a mirror at about 10 frames per second by default. Fast motion can look uneven at that rate.
- Prefer static layouts. Change content in steps, on a page function call or on a data refresh. Change only what changed, for example the numbers on a dashboard.
- Avoid continuous animation, video inside the page, canvas or WebGL loops, and heavy blur or shadow effects. Rendering and encoding share the HA host’s CPU.
- A short fade between steps is fine.
- To play a video, use `tv_play {"media": "<URL>"}` or an app, not a page. The Apple TV fetches `media` URLs itself, so the URL must be reachable from the TV.

## Input

- Siri Remote keys operate tvOS. For agent input on a mirrored page, give each control an accessible name and use a status region for visible changes. Give controls stable IDs so agents can call `document.querySelector('#next').click()` or read a status directly in `tv_page_run`.
- Refresh recorded pages with a new `tv_show`.

## Audio and reading aloud

- Page audio (`<audio>`, Web Audio) plays on the TV. The bridge browser allows autoplay, so a page can start audio without a click.
- Pass `audio: "mute"` to `tv_show` for a silent page.
- To read text aloud, generate the speech audio yourself with any text-to-speech you have. Ship the clips in `files` or a bundle, or serve them from your live server. Play a clip from the page, for example when the caller invokes your playback function.
- Keep one clip per step or paragraph, so your playback function can jump to any of them.

## Recorded mode

For stored content in `play_url` mode or after `auto` falls back, `tv_show` returns `data.transport: "video"`. The bridge records the page to an MP4 and plays it over AirPlay. The first result may say `verified: false` while it renders, and a new `tv_show` records a new clip. Keep pages static in this mode. Live URLs always use mirroring.

## Starting points

- The resource `tv://page-guide` has the short rules.
- The MCP prompt `present` takes a `topic` and returns a TV-ready HTML scaffold that exposes a render function.
- [pending-page.html](pending-page.html) is a starter with the work-in-progress functions above.
