---
name: tv-use-web-apps
description: Build and show web apps on an Apple TV through tv-use. Covers the TV app contract, design rules for a mirrored 10-foot screen, five reference apps (cook-along, pick-one, home board, quiz night, answer card), bundle upload and live updates with tv_page_run. Use when an agent wants to put a page, dashboard, game or visual answer on the TV.
---

# Build web apps for the TV

`tv_show` puts a page on the TV. Chromium on the Home Assistant host renders it in a sandboxed iframe and mirrors it to the TV at about 10 frames per second. `tv_page_run` runs JavaScript in that page, so an agent changes the page in place instead of showing a new one.

Related skills: **tv-use-connect** reaches the TV and has the error table. **tv-use-realtime** covers how to run a live voice session around these pages.

## Start from a reference app

Each app is one self-contained HTML file in `assets/apps/`. It starts in a pending layout, takes data and changes through `app.apply(patch)` and includes the `tv` status kit. Read the header comment of the file you pick for its full patch API.

| App | Use it for | Interaction |
|---|---|---|
| `cook-along.html` | Recipes and any step-by-step task | Next and back, timers, checklist |
| `pick-one.html` | Choosing a movie, restaurant, trip or recipe | Numbered cards, details, filter, pick |
| `home-board.html` | An ambient dashboard that stays up for hours | Sections update quietly by patch |
| `quiz-night.html` | A trivia game with a voice host | Questions, countdown, reveal, scores |
| `answer-card.html` | The visual reply to a spoken question | Sections fill in as results arrive |
| `pending-page.html` (in `assets/`) | Anything else, quickly | A title, pending places and the status kit |

Adapt a reference app before writing one from scratch. Keep its contract: `window.app.apply`, `window.app.state` and `window.tv`.

## Show and update

1. **Upload once.** With a shell, run `scripts/upload-bundle.sh assets/apps/<app>.html`. It needs `HA_URL` and `HA_TOKEN` and prints a bundle ID. Without a shell, pass the whole file as `tv_show {"html":"..."}`.
2. **Show it at once,** before the data is ready: `tv_show {"tv":"<room>","bundle":"<id>","title":"<what it is>"}`. Starting the mirror takes a few seconds. The app shows its pending layout meanwhile.
3. **Change it with patches.** `tv_page_run {"tv":"<room>","script":"app.apply(arg)","arg":{...}}`. The result is `app.state()`. Combine changes into one patch; the bridge refuses a second script within 0.25 seconds.
4. **Show progress** with `tv.working(text)` in the same script, and `tv.done()` when finished.
5. **Hide it** with `tv_show {"tv":"<room>"}` when the user is done.

Reuse the bundle ID for later shows. If `tv_show` reports the bundle is gone, upload it again.

## Rules that matter most

- Design a fixed 1920×1080 stage and scale it to the window. Body text 32 px or more, headings 56 px or more, dark background.
- No continuous animation. The mirror runs at about 10 fps and shares the HA host's CPU.
- Number every selectable item, so the user can say "the third one".
- No network access is needed or assumed. Pages run in a sandbox with no storage.
- Video belongs in `tv_play`, not in a page.

## Load when needed

| File | Load it to |
|---|---|
| [app-contract.md](references/app-contract.md) | Build or change an app: canvas, type, motion, the `app` and `tv` APIs |
| [design.md](references/design.md) | Check design details: motion at 10 fps, input, audio and reading aloud, recorded mode |
| [delivery.md](references/delivery.md) | Choose `html`, `files`, `bundle` or a live `url`; upload limits; page lifetime; sandbox; mirror slots |
| [recipes.md](references/recipes.md) | Follow worked examples: a recipe, DOM recipes for any page, a live dashboard, a slideshow, reading an article aloud |

## Preview without a TV

`scripts/preview.py` renders an app in the bridge's sandbox and saves screenshots after each patch:

```sh
uv run --with playwright scripts/preview.py assets/apps/pick-one.html /tmp/preview \
  --step '{"demo":true}' --step '{"view":"detail","item":2}'
```

It uses an installed Chrome. Check `--size 1280x720` too.
