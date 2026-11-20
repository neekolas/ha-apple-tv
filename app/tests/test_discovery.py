from ipaddress import IPv4Address
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pyatv
import pytest
from atv_bridge_app.atv import Adapter
from atv_bridge_app.atv.discovery import Resolver, describe
from atv_bridge_app.state import Fault, State
from pyatv.conf import AppleTV, ManualService
from pyatv.const import Protocol
from pyatv.interface import DeviceInfo


def device(host, name="Living Room", mac="AA:00:BB:00:CC:01", uuid="uuid-1", model="AppleTV11,1", port=7000):
    cfg = AppleTV(IPv4Address(host), name, device_info=DeviceInfo({DeviceInfo.RAW_MODEL: model}))
    cfg.add_service(ManualService(mac, Protocol.AirPlay, port, {}))
    if uuid:
        cfg.add_service(ManualService(uuid, Protocol.Companion, 49153, {}))
    return cfg


@pytest.fixture
def lan(monkeypatch):
    directed, multicast, queries = {}, [], []

    async def scan(*, loop, hosts=None, timeout=5):
        queries.append((hosts, timeout))
        return multicast if hosts is None else directed.get(hosts[0], [])

    monkeypatch.setattr(pyatv, "scan", scan)
    return directed, multicast, queries


async def test_fixed_host_migrates_existing_pairings_only_after_authentication(tmp_path, lan):
    state = State(tmp_path)
    state.save_pairings(airplay={"ltpk": "ab"}, companion={"ltpk": "ab"})
    tv = device("192.0.2.15", port=7881)
    adapter = Adapter(str(tv.address), state)
    lan[0][adapter.host] = [tv]
    adapter._connect = AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    session = await adapter.connect(lambda *_: None)
    assert session is adapter._connect.return_value
    assert lan[2] == [([adapter.host], 5)] and adapter.resolver.source == "configured"
    reopened = State(tmp_path)
    assert reopened.pairings["tv"] == {
        "name": tv.name,
        "identifiers": sorted(tv.all_identifiers),
        "model": "AppleTV11,1",
        "last_known_host": str(tv.address),
        "apple_tv_id": tv.identifier,
        "airplay_port": 7881,
    }
    assert reopened.receiver("airplay") == {"ltpk": "ab"}
    assert reopened.identity == state.identity
    assert await adapter.resolver.airplay_endpoint() == (str(tv.address), 7881)


async def test_dhcp_move_skips_new_occupant_and_refreshes_partial_identifier_rotation(
    tmp_path, lan, caplog
):
    tv = device("192.0.2.15")
    state = State(tmp_path)
    adapter = Adapter("", state)
    adapter.resolver.remember(tv, "discovered", companion={"atv_id": "4142"})
    other = device("192.0.2.15", "Bedroom", "bb:bb", "uuid-2")
    moved = device("192.0.2.22", mac="aa:00:bb:00:cc:01", uuid="rotated")
    lan[0][adapter.host] = [other]
    lan[1].extend([other, moved])
    adapter._connect = AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    with caplog.at_level("INFO"):
        await adapter.connect(lambda *_: None)
    assert adapter._connect.await_args.args[0] is moved
    assert lan[2] == [(["192.0.2.15"], 2), (None, 5)]
    target = State(tmp_path).pairings["tv"]
    assert target["last_known_host"] == "192.0.2.22" and "rotated" in target["identifiers"]
    assert adapter.resolver.source == "discovered" and adapter.resolver.change["level"] == "info"
    assert "192.0.2.15 to 192.0.2.22" in caplog.text


async def test_sleeping_tv_uses_saved_host_without_bonjour(tmp_path, lan):
    tv = device("192.0.2.15")
    resolver = Resolver("", State(tmp_path))
    resolver.remember(tv, "discovered")
    lan[0][str(tv.address)] = [tv]
    assert await resolver.resolve() == (tv, "last_known")
    assert lan[2] == [([str(tv.address)], 2)]


async def test_name_fallback_requires_unique_apple_tv_and_successful_auth(tmp_path, lan, caplog):
    tv = device("192.0.2.15")
    adapter = Adapter("", State(tmp_path))
    adapter.resolver.remember(tv, "discovered", companion={"atv_id": "4142"})
    rotated = device("192.0.2.33", mac="new", uuid="new-uuid")
    lan[1].extend([device("192.0.2.11", "Bedroom", "other", "other-uuid"), rotated])
    adapter._connect = AsyncMock(side_effect=pyatv.exceptions.AuthenticationError())
    with pytest.raises(pyatv.exceptions.AuthenticationError):
        await adapter.connect(lambda *_: None)
    assert "exact name" in caplog.text and adapter.host == str(tv.address)
    assert "new-uuid" not in adapter.resolver.target["identifiers"]
    adapter._connect.side_effect = None
    await adapter.connect(lambda *_: None)
    assert "new-uuid" in State(tmp_path).pairings["tv"]["identifiers"]


async def test_ambiguity_and_model_only_match_do_not_select_any_tv(tmp_path, lan):
    resolver = Resolver("", State(tmp_path))
    resolver.remember(device("192.0.2.15"), "discovered")
    lan[1].append(device("192.0.2.11", "Bedroom", "other", "other-uuid"))
    with pytest.raises(Fault) as error:
        await resolver.resolve()
    assert error.value.code == "tv_unreachable" and error.value.remediation
    lan[1].extend(
        [device("192.0.2.31", mac="a", uuid="one"), device("192.0.2.32", mac="b", uuid="two")]
    )
    with pytest.raises(Fault):
        await resolver.resolve()


