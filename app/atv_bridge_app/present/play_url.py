import asyncio
import socket
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from ..state import Fault, classify
from . import browsers
from .render import StageRenderer
from .sender import Sender


def media_url(host, stage_url, filename):
    port = urlsplit(stage_url).port or 80
    # UDP connect selects the interface for this receiver without sending a datagram.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 7000))
        address = sock.getsockname()[0]
    return f"http://{address}:{port}/m/{filename}"


class PlayUrlSender(Sender):
    def __init__(self, engine, stage, renderer=None):
        super().__init__("play_url")
        self.engine = engine
        self.stage = stage
        self.renderer = renderer or StageRenderer(stage)
        self.job = None
        self.cache = stage.media.parent / "render-cache"
        self.cache.mkdir(exist_ok=True)

    async def _cancel_job(self):
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)
            self.job = None

    async def start(self, stage_url):
        await self._cancel_job()
        owner = getattr(self.renderer, "proc", None)
        await browsers.sweep(owner.pid if owner and owner.returncode is None else None)
        if not self.engine.adapter.supports_play_url:
            raise Fault(
                "fork_required",
                "MP4 presentation requires the pyatv fork.",
                "Stage vendor/pyatv before building the app.",
            )
        if not self.engine.adapter.state.pairings.get("airplay"):
            raise Fault("pairing_required", "AirPlay is unpaired.", "Pair AirPlay in Home Assistant.")
        self.transition("rendering", ["Stage queued for recording"])
        self.job = asyncio.create_task(self._present(stage_url))

    async def stop(self):
        await self._cancel_job()
        await self.renderer.close()
        await browsers.sweep()
        self.transition("idle")

    async def _present(self, stage_url):
        path = self.stage.media / f"stage-{uuid.uuid4().hex}.mp4"
        try:
            await self.renderer.open(stage_url)
            self.transition("rendering", ["Stage iframe loaded"])
            with tempfile.TemporaryDirectory(dir=self.cache) as directory:
                mp4, duration = await self.renderer.render(Path(directory))
                mp4.replace(path)
            await self._play(stage_url, path.name, duration)
        except Exception as exc:  # noqa: BLE001 - Asynchronous presentation failures are public status events.
            problem = (
                exc
                if isinstance(exc, Fault)
                else Fault(
                    "presentation_failed",
                    f"MP4 presentation failed ({type(exc).__name__}).",
                    "Check the stage media, AirPlay pairing and network.",
                    True,
                )
            )
            self.fail(problem)
            await self.renderer.close()
        finally:
            path.unlink(missing_ok=True)

    async def _play(self, stage_url, filename, duration):
        loop = asyncio.get_running_loop()
        position = 0
        failures = 0
        fresh = False
        while True:
            # A fresh URL prevents the receiver from serving a reopen from its
            # previous cache and distinguishes late reads by the previous session.
            session_id = uuid.uuid4().hex
            fetch_key = None
            self.transition("starting", ["MP4 encoded"], position=int(position))
            playback = fetched = None
            started = None
            try:
                async with self.engine.session(fresh=fresh) as client:
                    host = self.engine.adapter.host
                    fetch_key = (filename, host, session_id)
                    read = self.stage.media_reads[fetch_key] = asyncio.Event()
                    relative = (
                        f"{self.stage.media_prefix}/{filename}"
                        if self.stage.media_prefix
                        else filename
                    )
                    url = media_url(host, self.stage.media_origin or stage_url, relative)
                    session_url = f"{url}?session={session_id}"
                    # play_url owns a fresh AirPlay transport per invocation. Do not
                    # hold the Companion control lock for the duration of the clip.
                    playback = asyncio.create_task(client.play_url(session_url, int(position)))
                fetched = asyncio.create_task(read.wait())
                await asyncio.wait((playback, fetched), timeout=25, return_when=asyncio.FIRST_COMPLETED)
                if read.is_set():
                    started = loop.time()
                    self.transition("showing", ["Receiver requested the MP4"], position=int(position))
                if started is None and not playback.done():
                    raise Fault(
                        "playback_timeout",
                        "The receiver did not fetch the MP4.",
                        "Check access to the bridge on LAN port 8766.",
                        True,
                    )
                interrupted = await playback
                if started is None:
                    raise Fault(
                        "playback_unconfirmed",
                        "AirPlay closed before fetching the MP4.",
                        "Check access to the bridge on LAN port 8766.",
                        True,
                    )
                fresh = bool(interrupted)
            except asyncio.CancelledError:
                fresh = True
                if asyncio.current_task().cancelling():
                    raise
                # The control engine rebuilt its facade and cancelled its stream.
                if started is None and failures >= 2:
                    raise Fault(
                        "playback_interrupted",
                        "AirPlay repeatedly closed before playback.",
                        "Check receiver connectivity and try again.",
                        True,
                    ) from None
            except Exception as exc:
                fresh = True
                fault = classify(exc)
                if not fault.retryable or (started is None and failures >= 2):
                    if fault is exc:
                        raise
                    raise fault from exc
            finally:
                self.stage.media_reads.pop(fetch_key, None)
                tasks = [task for task in (playback, fetched) if task]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if started is not None:
                position = (position + loop.time() - started) % duration
                if duration - position < 1:
                    position = 0
                failures = 0
            else:
                failures += 1
            self.transition("reconnecting", ["AirPlay playback session closed"], position=int(position))
            await asyncio.sleep(min(15, 2**failures))
