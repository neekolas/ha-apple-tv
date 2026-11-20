"""Exercise stage, live iframe, frame refusal and return with real isolated Chromium/tvpipe."""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

from aiohttp import web

from atv_bridge_app.present.sender import STAGE_URL
from atv_bridge_app.present.tvpipe import TvpipeSender
from atv_bridge_app.stage import Stage
from atv_bridge_app.state import State, Fault
from atv_bridge_app.tools import Tools
from atv_bridge_app.tools.contract import FILL_RECIPE, OUTLINE_RECIPE, SELECT_RECIPE, WAIT_RECIPE
from atv_bridge_app.tools.public import PublicTools

PAGE = """<!doctype html><title>Recipe</title><h1>Live page</h1>
<section aria-label="Recipe steps"><div id="status" role="status">Step 1</div>
<button id="next">Next</button>
<input aria-label="Note"><input type="password" aria-label="Password" value="private-fixture">
<input aria-label="API token" value="private-fixture">
<input type="checkbox" checked aria-label="Ready">
<button hidden>Hidden control</button><div inert><button>Inert control</button></div>
<div aria-hidden="true"><button>Aria hidden</button></div><button style="display:none">CSS hidden</button>
</section><iframe srcdoc="<button>Nested secret</button>"></iframe>
<a href="/continued" id="continue">Continue</a><div style="height:300vh"></div>
<script nonce="fixture">window.setStep = step => document.querySelector('#status').textContent = 'Step ' + step;
document.querySelector('#next').onclick = () => window.setStep(2);</script>"""


def check_listener(profile):
    # Check the running browser, including the final flag order from tvpipe.
    commands = [p.read_bytes().split(b'\0') for p in Path('/proc').glob('[0-9]*/cmdline') if p.exists()]
    args = next(cmd for cmd in commands if ('--user-data-dir=' + profile.name).encode() in cmd
                and not any(a.startswith(b'--type=') for a in cmd))
    assert [a for a in args if a.startswith(b'--autoplay-policy=')][-1] == b'--autoplay-policy=no-user-gesture-required'
    port = int((Path(profile.name) / "DevToolsActivePort").read_text().splitlines()[0])
    addresses = []
    for table in ("tcp", "tcp6"):
        for line in Path("/proc/net", table).read_text().splitlines()[1:]:
            address, port_hex = line.split()[1].split(":")
            if int(port_hex, 16) == port and line.split()[3] == "0A":
                addresses.append(address)
    assert addresses and set(addresses) <= {"0100007F", "00000000000000000000000001000000"}, addresses


async def run(tools, script, arg=None, *, label=None, **options):
    await asyncio.sleep(.26)
    started = time.perf_counter()
    reply = await tools.call("tv_page_run", {"script": script, "arg": arg, **options})
    if label:
        print(label, "ms", round(1000 * (time.perf_counter() - started)), flush=True)
    assert reply["ok"], reply
    return reply["data"]


async def exercise_outline(tools, frame, direct):
    result = await run(tools, "return [window === top, window.setStep(arg)]", 1)
    assert result["result"] == [direct, "Step 1"]
    rows = (await run(tools, OUTLINE_RECIPE))["result"]
    assert {"role": "heading", "text": "Live page"} in rows and {"role": "button", "text": "Next"} in rows
    assert {"role": "checkbox", "text": "Ready", "value": True} in rows
    assert all(word not in str(rows) for word in ["private-fixture", "Hidden control", "Inert control",
                                                  "Aria hidden", "CSS hidden", "Nested secret"])
    await run(tools, "document.querySelector('#next').click()")
    assert (await run(tools, "document.querySelector('#status').innerText"))["result"] == "Step 2"
    await asyncio.sleep(.26)
    navigation = await tools.call("tv_page_run", {"script": "document.querySelector('#continue').click()"})
    assert navigation["ok"] or navigation["error"]["code"] == "stale_view", navigation
    await frame.wait_for_url("**/continued")
    assert (await run(tools, "return [location.pathname, document.querySelector('#status').innerText]"))["result"] == ["/continued", "Step 1"]


