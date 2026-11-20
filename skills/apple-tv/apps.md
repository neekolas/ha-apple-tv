# Drive native apps

Read `tv://apps/<id>` if the app name is ambiguous. For a YouTube video, inspect the reported title because playback metadata can omit the video ID. For a service link, use `data.landed` to decide whether to continue from a title page or browse the app. A regional Apple TV catalog link may hand off to a provider. Copy its visible action label before continuing. See [deep links](deeplinks.md).

## Batch a known route

Start with a text look to identify full labels. Use `tv_sequence` when the route is known and each later action can be guarded on the TV. Use `to` for a named menu row and `expect` for the state the next step needs. Raw keys are faster for a known transition. Insert a short pause when the UI needs time to settle, then check the destination.

To visit several rows on one menu, use `for_each` and `{"to":"$item"}`. A checkpoint can require both `focus` and `value` before a subsequent action. If a sequence stops, inspect the failed step and current screen. Earlier iterations may already be complete. See the [worked Settings visit](examples.md#visit-a-settings-menu-and-return-home).

## Profiles and dialogs

Before an action, the bridge may press Menu to dismiss a screen saver. For a recognized tvOS picker it tries the configured `default_profile`, then the focused profile. For app pickers, the preferred name is `app_profile`, the last observed system profile, or `default_profile`, in that order. If it cannot match the preferred name it may choose the focused profile or the first eligible candidate. It excludes Kids profiles when another eligible profile is visible. Read the chosen label in `evidence`.

With screen pairing, recognized privacy prompts can be dismissed by selecting a visible decline option. If the bridge cannot identify a choice, follow the returned fix. For a recognized custom scheme, the bridge checks for an Open confirmation. It can take one screenshot per launch to recognize the centered English dialog and can send Select when the named app and focused Open button match. A relaunch after a profile choice can repeat the check.

## Power and paid actions

`tv_power {"on":false}` ends the presentation and waits for Off. An already-off TV skips wake. `tv_remote {"keys":["pause"]}` requests Pause. `play_pause` toggles playback. Check `verified` and the resulting state before sending another key.

Someone may be watching. Use a text look before interrupting playback. Ask the user before selecting Buy, Rent, Subscribe or Upgrade. The bridge sends the selected action and has no purchase guard. Hand sign-in and TV-provider prompts to the user.
