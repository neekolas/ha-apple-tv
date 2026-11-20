# Drive native apps

Pass the room name or ID as `tv` on each call when there is more than one paired receiver. Read `tv://apps/<id>` for the installed names and bundle IDs. `tv_open` uses fuzzy matching. An ambiguous name returns `app_not_installed` with candidates in `data.apps`.

## Reach a title

1. For YouTube, use `tv_play {"youtube":"<video URL or 11-character ID>"}`. The bridge checks now-playing metadata in YouTube. The video ID may be absent from that metadata, so check the reported title.
2. For a known service link, use `tv_play {"link":"<URL>"}`. On success, `data.landed` distinguishes `playing`, `title_page` and `app_home`. Read `screen.app`, `screen.playing` and the available actions. A welcome screen or profile picker can count as app home. See [deep links](deeplinks.md).
3. For a title and an app, use `tv_play {"app":"<app>","title":"<title>"}`. It uses a known title recipe or searches, selects a matching label and checks playback in the app. If needed it tries a visible Play, Watch Now or Resume action.
4. To browse the results yourself, use `tv_open {"app":"<app>","search":"<query>"}`. YouTube and Spotify have search links. Other recipes select Search and replace the field text. Read the results before choosing a label.

On `launch_refused` or `uncertain`, look before trying another route. An earlier step can already have changed the screen. For a regional `tv.apple.com` link, the bridge sets `action=play`. The TV app can show an Open In button or hand off to a provider. Copy the actual label from a text look. The bridge can verify provider handoffs only for cataloged Apple title IDs.

## Read and navigate

- `tv_look {}` reads accessible items and focus without waking the TV. Focus can be null. The reported app can come from accessibility or now-playing metadata. Playback metadata can outlive the foreground app.
- `tv_navigate {"to":"<full label>","select":true}` moves one key at a time. Matching ignores case and allows a prefix. Navigation has a 40-press limit and stops after three direction presses that leave focus unchanged. Check the result's path and `verified` flag.
- For a known menu, use `tv_open {"app":"Settings","path":["Remotes and Devices"]}`. The bridge verifies the Settings root first. Inspect `data.steps` when present. A setting can verify through a changed `screen.focus.value` on the same row.
- If text is empty, let the app settle. When you need pixels, use `tv_look {"image":true}`. This shows a screenshot toast. Default maximum width is 1280 px, configurable from 1 to 4096. A successful capture can have an empty item list. Protected video can appear black.

## Profiles and dialogs

Before an action, the bridge may press Menu to dismiss a screen saver. For a recognized tvOS picker it tries the configured `default_profile`, then the focused profile. For app pickers, the preferred name is `app_profile`, the last observed system profile, or `default_profile`, in that order. If it cannot match the preferred name it may choose the focused profile or the first eligible candidate. It excludes Kids profiles when another eligible profile is visible. Read the chosen label in `evidence`.

With screen pairing, recognized privacy prompts can be dismissed by selecting a visible decline option. If the bridge cannot identify a choice, follow the returned fix. For a recognized custom scheme, the bridge checks for an Open confirmation. It can take one screenshot per launch to recognize the centered English dialog and can send Select when the named app and focused Open button match. A relaunch after a profile choice can repeat the check.

## Power and paid actions

`tv_power {"on":false}` ends the presentation and waits for Off. An already-off TV skips wake. `tv_remote {"keys":["pause"]}` requests Pause. `play_pause` toggles playback. Check `verified` and the resulting state before sending another key.

Someone may be watching. Use a text look before interrupting playback. Ask the user before selecting Buy, Rent, Subscribe or Upgrade. The bridge sends the selected action and has no purchase guard. Hand sign-in and TV-provider prompts to the user.
