"""Exercise stage, live iframe, frame refusal and return with real isolated Chromium/tvpipe."""

import asyncio
import re
import tempfile
import time
from pathlib import Path

from aiohttp import web

from atv_bridge_app.present.sender import STAGE_URL
from atv_bridge_app.present.tvpipe import TvpipeSender
from atv_bridge_app.stage import Stage
from atv_bridge_app.state import State, Fault
from atv_bridge_app.tools import Tools
from atv_bridge_app.tools.public import PublicTools

PAGE = """<!doctype html><script src="http://127.0.0.1:8766/tv.js"></script>
<title>Recipe</title><h1>Live page</h1>
<section aria-label="Recipe steps"><div role="status">Step 1</div>
<button onclick="document.querySelector('[role=status]').textContent='Step 2'">Next</button>
<input aria-label="Note" onkeydown="if(event.key==='Enter') document.querySelector('[role=status]').textContent='Saved'">
<select aria-label="Size"><option value="s">Small</option><option value="l">Large</option></select>
<input type="password" aria-label="Password" value="private-fixture">
<input aria-label="API token" value="private-fixture"></section>
<section aria-label="Notes" style="overflow:auto;height:4em"><p style="height:12em">Scrollable notes</p></section>
<a download href="/sample">Download fixture</a><input type="file" aria-label="Attach fixture">
<iframe srcdoc="<button>Nested secret</button>"></iframe><div style="height:300vh"></div><script>
tv.state = {phase:"initial"};
tv.on("update", data => {document.querySelector("h1").textContent=data.title; tv.state=data;});
tv.ready();
</script>"""


def element(snapshot, role, name):
    match = re.search(rf'^([a-z]\d+) {role} "{name}"', snapshot, re.M)
    assert match, snapshot
    return match[1]


def check_listener(profile):
    port = int((Path(profile.name) / "DevToolsActivePort").read_text().splitlines()[0])
    addresses = []
    for table in ("tcp", "tcp6"):
        for line in Path("/proc/net", table).read_text().splitlines()[1:]:
            address, port_hex = line.split()[1].split(":")
            if int(port_hex, 16) == port and line.split()[3] == "0A":
                addresses.append(address)
    assert addresses and set(addresses) <= {"0100007F", "00000000000000000000000001000000"}, addresses


async def exercise(tools, frame, mode):
    async def look(**args):
        await asyncio.sleep(1)
        start = time.perf_counter()
        result = await tools.call("tv_page_look", args)
        print(mode, "look ms", round(1000 * (time.perf_counter() - start)), flush=True)
        assert result["ok"], result
        return result["data"]

    async def act(view, ref, action, **args):
        await asyncio.sleep(.25)
        return await tools.call("tv_page_act", {"view": view, "ref": ref, "action": action, **args})

    data = await look()
    assert data["mode"] == mode and "private-fixture" not in data["snapshot"], data
    assert "Nested secret" not in data["snapshot"] and "[masked]" in data["snapshot"]
    assert not re.search(r'^e\d+ .*"(Password|API token|Download fixture|Attach fixture)"', data["snapshot"], re.M)
    container = element(data["snapshot"], "region", "Notes")
    scrolled = await act(data["view"], container, "scroll", direction="down")
    assert scrolled["ok"] and await frame.get_by_role("region", name="Notes").evaluate("el => el.scrollTop > 0")
    region = element(data["snapshot"], "region", "Recipe steps")
    data = await look(scope=region)
    assert "heading " not in data["snapshot"] and "Scrollable notes" not in data["snapshot"]
    next_button = element(data["snapshot"], "button", "Next")
    note = element(data["snapshot"], "textbox", "Note")
    size = element(data["snapshot"], "combobox", "Size")
    click = await act(data["view"], next_button, "click")
    assert click["data"]["status"] == "Step 2", click
    assert (await act(data["view"], next_button, "click"))["error"]["code"] == "stale_view"
    fill = await act(click["data"]["view"], note, "fill", text="Dinner")
    assert fill["data"]["value"] == await frame.get_by_label("Note", exact=True).input_value() == "Dinner", fill
    press = await act(fill["data"]["view"], note, "press", key="Enter")
    assert press["data"]["status"] == "Saved", press
    missing = await act(press["data"]["view"], size, "select", value="missing")
    assert missing["error"]["code"] == "target_missing" and await frame.get_by_label("Size").input_value() == "s"
    selected = await act(press["data"]["view"], size, "select", value="l")
    assert selected["data"]["value"] == await frame.get_by_label("Size").input_value() == "l"
    old_y = await frame.evaluate("scrollY")
    scroll = await act(selected["data"]["view"], "document", "scroll", direction="down")
    assert scroll["data"]["dispatched"] and await frame.evaluate("scrollY") > old_y, scroll
    await frame.evaluate("document.querySelector('button').replaceWith(document.querySelector('button').cloneNode(true))")
    assert (await act(scroll["data"]["view"], next_button, "click"))["error"]["code"] == "stale_view"
    options = await look(scope=size)
    assert 'option "Large" value="l" selected' in options["snapshot"], options
    data = await look(find="Next")
    next_button = element(data["snapshot"], "button", "Next")
    await frame.evaluate("location.reload()")
    assert (await act(data["view"], next_button, "click"))["error"]["code"] == "stale_view"


