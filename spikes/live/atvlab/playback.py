import asyncio
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
from pyatv import exceptions
from pyatv.const import Protocol
from pyatv.support.http import decode_bplist_from_body

from .common import attempt, error, log


def clock_movie(path, seconds=300):
    # This Mac's ffmpeg lacks drawtext. One clock tile per second also works on that build.
    with tempfile.TemporaryDirectory(dir=path.parent) as work:
        work = Path(work)
        font = ImageFont.load_default(size=100)
        for second in range(seconds):
            image = Image.new("RGB", (700, 180), "#112433")
            ImageDraw.Draw(image).text(
                (350, 90), f"{second // 60:02}:{second % 60:02}", anchor="mm", fill="white", font=font
            )
            image.save(work / f"{second:03}.png")
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                "1",
                "-i",
                str(work / "%03d.png"),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-vf",
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:0x112433,fps=24",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-t",
                str(seconds),
                "-movflags",
                "+faststart",
                str(work / "output.mp4"),
            ],
            check=True,
        )
        (work / "output.mp4").replace(path)


def route_ip(target):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((target, 7000))
        return sock.getsockname()[0]


@asynccontextmanager
async def serve_file(host, path):
    async def serve(request):
        log.info("media %s %s Range=%s", request.remote, request.method, request.headers.get("Range"))
        return web.FileResponse(path)

    app = web.Application()
    app.router.add_get("/" + path.name, serve)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host, 0)
        await site.start()
        yield f"http://{host}:{runner.addresses[0][1]}/{path.name}"
    finally:
        await runner.cleanup()


async def playback_trial(client, url, cutoff=290, timeout=350, interval=2):
    stream = client.facade.stream.get(Protocol.AirPlay)
    factory = stream.create_airplay_protocol
    start = time.monotonic()
    protocol = None
    legacy_state = None
    playing_at = ended_at = None
    samples, fcup = [], []

    def elapsed():
        return round(time.monotonic() - start, 3)

    def instrument(*args):
        nonlocal protocol
        protocol = factory(*args)
        if protocol.supports_playback_state:
            original = protocol._handle_event
            fetch = protocol.url_fetcher

            async def fetch_media(request_url, headers):
                if request_url != url:
                    raise exceptions.ProtocolError("FCUP URL differs from this trial's media")
                return await fetch(request_url, headers)

            def event(message):
                nonlocal playing_at, ended_at
                if message.get("type") == "unhandledURL":
                    request = message.get("request") or {}
                    fcup.append(
                        {
                            "t_s": elapsed(),
                            "message_id": message.get("messageID"),
                            "request_id": request.get("FCUP_Response_RequestID"),
                            "url": request.get("FCUP_Response_URL"),
                        }
                    )
                original(message)
                if message.get("type") == "playbackState":
                    if protocol.playback_state == "playing" and playing_at is None:
                        playing_at = elapsed()
                    if protocol.playback_state in ("stopped", "idle") and playing_at is not None:
                        ended_at = elapsed()

            protocol._handle_event = event
            protocol.url_fetcher = fetch_media
        else:
            # AirPlay 1 exposes state in /playback-info on the active playback connection.
            get = protocol.rtsp.connection.get

            async def playback_info(path, **kwargs):
                nonlocal legacy_state, playing_at, ended_at
                reply = await get(path, **kwargs)
                if path == "/playback-info":
                    info = decode_bplist_from_body(reply) if reply.body else {}
                    legacy_state = "playing" if "duration" in info else "idle"
                    if legacy_state == "playing" and playing_at is None:
                        playing_at = elapsed()
                    if legacy_state == "idle" and playing_at is not None:
                        ended_at = elapsed()
                return reply

            protocol.rtsp.connection.get = playback_info
        return protocol

    failure = {}
    with patch.object(stream, "create_airplay_protocol", instrument):
        task = asyncio.create_task(stream.play_url(url))
        try:
            async with asyncio.timeout(timeout):
                while not task.done():
                    await asyncio.wait([task], timeout=interval)
                    if protocol and protocol.supports_playback_state:
                        state = protocol.playback_state
                    else:
                        state = legacy_state
                    samples.append({"t_s": elapsed(), "state": state})
                    if state == "playing" and playing_at is None:
                        playing_at = elapsed()
                await task
        except Exception as exc:  # noqa: BLE001 - Preserve transport failures alongside trial evidence.
            failure = error(exc)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    duration = None if playing_at is None else round((ended_at or elapsed()) - playing_at, 3)
    cut = duration is not None and duration < cutoff
    return {
        "ok": not failure and duration is not None and not cut,
        **failure,
        "started_s": playing_at,
        "duration_s": duration,
        "early_cut": cut,
        "cut_time_s": duration if cut else None,
        "samples": samples,
        "fcup_events": fcup,
    }


async def playback(lab, repeat):
    path = lab.state.root / "media" / "clock-300.mp4"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        await asyncio.to_thread(clock_movie, path)
    async with serve_file(route_ip(lab.host), path) as url:
        rows = []
        for _ in range(repeat):

            async def trial():
                async with lab.engine.session() as client:
                    return await playback_trial(client, url)

            rows.append(await attempt(trial))
    return {"ok": all(row["ok"] for row in rows), "url": url, "media": str(path), "trials": rows}
