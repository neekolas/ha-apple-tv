import asyncio
import io
import stat
import zipfile

import pytest

from atv_bridge_app.stage import ContentStore, Stage
from atv_bridge_app.state import Fault


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, content in entries:
            z.writestr(name, content)
    return out.getvalue()


@pytest.mark.parametrize(
    "bad", ["../escaped.html", "/escaped.html", "a/../../escape", "C:/evil", "a\\b"]
)
async def test_zip_rejects_paths(tmp_path, bad):
    store = ContentStore(tmp_path / "content")
    with pytest.raises(Fault):
        await store.put(archive=archive([("index.html", "ok"), (bad, "bad")]))
    assert list(store.root.iterdir()) == []


async def test_zip_rejects_symlink_and_missing_index(tmp_path):
    store = ContentStore(tmp_path / "content")
    link = zipfile.ZipInfo("link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for entries in [[(link, "../outside"), ("index.html", "ok")], [("readme.txt", "none")]]:
        with pytest.raises(Fault):
            await store.put(archive=archive(entries))


async def test_lru_and_media_ranges(tmp_path, aiohttp_client):
    stage = Stage(tmp_path)
    stage.store.capacity = 6
    pinned = await stage.store.put(html="abc")
    stage.store.active = pinned
    old = await stage.store.put(html="aa")
    latest = await stage.store.put(archive=archive([("index.html", "xyz")]))
    assert not (stage.store.root / old).exists()
    client = await aiohttp_client(stage.app())
    resp = await client.get(f"/c/{latest}/index.html")
    assert (await resp.text()).endswith("xyz") and "/tv.js" in await resp.text()
    (stage.media / "test.bin").write_bytes(b"123456")
    resp = await client.get("/m/test.bin", headers={"Range": "bytes=1-3"})
    assert resp.status == 206 and await resp.read() == b"234"


async def test_stage_ready_send_and_reconnect(tmp_path, aiohttp_client):
    stage = Stage(tmp_path)
    client = await aiohttp_client(stage.app())
    assert (await client.get("/stage/ws", headers={"Origin": "null"})).status == 403
    await stage.show("example", "https://example.org")
    async with client.ws_connect("/stage/ws", origin="http://127.0.0.1:8766") as socket:
        first = await socket.receive_json()
        await stage.show("example", "https://example.org")
        second = await socket.receive_json()
        assert first["revision"] != second["revision"]
        await socket.send_json({"type": "ready", "page_id": "example", "revision": first["revision"]})
        # A subsequent state reply proves the server consumed the stale ready.
        await socket.send_json({"type": "state", "page_id": "example", "state": "ack"})
        async with asyncio.timeout(1):
            while stage.page_state != "ack":
                await asyncio.sleep(0)
        assert not stage.ready.is_set()
        await socket.send_json({"type": "ready", "page_id": "example", "revision": stage.page["revision"]})
        await asyncio.wait_for(stage.ready.wait(), 1)
        sending = asyncio.create_task(stage.send({"n": [1, 2]}))
        packet = await socket.receive_json(timeout=1)
        assert packet["data"] == {"n": [1, 2]}
        await socket.send_json({"type": "sent", "page_id": "example", "id": packet["id"]})
        await asyncio.wait_for(sending, 1)
        await stage.show("other", "/c/example/index.html")
        assert (await socket.receive_json())["page_id"] == "other"
    async with client.ws_connect("/stage/ws", origin="http://127.0.0.1:8766") as socket:
        assert (await socket.receive_json())["page_id"] == "other"
