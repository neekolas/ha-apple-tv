#!/usr/bin/env python3
"""Screenshot a TV app the way the bridge shows it, without a TV.

The page loads in an iframe with the bridge's sandbox (opaque origin, no
storage) at 1920x1080 or 1280x720, with the files beside it (such as fonts/)
as in a bundle. Each patch runs through app.apply, like tv_page_run, and each
step saves a PNG. --no-fonts leaves fonts/ out, as when a page is sent inline.

Usage:
  uv run --with playwright skills/tv-use-web-apps/scripts/preview.py APP.html OUT_DIR \
      [--size 1920x1080] [--step '{"demo":true}'] [--step '{"view":"detail","item":2}']

With no --step, it shows the app's pending state and then {"demo": true}.
It uses an installed Chrome. Install one, or run `playwright install chromium`
and pass --bundled.
"""

import argparse
import asyncio
import json
import pathlib
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HARNESS = """<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;height:100%;background:#000}iframe{border:0;width:100%;height:100%}</style>
<iframe id="app" sandbox="allow-scripts allow-forms" src="/app/index.html"></iframe>"""


TYPES = {".woff2": "font/woff2", ".jpg": "image/jpeg", ".png": "image/png", ".svg": "image/svg+xml"}


def serve(app: pathlib.Path, fonts: bool):
    root = {"/index.html": HARNESS.encode(), "/app/index.html": app.read_bytes(), "/favicon.ico": b""}
    for f in app.parent.rglob("*"):
        rel = f.relative_to(app.parent).as_posix()
        if f.is_file() and f != app and (fonts or not rel.startswith("fonts/")):
            root["/app/" + rel] = f.read_bytes()

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            body = root.get("/index.html" if path == "/" else path)
            if body is None:
                self.send_response(404)  # with the CORS header, so a missing font logs a 404, not a CORS error
                self.send_header("Content-Length", "0")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", TYPES.get(pathlib.PurePath(path).suffix, "text/html; charset=utf-8"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")  # as the bridge sends; sandboxed fonts need it
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=pathlib.Path)
    parser.add_argument("out", type=pathlib.Path)
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--step", action="append", default=[])
    parser.add_argument("--bundled", action="store_true", help="use Playwright's bundled Chromium")
    parser.add_argument("--no-fonts", action="store_true", help="leave out fonts/, as when the page is sent inline")
    args = parser.parse_args()
    width, height = map(int, args.size.split("x"))
    steps = [json.loads(s) for s in args.step] or [{"demo": True}]
    args.out.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright

    server = serve(args.app, not args.no_fonts)
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(**({} if args.bundled else {"channel": "chrome"}))
        page = await browser.new_page(viewport={"width": width, "height": height})
        page.on("console", lambda m: m.type == "error" and not (args.no_fonts and "404" in m.text)
                and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(f"http://127.0.0.1:{server.server_port}/")
        frame = page.frame_locator("#app")
        await frame.locator("body").wait_for()
        app = next(f for f in page.frames if f.url.endswith("/app/index.html"))
        await page.wait_for_timeout(400)
        await page.screenshot(path=args.out / "0-initial.png")
        print("0-initial", json.dumps(await app.evaluate("window.app && window.app.state()")))
        for n, patch in enumerate(steps, 1):
            try:
                state = await app.evaluate("p => window.app.apply(p)", patch)
            except Exception as exc:  # report and keep going
                state = {"error": str(exc).splitlines()[0]}
            await page.wait_for_timeout(450)
            await page.screenshot(path=args.out / f"{n}.png")
            print(n, json.dumps(patch)[:80], "->", json.dumps(state)[:300])
        await browser.close()
    server.shutdown()
    if errors:
        print("PAGE ERRORS:", *errors, sep="\n  ")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