async def test_discover_endpoint_and_pair_selected_tv(service, aiohttp_client, lan):
    adapter = Adapter("", service.state)
    service.engine.adapter = service.pairing.adapter = adapter
    bedroom = device("192.0.2.20", "Bedroom", "bb:bb", "uuid-2")
    living = device("192.0.2.15")
    lan[1].extend(
        [
            device("192.0.2.21", "Bedroom", "sonos", None, "One"),
            bedroom,
            device("192.0.2.99", "Living Room", "mac", "mac-uuid", "Mac16,5"),
            living,
        ]
    )
    handler = SimpleNamespace(close=AsyncMock())
    adapter._begin = AsyncMock(return_value=handler)
    adapter.finish_pair = AsyncMock(return_value={"atv_id": "4142", "ltpk": "ab" * 32})
    adapter.verify_reuse = AsyncMock()
    adapter._connect = AsyncMock(
        return_value=SimpleNamespace(close=AsyncMock(), status=AsyncMock(return_value={"power": "on"}))
    )
    client = await aiohttp_client(service.app())
    headers = {"Authorization": "Bearer " + service.state.token}
    listing = await (await client.get("/api/discover", headers=headers)).json()
    assert listing["devices"] == [describe(bedroom), describe(living)]
    prompt = await (
        await client.post(
            "/api/pair/start", headers=headers, json={"protocol": "airplay", "apple_tv_id": "uuid-2"}
        )
    ).json()
    adapter._begin.assert_awaited_once_with(bedroom, protocol="airplay")
    assert not adapter.resolver.target
    paired = await (
        await client.post(
            "/api/pair/finish",
            headers=headers,
            json={"pairing_id": prompt["pairing_id"], "pin": "1234"},
        )
    ).json()
    assert paired["ok"] and State(service.state.root).pairings["tv"]["apple_tv_id"] == "uuid-2"


async def test_connection_failure_resolves_again_before_error(tmp_path, lan):
    tv = device("192.0.2.15")
    adapter = Adapter("", State(tmp_path))
    adapter.resolver.remember(tv, "discovered", companion={"atv_id": "4142"})
    lan[0][str(tv.address)] = [tv]
    moved = device("192.0.2.25")
    lan[1].append(moved)
    adapter._connect = AsyncMock(
        side_effect=[ConnectionRefusedError(), SimpleNamespace(close=AsyncMock())]
    )
    await adapter.connect(lambda *_: None)
    assert [c.args[0] for c in adapter._connect.await_args_list] == [tv, moved]
    assert adapter.host == "192.0.2.25"


async def test_address_change_is_informational_in_health_and_tv_status(service):
    resolver = service.engine.adapter.resolver
    resolver.remember(device("192.0.2.15"), "configured")
    resolver.remember(device("192.0.2.33"), "discovered")
    service.state.save_pairings(airplay={"atv_id": "4142"}, companion={"atv_id": "4142"})
    health = await service.operations.tv_health()
    status = (await service.tools.call("tv_status", {}))["data"]
    for result in (health, status):
        assert (
            result["tv"]["address"] == "192.0.2.33" and result["tv"]["address_source"] == "discovered"
        )
        assert resolver.change in result["problems"]
    assert status["capabilities"]["show"]


@pytest.mark.parametrize("configured", [False, True])
async def test_observer_reopens_at_resolved_host_after_tunnel_failure(tmp_path, configured):
    from atv_bridge_app.observer import ReceiverObserver

    state = State(tmp_path)
    state.save_pairings(remote_pairing={"path": "observer/remote.plist"})
    resolver = Resolver("192.0.2.15" if configured else "", state)
    resolver.host = AsyncMock(
        side_effect=["192.0.2.15", "192.0.2.33"] if configured else ["192.0.2.33"]
    )
    observer = ReceiverObserver(resolver, state)
    attempts = []

    async def call(method, **params):
        env = observer.transport.env
        attempts.append((env["ATV_BRIDGE_HOST"], env["ATV_BRIDGE_BONJOUR"]))
        if len(attempts) == 1:
            raise Fault("observer_failed", "Tunnel failed.", "Reconnect.")
        return {"ok": True}

    observer.transport.call = call
    assert await observer.call("ax_items") == {"ok": True}
    assert attempts == [("192.0.2.15", "0") if configured else ("", "1"), ("192.0.2.33", "0")]
    await observer.close()


@pytest.mark.parametrize("network_up", [False, True])
async def test_missing_screen_service_rechecks_receiver_reachability(tmp_path, network_up):
    from atv_bridge_app.observer import ReceiverObserver

    state = State(tmp_path)
    state.save_pairings(remote_pairing={"path": "remote.plist"})
    resolver = Resolver("192.0.2.15", state)
    resolver.host = AsyncMock(
        side_effect=[
            "192.0.2.15",
            "192.0.2.15" if network_up else Fault("tv_unreachable", "Asleep", "Wake the TV"),
        ]
    )
    observer = ReceiverObserver(resolver, state)
    observer.transport.call = AsyncMock(
        side_effect=Fault("screen_service_missing", "No service", "Retry")
    )
    with pytest.raises(Fault) as exc:
        await observer.call("health", verify=True)
    assert exc.value.code == ("screen_pairing_invalid" if network_up else "tv_unreachable")
    assert observer.invalid is network_up
