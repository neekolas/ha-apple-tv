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
       return {step};
     }
     render(1);
     window.setStep = render;
   </script>
   ```

2. When the user says “next”, call `tv_page_run {"script":"window.setStep(arg)","arg":2}`. Check that `data.result.step` is 2.
3. When cooking is done, call `tv_show {}`.

## Read and operate a page

Read a known field with `tv_page_run {"script":"document.querySelector('#note').value"}`. Pass each recipe below as the tool's `script`.

For quick discovery, narrow the selector when you know a container or control type. This example uses common roles and input types and includes checkbox and radio state. It omits free-text field values.

```js
const roles = {a:'link', button:'button', text:'textbox', password:'textbox', search:'searchbox', h1:'heading', h2:'heading', h3:'heading', h4:'heading', h5:'heading', h6:'heading'};
const items = [...document.querySelectorAll('button,a[href],input,h1,h2,h3,h4,h5,h6')];
return items.filter(el => el.checkVisibility({visibilityProperty: true}) && !el.closest('[inert],[aria-hidden=true]')).slice(0, 40)
  .map(el => ({role: el.getAttribute('role') || roles[el.localName === 'input' ? el.type : el.localName] || el.type, text: (el.getAttribute('aria-label') || el.innerText).trim().slice(0, 100),
    value: el.matches('input[type=checkbox],input[type=radio]') ? el.checked : undefined}));
```

Fill a framework-controlled input or textarea. Pass `{"selector":"#note","text":"Dinner"}` as `arg`. The native setter and bubbling events let the framework observe the change.

```js
const el = document.querySelector(arg.selector);
const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, arg.text);
for (const type of ['input', 'change']) el.dispatchEvent(new Event(type, {bubbles: true}));
```

Select an option. Pass its value as `arg`.

```js
const el = document.querySelector('#size');
el.value = arg;
el.dispatchEvent(new Event('change', {bubbles: true}));
```

Click with `document.querySelector('#advance').click()`. Submit with `document.querySelector('form').requestSubmit()`. Read the destination before retrying if navigation interrupted the result.

Wait for a status with a bounded poll. Return the status when the page reaches it.

```js
const end = performance.now() + 1500;
while (document.querySelector('#result')?.textContent !== 'Done') {
  if (performance.now() >= end) throw new Error('Status did not reach Done');
  await new Promise(resolve => setTimeout(resolve, 25));
}
return 'Done';
```

To call a key handler, dispatch bubbling `KeyboardEvent` events for `keydown` and `keyup` on `document.activeElement`. Synthetic keys do not type text or trigger browser default actions. Scroll with `el.scrollIntoView()` or `window.scrollBy(0, 500)`.

## Find a YouTube podcast and play it

1. Check `screen.playing` with `tv_look {}`. If something is playing, ask before you replace it.
2. Find the episode URL with your own web search.
3. `tv_play {"youtube": "<URL or ID>"}`. Check `verified` and `screen.playing`.
4. With no URL: `tv_open {"app": "YouTube", "search": "<podcast name>"}`, then `tv_look {}`, then `tv_navigate {"to": "<exact episode label>", "select": true}`.

## Live dashboard from your machine

1. Serve the dashboard on `127.0.0.1:5173`. Have it fetch fresh data every few seconds, or use a WebSocket. Update only the numbers; do not animate.
2. `tailscale serve --bg --https=8443 http://127.0.0.1:5173`
3. `tv_show {"url": "https://<agent-machine>.<tailnet>.ts.net:8443/", "title": "Dashboard"}`. The page updates itself.
4. When the user is done: `tv_show {}`, then stop the server and `tailscale serve --https=8443 off`.

## Photo slideshow

1. Resize the photos to fit 1920×1080, as JPEG. This keeps the bundle small and decoding cheap on the HA host.
2. Write `index.html` that shows one photo at a time and switches every 8 to 10 s with a short fade. Expose functions such as `window.goTo(index)` and `window.pauseSlides()`.
3. Zip `index.html` and the photos with `index.html` at the root. Upload the ZIP to `/api/atv_bridge/bundle` (see [content.md](content.md)).
4. `tv_show {"bundle": "<id>", "title": "Photos", "audio": "mute"}`.
5. To go back to the first photo, send `tv_page_run {"script":"window.goTo(0)"}`. To pause, send `tv_page_run {"script":"window.pauseSlides()"}`. The stored bundle can be reused until the cache evicts it.

## Read an article aloud

1. Fetch the article and clean it into paragraphs.
2. Generate one speech clip per paragraph with your text-to-speech tool, for example `p1.mp3`, `p2.mp3`.
3. Build `files` (or a bundle if the clips are large): `index.html` shows the title and the current paragraph in large type. Expose `window.readParagraph(p)` to show paragraph `p` and play its clip. When a clip ends, it moves to the next paragraph by itself and updates `window.currentParagraph`.
4. `tv_show {"files": {"index.html": "...", "p1.mp3": "base64:...", "p2.mp3": "base64:..."}, "title": "<article title>"}`.
5. Start with `tv_page_run {"script":"window.readParagraph(1)"}`. Read the current position with `tv_page_run {"script":"window.currentParagraph"}`.

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
