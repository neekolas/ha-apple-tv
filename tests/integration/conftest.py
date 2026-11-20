"""Exercise the integration over loopback with the real HA core and aiohttp."""

import asyncio
from dataclasses import dataclass, field

import pytest
from aiohttp import web
from pytest_homeassistant.common import MockConfigEntry

from custom_components.atv_bridge.const import DOMAIN, NAME


def envelope(state="ready", **values):
    return {"ok": True, "state": state, "evidence": [], **values}


@dataclass
class App:
    config: dict = field(default_factory=dict)
    status: dict = field(
        default_factory=lambda: envelope(
            power="on",
            app={"name": "TV", "bundle_id": "com.apple.TV"},
            now_playing={"state": "playing", "title": "News", "artist": "ABC"},
            keyboard_focused=False,
            connected=True,
            tvos="27.2",
            volume_supported=True,
        )
    )
    health: dict = field(default_factory=envelope)
    tvs: list | None = None
    calls: list = field(default_factory=list)
    proxy_calls: list = field(default_factory=list)
    replies: dict = field(default_factory=dict)
    sockets: asyncio.Queue = field(default_factory=asyncio.Queue)
    release_stream: asyncio.Event = field(default_factory=asyncio.Event)
    received_upload: asyncio.Event = field(default_factory=asyncio.Event)

    async def handle(self, request):
        if request.headers.get("Authorization") != "Bearer app-token":
            raise web.HTTPUnauthorized()
        path = request.path
        tv = None
        recorded_path = path
        if path.startswith("/api/tvs/") and request.method == "DELETE":
            self.calls.append((path, "DELETE"))
            self.tvs = [tv for tv in self.tvs if tv["id"] != path.rsplit("/", 1)[1]]
            return web.json_response(envelope("removed"))
        if path.startswith("/api/tvs/"):
            _, _, _, tv, operation = path.split("/", 4)
            assert any(r["id"] == tv for r in self.tvs)
            path = "/api/" + operation
        if path == "/api/events":
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            self.sockets.put_nowait(socket)
            async for _ in socket:
                pass
            return socket
        if path in ("/mcp", "/api/bundle"):
            self.proxy_calls.append((path, request.method, dict(request.headers)))
            if request.method == "GET":
                stream = web.StreamResponse(
                    headers={
                        "Content-Type": "text/event-stream",
                        "Mcp-Session-Id": "session",
                    }
                )
                await stream.prepare(request)
                await stream.write(b"data: first\n\n")
                await self.release_stream.wait()
                await stream.write(b"data: second\n\n")
                return stream
            if request.method == "DELETE":
                return web.Response(status=204)
            if path == "/api/bundle":
                first = await request.content.readexactly(4)
                self.received_upload.set()
                assert first + await request.content.read() == b"PK\x03\x04trailer"
                return web.json_response({"bundle":"bundle"})
            return web.Response(
                body=await request.read(),
                content_type="application/json",
                headers={"Mcp-Session-Id": "session"},
            )
        body = await request.json() if request.method == "POST" else None
        self.calls.append((recorded_path, body))
        if path == "/api/tvs" and self.tvs is not None:
            if body is None:
                result = envelope(tvs=self.tvs)
            else:
                row = next(
                    r for r in self.replies["/api/discover"] if body["id"] in r["identifiers"]
                )
                new = {
                    "name": row["name"],
                    "id": body["id"],
                    "identifiers": row["identifiers"],
                    "host": row["address"],
                    "paired": False,
                    "legacy": False,
                }
                self.tvs.append(new)
                result = envelope(tv=new)
        elif path == "/api/status":
            result = self.status
        elif path == "/api/discover":
            result = envelope(devices=self.replies[path])
        elif path == "/api/health":
            result = self.replies.get(recorded_path, self.health)
        elif path == "/api/pair/start":
            result = envelope(pairing_id=body["protocol"] + "-id")
        elif path == "/api/pair/finish":
            result = envelope(ok=body["pin"] == "0123")
            if tv and result["ok"] and body["pairing_id"] == "airplay-id":
                next(r for r in self.tvs if r["id"]==tv)["paired"]=True
        elif path == "/api/apps":
            result = envelope(
                apps=[
                    {"name": "TV", "bundle_id": "com.apple.TV"},
                    {"name": "Music", "bundle_id": "music"},
                ]
            )
        elif path.startswith("/api/tool/"):
            result = self.replies.get(path, envelope(tool=path.rsplit("/", 1)[-1]))
        else:
            raise web.HTTPNotFound()
        return web.json_response(result)


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
async def bridge(aiohttp_server, socket_enabled):
    app = App()
    server = web.Application()
    server.router.add_route("*", "/{path:.*}", app.handle)
    listener = await aiohttp_server(server)
    app.config = {"host": listener.host, "port": listener.port, "token": "app-token"}
    yield app
    app.release_stream.set()


@pytest.fixture
async def entry(hass, bridge):
    result = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title=NAME, data=bridge.config
    )
    result.add_to_hass(hass)
    assert await hass.config_entries.async_setup(result.entry_id)
    await hass.async_block_till_done()
    yield result
    if hass.config_entries.async_loaded_entries(DOMAIN):
        await hass.config_entries.async_unload(result.entry_id)
