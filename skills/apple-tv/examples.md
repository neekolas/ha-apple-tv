# Worked examples

These examples assume a default TV. With several paired TVs, add the same `tv` name or ID to every call, including the final hide. Replace sample values and file contents with your own.

## Step-by-step recipe

1. Pass this page as the `html` string to `tv_show`, with `title` set to `Pasta e ceci`.

   ```html
   <main style="font:48px system-ui"></main>
   <script>
     const steps = ["Bring water to a boil", "Add pasta"];
     function render(step) {
       document.querySelector("main").textContent = steps[step - 1];
       tv.state = {step};
     }
     render(1);
     tv.on("update", data => render(data.step));
   </script>
   ```

2. When the user says “next”, call `tv_update {"data":{"step":2}}`. Check that `data.page_state.step` is 2.
3. When cooking is done, call `tv_show {}`.

## Click Next on the recipe page

When the user asks for the next step on a mirrored recipe with a **Next** button, read `tv_page_look {"find":"Next"}`. If the snapshot shows `e2 button "Next"`, send `tv_page_act {"view":"<returned view>","ref":"e2","action":"click"}` to the same TV. Check `data.status` for the new step. If the page updates later, read `tv_page_look {"find":"Step 3"}` after one second. A click can succeed before the page displays the next step.

## Find a YouTube podcast and play it

1. Check `screen.playing` with `tv_look {}`. If something is playing, ask before you replace it.
2. Find the episode URL with your own web search.
3. `tv_play {"youtube": "<URL or ID>"}`. Check `verified` and `screen.playing`.
4. With no URL: `tv_open {"app": "YouTube", "search": "<podcast name>"}`, then `tv_look {}`, then `tv_navigate {"to": "<exact episode label>", "select": true}`.

## Live dashboard from your machine

1. Serve the dashboard on `127.0.0.1:5173`. Have it fetch fresh data every few seconds, or use a WebSocket. Update only the numbers; do not animate.
2. `tailscale serve --bg --https=8443 http://127.0.0.1:5173`
3. `tv_show {"url": "https://<agent-machine>.<tailnet>.ts.net:8443/", "title": "Dashboard"}`. The page updates itself, so `tv_update` is not needed.
4. When the user is done: `tv_show {}`, then stop the server and `tailscale serve --https=8443 off`.

## Photo slideshow

1. Resize the photos to fit 1920×1080, as JPEG. This keeps the bundle small and decoding cheap on the HA host.
2. Write `index.html` that shows one photo at a time and switches every 8 to 10 s with a short fade. Accept `{"action": "next"}`, `{"action": "pause"}` and `{"index":0}` through `tv.on("update", ...)`. Set `tv.state = {index, paused}`.
3. Zip `index.html` and the photos with `index.html` at the root. Upload the ZIP to `/api/atv_bridge/bundle` (see [content.md](content.md)).
4. `tv_show {"bundle": "<id>", "title": "Photos", "audio": "mute"}`.
5. To go back to the first photo, send `tv_update {"data":{"index":0}}`. To pause, send `tv_update {"data":{"action":"pause"}}`. The stored bundle can be reused until the cache evicts it.

## Read an article aloud

1. Fetch the article and clean it into paragraphs.
2. Generate one speech clip per paragraph with your text-to-speech tool, for example `p1.mp3`, `p2.mp3`.
3. Build `files` (or a bundle if the clips are large): `index.html` shows the title and the current paragraph in large type. On `tv.on("update", d => ...)` it shows paragraph `d.p` and plays `p{d.p}.mp3`. When a clip ends, it moves to the next paragraph by itself and sets `tv.state = {p}`.
4. `tv_show {"files": {"index.html": "...", "p1.mp3": "base64:...", "p2.mp3": "base64:..."}, "title": "<article title>"}`.
5. Start with `tv_update {"data": {"p": 1}}`. Use `tv_look {}` to read `data.page_state` and see where the reading is.

## Find which installed service has a show

1. Read `tv://apps/<id>` for the target receiver’s installed apps.
2. Use your own web search to find which streaming services carry the show in the user's country.
3. Keep only services that are installed. If there are several, ask the user which one to use.
4. If the service guide has a confirmed template for that service, find the title's ID and call `tv_play {"link": "..."}`. Otherwise `tv_open {"app": "<service>", "search": "<show>"}`, then `tv_look {}` to check the results. See [deep links by service](deeplinks.md).
5. The bridge can answer recognized profile pickers. Check `evidence` for the chosen profile. Stop at any sign-in or upgrade screen and ask the user (see [apps.md](apps.md)).

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
