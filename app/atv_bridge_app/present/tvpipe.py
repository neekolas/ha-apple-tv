import asyncio
import json
import struct
import tempfile
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from ..live import page_failure
from ..processes import child_env, reap
from ..state import Fault
from . import browsers
from .sender import STAGE_URL, Sender


async def stop_sender(proc):
    # A machine-mode stop sends AirPlay TEARDOWN before Chromium exits.
    payload = b'{"type":"stop"}'
    record = struct.pack("<BQI", 6, 0, len(payload)) + payload
    try:
        async with asyncio.timeout(4):
            if proc.returncode is None:
                await proc.communicate(record)
    except (TimeoutError, BrokenPipeError, ConnectionResetError):
        pass
    finally:
        await reap(proc, group=True, sweep=browsers.sweep)


class TvpipeSender(Sender):
    def __init__(
        self,
        host,
        state,
        executable="tvpipe",
        restart_delay=1,
        ready=None,
        *,
        size="1920x1080",
        fps=10,
        resolve_endpoint=None,
    ):
        super().__init__("mirror")
        self.size = size
        self.fps = fps
        self.stage_ready = ready
        self.stage_url = STAGE_URL
        self.host = host
        self.resolve_endpoint = resolve_endpoint
        self.state = state
        self.executable = executable
        self.restart_delay = restart_delay
        self.url = None
        self.audio = "play"
        self.running_audio = None
        self.frames = 0
        self.profile = self.driver = self.page = None
        self.runner = self.proc = None
        self.child_lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.started = False
        self.problem = None

    async def start(self, stage_url):
        if self.runner and self.running_audio != self.audio:
            await self.stop()
        async with self.child_lock:
            await browsers.sweep(
                self.proc.pid if self.proc and self.proc.returncode is None else None
            )
        if not self.runner or self.runner.done():
            browsers.log_memory("before mirror")
            self.url, self.running_audio = stage_url, self.audio
            if self.profile is None:
                self.profile = tempfile.TemporaryDirectory(prefix="atv-mirror-")
            self.problem = None
            self.changed.clear()
            self.runner = asyncio.create_task(self._run())
        try:
            async with asyncio.timeout(20):
                while not self.started and not self.problem:
                    self.changed.clear()
                    await self.changed.wait()
        except TimeoutError as exc:
            raise Fault(
                "sender_failed",
                "The mirror did not start.",
                "Check the receiver and AirPlay access.",
                True,
            ) from exc
        if self.problem:
            raise self.problem
        if self.url != stage_url:
            page = await self.browser_page()
            try:
                response = await page.goto(stage_url, wait_until="domcontentloaded", timeout=8000)
                if response and response.status >= 500:
                    raise ValueError(f"HTTP {response.status}")
            except Exception as exc:
                raise page_failure(urlsplit(stage_url).hostname, exc) from exc
            self.url = stage_url
        if stage_url == self.stage_url and self.stage_ready is not None:
            await asyncio.wait_for(self.stage_ready.wait(), 8)
        self.transition("showing", ["tvpipe started", "Page loaded"])

    async def browser_page(self):
        if self.page is None or self.page.is_closed():
            port = (Path(self.profile.name) / "DevToolsActivePort").read_text().splitlines()[0]
            if not self.driver:
                self.driver = await async_playwright().start()
            browser = await self.driver.chromium.connect_over_cdp(f"http://127.0.0.1:{int(port)}")
            pages = [
                page
                for context in browser.contexts
                for page in context.pages
                if page.url.startswith(("http://", "https://"))
            ]
            self.page = next((page for page in pages if page.url == self.url), None)
            if self.page is None and len(pages) == 1:
                self.page = pages[0]  # Site redirected the captured tab.
            if self.page is None:
                raise Fault(
                    "page_unreachable",
                    "The captured browser tab is unavailable.",
                    "Show the page again.",
                )
        return self.page

    async def verify_document(self, url, page_id, direct):
        """Inspect the committed document in the exact iframe captured by tvpipe."""
        host = urlsplit(url).hostname
        try:
            async with asyncio.timeout(8):
                page = await self.browser_page()
                if direct:
                    frame = page.main_frame
                else:
                    element = await page.locator(
                        f'iframe[data-page-id="{page_id}"]'
                    ).element_handle(timeout=8000)
                    frame = await element.content_frame()
                if not frame:
                    raise ValueError("Chromium did not attach the requested document")
                # CDP evaluates in the target frame, including cross-origin
                # frames. An error document can have the requested frame URL.
                document = await frame.evaluate("""() => ({
                    url: document.documentURI,
                    state: document.readyState,
                    error: document.querySelector('.error-code, #error-code')?.textContent
                })""")
                if (
                    urlsplit(document["url"]).scheme not in ("http", "https")
                    or document["state"] == "loading"
                ):
                    raise ValueError(
                        document["error"]
                        or f"Chromium loaded {document['url']} ({document['state']})"
                    )
        except Exception as exc:
            raise page_failure(host, exc) from exc

    async def post_message(self, data):
        page = await self.browser_page()
        await page.evaluate(
            "data => window.dispatchEvent(new MessageEvent('message', {data, source:window}))", data
        )

    async def page_state(self):
        page = await self.browser_page()
        return await page.evaluate("window.tv?.state ?? null")

    async def confirm_frame(self, timeout=3):
        initial = self.frames
        try:
            async with asyncio.timeout(timeout):
                while self.frames <= initial:
                    await asyncio.sleep(0.05)
            return True
        except TimeoutError:
            return False

    async def stop(self):
        try:
            if self.runner:
                self.runner.cancel()
                with suppress(asyncio.CancelledError):
                    await self.runner
        finally:
            self.runner = None
            try:
                if self.driver:
                    async with asyncio.timeout(3):
                        await self.driver.stop()
            finally:
                self.page = self.driver = None
                self.started, self.problem, self.frames = False, None, 0
                try:
                    if self.profile:
                        self.profile.cleanup()
                        self.profile = None
                finally:
                    await browsers.sweep()
                    browsers.log_memory("after mirror")
        self.transition("idle")

    async def _run(self):
        delay = self.restart_delay
        failed_host = None
        while True:
            proc = None
            self.frames = 0
            self.transition("starting")
            try:
                host, port = (
                    await self.resolve_endpoint(failed_host) if self.resolve_endpoint else (self.host, 7000)
                )
                self.state.write_tvpipe(host, port)
                async with self.child_lock:
                    await browsers.sweep()
                    self.proc = proc = await browsers.spawn(
                        self.executable,
                        "--machine",
                        "share",
                        self.url,
                        "--to",
                        f"{host}:{port}",
                        "--size",
                        self.size,
                        "--fps",
                        str(self.fps),
                        "--bitrate",
                        "2500" if self.size == "1280x720" else "4000",
                        *(["--profile", self.profile.name] if self.profile else []),
                        *(["--no-audio"] if self.audio == "mute" else []),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        env=child_env(TVPIPE_CONFIG_DIR=str(self.state.root / "tvpipe")),
                    )
                proc.stdin.write(b"SLRW")
                await proc.stdin.drain()
                while True:
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), 0.5)
                    except TimeoutError:
                        if proc.returncode is not None:
                            break
                        continue
                    if not line:
                        break
                    event = json.loads(line)
                    kind = event.get("type")
                    if kind == "pin-required" or event.get("code") in (
                        "pairing-required",
                        "pin-missing",
                    ):
                        raise Fault(
                            "pairing_required",
                            "tvpipe requested AirPlay pairing.",
                            "Pair AirPlay and show the page again.",
                        )
                    if kind == "stats":
                        self.frames = sum(
                            row.get("pushed", 0) for row in event.get("receivers", [])
                        )
                    if kind == "started":
                        self.transition("showing", ["tvpipe started"])
                        failed_host = None
                        self.started = True
                        self.changed.set()
                        delay = self.restart_delay
                    if kind == "error" and not self.started:
                        raise Fault(
                            "sender_failed",
                            f"tvpipe startup failed ({event.get('code') or event.get('kind')}).",
                            "Check the receiver address, AirPlay access and Chromium.",
                            True,
                        )
                    if kind == "receiver" and event.get("state") == "ended":
                        break
                    if kind == "end" and event.get("reason") != "error":
                        break
                if not self.started:
                    raise Fault(
                        "sender_failed",
                        "tvpipe ended before starting.",
                        "Check AirPlay access.",
                        True,
                    )
            except Exception as exc:  # noqa: BLE001 - Report child process and machine protocol errors to the waiting caller.
                if (
                    self.resolve_endpoint
                    and proc
                    and failed_host is None
                    and isinstance(exc, Fault)
                    and exc.code == "sender_failed"
                ):
                    failed_host = host
                    continue
                self.problem = (
                    exc
                    if isinstance(exc, Fault)
                    else Fault(
                        "sender_failed",
                        "Cannot start or read tvpipe.",
                        "Check the app installation.",
                        True,
                    )
                )
                self.fail(self.problem)
                self.changed.set()
                return
            finally:
                self.started = False
                self.page = None
                if proc:
                    cleanup = asyncio.create_task(self._clean_child(proc))
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
            failed_host = host
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _clean_child(self, proc):
        async with self.child_lock:
            try:
                await stop_sender(proc)
            finally:
                self.proc = None
                try:
                    if self.driver:
                        async with asyncio.timeout(3):
                            await self.driver.stop()
                finally:
                    self.driver = None
                    await browsers.sweep()