async def exercise_scripts(tools, frame, submissions):
    # Simulate a controlled input. A framework observes an own-property setter
    # and ignores input events when the DOM value equals its cached value.
    await frame.evaluate("""() => {
        const form = document.createElement('form');
        form.id = 'edit';
        form.action = '/submitted';
        form.innerHTML = '<input id="note" name="note"><textarea id="details"></textarea>'
           + '<select id="size" name="size"><option>s</option><option>l</option></select>'
           + '<button id="advance" type="button">Advance</button><output id="result"></output>';
        document.body.append(form);
        const el = form.querySelector('#note');
        const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        let tracked = el.value;
        Object.defineProperty(el, 'value', {get: native.get, set(value) {
            tracked = String(value); native.set.call(this, value);
        }});
        form.addEventListener('input', event => {
            if (event.target === el && tracked !== el.value) form.dataset.note = tracked = el.value;
        });
        form.addEventListener('change', event => form.dataset[event.target.id + 'Change'] = event.target.value);
        form.querySelector('#advance').onclick = event => {
            form.dataset.trusted = String(event.isTrusted);
            setTimeout(() => form.querySelector('#result').textContent = 'Done', 500);
        };
        el.addEventListener('keydown', event => form.dataset.down = event.key);
        el.addEventListener('keyup', event => form.dataset.up = event.key);
    }""")
    await run(tools, FILL_RECIPE, {"selector": "#note", "text": "fixture"})
    await run(tools, FILL_RECIPE, {"selector": "#details", "text": "Extra"})
    await run(tools, SELECT_RECIPE, "l")
    await run(tools, "document.querySelector('#advance').click()")
    assert (await run(tools, WAIT_RECIPE))["result"] == "Done"
    data = await run(tools, """const form = document.querySelector('#edit');
            const el = form.querySelector('#note'); el.focus();
            for (const type of ['keydown', 'keyup'])
                document.activeElement.dispatchEvent(new KeyboardEvent(type, {key: 'Z', bubbles: true}));
            form.scrollIntoView();
            return {state: {...form.dataset}, value: el.value, scrolled: scrollY > 0};""")
    assert data["result"] == {"state": {"note": "fixture", "noteChange": "fixture", "detailsChange": "Extra",
        "sizeChange": "l", "trusted": "false", "down": "Z", "up": "Z"}, "value": "fixture", "scrolled": True}, data
    previous = len(submissions)
    await asyncio.sleep(.25)
    submitted = await tools.call("tv_page_run", {"script": "document.querySelector('form').requestSubmit()"})
    assert submitted["ok"] or submitted["error"]["code"] == "stale_view", submitted
    await frame.wait_for_url("**/submitted?*")
    assert submissions[previous:] == ["note=fixture&size=l"]
    assert (await run(tools, "document.querySelector('h1').innerText"))["result"] == "Live page"


async def exercise_large_page(tools, frame):
    await frame.evaluate("""() => {
        const buttons = Array.from({length: 1500}, (_, i) => {
            const button = document.createElement('button');
            button.textContent = 'Control ' + i;
            return button;
        });
        document.body.replaceChildren(...buttons);
    }""")
    rows = (await run(tools, OUTLINE_RECIPE, label="1500 controls first page"))["result"]
    assert len(rows) == 40 and rows[0]["text"] == "Control 0"
    find = "[...document.querySelectorAll(arg.selector)].filter(el => el.innerText.includes(arg.text)).map(el => el.innerText)"
    matches = await run(tools, find, {"selector": "button", "text": "Control 1499"}, label="1500 controls find last")
    assert matches["result"] == ["Control 1499"]

    await frame.evaluate("""() => {
        document.body.innerHTML = '<header><nav aria-label="Repository"><a href="/code">Code</a>'
          + '<a href="/issues">Issues</a><button>Watch</button></nav></header>'
          + '<main><h1>Project files</h1><table><tbody></tbody></table></main>';
        const body = document.querySelector('tbody');
        for (let i = 0; i < 145; i++) {
            const tr = body.insertRow();
            tr.innerHTML = '<td><a href="/file/' + i + '">module-' + i + '.py</a></td>'
              + '<td>Update the module documentation</td><td><button>History ' + i + '</button></td>';
        }
        const article = document.querySelector('main').appendChild(document.createElement('article'));
        for (let i = 0; i < 60; i++) {
            article.insertAdjacentHTML('beforeend', '<section aria-label="Guide ' + i + '">'
              + '<h2>Configuration ' + i + '</h2><p>See the reference for details.</p></section>');
        }
    }""")
    rows = (await run(tools, OUTLINE_RECIPE, label="repository first page"))["result"]
    assert len(rows) == 40 and {"role": "link", "text": "Issues"} in rows
    links = await run(tools, find, {"selector": "a[href]", "text": "Issues"}, label="repository find Issues")
    assert links["result"] == ["Issues"]
    assert (await run(tools, "document.querySelector('h1').innerText", label="repository targeted read"))["result"] == "Project files"
    await run(tools, "document.querySelector('nav button').click()", label="repository script click")


async def exercise_limits(tools):
    capped = await run(tools, "'🍅'.repeat(20000)")
    assert capped["truncated"] and capped["bytes"] == 80002
    assert len(capped["preview"].encode()) <= 65536 and "�" not in capped["preview"]
    await asyncio.sleep(.26)
    timeout = await tools.call("tv_page_run", {"script": "new Promise(() => {})", "timeout_ms": 150})
    assert timeout["error"]["code"] == "script_error" and "150 ms" in timeout["summary"], timeout
    tools.page_scripts = False
    try:
        for script in ("document.title", OUTLINE_RECIPE):
            reply = await tools.call("tv_page_run", {"script": script})
            assert reply["error"]["code"] == "scripts_disabled" and "page_scripts" in reply["error"]["fix"]
    finally:
        tools.page_scripts = True
    assert (await run(tools, "Promise.resolve(document.title)"))["result"] == "Recipe"


