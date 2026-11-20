"""Run with tests/e2e/run.sh so HA and the app each use their supported Python."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from custom_components.atv_bridge.const import DOMAIN
from homeassistant.config_entries import SOURCE_HASSIO
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.mark.parametrize("discover", [False, True])
async def test_pair_and_press_through_home_assistant(hass, hass_client, socket_enabled, tmp_path, discover):
    data = tmp_path / "bridge-data"
    env = os.environ | {"PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in ("app", "vendor/pyatv"))}
    env.pop("SUPERVISOR_TOKEN", None)
    proc = await asyncio.create_subprocess_exec(
        ROOT / "app/.venv/bin/python",
        Path(__file__).with_name("fake_service.py"),
        data,
        *(["--discover"] if discover else []),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    entry = None
    try:
        async with asyncio.timeout(45):
            port = json.loads(await proc.stdout.readline())["port"]
            config = {"host": "127.0.0.1", "port": port, "token": (data / "internal_token").read_text()}
            discovery = HassioServiceInfo(config=config, name="Bridge", slug="atv_bridge", uuid="e2e")
            flow = hass.config_entries.flow
            step = await flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery)
            assert step["step_id"] == "hassio_confirm"
            step = await flow.async_configure(step["flow_id"], {})
            if discover:
                assert step["step_id"] == "select_tv"
                step = await flow.async_configure(step["flow_id"], {"apple_tv_id": "AirPlay"})
            assert step["step_id"] == "pin"
            step = await flow.async_configure(step["flow_id"], {"pin": "2271"})
            if step.get("description_placeholders", {}).get("protocol") == "Companion":
                step = await flow.async_configure(step["flow_id"], {"pair": True})
                assert step["step_id"] == "pin"
                step = await flow.async_configure(step["flow_id"], {"pin": "1111"})
            assert step["description_placeholders"]["protocol"] == "screenshot"
            step = await flow.async_configure(step["flow_id"], {"pair": False})
            assert step["type"] == "create_entry"
            entry = step["result"]
            await hass.async_block_till_done()

            assert len(entry.subentries) == 1
            snapshot = entry.runtime_data.receivers[0].data
            assert snapshot.health["pairings"] == {
                "airplay": True,
                "companion": True,
                "remote_pairing": False,
            }
            assert snapshot.health["pairing_needed"] == ["remote_pairing"]
            assert snapshot.health["classification"] == snapshot.health["state"]
            assert snapshot.status["connected"] and snapshot.status["volume_supported"]
            assert snapshot.apps == ["Example Player"]
            assert (await entry.runtime_data.client.request("/api/apps"))["apps"] == [
                {"id": "com.example.player", "name": "Example Player"}
            ]
            client = await hass_client()
            async with client.post("/api/atv_bridge/tool/tv_status", json={}) as response:
                assert response.status == 200
                health = await response.json()
                assert "screen" not in health and health["data"]["pairings"] == {
                    "control": "ok",
                    "screen": "missing",
                }
            path = "/api/atv_bridge/mcp"
            headers = {"Accept": "application/json, text/event-stream"}
            message = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "bridge-e2e", "version": "1"},
                },
            }
            async with client.post(path, headers=headers, json=message) as response:
                assert response.status == 200
                result = (await response.json())["result"]
                assert result["serverInfo"]["name"] == "Apple TV Agent Bridge"
                headers["MCP-Protocol-Version"] = result["protocolVersion"]
            async with client.post(
                path, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
            ) as response:
                assert response.status == 202
            message = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "tv_remote", "arguments": {"keys": ["up"]}},
            }
            async with client.post(path, headers=headers, json=message) as response:
                assert response.status == 200
                result = (await response.json())["result"]["structuredContent"]
                assert (
                    result["ok"]
                    and result["data"]["delivered"] == "confirmed"
                    and result["data"]["confirmed_keys"] == 1
                )
    finally:
        try:
            if entry:
                await hass.config_entries.async_unload(entry.entry_id)
        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise
    assert proc.returncode == 0
    assert (data / "last_button").read_text() == "up"
