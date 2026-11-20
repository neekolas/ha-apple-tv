import pytest
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from custom_components.atv_bridge.const import DOMAIN


@pytest.mark.parametrize("source", [SOURCE_HASSIO, SOURCE_USER])
async def test_pairing_flow(hass, bridge, source):
    bridge.health["pairing_needed"] = ["companion", "remote_pairing"]
    data = (
        HassioServiceInfo(
            config=bridge.config | {"addon": "Bridge"},
            name="Bridge",
            slug="atv",
            uuid="abcd",
        )
        if source == SOURCE_HASSIO
        else None
    )
    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": source}, data=data)
    assert result["step_id"] == ("hassio_confirm" if data else "user")
    result = await flow.async_configure(
        result["flow_id"], {} if data else bridge.config
    )
    assert result["step_id"] == "pin"
    assert bridge.calls[-1] == ("/api/pair/start", {"protocol": "airplay"})
    result = await flow.async_configure(result["flow_id"], {"pin": "bad"})
    assert result["errors"] == {"base": "pairing_failed"}
    result = await flow.async_configure(result["flow_id"], {})
    result = await flow.async_configure(result["flow_id"], {"pin": "0123"})
    assert (
        "/api/pair/finish",
        {"pairing_id": "airplay-id", "pin": "0123"},
    ) in bridge.calls
    assert result["description_placeholders"]["protocol"] == "Companion"
    result = await flow.async_configure(result["flow_id"], {"pair": True})
    assert result["step_id"] == "pin"
    result = await flow.async_configure(result["flow_id"], {"pin": "0123"})
    assert result["description_placeholders"]["protocol"] == "screenshot"
    if source == SOURCE_HASSIO:
        result = await flow.async_configure(result["flow_id"], {"pair": True})
        assert bridge.calls[-1] == ("/api/pair/start", {"protocol": "remote_pairing"})
        result = await flow.async_configure(result["flow_id"], {"pin": "0123"})
    else:
        result = await flow.async_configure(result["flow_id"], {"pair": False})
    assert result["type"] == "create_entry"
    assert result["data"] == bridge.config
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_options(hass, bridge, entry):
    flow = hass.config_entries.options
    result = await flow.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await flow.async_configure(result["flow_id"], {})
    result = await flow.async_configure(result["flow_id"], {"pin": "0123"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.config_entries.async_loaded_entries(DOMAIN) == [entry]


async def test_repairs_and_reauth(hass, bridge, entry):
    for code in ("allow_access_changed", "unreachable", "pairing_removed"):
        bridge.health.update(
            ok=False, state="failed", error={"code": code, "message": code}
        )
        await entry.runtime_data.async_refresh()
        assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_{code}")
    await hass.async_block_till_done()
    [reauth] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert reauth["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(reauth["flow_id"], {})
    bridge.health.update(ok=True, state="ready", error=None)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "0123"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"{entry.entry_id}_pairing_removed"
    )


async def test_discovery_updates_loaded_bridge(hass, entry, bridge):
    # The persisted endpoint may change on app reinstall or network reconfiguration.
    hass.config_entries.async_update_entry(entry, data=entry.data | {"port": 8765})
    info = HassioServiceInfo(
        config=bridge.config, name="Bridge", slug="atv", uuid="abcd"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=info
    )
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert entry.data == bridge.config
    assert hass.config_entries.async_loaded_entries(DOMAIN) == [entry]


@pytest.mark.parametrize("count", [1, 2])
async def test_select_tv_before_first_pin(hass, bridge, count):
    bridge.health["discover_for_pairing"] = True
    devices = [
        {"name": name, "model": "AppleTV11,1", "identifiers": [f"id-{i}"], "address": f"192.0.2.{i+1}"}
        for i, name in enumerate(["Living Room", "Bedroom"][:count])
    ]
    bridge.replies["/api/discover"] = []
    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await flow.async_configure(result["flow_id"], bridge.config)
    assert result["step_id"] == "select_tv" and result["errors"]["base"] == "no_tvs"
    bridge.replies["/api/discover"] = devices
    result = await flow.async_configure(result["flow_id"], {})
    [field] = result["data_schema"].schema
    if count == 1:
        assert field.default() == "id-0"
    options = result["data_schema"].schema[field].config["options"]
    assert [o["value"] for o in options] == [d["identifiers"][0] for d in devices]
    chosen = devices[-1]["identifiers"][0]
    result = await flow.async_configure(result["flow_id"], {"apple_tv_id": chosen})
    assert result["step_id"] == "pin"
    assert bridge.calls[-1] == (
        "/api/pair/start",
        {"protocol": "airplay", "apple_tv_id": chosen},
    )


@pytest.mark.parametrize("protocol", ["remote_pairing", "airplay"])
async def test_configure_replaces_existing_pairing(hass, bridge, entry, protocol):
    bridge.health["pairings"] = dict(airplay=True, companion=True, remote_pairing=True)
    bridge.health["problems"] = [{"code": "screen_pairing_invalid"}]
    await entry.runtime_data.async_refresh()
    issue_id = f"{entry.entry_id}_screen_pairing_invalid"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    flow = hass.config_entries.options
    result = await flow.async_init(entry.entry_id)
    result = await flow.async_configure(result["flow_id"], {"protocol": protocol})
    assert result["step_id"] == "pin"
    body = {"protocol": protocol, "replace": True}
    assert bridge.calls[-1] == ("/api/pair/start", body)
    bridge.health["problems"] = []
    result = await flow.async_configure(result["flow_id"], {"pin": "0123"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
