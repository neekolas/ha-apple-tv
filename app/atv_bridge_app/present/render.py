import asyncio
import base64
import json
import logging
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

from ..page_proxy import PageProxy
from ..processes import child_env, reap
from ..state import Fault
from . import browsers

CAPTURE = Path(__file__).with_name("capture.js").read_text()
PAGE_INFO = """() => {
  const media = [...document.querySelectorAll("audio, video")];
  const active = media.filter(el => !el.paused || (el.autoplay && el.ended));
  const finite = active.length && active.every(el => !el.loop && Number.isFinite(el.duration));
  const dynamic = document.querySelector("script, video, canvas, iframe, svg animate, svg animateTransform, picture, img[src$='.gif' i], img[src$='.webp' i]") ||
    document.getAnimations().length || [...document.images].some(img => !img.complete);
  if (!dynamic && finite) {
    for (const el of active) { el.pause(); el.currentTime = 0; }
  }
  return {still: !dynamic, audio: active.length > 0, restartAudio: !dynamic && finite,
          duration: finite ? Math.max(...active.map(el => el.duration - el.currentTime)) : null};
}"""


class StageRenderer:
    def __init__(self, stage, default_duration=60):
        self.stage = stage
        self.default_duration = default_duration
        self.proxy = None
        self.driver = self.browser = self.page = self.proc = self.profile = None

    async def open(self, stage_url):
        if self.page is None or self.page.is_closed():
            await self.close()
            self.profile = tempfile.TemporaryDirectory(prefix="atv-render-")
            self.proxy = PageProxy(self.stage.page_network)
            proxy_url = await self.proxy.start()
            self.proc = await browsers.spawn(
                browsers.CHROMIUM_WRAPPER,
                "--headless",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--auto-accept-this-tab-capture",
                "--autoplay-policy=no-user-gesture-required",
                "--screen-info={0,0 1920x1080}",
                "--disable-background-timer-throttling",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self.profile.name}",
                "about:blank",
                env=child_env(ATV_PAGE_PROXY=proxy_url),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            port_file = Path(self.profile.name) / "DevToolsActivePort"
            async with asyncio.timeout(12):
                while not port_file.is_file():
                    if self.proc.returncode is not None:
                        raise RuntimeError("Chromium exited before opening its debugging port")
                    await asyncio.sleep(0.05)
                port = int(port_file.read_text().splitlines()[0])
                self.driver = await async_playwright().start()
                self.browser = await self.driver.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}"
                )
            self.page = await self.browser.new_page(viewport={"width": 1920, "height": 1080})
            await self.page.goto(stage_url, wait_until="domcontentloaded")
        await asyncio.wait_for(self.stage.ready.wait(), 15)
        await self.page.locator("iframe.visible").last.wait_for(state="visible")
        # Let the stage's 220 ms crossfade finish before capturing.
        await asyncio.sleep(0.24)

    async def close(self):
        try:
            if self.driver:
                async with asyncio.timeout(3):
                    await self.driver.stop()
        finally:
            try:
                if self.proc:
                    await reap(self.proc, group=True, sweep=browsers.sweep)
            finally:
                if self.proxy:
                    await self.proxy.close()
                    self.proxy = None
                self.page = self.browser = self.driver = self.proc = None
                if self.profile:
                    self.profile.cleanup()
                    self.profile = None
                await browsers.sweep()

    async def _record(self, path, duration, still, restart_frame=None):
        await self.page.bring_to_front()
        cdp = await self.page.context.new_cdp_session(self.page)
        options = json.dumps({"duration": duration, "still": still})
        try:
            reply = await asyncio.wait_for(
                cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": f"({CAPTURE})({options})",
                        "awaitPromise": True,
                        "userGesture": True,
                    },
                ),
                12,
            )
            if "exceptionDetails" in reply:
                detail = (
                    reply["exceptionDetails"]
                    .get("exception", {})
                    .get("description", "unknown error")
                )
                raise Fault(
                    "capture_failed",
                    f"Chromium tab capture failed: {detail.splitlines()[0]}.",
                    "Check Chromium tab capture and the stage audio.",
                )
            if restart_frame:
                await restart_frame.evaluate(
                    "Promise.all([...document.querySelectorAll('audio')].filter(el => el.autoplay || el.played.length).map(el => el.play()))"
                )
            with path.open("xb") as output:
                async with asyncio.timeout(duration + 15):
                    while chunk := await self.page.evaluate("window.stageRecorder.next()"):
                        output.write(base64.b64decode(chunk, validate=True))
        finally:
            try:
                await self.page.evaluate("window.stageRecorder?.stop()")
            finally:
                await cdp.detach()

    async def render(self, work):
        frame_handle = await self.page.locator("iframe.visible").last.element_handle()
        frame = await frame_handle.content_frame()
        info = await frame.evaluate(PAGE_INFO)
        duration = max(0.25, info["duration"] or self.default_duration)
        args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
        if info["still"]:
            shot = work / "still.png"
            await self.page.screenshot(path=shot)
            args += ["-loop", "1", "-framerate", "2", "-i", str(shot)]
        if info["audio"] or not info["still"]:
            recording = work / "tab.webm"
            await self._record(
                recording, duration, info["still"], frame if info["restartAudio"] else None
            )
            args += ["-i", str(recording)]
        else:
            args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        output = work / "stage.mp4"
        args += [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0" if info["still"] else "0:a:0",
            "-t",
            str(duration),
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            *(["-af", "volume=0"] if (self.stage.page or {}).get("audio") == "mute" else []),
            "-movflags",
            "+faststart",
            str(output),
        ]
        proc = await asyncio.create_subprocess_exec(
            *args, stderr=asyncio.subprocess.PIPE, env=child_env()
        )
        try:
            _, stderr = await proc.communicate()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if proc.returncode != 0:
            logging.getLogger(__name__).warning(
                "FFmpeg encoding failed: %s", stderr[-2048:].decode(errors="replace")
            )
            raise Fault(
                "render_failed",
                "FFmpeg could not encode the stage.",
                "Check page media and app logs.",
            )
        return output, duration
