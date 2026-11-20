import asyncio
import hmac
import logging
import os
from contextlib import contextmanager
from functools import cached_property

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .atv import Adapter
from .atv.discovery import describe
from .engine import Engine
from .mcp_http import MCP
from .pairing import Pairing
from .present.play_url import PlayUrlSender
from .present.sender import AutoSender
from .present.tvpipe import TvpipeSender
from .observer import ReceiverObserver
from .processes import supervisor_token
from .stage import MAX_UPLOAD, Stage
from .state import Fault, classify, success
from .tools import Tools
from .tools.public import PublicTools


async def discover(token):
    supervisor = supervisor_token()
    if not supervisor:
        return
    headers = {"Authorization": f"Bearer {supervisor}"}
    payload = {"service": "atv_bridge", "config": {"host": "127.0.0.1", "port": 8765, "token": token}}
    delay = 1
    async with ClientSession(timeout=ClientTimeout(total=10)) as http:
        while True:
            try:
                async with http.post(
                    "http://supervisor/discovery", headers=headers, json=payload
                ) as resp:
                    resp.raise_for_status()
                    return
            except (TimeoutError, ClientError) as exc:
                logging.getLogger(__name__).warning(
                    "Supervisor discovery failed (%s), retry in %ss", type(exc).__name__, delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)


