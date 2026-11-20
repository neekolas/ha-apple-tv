import pytest
from homeassistant.core import Event


@pytest.mark.parametrize(
    "kind,invalid,field",
    [
        ("screen", "screen_pairing_invalid", "remote_pairing"),
        ("control", "pairing_removed", "airplay"),
    ],
)
async def test_invalid_pairing_notification_and_reply(
    hass, entry, bridge, kind, invalid, field
):
    deliveries = []

    async def notify(call):
        deliveries.append(call.data)

    hass.services.async_register("notify", "mobile_app_test_phone", notify)
    hass.config_entries.async_update_entry(
        entry, options={"notify_target": "notify.mobile_app_test_phone"}
    )
    manager = entry.runtime_data.notifications
    prompt = {
        "pairing_id": "secret-session",
        "kind": kind,
        "code_length": 6 if kind == "screen" else 4,
        "ask_user": "Please read the code.",
        "expires_in_s": 179,
    }
    bridge.replies["/api/tool/tv_pair"] = {"ok": True, "data": prompt}
    await manager.update({"pairings": {}}, {invalid})
    assert not deliveries
    await manager.update({"pairings": {field: True}}, {invalid})
    action = deliveries[0]["data"]["actions"][0]
    assert action["behavior"] == "textInput" and action["authenticationRequired"]
    assert ("/api/tool/tv_pair", {"kind": kind, "notify": True}) in bridge.calls
    await manager.update({"pending_pairing": prompt | {"notify": True}}, set())
    assert len(deliveries) == 1
    bridge.replies["/api/tool/tv_pair"] = {
        "ok": True,
        "verified": True,
        "summary": "Pairing verified.",
    }
    code = "0" * prompt["code_length"]
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "another-session", "reply_text": code},
    )
    await hass.async_block_till_done()
    assert len(deliveries) == 1
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": action["action"], "reply_text": code},
    )
    await hass.async_block_till_done()
    assert deliveries[-1]["message"] == "Pairing verified."
    assert (
        "/api/tool/tv_pair",
        {"kind": kind, "pairing_id": "secret-session", "code": code},
    ) in bridge.calls
    await manager.reply(
        Event(
            "mobile_app_notification_action",
            {"action": action["action"], "reply_text": code},
        )
    )
    assert len(deliveries) == 2


async def test_notify_option_without_starting_pairing(hass, entry, bridge):
    hass.services.async_register("notify", "mobile_app_phone", lambda call: None)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"protocol": "notifications"}
    )
    assert flow["step_id"] == "notifications"
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"notify_target": "notify.mobile_app_phone"}
    )
    assert result["data"]["notify_target"] == "notify.mobile_app_phone"
    assert not any("pair/start" in path for path, _ in bridge.calls)


async def test_explicit_notify_request_does_not_start_another_exchange(
    hass, entry, bridge
):
    deliveries = []

    async def notify(call):
        deliveries.append(call.data)

    hass.services.async_register("notify", "mobile_app_phone", notify)
    hass.config_entries.async_update_entry(
        entry, options={"notify_target": "notify.mobile_app_phone"}
    )
    manager = entry.runtime_data.notifications
    prompt = {
        "pairing_id": "explicit",
        "notify": True,
        "kind": "control",
        "code_length": 4,
        "ask_user": "Read the code.",
        "expires_in_s": 120,
    }
    await manager.update({"pending_pairing": prompt}, set())
    await manager.update({"pending_pairing": prompt}, set())
    assert len(deliveries) == 1 and all("tv_pair" not in p for p, _ in bridge.calls)


async def test_phone_offers_reply_after_manual_service_appears(hass, entry):
    deliveries = []

    async def notify(call):
        deliveries.append(call.data)

    hass.services.async_register("notify", "mobile_app_phone", notify)
    hass.config_entries.async_update_entry(
        entry, options={"notify_target": "notify.mobile_app_phone"}
    )
    manager = entry.runtime_data.notifications
    prompt = {
        "kind": "screen",
        "pairing_id": "same",
        "notify": True,
        "code_length": 6,
        "expires_in_s": 170,
        "state": "waiting_for_pairing_screen",
        "ask_user": "Open Remote App and Devices.",
    }
    for _ in range(2):
        await manager.update({"pending_pairing": prompt}, set())
    assert len(deliveries) == 1 and "data" not in deliveries[0]
    prompt.update(state="code_on_screen", ask_user="Read the six digits.")
    for _ in range(2):
        await manager.update({"pending_pairing": prompt}, set())
    assert (
        len(deliveries) == 2
        and deliveries[1]["data"]["actions"][0]["behavior"] == "textInput"
    )