async def exercise_snapshot(tools, frame):
    await frame.evaluate(r"""() => {
        const section = document.createElement('section');
        section.innerHTML = '<input type=password role=note value=private-note>'
          + '<input type=password role=status value=private-status>'
          + '<div role=listbox aria-label="Colors"><div role=option aria-selected=true>Red</div></div>'
          + '<div role=tree><div role=treeitem aria-label="Folder"><div role=group>'
          + '<div role=treeitem>Leaf</div></div></div></div><p></p>';
        section.querySelector('p').textContent = 'broken\uD800text\uFFFE';
        document.title = 'Recipe\uDFFF';
        document.body.append(section);
    }""")
    for name, role, child in (("Colors", "listbox", 'option "Red" selected'),
                              ("Folder", "treeitem", 'treeitem "Leaf"')):
        await asyncio.sleep(1)
        data = (await tools.call("tv_page_look", {}))["data"]
        assert "private-" not in data["snapshot"] and 'status "[masked]"' in data["snapshot"], data
        assert 'broken�text�' in data["snapshot"] and 'title="Recipe�"' in data["snapshot"]
        await asyncio.sleep(1)
        scoped = (await tools.call("tv_page_look", {"scope": element(data["snapshot"], role, name)}))["data"]
        assert child in scoped["snapshot"], scoped

    await asyncio.sleep(1)
    data = (await tools.call("tv_page_look", {"find": "Size"}))["data"]
    ref = element(data["snapshot"], "combobox", "Size")
    await frame.get_by_label("Size").evaluate("el => el.id = 'secret-token'")
    await asyncio.sleep(1)
    blocked = await tools.call("tv_page_look", {"scope": ref})
    assert blocked["error"]["code"] == "target_missing", blocked


async def exercise_form(tools, frame, submissions):
    await frame.evaluate("""() => {
        const section = document.createElement('section');
        section.innerHTML = '<form id="search" action="/submitted"><input name="q" aria-label="Form field"><button type="submit">Submit form</button></form>'
          + '<input form="search" aria-label="External field">';
        document.body.append(section);
    }""")
    await asyncio.sleep(1)
    data = (await tools.call("tv_page_look", {"find": "field"}))["data"]
    field = element(data["snapshot"], "textbox", "Form field")
    external = element(data["snapshot"], "textbox", "External field")
    url = frame.url
    filled = await tools.call("tv_page_act", {"view": data["view"], "ref": field, "action": "fill", "text": "fixture"})
    assert filled["ok"], filled
    for ref, key in ((field, "Enter"), (external, "Space")):
        await asyncio.sleep(.25)
        result = await tools.call("tv_page_act", {"view": filled["data"]["view"], "ref": ref, "action": "press", "key": key})
        assert result["error"]["code"] == "target_missing" and "form" in result["error"]["message"], result
    assert frame.url == url and submissions == []
    assert await frame.get_by_label("Form field").input_value() == "fixture"


async def exercise_revision(tools, ops, options, frame):
    await frame.evaluate("""() => {
        for (let i=0; i<60; ++i) {
            const button = document.createElement('button');
            button.textContent = 'Choice ' + i;
            document.body.append(button);
        }
    }""")
    await asyncio.sleep(1)
    started = time.perf_counter()
    read = (await tools.call("tv_page_look", {}))["data"]
    print("wide page look ms", round(1000 * (time.perf_counter() - started)), flush=True)
    assert read["more"] and len(read["snapshot"].encode()) <= 2048
    await asyncio.sleep(1)
    second = (await tools.call("tv_page_look", {"cursor": read["cursor"]}))["data"]
    assert second["view"] == read["view"] and 'button "Choice 59"' in second["snapshot"]
    ref = element(second["snapshot"], "button", "Choice 59")
    first_revision = ops.stage.page["revision"]
    await ops.present(**(options | {"bundle": read["page_id"]}))
    assert ops.stage.page["page_id"] == read["page_id"] and ops.stage.page["revision"] != first_revision
    assert (await tools.call("tv_page_act", {"view": read["view"], "ref": ref, "action": "click"}))["error"]["code"] == "stale_view"


async def exercise_large_page(tools, frame):
    await frame.evaluate("""() => {
        const buttons = Array.from({length: 1500}, (_, i) => {
            const button = document.createElement('button');
            button.textContent = 'Control ' + i;
            return button;
        });
        document.body.replaceChildren(...buttons);
    }""")
    for label, args in (("first page", {}), ("find last", {"find": "Control 1499"})):
        await asyncio.sleep(1)
        started = time.perf_counter()
        result = await tools.call("tv_page_look", args)
        print("1500 controls", label, "ms", round(1000 * (time.perf_counter() - started)), flush=True)
        assert result["ok"], result
        data = result["data"]
        assert data["more"] == (not args)
        element(data["snapshot"], "button", "Control 1499" if args else "Control 0")


