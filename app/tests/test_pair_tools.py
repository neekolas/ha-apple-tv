from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atv_bridge_app import ingress
from atv_bridge_app.state import Fault
from atv_bridge_app.tools.pair import airplay_code, SETTINGS_PATH
from atv_bridge_app.tools.prompts import prompt_choice
from test_fleet import rooms


def fake_control(rt):
    adapter = rt.engine.adapter
    adapter.begin_pair = AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    adapter.finish_pair = AsyncMock(return_value={"atv_id": "4142", "ltpk": "ff" * 32})
    adapter.verify_reuse = AsyncMock()


async def test_public_pair_unpaired_room_and_stale_replies(tmp_path):
    fleet = rooms(tmp_path)
    living, bedroom = fleet.runtimes.values()
    living.state.save_pairings(airplay={"atv_id": "old"})
    fake_control(bedroom)
    args = {"tv": "Bedroom", "kind": "control"}
    start = await fleet.call("tv_pair", args)
    data = start["data"]
    assert start["ok"] and not start["verified"]
    assert set(data) == {
        "pairing_id",
        "code_length",
        "state",
        "kind",
        "ask_user",
        "expires_in_s",
    }
    assert data["code_length"] == 4 and 0 < data["expires_in_s"] <= 180
    again = await fleet.call("tv_pair", args | {"notify": True})
    assert again["data"]["pairing_id"] == data["pairing_id"]
    assert bedroom.pairing.prompt()["notify"]
    for change in (
        {"tv": "Living Room"},
        {"kind": "screen", "code": "123456"},
        {"pairing_id": "stale"},
    ):
        result = await fleet.call("tv_pair", args | {"code": "0123"} | change)
        assert not result["ok"]
    result = await fleet.call("tv_pair", args | {"code": "0123"})
    assert result["verified"] and "Companion app list" in result["evidence"][0]
    assert living.state.receiver("airplay") == {"atv_id": "old"}
    assert bedroom.state.receiver("companion") == bedroom.state.receiver("airplay")
    assert "0123" not in (bedroom.state.root / "audit.jsonl").read_text()
    await bedroom.engine.stop()


async def test_cancel_keeps_existing_pairings(service):
    state = dict(airplay={"atv_id": "previous"}, remote_pairing={"path": "previous"})
    service.state.save_pairings(**state)
    fake_control(service)
    prompt = await service.tools.call("tv_pair", {"kind": "control"})
    result = await service.tools.call(
        "tv_pair",
        {"kind": "control", "pairing_id": prompt["data"]["pairing_id"], "cancel": True},
    )
    assert result["ok"] and service.pairing.prompt() is None
    for k, v in state.items():
        assert service.state.receiver(k) == v
    assert not (
        await service.tools.call("tv_pair", {"kind": "control", "code": "0102"})
    )["ok"]


@pytest.mark.parametrize(
    "items,expected",
    [
        (["AirPlay Code", "0", "4", "1", "9"], "0419"),
        (["AirPlay", "Passcode: 0912"], "0912"),
        (["Movie", "0912"], None),
        (["AirPlay code", "1111", "2222"], None),
    ],
)
def test_only_unambiguous_airplay_codes(items, expected):
    assert airplay_code([{"label": i} for i in items]) == expected


async def test_explicit_screen_read(service):
    fake_control(service)
    service.state.save_pairings(remote_pairing=True)
    service.observer.call = AsyncMock(
        return_value={
            "items": [{"label": "AirPlay Code", "value": "0345"}],
            "focused": None,
        }
    )
    prompt = await service.tools.call("tv_pair", {"kind": "control"})
    service.observer.call.assert_not_awaited()
    result = await service.tools.call(
        "tv_pair",
        {
            "kind": "control",
            "read_code_from_screen": True,
            "pairing_id": prompt["data"]["pairing_id"],
        },
    )
    assert result["verified"] and "0345" not in str(result)
    service.engine.adapter.finish_pair.assert_awaited_once()
    assert service.engine.adapter.finish_pair.await_args.args[1] == "0345"
    assert not (
        await service.tools.call(
            "tv_pair", {"kind": "screen", "read_code_from_screen": True}
        )
    )["ok"]