async def check(root, benchmark=False):
    store = State(root)
    stage = Stage(root)
    site = web.Application()

    submissions = []

    async def content(request):
        if request.path == "/redirect":
            raise web.HTTPFound("/direct")
        if request.path == "/submitted":
            submissions.append(request.query_string)
        if request.path.startswith("/browser-error") and "Chrom" in request.headers.get(
            "User-Agent", ""
        ):
            # The app's GET succeeds; only Chromium encounters a broken connection.
            raise web.HTTPFound("http://127.0.0.1:18991/unreachable")
        headers = (
            {"X-Frame-Options": "DENY"}
            if request.path in ("/direct", "/auth", "/browser-error-direct")
            else {}
        )
        headers["Content-Security-Policy"] = "script-src 'nonce-fixture'"
        return web.Response(
            text=PAGE,
            content_type="text/html",
            headers=headers,
            status=401 if request.path == "/auth" else 200,
        )

    site.router.add_get("/{name}", content)
    stage_server, site_server = web.AppRunner(stage.app()), web.AppRunner(site)

    # Substitute tvpipe's browser sink for its AirPlay receiver. Keep the real
    # machine protocol, profile, capture tab and navigation code.
    executable = root / "tvpipe-browser"
    executable.write_text("""#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
i=args.index('--to')+1
assert args[i]=='127.0.0.1:7000'
args[i]='browser'
os.execvp('tvpipe',['tvpipe',*args,'--port','0'])
""")
    executable.chmod(0o700)
    mirror = TvpipeSender("127.0.0.1", store, str(executable), ready=stage.ready)
    ops = Tools(None, stage, mirror, None, store)
    ops.live_mirror = mirror
    tools = PublicTools(ops, mirror.size)
    try:
        await stage_server.setup()
        await site_server.setup()
        await web.TCPSite(stage_server, "127.0.0.1", 8766).start()
        await web.TCPSite(site_server, "127.0.0.1", 8767).start()
        runner = None
        for label, source in [
            (
                "inline",
                {"html": PAGE},
            ),
            ("live frame", {"url": "http://localhost:8767/frame"}),
            ("direct", {"url": "http://127.0.0.1:8767/redirect"}),
            ("HTTP 401 page", {"url": "http://127.0.0.1:8767/auth"}),
            ("back on stage", {"files": {"index.html": PAGE}}),
        ]:
            if benchmark and label != "live frame":
                continue
            options = dict(
                url=None, html=None, files=None, bundle=None, title=label, audio="mute", keep_awake=True
            )
            await ops.present(**(options | source))
            if benchmark:
                frame = await mirror.presented_frame(stage.page["page_id"], stage.page["revision"], False)
                await exercise_large_page(tools, frame)
                return
            capture = await mirror.browser_page()
            assert capture.url == (
                "http://127.0.0.1:8767/direct" if label == "direct" else source["url"] if label == "HTTP 401 page" else STAGE_URL
            ), capture.url
            check_listener(mirror.profile)
            direct = stage.direct_browser is mirror
            frame = await mirror.presented_frame(stage.page["page_id"], stage.page["revision"], direct)
            assert (await run(tools, "window.setStep(3)"))["result"] == "Step 3"
            if label == "inline":
                await exercise_limits(tools)
            if label in ("live frame", "direct"):
                await exercise_outline(tools, frame, direct)
                await exercise_scripts(tools, frame, submissions)
            if label == "live frame":
                await exercise_large_page(tools, frame)
            if label == "direct":
                async with capture.expect_popup() as opened:
                    await capture.evaluate("() => { window.open(location.href) }")
                popup = await opened.value
                await popup.wait_for_load_state()
                await popup.evaluate("document.title = 'Popup only'")
                assert (await run(tools, "document.title"))["result"] == "Recipe"
                await popup.close()
                previous = mirror.proc
                previous.terminate()
                async with asyncio.timeout(25):
                    while mirror.proc is previous or not mirror.started:
                        await asyncio.sleep(.1)
                assert (await run(tools, "document.title"))["result"] == "Recipe"
            if runner:
                assert runner is mirror.runner
            runner = mirror.runner
            print(label, "script confirmed", flush=True)
        bundle = stage.page["page_id"]
        await ops.present(**(options | {"url": f"http://localhost:8766/c/{bundle}/index.html"}))
        await asyncio.sleep(1)
        blocked = await tools.call("tv_page_run", {"script": "document.title"})
        assert blocked["error"]["code"] == "target_missing", blocked
        for endpoint in ("browser-error", "browser-error-direct"):
            try:
                await ops.present(**(options | {"url": "http://127.0.0.1:8767/" + endpoint}))
            except Fault as exc:
                assert exc.code == "page_unreachable" and stage.page is None, (endpoint, exc)
                print(endpoint, str(exc), flush=True)
            else:
                raise AssertionError(f"{endpoint} was reported loaded")
    finally:
        await mirror.stop()
        await site_server.cleanup()
        await stage_server.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as root:
        asyncio.run(check(Path(root), "--benchmark" in sys.argv))
