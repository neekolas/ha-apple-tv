import asyncio
import base64
import json
import sys
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from atv_bridge_app.present.sender import STAGE_URL
from atv_bridge_app.present.tvpipe import TvpipeSender
from atv_bridge_app.state import Fault
from atv_bridge_app.tools.contract import SCHEMAS, INSTRUCTIONS


async def test_real_mcp_sdk_and_rest(service, aiohttp_client):
    client = await aiohttp_client(service.app())
    headers = {"Authorization": "Bearer " + service.state.token}
    assert (await client.get("/api/health")).status == 401
    async with (
        httpx.AsyncClient(headers=headers) as http,
        streamable_http_client(str(client.make_url("/mcp")), http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        assert initialized.instructions == INSTRUCTIONS
        assert {str(r.uri).rstrip("/") for r in (await session.list_resources()).resources} == {
            "tv://apps",
            "tv://screen",
            "tv://page-guide",
        }
        assert (await session.list_prompts()).prompts[0].name == "present"
        assert (
            "Hello"
            in (await session.get_prompt("present", {"topic": "Hello"})).messages[0].content.text
        )
        assert {t.name for t in (await session.list_tools()).tools} == set(SCHEMAS)
        screen = await session.call_tool("tv_look", {})
        assert not screen.isError and all(isinstance(c, types.TextContent) for c in screen.content)
        assert screen.structuredContent["screen"]["focus"]["label"] == "TV"
        assert screen.structuredContent["data"]["items"][0]["label"] == "TV"
        capture = await session.call_tool("tv_look", {"image": True, "max_width": 320})
        image = next(x for x in capture.content if isinstance(x, types.ImageContent))
        assert base64.b64decode(image.data).startswith(b"\x89PNG")
        assert capture.structuredContent["data"]["toast_visible"] is True
        assert not capture.isError
        assert (
            json.loads((await session.read_resource("tv://screen")).contents[0].text)["data"]["items"][
                0
            ]["label"]
            == "TV"
        )
        assert (
            json.loads((await session.read_resource("tv://apps")).contents[0].text)[0]["name"]
            == "YouTube"
        )
        assert "/tv.js" in (await session.read_resource("tv://page-guide")).contents[0].text
        alias = await session.call_tool("tv_hide", {})
        assert not alias.isError and alias.structuredContent["screen"]["presenting"] is None
        bad = await session.call_tool("tv_open", {})
        assert bad.isError and bad.structuredContent["error"]["code"] == "invalid_arguments"
    result = await client.post("/api/tool/tv_look", json={"image": True}, headers=headers)
    assert (await result.json())["data"]["image"]["data"] == image.data
    assert len((service.state.root / "audit.jsonl").read_text().splitlines()) == 5


async def test_observer_reuses_process(service):
    try:
        first = await service.observer.call("health")
        next_request = await service.observer.call("screenshot", max_width=100)
        assert first["pid"] == next_request["pid"]
    finally:
        await service.observer.close()


def executable(path, code):
    path.write_text("#!" + sys.executable + "\n" + code)
    path.chmod(0o700)
    return str(path)


@pytest.mark.parametrize(
    "options,size,fps,bitrate",
    [
        ({}, "1920x1080", "10", "4000"),
        ({"size": "1280x720", "fps": 24}, "1280x720", "24", "2500"),
    ],
)
async def test_sender_receiver_end_restarts_and_hide_reaps(
    service, tmp_path, options, size, fps, bitrate
):
    script = executable(
        tmp_path / "fake-tvpipe",
        """import json, os, sys, time
from pathlib import Path
root = Path(os.environ['TVPIPE_CONFIG_DIR'])
count = root / 'launches'
with count.open('a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
print('{"type":"started"}', flush=True)
if len(count.read_text().splitlines()) == 1:
    time.sleep(0.05)
    print('{"type":"receiver","state":"ended"}', flush=True)
framing = sys.stdin.buffer.read()
assert framing.startswith(b'SLRW') and framing.endswith(b'{"type":"stop"}')
with (root / "stops").open("a") as f:
    f.write("stop\\n")
""",
    )
    service.state.save_pairings(airplay={"ltpk": "ab" * 32, "atv_id": b"AA:BB:CC:DD:EE:FF".hex()})
    resolve = AsyncMock(side_effect=[("192.0.2.1", 7012), ("192.0.2.2", 8123)])
    sender = TvpipeSender("", service.state, script, restart_delay=0.01, resolve_endpoint=resolve, **options)
    try:
        await sender.start(STAGE_URL)
        count = tmp_path / "tvpipe" / "launches"
        async with asyncio.timeout(3):
            while len(count.read_text().splitlines()) < 2:
                await asyncio.sleep(0.03)
        second = json.loads(count.read_text().splitlines()[1])
        assert second[second.index("--to") + 1] == "192.0.2.2:8123"
        assert "192.0.2.2:8123" in (count.parent / "credentials.txt").read_text()
        assert resolve.await_args.args == ("192.0.2.1",)
        assert json.loads(count.read_text().splitlines()[0]) == [
            "--machine",
            "share",
            "http://127.0.0.1:8766/stage",
            "--to",
            "192.0.2.1:7012",
            "--size",
            size,
            "--fps",
            fps,
            "--bitrate",
            bitrate,
            "--profile",
            sender.profile.name,
        ]
    finally:
        await sender.stop()
    assert (tmp_path / "tvpipe" / "stops").read_text().splitlines() == ["stop", "stop"]


async def test_sender_pin_stops_restart(service, tmp_path):
    script = executable(tmp_path / "pin", """print('{"type":"pin-required"}',flush=True)""")
    sender = TvpipeSender("192.0.2.1", service.state, script)
    try:
        with pytest.raises(Fault) as error:
            await sender.start(STAGE_URL)
        assert error.value.code == "pairing_required"
        await sender.runner
    finally:
        await sender.stop()


async def test_event_notification(service, aiohttp_client):
    client = await aiohttp_client(service.app())
    headers = {"Authorization": "Bearer " + service.state.token}
    async with client.ws_connect("/api/events", headers=headers) as ws:
        service.publish({"power": "off"})
        change = await ws.receive_json(timeout=1)
        assert change["state"] == "changed" and change["power"] == "off"


async def test_sender_show_reuses_session(service, aiohttp_client, tmp_path):
    script = executable(
        tmp_path / "show-sender",
        """import json, os, sys
from pathlib import Path
with (Path(os.environ['TVPIPE_CONFIG_DIR']) / 'launches').open('a') as f:
    f.write("start\\n")
print('{"type":"started"}', flush=True)
import threading,time
def stats():
    frames=0
    while True:
        frames+=1
        print(json.dumps({"type":"stats","receivers":[{"pushed":frames}]}),flush=True)
        time.sleep(.02)
threading.Thread(target=stats,daemon=True).start()
sys.stdin.buffer.read()
""",
    )
    client = await aiohttp_client(service.stage.app())
    service.sender.executable = script
    async with client.ws_connect("/stage/ws", origin="http://127.0.0.1:8766") as socket:
        try:
            for html in ("<h1>One</h1>", "<h1>Two</h1>"):
                show = asyncio.create_task(service.tools.call("tv_show", {"html": html}))
                page = await socket.receive_json(timeout=2)
                await socket.send_json({"type": "ready", "page_id": page["page_id"]})
                result = await asyncio.wait_for(show, 2)
                assert result["ok"] and result["data"]["page_id"] == page["page_id"]
            assert (tmp_path / "tvpipe" / "launches").read_text().splitlines() == ["start"]
        finally:
            # Simulate the Chromium tab closing during sender teardown.
            await socket.close()
            assert (await service.tools.call("tv_hide", {}))["ok"]


def test_ocr_and_implicit_children_cannot_inherit_supervisor_token(monkeypatch):
    import io
    import os
    from unittest.mock import Mock
    from PIL import Image
    from atv_bridge_app import processes
    from atv_bridge_app.tools import deep_link

    monkeypatch.setenv("SUPERVISOR_TOKEN", "sentinel")
    monkeypatch.setattr(processes, "_supervisor_token", None)
    run = Mock(return_value=Mock(stdout=b"Open in Hulu Open Cancel"))
    monkeypatch.setattr(deep_link.subprocess, "run", run)
    frame = Image.new("L", (1280, 720))
    frame.paste(255, (475, 372, 622, 408))
    data = io.BytesIO()
    frame.save(data, format="PNG")
    assert deep_link.confirmed_open_image(data.getvalue(), ["Hulu"])
    assert "SUPERVISOR_TOKEN" not in run.call_args.kwargs["env"]
    processes.isolate_supervisor_token()
    assert "SUPERVISOR_TOKEN" not in os.environ
    assert processes.supervisor_token() == "sentinel"
