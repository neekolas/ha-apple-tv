import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component


@pytest.mark.parametrize("api_id", ["assist", "homeassistant"])
async def test_registered_tools(hass, entry, bridge, hass_admin_user, api_id):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "llm", {})
    context = llm.LLMContext(
        platform="mcp_server",
        context=Context(user_id=hass_admin_user.id),
        language="en",
        assistant="conversation",
        device_id=None,
    )
    player = "media_player.apple_tv_agent_bridge_player"
    async_expose_entity(hass, "conversation", player, False)
    if api_id == "assist":
        hidden = await llm.async_get_api(hass, api_id, context)
        assert not any(tool.name.startswith("atv_bridge__") for tool in hidden.tools)
    async_expose_entity(hass, "conversation", player, True)
    api = await llm.async_get_api(hass, api_id, context)
    specs = {
        "pair": ("tv_pair", {"kind": "control"}),
        "status": ("tv_status", {}),
        "power": ("tv_power", {"on":True}),
        "open": ("tv_open", {"app": "Music"}),
        "play": ("tv_play", {"youtube": "https://youtu.be/example"}),
        "show": ("tv_show", {"html": "<p>Hello</p>"}),
    }
    assert {
        tool.name for tool in api.tools if tool.name.startswith("atv_bridge__")
    } == {"atv_bridge__" + suffix for suffix in specs}
    for suffix, (name, args) in specs.items():
        result = await api.async_call_tool(
            llm.ToolInput(tool_name=f"atv_bridge__{suffix}", tool_args=args)
        )
        assert isinstance(result, llm.ToolResult)
        assert not result.error and result.data["tool"] == name
        assert (f"/api/tool/{name}", args) in bridge.calls
    bridge.replies["/api/tool/tv_power"] = {
        "ok": False,
        "state": "failed",
        "evidence": ["TV did not wake"],
    }
    failure = await api.async_call_tool(
        llm.ToolInput(tool_name="atv_bridge__power", tool_args={"on": True})
    )
    assert failure.error and failure.data["evidence"] == ["TV did not wake"]
    await hass.config_entries.async_unload(entry.entry_id)
    api = await llm.async_get_api(hass, api_id, context)
    assert not any(tool.name.startswith("atv_bridge__") for tool in api.tools)
