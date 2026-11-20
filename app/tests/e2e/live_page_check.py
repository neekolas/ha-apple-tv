"""Exercise stage, live iframe, frame refusal and return with real isolated Chromium/tvpipe."""

import asyncio
import tempfile
from pathlib import Path

from aiohttp import web

from atv_bridge_app.present.sender import STAGE_URL
from atv_bridge_app.present.tvpipe import TvpipeSender
from atv_bridge_app.stage import Stage
from atv_bridge_app.state import State, Fault
from atv_bridge_app.tools import Tools

PAGE = """<!doctype html><script src="http://127.0.0.1:8766/tv.js"></script>
<h1>Live page</h1><script>
tv.state = {phase:"initial"};
tv.on("update", data => {document.querySelector("h1").textContent=data.title; tv.state=data;});
tv.ready();
</script>"""


async def check(root):
    store = State(root)
    stage = Stage(root)
    site = web.Application()

    async def content(request):
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
            ("live frame", {"url": "http://127.0.0.1:8767/frame"}),
            ("direct", {"url": "http://127.0.0.1:8767/direct"}),
            ("HTTP 401 page", {"url": "http://127.0.0.1:8767/auth"}),
            ("back on stage", {"files": {"index.html": PAGE}}),
        ]:
            options = dict(
                url=None, html=None, files=None, bundle=None, title=label, audio="mute", keep_awake=True
            )
            await ops.present(**(options | source))
            capture = await mirror.browser_page()
            assert capture.url == (
                source["url"] if label in ("direct", "HTTP 401 page") else STAGE_URL
            ), capture.url
            assert await stage.current_state() == {"phase": "initial"}, (
                label,
                await stage.current_state(),
            )
            await ops.update_page({"title": label})
            assert await stage.current_state() == {"title": label}
            if runner:
                assert runner is mirror.runner
            runner = mirror.runner
            print(label, "update and state confirmed", flush=True)
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
