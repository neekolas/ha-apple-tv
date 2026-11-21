# Worked examples

These examples assume a default TV. With several paired TVs, add the same `tv` name or ID to every call, including the final hide. Replace sample values and file contents with your own.

## Find a YouTube podcast and play it

1. Check `screen.playing` with `tv_look {}`. If something is playing, ask before you replace it.
2. Find the episode URL with your own web search.
3. `tv_play {"youtube": "<URL or ID>"}`. Check `verified` and `screen.playing`.
4. With no URL: `tv_open {"app": "YouTube", "search": "<podcast name>"}`, then `tv_look {}`, then `tv_navigate {"to": "<exact episode label>", "select": true}`.

## Find which installed service has a show

1. Read `tv://apps/<id>` for the target receiver’s installed apps.
2. Use your own web search to find which streaming services carry the show in the user's country.
3. Keep only services that are installed. If there are several, ask the user which one to use.
4. If the service guide has a confirmed template for that service, find the title's ID and call `tv_play {"link": "..."}`. Otherwise `tv_open {"app": "<service>", "search": "<show>"}`, then `tv_look {}` to check the results. See [deep links by service](deeplinks.md).
5. The bridge can answer recognized profile pickers. Check `evidence` for the chosen profile. Stop at any sign-in or upgrade screen and ask the user (see [native-apps.md](native-apps.md)).

## Visit a Settings menu and return Home

Use one `tv_sequence` call to open the device list, confirm the page heading, back out and return Home. The path opens the list without selecting a device.

```json
{
  "steps": [
    {"app":"Settings","path":["Remotes and Devices","Remote App and Devices"]},
    {"expect":{"app":"Settings","visible":"Remote App and Devices"}},
    {"key":"menu"},
    {"pause_ms":1500},
    {"expect":{"app":"Settings","focus":"Remote App and Devices"}},
    {"key":"home"},
    {"pause_ms":1500},
    {"expect":{"app":"Home"}}
  ]
}
```

Inspect the final checkpoint and `screen.app`. If the device list uses a different accessible heading, the sequence stops there and reports the labels it observed.
