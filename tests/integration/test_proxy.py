import asyncio


async def test_admin_streamable_http_and_bundle(
    hass, entry, bridge, hass_client, hass_read_only_access_token, monkeypatch
):
    client = await hass_client()
    url = "/api/atv_bridge/mcp"
    headers = {
        "Mcp-Session-Id": "original",
        "MCP-Protocol-Version": "2025-11-25",
        "Accept": "application/json, text/event-stream",
    }
    response = await client.post(url, json={"jsonrpc": "2.0"}, headers=headers)
    assert response.status == 200
    assert await response.json() == {"jsonrpc": "2.0"}
    assert response.headers["Mcp-Session-Id"] == "session"
    _, _, sent = bridge.proxy_calls[-1]
    assert sent["Authorization"] == "Bearer app-token"
    assert sent["Mcp-Session-Id"] == "original"
    assert sent["MCP-Protocol-Version"] == headers["MCP-Protocol-Version"]
    assert sent["Content-Type"] == "application/json"

    async with asyncio.timeout(3):
        response = await client.get(url, headers=headers | {"Last-Event-ID": "3"})
        assert response.content_type == "text/event-stream"
        assert await response.content.readuntil(b"\n\n") == b"data: first\n\n"
        assert bridge.proxy_calls[-1][2]["Last-Event-ID"] == "3"
        bridge.release_stream.set()
        assert await response.read() == b"data: second\n\n"
    assert (await client.delete(url, headers=headers)).status == 204

    async def upload():
        yield b"PK\x03\x04"
        await bridge.received_upload.wait()
        yield b"trailer"

    async with asyncio.timeout(3):
        response = await client.post(
            "/api/atv_bridge/bundle",
            data=upload(),
            headers={"Content-Type": "application/zip"},
        )
        assert (await response.json())["bundle"] == "bundle"
    assert bridge.proxy_calls[-1][2]["Content-Type"] == "application/zip"

    response = await client.post("/api/atv_bridge/tool/tv_status", json={})
    assert response.status == 200 and (await response.json())["tool"] == "tv_status"
    script_url = "/api/atv_bridge/tool/tv_page_run"
    script = {"script": "document.querySelector(arg).value", "arg": "#note"}
    response = await client.post(script_url, json=script)
    assert response.status == 200 and (await response.json())["tool"] == "tv_page_run"

    # Core performs a reverse DNS lookup for failed logins. Keep this test on loopback.
    monkeypatch.setattr(
        "homeassistant.components.http.ban.gethostbyaddr",
        lambda address: ("localhost", [], [address]),
    )
    count = len(bridge.proxy_calls)
    for path in (url, "/api/atv_bridge/bundle", "/api/atv_bridge/tool/tv_status", script_url):
        for token in ("bad-token", hass_read_only_access_token):
            assert (
                await client.post(path, headers={"Authorization": f"Bearer {token}"})
            ).status == 401
    assert len(bridge.proxy_calls) == count
    await hass.config_entries.async_unload(entry.entry_id)
    assert (await client.post(url)).status == 503
