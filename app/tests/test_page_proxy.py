import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

from aiohttp import ClientSession, web
import pytest

from atv_bridge_app import page_proxy
from atv_bridge_app.page_network import PageNetworkPolicy
from atv_bridge_app.page_proxy import PageProxy


@asynccontextmanager
async def serving(policy, **options):
    proxy = PageProxy(policy, **options)
    await proxy.start()
    try:
        yield proxy
    finally:
        await proxy.close()


async def request(proxy, data):
    reader, writer = await asyncio.open_connection("127.0.0.1", urlsplit(proxy.url).port)
    writer.write(data)
    await writer.drain()
    return reader, writer


async def test_http_framing_and_pinned_virtual_host(aiohttp_server):
    got = {}

    async def receive(req):
        got.update(body=await req.read(), host=req.host, path=req.raw_path,
                   proxy_auth=req.headers.get("Proxy-Authorization"))
        return web.Response(text="forwarded")

    app = web.Application()
    app.router.add_post("/submit", receive)
    origin = await aiohttp_server(app)
    policy = PageNetworkPolicy()
    policy.resolve = AsyncMock(return_value=["127.0.0.1"])
    async with serving(policy) as proxy, ClientSession() as session:
        async def chunks():
            yield b"hello "
            yield b"from Chromium"
        url = f"http://virtual.example:{origin.port}/submit?q=a%2Bb"
        async with session.post(url, proxy=proxy.url, data=chunks(),
                                headers={"Proxy-Authorization": "test"}) as response:
            assert await response.text() == "forwarded"
    policy.resolve.assert_awaited_once_with("virtual.example", origin.port, stage_allowed=True)
    assert got == {"body": b"hello from Chromium", "host": f"virtual.example:{origin.port}",
                   "path": "/submit?q=a%2Bb", "proxy_auth": None}


async def test_connect_copies_both_directions_and_buffered_bytes():
    async def echo(reader, writer):
        writer.write(await reader.readexactly(5))
        await writer.drain()
        writer.close()

    origin = await asyncio.start_server(echo, "127.0.0.1", 0)
    policy = PageNetworkPolicy()
    policy.resolve = AsyncMock(return_value=["127.0.0.1"])
    port = origin.sockets[0].getsockname()[1]
    async with origin, serving(policy) as proxy:
        reader, writer = await request(proxy, (
            f"CONNECT virtual.example:{port} HTTP/1.1\r\nHost: virtual.example:{port}\r\n\r\nhello"
        ).encode())
        assert b" 200 " in await reader.readuntil(b"\r\n\r\n")
        assert await reader.readexactly(5) == b"hello"
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("method", ["GET", "CONNECT"])
async def test_blocked_requests_close_without_opening_socket(monkeypatch, method):
    dial = AsyncMock(side_effect=AssertionError("Blocked destination must not be dialed"))
    monkeypatch.setattr(page_proxy, "_connect", dial)
    async with serving(PageNetworkPolicy()) as proxy:
        host = "127.0.0.1:8765"
        target = host if method == "CONNECT" else f"http://{host}/api/health"
        reader, writer = await request(proxy, f"{method} {target} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert proxy.denial_for(f"http://{host}/another-path").kind == "loopback"
        assert proxy.denial_for("http://127.0.0.1:8766") is None
        writer.close()
        await writer.wait_closed()
    dial.assert_not_called()


async def test_live_sse_and_websocket_on_stage(aiohttp_server):
    release = asyncio.Event()

    async def events(req):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(req)
        await response.write(b"data: started\n\n")
        await release.wait()
        return response

    async def websocket(req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        await ws.send_str(await ws.receive_str())
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/events", events)
    app.router.add_get("/stage/ws", websocket)
    origin = await aiohttp_server(app)
    async with serving(PageNetworkPolicy(stage_ports={origin.port})) as proxy, ClientSession() as session:
        try:
            async with session.get(origin.make_url("/events"), proxy=proxy.url) as response:
                assert await asyncio.wait_for(response.content.readuntil(b"\n\n"), 1) == b"data: started\n\n"
        finally:
            release.set()
        async with session.ws_connect(origin.make_url("/stage/ws"), proxy=proxy.url) as ws:
            await ws.send_str("ready")
            assert await ws.receive_str(timeout=1) == "ready"


async def test_revoked_stage_port_sends_no_bytes(monkeypatch):
    received = asyncio.get_running_loop().create_future()

    async def listener(reader, writer):
        received.set_result(await reader.read())
        writer.close()

    origin = await asyncio.start_server(listener, "127.0.0.1", 0)
    port = origin.sockets[0].getsockname()[1]
    policy = PageNetworkPolicy(stage_ports={port})
    dial = page_proxy._connect

    async def revoke_while_connecting(addresses, target_port):
        streams = await dial(addresses, target_port)
        policy.stage_ports.remove(port)
        return streams

    monkeypatch.setattr(page_proxy, "_connect", revoke_while_connecting)
    async with origin, serving(policy) as proxy:
        host = f"127.0.0.1:{port}"
        reader, writer = await request(proxy, f"GET http://{host}/stage HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert await asyncio.wait_for(received, 1) == b""
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize(("path", "permitted"), [("/stage", False), ("/ws?t=" + "a" * 32, True)])
async def test_capture_connect_only_forwards_capture_handshake(path, permitted):
    received = asyncio.get_running_loop().create_future()

    async def listener(reader, writer):
        line = await reader.readline()
        received.set_result(line)
        writer.close()

    origin = await asyncio.start_server(listener, "127.0.0.1", 0)
    port = origin.sockets[0].getsockname()[1]

    async def verified_capture(host, candidate):
        assert (host, candidate) == ("127.0.0.1", port)
        return await asyncio.open_connection(host, candidate)

    async with origin, serving(PageNetworkPolicy(), open_capture=verified_capture) as proxy:
        host = f"127.0.0.1:{port}"
        reader, writer = await request(proxy, f"CONNECT {host} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        assert b" 200 " in await reader.readuntil(b"\r\n\r\n")
        writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n".encode())
        await writer.drain()
        assert await asyncio.wait_for(received, 1) == (f"GET {path} HTTP/1.1\r\n".encode() if permitted else b"")
        writer.close()
        await writer.wait_closed()