@pytest.mark.parametrize("control", [False, True])
async def test_screen_pair_verification(service, control):
    if control:
        service.state.save_pairings(companion=True)
    service.tools.tv_open = AsyncMock()
    service.observer.call = AsyncMock(
        side_effect=[
            {},
            {"path": "remote.plist"},
            {"items": [{"label": "Remote App and Devices"}], "focused": None},
        ]
    )
    prompt = await service.tools.call("tv_pair", {"kind": "screen"})
    assert prompt["data"]["code_length"] == 6
    if control:
        service.tools.tv_open.assert_awaited_once_with("Settings", path=SETTINGS_PATH)
    assert prompt["data"]["state"] == "code_on_screen"
    r = await service.tools.call("tv_pair", {"kind": "screen", "code": "012345"})
    assert r["verified"] and r["evidence"] == ["Authenticated accessibility read"]


async def test_ingress_only_accepts_supervisor_and_shares_pending(
    tmp_path, aiohttp_client, monkeypatch
):
    fleet = rooms(tmp_path)
    fake_control(fleet.select("Bedroom"))
    client = await aiohttp_client(ingress.panel(fleet))
    assert (
        await client.get("/", headers={"X-Forwarded-For": "172.30.32.2"})
    ).status == 403
    monkeypatch.setattr(ingress, "SUPERVISOR", "127.0.0.1")
    page = await (await client.get("/")).text()
    assert all(
        v in page
        for v in ("Living Room", "Bedroom", "192.0.2.159", "Pair screen", "Submit code")
    )
    start = await (
        await client.post("/pair", json={"tv": "Bedroom", "kind": "control"})
    ).json()
    session = start["data"]["pairing_id"]
    rows = await (await client.get("/status")).json()
    assert (
        next(r for r in rows if r["name"] == "Bedroom")["pending"]["pairing_id"]
        == session
    )
    end = await (
        await client.post(
            "/pair",
            json={
                "tv": "Bedroom",
                "kind": "control",
                "code": "0123",
                "pairing_id": session,
            },
        )
    ).json()
    assert end["verified"]
    assert (await client.post("/pair", data={"tv": "Bedroom"})).status == 415
    await fleet.select("Bedroom").engine.stop()


def view(labels, focus, bundle="com.wbd.stream", name="HBO Max"):
    items = [{"label": v} for v in labels]
    return {
        "app": {"bundle_id": bundle, "name": name},
        "items": items,
        "focused": {"label": focus},
    }


@pytest.mark.parametrize(
    "labels,focused,preferred,expected",
    [
        (["Who’s Watching?", "Kid", "Alex", "Edit"], "Kid", "Kid", "Alex"),
        (["Who's Watching?", "Kids", "Edit"], "Kids", "", "Kids"),
        (["Manage Profiles", "Alex", "Jane"], "Alex", "Jane", "Jane"),
        (["Allow", "Ask App Not to Track"], "Allow", "", "Ask App Not to Track"),
        (["Notifications", "Not Now", "Allow"], "Allow", "", "Not Now"),
    ],
)
def test_app_prompts_choose_profiles_or_decline(labels, focused, preferred, expected):
    assert prompt_choice(view(labels, focused), preferred=preferred)[1] == expected


async def test_ready_selects_tvos_profile_from_screensaver(service):
    tools = service.tools
    service.state.save_pairings(remote_pairing=True)
    service.engine.last_power = "screensaver"
    service.engine.status = AsyncMock(return_value={"power": "on"})
    tools.default_profile = "Alex"
    picker = view(
        ["Jane", "Alex", "Add Profile"],
        "Jane",
        "com.apple.TVSystemUIService",
        "System UI",
    )
    after = view(["Home"], "Home", "com.apple.HeadBoard", "Home")
    tools.read_observation = AsyncMock(side_effect=[picker, after])
    tools.observe = AsyncMock(return_value=({"label": "Alex"}, picker["items"]))
    tools.press = AsyncMock()
    await tools.prepare_ui()
    assert [c.args[0] for c in tools.press.await_args_list] == ["menu", "select"]
    assert (
        tools.profile_name == "Alex"
        and "tvOS profile: Alex" in tools.action_evidence
    )