async def check(root):
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
                {"html": PAGE.replace('<script src="http://127.0.0.1:8766/tv.js"></script>', "")},
            ),
            ("live frame", {"url": "http://localhost:8767/frame"}),
            ("direct", {"url": "http://127.0.0.1:8767/redirect"}),
            ("HTTP 401 page", {"url": "http://127.0.0.1:8767/auth"}),
            ("back on stage", {"files": {"index.html": PAGE}}),
        ]:
            options = dict(
                url=None, html=None, files=None, bundle=None, title=label, audio="mute", keep_awake=True
            )
            await ops.present(**(options | source))
            capture = await mirror.browser_page()
            assert capture.url == (
                "http://127.0.0.1:8767/direct" if label == "direct" else source["url"] if label == "HTTP 401 page" else STAGE_URL
            ), capture.url
            assert await stage.current_state() == {"phase": "initial"}, (
                label,
                await stage.current_state(),
            )
            await ops.update_page({"title": label})
            assert await stage.current_state() == {"title": label}
            if label in ("inline", "live frame", "direct"):
                check_listener(mirror.profile)
                direct = stage.direct_browser is mirror
                frame = await mirror.presented_frame(stage.page["page_id"], stage.page["revision"], direct)
                await exercise(tools, frame, "direct" if direct else "iframe")
                if label == "inline":
                    await exercise_revision(tools, ops, options, frame)
                if label == "live frame":
                    # Single-page apps change the URL in place. The document stays bound.
                    await frame.evaluate("""() => { document.querySelector('button').onclick = () => {
                      history.pushState({}, '', '/frame/step-2?x=1#top');
                      document.querySelector('[role=status]').textContent = 'Routed';
                    } }""")
                    await asyncio.sleep(1)
                    data = (await tools.call("tv_page_look", {"find": "Next"}))["data"]
                    args = {"view": data["view"], "ref": element(data["snapshot"], "button", "Next"), "action": "click"}
                    routed = await tools.call("tv_page_act", args)
                    assert routed["ok"] and routed["data"]["status"] == "Routed", routed
                    await asyncio.sleep(1)
                    after = await tools.call("tv_page_look", {})
                    assert after["ok"] and 'status "Routed"' in after["data"]["snapshot"], after
                    await frame.evaluate("history.replaceState({}, '', '/frame/step-3')")
                    await exercise_large_page(tools, frame)
                if label == "direct":
                    await exercise_form(tools, frame, submissions)
                    await exercise_snapshot(tools, frame)
                    async with capture.expect_popup() as opened:
                        await capture.evaluate("() => { window.open(location.href) }")
                    popup = await opened.value
                    await popup.wait_for_load_state()
                    await popup.evaluate("document.querySelector('button').textContent = 'Popup only'")
                    await asyncio.sleep(1)
                    pinned = (await tools.call("tv_page_look", {}))["data"]["snapshot"]
                    assert 'button "Next"' in pinned and "Popup only" not in pinned
                    await popup.close()
                    # The handler reloads while the input is being dispatched.
                    await frame.evaluate("() => { document.querySelector('button').onclick = () => location.reload() }")
                    await asyncio.sleep(1)
                    data = (await tools.call("tv_page_look", {"find": "Next"}))["data"]
                    action = {"view": data["view"], "ref": element(data["snapshot"], "button", "Next"), "action": "click"}
                    assert (await tools.call("tv_page_act", action))["error"]["code"] == "delivery_uncertain"
                    # End the real sender. Its supervisor starts a fresh capture at the raw redirect URL.
                    previous = mirror.proc
                    previous.terminate()
                    async with asyncio.timeout(25):
                        while mirror.proc is previous or not mirror.started:
                            await asyncio.sleep(.1)
                    await asyncio.sleep(1)
                    lost = await tools.call("tv_page_look", {})
                    assert lost["error"]["code"] == "stale_view" and "tv_show" in lost["error"]["fix"], lost
                    assert await stage.current_state() == {"phase": "initial"}
                    await ops.update_page({"title": "Restarted"})
                    assert await stage.current_state() == {"title": "Restarted"}
                    await ops.present(**(options | source))
                    await asyncio.sleep(1)
                    assert (await tools.call("tv_page_look", {}))["ok"]
            if runner:
                assert runner is mirror.runner
            runner = mirror.runner
            print(label, "update and state confirmed", flush=True)
        bundle = stage.page["page_id"]
        await ops.present(**(options | {"url": f"http://localhost:8766/c/{bundle}/index.html"}))
        await asyncio.sleep(1)
        blocked = await tools.call("tv_page_look", {})
        assert blocked["error"]["code"] == "target_missing" and "listener" in blocked["error"]["message"], blocked
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
        asyncio.run(check(Path(root)))