class TVRuntime:
    def __init__(
        self,
        host,
        state,
        adapter=None,
        observer=None,
        presentation_mode="mirror",
        mirror_size="1920x1080",
        mirror_fps=10,
        apple_tv_id="",
        default_profile="",
        app_profile="",
        page_scripts=True,
    ):
        self.state = state
        self.subscribers = set()
        self.sockets = set()
        self.retired = False
        self.requests = 0
        self.idle = asyncio.Event()
        self.idle.set()
        adapter = adapter or Adapter(host, state, apple_tv_id)
        self.engine = Engine(adapter, self.publish)
        resolver = adapter.resolver
        self.stage = Stage(state.root)
        # tvpipe v0.2.0 accepts a maximum of 1080p. Preserve older 4K options.
        if mirror_size == "3840x2160":
            mirror_size = "1920x1080"
        mirror = TvpipeSender(
            host,
            state,
            ready=self.stage.ready,
            size=mirror_size,
            fps=mirror_fps,
            resolve_endpoint=resolver.airplay_endpoint,
        )
        play_url = PlayUrlSender(self.engine, self.stage)
        match presentation_mode:
            case "mirror":
                self.sender = mirror
            case "play_url":
                self.sender = play_url
            case "auto":
                self.sender = AutoSender(mirror, play_url)
            case _:
                raise ValueError(f"Unknown presentation_mode: {presentation_mode}")
        self.sender.events.subscribe(lambda status: self.publish({"presentation": status}))
        self.observer = observer or ReceiverObserver(resolver, state)
        self.operations = Tools(self.engine, self.stage, self.sender, self.observer, state)
        self.operations.live_mirror = mirror
        self.tools = PublicTools(self.operations, mirror_size, page_scripts)
        self.pairing = Pairing(self.engine, self.observer)
        self.tools.pairing = self.pairing
        self.tools.default_profile = default_profile
        self.tools.app_profile = app_profile

    @cached_property
    def mcp(self):
        return MCP(self.tools)

    @contextmanager
    def use(self):
        if self.retired:
            raise Fault("tv_not_found", "Receiver has been removed.", "Read tv://tvs.")
        self.requests += 1
        self.idle.clear()
        try:
            yield
        finally:
            self.requests -= 1
            if not self.requests:
                self.idle.set()

    def publish(self, change):
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(success("changed", **change))

    async def events(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        with self.use():
            await ws.prepare(request)
            self.sockets.add(ws)
        queue = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)

        async def write():
            while True:
                await ws.send_json(await queue.get())

        task = asyncio.create_task(write())
        try:
            async for _ in ws:
                pass
        finally:
            self.sockets.discard(ws)
            self.subscribers.remove(queue)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return ws

    async def close(self):
        await asyncio.gather(*(ws.close() for ws in list(self.sockets)))
        await self.pairing.stop()
        await self.tools.media.stop()
        await getattr(self.operations, "presenter", self.sender).stop()
        await self.engine.stop()
        await self.stage.hide()
        await self.observer.close()

    def app(self, fleet=None):
        mcp = fleet.mcp if fleet else self.mcp

        def receiver(request):
            if fleet is None or (not fleet.runtimes and "tv" not in request.match_info):
                return self
            return fleet.select(request.match_info.get("tv"))

        async def inventory(request):
            return web.json_response(success("listed", tvs=fleet.inventory()))

        async def enrol(request):
            body = await request.json()
            return web.json_response(success("registered", tv=await fleet.enrol(body["id"])))

        async def remove(request):
            await fleet.remove(request.match_info["tv"])
            return web.json_response(success("removed"))

        async def events(request):
            return await receiver(request).events(request)

        @web.middleware
        async def auth(request, handler):
            supplied = request.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(supplied, f"Bearer {self.state.token}".encode()):
                fault = Fault(
                    "unauthorized", "Bearer token required.", "Use the token from Supervisor discovery."
                )
                return web.json_response(fault.result(), status=401)
            try:
                return await handler(request)
            except web.HTTPException as exc:
                fault = Fault("http_error", exc.reason, "Check the request path, method and size.")
                return web.json_response(fault.result(), status=exc.status)
            except (TypeError, ValueError, KeyError):
                return web.json_response(
                    Fault(
                        "invalid_request", "Invalid request body.", "Check the request fields."
                    ).result(),
                    status=400,
                )
            except Exception as exc:  # noqa: BLE001 - Keep HTTP failures in the bridge result format.
                return web.json_response(classify(exc).result())

        async def status(request):
            # Reads by HA entities are not agent tool calls.
            rt = receiver(request)
            with rt.use():
                return web.json_response(await rt.operations.status())

        async def health(request):
            rt = receiver(request)
            with rt.use():
                return web.json_response(await rt.operations.tv_health() | {"pending_pairing": rt.pairing.prompt()})

        async def tool(request):
            try:
                args = await request.json()
            except ValueError:
                args = None
            if fleet and "tv" in request.match_info and isinstance(args, dict):
                args["tv"] = request.match_info["tv"]
            result = await (fleet or self.tools).call(request.match_info["name"], args)
            if "image" in result:
                result["data"]["image"] = result.pop("image")
            return web.json_response(result)

        async def apps(request):
            rt = receiver(request)
            with rt.use():
                return web.json_response(success("listed", apps=await rt.tools.apps()))

        async def discover_tvs(request):
            rt = (fleet.public_receiver or self) if fleet else self
            resolver = rt.engine.adapter.resolver
            return web.json_response(
                success(
                    "listed",
                    devices=[describe(c) for c in await resolver.scan()],
                    paired_tv=resolver.target or None,
                )
            )

        async def start(request):
            body = await request.json()
            selected = body.get("apple_tv_id")
            if selected is not None and (
                not isinstance(selected, str) or not selected.strip() or body["protocol"] != "airplay"
            ):
                raise ValueError("Invalid Apple TV selection")
            if fleet and selected and "tv" not in request.match_info:
                row = await fleet.enrol(selected)
                rt = fleet.select(row["id"])
            elif fleet and not fleet.runtimes:
                raise Fault(
                    "tv_required",
                    "Select a receiver to pair.",
                    "Discover and register the TV first.",
                )
            else:
                rt = receiver(request)
            if fleet and selected is not None:
                if not fleet.runtimes or selected.casefold() not in {
                    s.casefold() for s in fleet.info(rt)["identifiers"]
                }:
                    raise ValueError("Pairing selection must match the receiver path")
            replace = body.get("replace", False)
            if type(replace) is not bool:
                raise ValueError("Invalid replacement flag")
            with rt.use():
                return web.json_response(await rt.pairing.start(body["protocol"], selected, replace=replace))

        async def finish(request):
            body = await request.json()
            if fleet and "tv" not in request.match_info:
                rt = next(
                    (
                        r
                        for r in fleet.runtimes.values()
                        if r.pairing.id and r.pairing.id == body["pairing_id"]
                    ),
                    None,
                )
                if rt is None:
                    raise Fault(
                        "invalid_pairing_id", "Unknown pairing session.", "Start pairing again."
                    )
            else:
                rt = receiver(request)
            with rt.use():
                result = await rt.pairing.finish(body["pairing_id"], body["pin"])
            rt.publish({"pairing": result["state"]})
            return web.json_response(result)

        async def bundle(request):
            async with self.stage.lock:
                page_id = await self.stage.store.put(archive=await request.read())
            return web.json_response({"bundle": page_id})

        async def crash_tvpipe(request):
            mirror = receiver(request).operations.live_mirror
            proc = mirror.proc
            action = (await request.json()).get("action")
            if action == "inspect":
                return web.json_response(success(
                    "inspected",
                    pid=proc.pid if proc and proc.returncode is None else None,
                    started=mirror.started,
                    frames=mirror.frames,
                ))
            if action != "crash":
                raise web.HTTPBadRequest(reason="Unknown test action")
            if proc is None or proc.returncode is not None:
                raise web.HTTPConflict(reason="No running tvpipe")
            proc.kill()
            return web.json_response(success("signalled", pid=proc.pid))

        async def lifecycle(app):
            async with mcp.running():
                if fleet:
                    await fleet.start()
                else:
                    self.engine.start()
                announce = asyncio.create_task(discover(self.state.token))
                try:
                    yield
                finally:
                    announce.cancel()
                    await asyncio.gather(announce, return_exceptions=True)
                    await (fleet.close() if fleet else self.close())

        app = web.Application(middlewares=[auth], client_max_size=MAX_UPLOAD + 1)
        app.cleanup_ctx.append(lifecycle)
        if os.getenv("ATV_BRIDGE_LIFECYCLE_TEST") == "1":
            app.router.add_post("/api/tool/test_crash_tvpipe", crash_tvpipe)
        app.add_routes(
            [
                web.get("/api/discover", discover_tvs),
                web.get("/api/status", status),
                web.get("/api/apps", apps),
                web.get("/api/health", health),
                web.get("/api/events", events),
                web.post("/api/tool/{name}", tool),
                web.post("/api/pair/start", start),
                web.post("/api/pair/finish", finish),
                web.post("/api/bundle", bundle),
                web.post("/mcp", mcp.handle),
            ]
        )
        if fleet:
            app.add_routes([web.get("/api/tvs", inventory), web.post("/api/tvs", enrol),
                            web.delete("/api/tvs/{tv}", remove)])
            for suffix, handler in [
                ("status", status),
                ("apps", apps),
                ("health", health),
                ("events", events),
            ]:
                app.router.add_get("/api/tvs/{tv}/" + suffix, handler)
            for suffix, handler in [
                ("pair/start", start),
                ("pair/finish", finish),
                ("tool/{name}", tool),
            ]:
                app.router.add_post("/api/tvs/{tv}/" + suffix, handler)
        return app


# Embedded single-receiver hosts and the hardware spike share this runtime.
Service = TVRuntime