async def test_settings_returns_to_root_without_selecting_device(service):
    tools = service.tools

    def page(items):
        return view(items, items[-1], "com.apple.TVSettings", "Settings")

    tools.read_observation = AsyncMock(
        side_effect=[
            page(["Remote App and Devices", "Agent Bridge"]),
            page(["Settings", "Remotes and Devices", "TV Button", "Remote App and Devices"]),
            page(["Settings", "Network", "Remotes and Devices"]),
        ]
    )
    tools.press = AsyncMock()
    await tools.settings_root()
    assert [c.args for c in tools.press.await_args_list] == [("menu",), ("menu",)]


async def test_toggle_value_verifies_same_focus(service):
    tools = service.tools
    before, after = (
        {"label": "Clickpad", "value": "On"},
        {"label": "Clickpad", "value": "Off"},
    )
    tools.observe = AsyncMock(return_value=(before, [before]))
    tools.press = AsyncMock()
    tools.snapshot = AsyncMock(return_value={"focus": after})
    result = await tools.tv_navigate("Clickpad", select=True)
    assert result["verified"] and "Accessibility value changed" in result["evidence"]


async def test_sleep_retry_on_fresh_session(service):
    engine = service.engine
    first = await engine.ensure()
    second = SimpleNamespace(
        power=AsyncMock(side_effect=["on", "off"]), hid=AsyncMock()
    )
    engine.adapter.connect = AsyncMock(return_value=second)
    await engine.sleep_verified()
    assert first.keys == ["sleep"] and first.closed
    second.hid.assert_awaited_once_with("sleep", 0)
    engine.client = None


async def test_play_reopens_deep_link_after_safe_profile_selection(service):
    tools = service.tools
    client = await service.engine.ensure()
    url = "https://play.hbomax.com/video/watch/test"
    app = {"bundle_id": "com.wbd.stream", "name": "HBO Max"}
    service.state.save_pairings(remote_pairing=True)
    picker = view(["Who’s Watching?", "Alex", "Kids"], "Alex")
    playing = view(["Playing"], "Playing")
    observation = picker

    async def observe(*args, **kwargs):
        return observation

    async def select(key, hold=0):
        nonlocal observation
        assert key == "select"
        observation = playing
        client.media = {"title": "Requested episode", "state": "playing"}
        client.app = app

    tools.read_observation = observe
    tools.press = select
    service.operations.launch = AsyncMock()
    tools.play_timeout = 0
    result = await tools.tv_play(link=url)
    assert result["verified"] and result["data"]["landed"] == "playing"
    targets = [c.args[1] for c in service.operations.launch.await_args_list]
    assert targets == [url, url] and tools.action_evidence == ["App profile: Alex"]


async def test_never_select_allow_on_moving_prompt(service):
    tools = service.tools
    service.state.save_pairings(remote_pairing=True)
    prompt = view(["Ask App Not to Track", "Allow"], "Ask App Not to Track")
    tools.read_observation = AsyncMock(return_value=prompt)
    tools.observe = AsyncMock(
        side_effect=[
            (prompt["focused"], prompt["items"]),
            ({"label": "Allow"}, prompt["items"]),
        ]
    )
    tools.press = AsyncMock()
    with pytest.raises(Fault):
        await tools.resolve_prompts()
    tools.press.assert_not_awaited()


