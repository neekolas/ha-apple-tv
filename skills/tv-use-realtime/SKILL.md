---
name: tv-use-realtime
description: Run a live, real-time voice session on an Apple TV through tv-use, where the TV is the reply and the agent rarely speaks. Covers when to speak and when to stay silent, showing progress on screen instead of narrating, the latency of each tool and patterns for fast turns. Use when a user talks to an agent while watching the TV.
---

# Run a real-time session on the TV

The usual session is a live voice conversation. The user watches the TV and talks. The agent acts, and the screen answers. The agent almost never speaks.

Related skills: **tv-use-connect** has the tools and error handling. **tv-use-web-apps** has the reference apps and the `tv` status kit used below.

## Act, don't narrate

- Do the task without announcing it. Do not say what you are about to do or that you did it. No "Sure, putting it up now."
- Put the answer on the screen. Do not read aloud what the screen already shows.
- Show progress on the screen, not in speech. Do not say "I'm working on it."
- Speak only when the screen cannot carry the message, in one short sentence:
  - a choice or approval only the user can give: purchases, rentals, upgrades, sign-in, replacing another room's page, or choosing between services
  - a pairing code request from `data.ask_user`
  - a failure the screen does not show
  - an answer to a question that is not on the screen
  - a step that takes much longer than usual with nothing new on screen

## Show something within two seconds

1. Pick the page for the request. Use a reference app from **tv-use-web-apps**, or `pending-page.html` for anything else.
2. Show it at once, in its pending state, with a status line: `tv_show`, then `tv_page_run {"script":"tv.working('Finding a recipe')"}`. Starting the mirror takes a few seconds, so start it before the real work.
3. Do the work. As each result arrives, fill it and update the status in one call: `{"script":"app.apply(arg); return tv.working('Writing the steps')","arg":{...}}`.
4. When everything is in place, `tv.done()`. The page now holds the answer. Say nothing.

One page per task. Change it in place with `tv_page_run`. Do not show a new page for each change.

## Latency budget

Measured from a Mac on the home network, over a local connection:

| Call | Typical time |
|---|---|
| `tv_status` | about 0.07 s |
| `tv_page_run` update | 0.03 to 0.7 s |
| `tv_look`, text only | about 0.6 s |
| `tv_look` with a screenshot | about 2.5 s |
| `tv_remote` burst, with the new screen | about 2 s |
| `tv_show` of a new page | 2.7 to 5.2 s, mostly starting the mirror |
| `tv_navigate` over three moves | about 8 s |

- Use a local connection when you can. A remote connection adds the trip between the agent and the home to every call.
- Prefer `tv_page_run` patches over new `tv_show` calls.
- Prefer text-only `tv_look`. Take a screenshot only when labels are not enough. It also shows a toast on the TV.
- Send known remote keys as one `tv_remote` burst. Use `tv_sequence` for a known route with checkpoints.
- Leave 0.25 seconds between `tv_page_run` calls, or combine them.

## When the screen cannot show progress

- **Someone is watching.** If `tv_look` shows playback or another app in use, do not replace the screen just to show progress. Ask first, or answer by voice.
- **Recorded mode.** If `tv_show` returns `data.transport: "video"`, `tv_page_run` cannot change the page. Show the final page with a new `tv_show`.
- **Native apps.** While a native app is on screen there is no page to update. Let the app show the result, and check it with `tv_look`.
- **Mirror slot taken.** On `mirror_busy`, ask before using `replace: true`. Another room may be watching.

In these cases, a short spoken line is the right fallback.

## Load when needed

| File | Load it to |
|---|---|
| [flows.md](references/flows.md) | Follow worked voice flows: cook along, pick something to watch, answer a question, run a quiz, drive a native app |
