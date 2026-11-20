import pytest
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from pytest_homeassistant.common import MockConfigEntry

from custom_components.atv_bridge.const import DOMAIN, NAME
from conftest import envelope


@pytest.mark.parametrize("skip_screen", [True, False])
async def test_add_tv_is_scoped_and_preserves_existing_entities(hass, bridge, skip_screen):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title=NAME, data=bridge.config
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    original = registry.async_get_entity_id(
        "media_player", DOMAIN, entry.entry_id + "_player"
    )
    device = registry.async_get(original).device_id
    bridge.tvs = [
        {
            "id": "living",
            "name": "Living Room",
            "paired": True,
            "legacy": True,
            "host": "192.0.2.15",
            "identifiers": ["living"],
        }
    ]
    bridge.replies["/api/discover"] = [
        {
            "identifiers": ["bed"],
            "name": "Bedroom",
            "address": "192.0.2.159",
            "model": "Apple TV",
        }
    ]
    try:
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert registry.async_get(original).device_id == device
        assert len(entry.subentries) == 1
        flow = hass.config_entries.subentries
        result = await flow.async_init(
            (entry.entry_id, "apple_tv"), context={"source": "user"}
        )
        assert result["step_id"] == "select_tv"
        fid = result["flow_id"]
        result = await flow.async_configure(fid, {"apple_tv_id": "bed"})
        assert result["step_id"] == "pin"
        bridge.replies["/api/tvs/bed/health"] = envelope(
            pairing_needed=["remote_pairing"]
        )
        result = await flow.async_configure(fid, {"pin": "0123"})
        assert result["step_id"] == "prepare_pin"
        assert "skip_screen" in result["data_schema"].schema
        result = await flow.async_configure(fid, {"skip_screen": skip_screen})
        if skip_screen:
            assert ("/api/tvs/bed/pair/start", {"protocol": "remote_pairing"}) not in bridge.calls
        else:
            assert result["step_id"] == "pin"
            result = await flow.async_configure(fid, {"pin": "0123"})
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        assert {s.unique_id for s in entry.subentries.values()} == {"living", "bed"}
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        assert {d.name for d in devices} == {"Living Room", "Bedroom"}
        assert registry.async_get(original).device_id == device
        assert not any("/living/pair/" in path for path, _ in bridge.calls)
        bedroom = registry.async_get_entity_id(
            "remote", DOMAIN, f"{entry.entry_id}_bed_remote"
        )
        await hass.services.async_call(
            "remote", "turn_off", {"entity_id": bedroom}, blocking=True
        )
        assert ("/api/tvs/bed/tool/tv_power", {"on": False}) in bridge.calls
        bridge.replies["/api/tvs/bed/health"] = envelope(
            problems=[{"code": "screen_pairing_invalid"}]
        )
        await next(
            c for c in entry.runtime_data.receivers if c.tv["id"] == "bed"
        ).async_refresh()
        issues = ir.async_get(hass)
        assert issues.async_get_issue(
            DOMAIN, entry.entry_id + "_bed_screen_pairing_invalid"
        )
        assert not issues.async_get_issue(
            DOMAIN, entry.entry_id + "_screen_pairing_invalid"
        )
        for protocol in ["airplay", "remote_pairing"]:
            flow = hass.config_entries.options
            result = await flow.async_init(entry.entry_id)
            result = await flow.async_configure(
                result["flow_id"], {"protocol": protocol, "tv": "bed"}
            )
            assert result["step_id"] == "pin"
            assert (
                "/api/tvs/bed/pair/start",
                {"protocol": protocol, "replace": True},
            ) in bridge.calls
            flow.async_abort(result["flow_id"])
        assert not any("/living/pair/" in path for path, _ in bridge.calls)
        # Exercise removal in both directions. Only a HA-initiated removal calls DELETE.
        if skip_screen:
            sub = next(s for s in entry.subentries.values() if s.unique_id == "bed")
            hass.config_entries.async_remove_subentry(entry, sub.subentry_id)
            await hass.async_block_till_done()
            assert ("/api/tvs/bed", "DELETE") in bridge.calls
        else:
            bridge.tvs = [tv for tv in bridge.tvs if tv["id"] != "bed"]
            await hass.config_entries.async_reload(entry.entry_id)
            assert ("/api/tvs/bed", "DELETE") not in bridge.calls
        assert not issues.async_get_issue(DOMAIN, entry.entry_id + "_bed_screen_pairing_invalid")
        assert [tv["id"] for tv in bridge.tvs] == ["living"]
        await hass.config_entries.async_reload(entry.entry_id)
        assert {s.unique_id for s in entry.subentries.values()} == {"living"}
        assert registry.async_get(bedroom) is None and registry.async_get(original).device_id == device
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