async def test_overnight_script_records_both_mcp_results(
    service, aiohttp_client, tmp_path, monkeypatch
):
    from datetime import datetime, timezone
    from pathlib import Path
    from runpy import run_path
    import json
    from aiohttp import web

    script = run_path(str(Path(__file__).parent / "live/overnight_wake.py"))
    app = web.Application()

    async def authenticated(request):
        assert request.headers["Authorization"] == "Bearer test-overnight"
        return await service.mcp.handle(request)

    app.router.add_route("*", "/api/atv_bridge/mcp", authenticated)
    service.tools.call = AsyncMock(
        side_effect=[
            {
                "ok": False,
                "verified": False,
                "error": {"code": "tv_unreachable"},
                "data": {"errno": 61},
            },
            {"ok": True, "verified": True, "data": {}},
        ]
    )
    monkeypatch.setenv("HA_TOKEN", "test-overnight")
    async with service.mcp.running():
        client = await aiohttp_client(app)
        args = SimpleNamespace(
            url=str(client.make_url("/")),
            tv="Test Room",
            at=datetime.now(timezone.utc),
            hold_seconds=0,
            output=tmp_path / "wake.jsonl",
        )
        assert await script["main"](args) == 1
    rows = [json.loads(line) for line in args.output.read_text().splitlines()]
    assert [r["on"] for r in rows] == [True, False]
    assert [r["errno"] for r in rows] == [61, None]
    assert all(r["duration_s"] >= 0 and r["tv"] == "Test Room" for r in rows)


async def test_poll_screen_pairing_waits_for_consent(service):
    fault = Fault("pairing_screen_unavailable", "No manual service.", "Open Settings.")
    service.observer.call = AsyncMock(side_effect=[fault, fault, {}])
    call = service.tools.call
    start = await call("tv_pair", {"kind": "screen"})
    session = {"kind": "screen", "pairing_id": start["data"]["pairing_id"]}
    assert start["data"]["state"] == "waiting_for_pairing_screen"
    assert (
        "open Settings" in start["data"]["ask_user"] and "code" not in start["summary"]
    )
    polled = await call("tv_pair", session)
    assert polled["data"]["state"] == "waiting_for_pairing_screen"
    assert service.observer.call.await_count == 1
    assert (await call("tv_pair", session | {"code": "000000"}))["error"][
        "code"
    ] == "busy"
    await service.pairing.preparation
    ready = await call("tv_pair", session)
    assert ready["data"]["state"] == "code_on_screen" and "read me" in ready["summary"]
    assert (
        ready["data"]["pairing_id"] == session["pairing_id"]
        and service.observer.call.await_count == 3
    )
    assert (await call("tv_pair", session | {"cancel": True}))["verified"]
    assert (await call("tv_pair", session))["error"]["code"] == "invalid_arguments"


async def test_cancelling_waiting_session_stops_discovery(service):
    service.observer.call = AsyncMock(
        side_effect=Fault("pairing_screen_unavailable", "No service.", "Open Settings.")
    )
    prompt = await service.tools.call("tv_pair", {"kind": "screen"})
    task = service.pairing.preparation
    await service.tools.call(
        "tv_pair",
        {"kind": "screen", "cancel": True, "pairing_id": prompt["data"]["pairing_id"]},
    )
    assert task.cancelled() and not service.pairing.prompt()


async def test_wait_for_screen_stops_at_original_expiry(service, monkeypatch):
    import atv_bridge_app.pairing as pairing

    monkeypatch.setattr(pairing, "PAIR_WINDOW", 0.12)
    monkeypatch.setattr(pairing, "SCREEN_POLL_INTERVAL", 0.02)
    service.observer.call = AsyncMock(
        side_effect=Fault("pairing_screen_unavailable", "Waiting.", "Open Settings.")
    )
    prompt = await service.tools.call("tv_pair", {"kind": "screen"})
    assert prompt["data"]["state"] == "waiting_for_pairing_screen"
    expiry = service.pairing.expiry
    await expiry
    assert service.pairing.preparation is None and service.observer.call.await_count > 1
    poll = await service.tools.call(
        "tv_pair", {"kind": "screen", "pairing_id": prompt["data"]["pairing_id"]}
    )
    assert not poll["ok"] and poll["error"]["code"] == "invalid_arguments"
