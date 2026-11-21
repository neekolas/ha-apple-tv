import ast
import io
import json
import logging
import os
import re
import signal
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
import pytest
import yaml
from aiohttp import web

from atv_bridge_app import __main__ as entry
from atv_bridge_app import processes, state
from atv_bridge_app.fleet import Fleet
from atv_bridge_app.server import TVRuntime
from atv_bridge_app.stage import Stage
from atv_bridge_app.standalone import DEFAULTS, REMEDIATION, configure, mode, settings
from atv_bridge_app.state import Fault
from conftest import FakeAdapter

TOKEN = "standalone-test-token-0123456789"
CONFIG = Path(__file__).parents[1] / "config.yaml"
PACKAGE = Path(__file__).parents[1] / "atv_bridge_app"


def standalone(argv=(), **env):
    """Settings with a token. Discovery permits the empty TV list that most tests use."""
    return settings(list(argv), {"TV_USE_TOKEN": TOKEN, "TV_USE_DISCOVER": "true"} | env)


def test_defaults_match_the_home_assistant_options():
    assert DEFAULTS == yaml.safe_load(CONFIG.read_text())["options"]
    assert standalone().options == DEFAULTS


def test_environment_sets_every_option():
    plan = standalone(
        TV_USE_TVS="host=192.0.2.15,id=AA:01,name=Living Room; 192.0.2.16 ;name=Den",
        TV_USE_PRESENTATION_MODE="auto",
        TV_USE_MIRROR_SIZE="1280x720",
        TV_USE_MIRROR_FPS="30",
        TV_USE_MAX_ACTIVE_MIRRORS="2",
        TV_USE_PAGE_SCRIPTS="false",
        TV_USE_PAGE_NETWORK_ALLOW="192.0.2.8:8443, dashboard.example",
        TV_USE_DEFAULT_PROFILE="Sam",
        TV_USE_APP_PROFILE="Kids",
        TV_USE_WATCH_HISTORY="off",
        TV_USE_HISTORY_INTERVAL="600",
        TV_USE_HISTORY_FAST_INTERVAL="30",
        TV_USE_HISTORY_MAX_ENTRIES="500",
        TV_USE_LOG_LEVEL="DEBUG",
        TV_USE_LISTEN="192.0.2.64",
        TV_USE_PORT="9000",
        TV_USE_STAGE_LISTEN="0.0.0.0",
        TV_USE_DISCOVER="false",
    )
    assert plan.options == DEFAULTS | {
        "apple_tvs": [
            {"host": "192.0.2.15", "id": "AA:01", "name": "Living Room"},
            {"host": "192.0.2.16"},
            {"name": "Den"},
        ],
        "presentation_mode": "auto",
        "mirror_size": "1280x720",
        "mirror_fps": 30,
        "max_active_mirrors": 2,
        "page_scripts": False,
        "page_network_allow": ["192.0.2.8:8443", "dashboard.example"],
        "default_profile": "Sam",
        "app_profile": "Kids",
        "watch_history": False,
        "history_interval": 600,
        "history_fast_interval": 30,
        "history_max_entries": 500,
        "log_level": "debug",
    }
    assert (plan.listen, plan.port, plan.stage_listen, plan.token) == ("192.0.2.64", 9000, "0.0.0.0", TOKEN)
    assert plan.fleet == {"stage_host": "0.0.0.0", "stage_key": plan.stage_key, "keep_unlisted": True}
    assert re.fullmatch("[0-9a-f]{32}", plan.stage_key) and plan.stage_key != standalone().stage_key
    assert plan.warnings == ()


def test_flags_win_over_environment():
    plan = standalone(
        ["--tv", "192.0.2.20", "--tv", "id=BB:02", "--mirror-fps", "15", "--no-watch-history",
         "--page-network-allow", "10.0.0.5", "--port", "9100", "--log-level", "warning"],
        TV_USE_TVS="192.0.2.15",
        TV_USE_MIRROR_FPS="30",
        TV_USE_WATCH_HISTORY="true",
        TV_USE_PAGE_NETWORK_ALLOW="192.0.2.8",
        TV_USE_PORT="9000",
    )
    assert plan.options["apple_tvs"] == [{"host": "192.0.2.20"}, {"id": "BB:02"}]
    assert plan.options["mirror_fps"] == 15
    assert plan.options["watch_history"] is False
    assert plan.options["page_network_allow"] == ["10.0.0.5"]
    assert plan.options["log_level"] == "warning"
    assert plan.port == 9100
    # Defaults keep the token required and the stage on loopback.
    assert (plan.listen, plan.stage_listen) == ("0.0.0.0", "127.0.0.1")


def test_options_are_copies_of_the_defaults():
    standalone().options["apple_tvs"].append({"host": "192.0.2.1"})
    assert DEFAULTS["apple_tvs"] == [] and standalone().options["apple_tvs"] == []


@pytest.mark.parametrize(
    "argv, env, message",
    [
        ([], {"TV_USE_MIRROR_FPS": "61"}, "TV_USE_MIRROR_FPS: expected 1 through 60"),
        (["--mirror-fps", "0"], {}, "expected 1 through 60"),
        ([], {"TV_USE_MIRROR_SIZE": "3840x2160"}, "TV_USE_MIRROR_SIZE: choose one of"),
        ([], {"TV_USE_PRESENTATION_MODE": "cast"}, "choose one of mirror, play_url, auto"),
        ([], {"TV_USE_HISTORY_INTERVAL": "10"}, "expected 30 through 3600"),
        ([], {"TV_USE_HISTORY_FAST_INTERVAL": "301"}, "expected 5 through 300"),
        ([], {"TV_USE_HISTORY_MAX_ENTRIES": "50"}, "expected 100 through 20000"),
        ([], {"TV_USE_MAX_ACTIVE_MIRRORS": "0"}, "expected at least 1"),
        ([], {"TV_USE_LOG_LEVEL": "trace"}, "choose one of debug"),
        ([], {"TV_USE_PAGE_SCRIPTS": "maybe"}, "expected true or false"),
        ([], {"TV_USE_PORT": "70000"}, "expected 1 through 65535"),
        ([], {"TV_USE_PORT": "8766"}, "8766 is the stage port"),
        ([], {"TV_USE_LISTEN": "localhost"}, "expected an IP address"),
        # The stage origin is always http://127.0.0.1:<port>.
        ([], {"TV_USE_STAGE_LISTEN": "::1"}, "choose one of 127.0.0.1, 0.0.0.0"),
        (["--stage-listen", "192.0.2.5"], {}, "choose one of 127.0.0.1, 0.0.0.0"),
        ([], {"TV_USE_TVS": "living-room.local"}, "expected an IPv4 address"),
        ([], {"TV_USE_TVS": "host=192.0.2.1,colour=red"}, "expected host=IP"),
        (["--tv", "host=192.0.2.1,host=192.0.2.2"], {}, "expected host=IP"),
        (["--tv", "name="], {}, "expected host=IP"),
        ([], {"TV_USE_PAGE_NETWORK_ALLOW": "a.local:99999"}, "Invalid page_network_allow entry"),
        (["--unknown"], {}, "unrecognized arguments"),
    ],
)
def test_invalid_settings_stop_with_a_message(argv, env, message, capsys):
    with pytest.raises(SystemExit) as exit:
        standalone(argv, **env)
    assert exit.value.code == 2
    assert message in capsys.readouterr().err


def test_token_is_required(capsys, tmp_path):
    with pytest.raises(SystemExit):
        settings(["--discover"], {})
    assert "A bearer token is required" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        settings(["--discover"], {"TV_USE_TOKEN": "   "})
    assert "A bearer token is required" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        settings(["--discover"], {"TV_USE_TOKEN": "short"})
    assert "at least 16 characters" in capsys.readouterr().err
    secret = tmp_path / "token"
    secret.write_text(TOKEN + "\n")
    assert settings(["--discover"], {"TV_USE_TOKEN_FILE": str(secret)}).token == TOKEN
    assert settings(["--discover", "--token-file", str(secret)], {}).token == TOKEN
    assert settings(["--discover", "--token", TOKEN], {}).token == TOKEN
    with pytest.raises(SystemExit):
        settings(["--discover"], {"TV_USE_TOKEN": TOKEN, "TV_USE_TOKEN_FILE": str(secret)})
    assert "not both" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        settings(["--discover"], {"TV_USE_TOKEN_FILE": str(tmp_path / "missing")})
    assert "Cannot read the token file" in capsys.readouterr().err


def test_insecure_local_needs_no_token_and_stays_on_loopback(capsys):
    plan = settings(["--insecure-local", "--discover"], {})
    assert (plan.token, plan.listen) == (None, "127.0.0.1")
    env = {"TV_USE_INSECURE_LOCAL": "1", "TV_USE_LISTEN": "::1", "TV_USE_DISCOVER": "1"}
    assert settings([], env).listen == "::1"
    for argv, env, message in [
        (["--insecure-local", "--listen", "0.0.0.0"], {}, "loopback address"),
        (["--insecure-local"], {"TV_USE_TOKEN": TOKEN}, "not both"),
    ]:
        with pytest.raises(SystemExit):
            settings(argv, env)
        assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, env, options_json, supervisor, expected, reason",
    [
        ([], {}, True, None, "ha", "options.json exists"),
        ([], {}, False, "supervisor-token", "ha", "Supervisor supplied a token"),
        ([], {}, True, "supervisor-token", "ha", "Supervisor supplied a token"),
        ([], {"TV_USE_MODE": "ha"}, False, None, "ha", "TV_USE_MODE=ha"),
        ([], {"TV_USE_MODE": "ha", "TV_USE_TOKEN": TOKEN}, True, None, "ha", "TV_USE_MODE=ha"),
        ([], {}, False, None, "standalone", "options.json does not exist"),
        ([], {"TV_USE_MODE": " "}, True, None, "ha", "options.json exists"),
        (["--standalone"], {}, True, "supervisor-token", "standalone", "--standalone"),
        ([], {"TV_USE_MODE": "Standalone"}, True, None, "standalone", "TV_USE_MODE=standalone"),
        # Without Supervisor, a standalone variable wins over a leftover options.json.
        ([], {"TV_USE_TOKEN": TOKEN}, True, None, "standalone", "TV_USE_TOKEN is set"),
        ([], {"TV_USE_TOKEN_FILE": "/run/token"}, True, None, "standalone", "TV_USE_TOKEN_FILE is set"),
        ([], {"TV_USE_TVS": "192.0.2.1"}, True, None, "standalone", "TV_USE_TVS is set"),
        # A stray standalone variable does not switch a Supervisor install to standalone.
        ([], {"TV_USE_TOKEN": TOKEN}, True, "supervisor-token", "ha", "Supervisor supplied a token"),
    ],
)
def test_mode_detection(tmp_path, argv, env, options_json, supervisor, expected, reason):
    if options_json:
        (tmp_path / "options.json").write_text("{}")
    chosen, why = mode(argv, env, tmp_path, supervisor)
    assert chosen == expected and reason in why


def test_mode_rejects_unknown_values_and_flags_in_home_assistant_mode(tmp_path):
    with pytest.raises(SystemExit, match="TV_USE_MODE must be"):
        mode([], {"TV_USE_MODE": "docker"}, tmp_path, None)
    (tmp_path / "options.json").write_text("{}")
    assert configure([], {}, tmp_path, None)[0] is None
    with pytest.raises(SystemExit, match="only in standalone mode"):
        configure(["--tv", "192.0.2.1"], {}, tmp_path, None)


def test_empty_tv_list_stops_the_container_unless_discovery_is_requested(capsys):
    for argv, env in [([], {}), ([], {"TV_USE_TVS": " ; "})]:
        with pytest.raises(SystemExit):
            settings(argv, {"TV_USE_TOKEN": TOKEN} | env)
        assert "No TVs are configured" in capsys.readouterr().err
    # A misspelled variable is the usual cause. The message names it.
    with pytest.raises(SystemExit):
        settings([], {"TV_USE_TOKEN": TOKEN, "TV_USE_TV": "192.0.2.1"})
    assert "These variables are not settings: TV_USE_TV." in capsys.readouterr().err
    assert settings(["--discover"], {"TV_USE_TOKEN": TOKEN}).options["apple_tvs"] == []
    assert settings([], {"TV_USE_TOKEN": TOKEN, "TV_USE_DISCOVER": "yes"}).options["apple_tvs"] == []


def test_unknown_variables_are_reported():
    plan = standalone(TV_USE_TVS="192.0.2.1", TV_USE_MIROR_FPS="30", TV_USE_MODE="standalone")
    assert plan.warnings == (
        "TV_USE_MIROR_FPS is not a tv-use setting and is ignored. Check its spelling. --help lists the settings.",
    )


def receivers(tmp_path, **kwargs):
    def factory(host, state, **options):
        adapter = FakeAdapter(state)
        adapter.host = adapter.resolver.address = host
        observer = AsyncMock()
        observer.call = AsyncMock(return_value={"focused": None, "app": None, "apps": []})
        return TVRuntime(host, state, adapter, observer, **options)

    options = DEFAULTS | {"apple_tvs": [{"name": "Living Room", "host": "192.0.2.15", "id": "AA:01"}]}
    return Fleet(tmp_path, options, factory=factory, stage_port=0, **kwargs)


async def test_standalone_token_replaces_the_internal_token(tmp_path, aiohttp_client):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    fleet = receivers(tmp_path)
    plan = standalone()
    client = await aiohttp_client(plan.listeners(fleet)[0][0])
    tool = "/api/tool/tv_status"
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer " + fleet.controller.token}):
        response = await client.post(tool, json={}, headers=headers)
        assert response.status == 401
        reply = await response.json()
        assert reply["error"]["code"] == "unauthorized"
        assert "TV_USE_TOKEN" in reply["error"]["remediation"]
    assert (await client.post("/mcp", json={}, headers={})).status == 401
    headers = {"Authorization": "Bearer " + TOKEN}
    reply = await (await client.post(tool, json={}, headers=headers)).json()
    assert reply["ok"] and reply["data"]["tv"]["name"] == "Living Room"
    async with (
        httpx2.AsyncClient(headers=headers) as http,
        streamable_http_client(str(client.make_url("/mcp")), http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        assert "tv_pair" in {tool.name for tool in (await session.list_tools()).tools}


def fake_runtime(host, state, **kwargs):
    adapter = FakeAdapter(state)
    adapter.host = adapter.resolver.address = host
    return TVRuntime(host, state, adapter, AsyncMock(), **kwargs)


def start_fleet(root, tvs="", argv=()):
    """Build the fleet as a container start with this TV_USE_TVS value and these flags would."""
    plan = standalone(argv, TV_USE_TVS=tvs)
    return Fleet(root, plan.options, factory=fake_runtime, stage_port=0, **plan.fleet)


def pair(fleet, name=None):
    rt = fleet.select(name) if name else next(iter(fleet.runtimes.values()))
    rt.state.save_pairings(airplay={"ltpk": "test"}, tv={"identifiers": ["AA:01"], "last_known_host": "192.0.2.15"})
    return rt.metadata["id"]


def paired(root, key):
    return json.loads((root / "tvs" / key / "pairings.json").read_text())["airplay"] == {"ltpk": "test"}


@pytest.mark.parametrize(
    "first, second, active",
    [
        # The identifier keeps the record when the address changes.
        ("host=192.0.2.15,id=AA:01", "host=192.0.2.99,id=AA:01", True),
        # Adding the identifier later keeps the record.
        ("host=192.0.2.15", "host=192.0.2.15,id=AA:01", True),
        # A typo in the address of a named TV reattaches the record by its name.
        ("name=Living Room,host=192.0.2.15", "name=Living Room,host=192.0.2.5", True),
        # The stored identifier reattaches a record that was listed by address.
        ("host=192.0.2.15", "id=aa:01", True),
        # Without an identifier or a name, a new address is a new TV. The old record is kept.
        ("host=192.0.2.15", "host=192.0.2.99", False),
        # Another TV's identifier never takes over the record.
        ("name=Living Room,host=192.0.2.15", "name=Living Room,id=BB:02", False),
    ],
)
def test_tv_list_changes_never_delete_pairings(tmp_path, first, second, active):
    key = pair(start_fleet(tmp_path, first))
    fleet = start_fleet(tmp_path, second)
    assert paired(tmp_path, key)
    assert (key in fleet.runtimes) is active
    assert (key in fleet.registry["kept"]) is not active
    assert len(fleet.runtimes) == 1


@pytest.mark.parametrize(
    "second",
    [
        # A start with no list, which needs --discover, keeps every TV.
        {"argv": ["--discover"]},
        # One --tv flag replaces the list from the variable.
        {"tvs": "name=Living Room,host=192.0.2.15", "argv": ["--tv", "name=Den,host=192.0.2.16"]},
        # A misspelled variable leaves the list empty.
        {"argv": ["--discover", "--tv", "192.0.2.30"]},
    ],
)
def test_a_tv_that_leaves_the_list_is_kept_and_returns(tmp_path, second, caplog):
    tvs = "name=Living Room,host=192.0.2.15;name=Den,host=192.0.2.16"
    first = start_fleet(tmp_path, tvs)
    keys = {pair(first, name) for name in ("Living Room", "Den")}
    with caplog.at_level(logging.WARNING):
        start_fleet(tmp_path, **second)
    assert all(paired(tmp_path, key) for key in keys)
    assert "Kept the records of TV Living Room" in caplog.text
    returned = start_fleet(tmp_path, tvs)
    assert set(returned.runtimes) == keys and not keys & set(returned.registry["kept"])
    assert {returned.select(name).state.receiver("airplay")["ltpk"] for name in ("Living Room", "Den")} == {"test"}


def test_two_containers_on_one_volume_keep_each_others_tvs(tmp_path):
    living = pair(start_fleet(tmp_path, "name=Living Room,host=192.0.2.15"))
    den = pair(start_fleet(tmp_path, "name=Den,host=192.0.2.16"))
    assert paired(tmp_path, living) and paired(tmp_path, den)
    assert set(start_fleet(tmp_path, "name=Living Room,host=192.0.2.15").runtimes) == {living}
    assert set(start_fleet(tmp_path, "name=Den,host=192.0.2.16").runtimes) == {den}


async def test_explicit_removal_still_deletes_the_pairings(tmp_path):
    fleet = start_fleet(tmp_path, "name=Living Room,host=192.0.2.15")
    key = pair(fleet)
    await fleet.remove(key)
    assert not (tmp_path / "tvs" / key).exists()
    # The listed TV stays suppressed, as in Home Assistant, until it is enrolled again.
    assert start_fleet(tmp_path, "name=Living Room,host=192.0.2.15").runtimes == {}
    # A kept TV can be deleted too. A TV with its name is then a new TV.
    den = pair(start_fleet(tmp_path, "name=Den,host=192.0.2.16"))
    fleet = start_fleet(tmp_path, "name=Office,host=192.0.2.17")
    assert den in fleet.kept
    await fleet.remove(den)
    assert not (tmp_path / "tvs" / den).exists() and den not in fleet.registry["kept"]
    assert fleet.register({"name": "Den", "host": "192.0.2.18"}).metadata["id"] != den


def test_home_assistant_still_deletes_a_tv_that_leaves_the_options(tmp_path):
    def start(tvs):
        return Fleet(tmp_path, DEFAULTS | {"apple_tvs": tvs}, factory=fake_runtime, stage_port=0)

    key = pair(start([{"name": "Living Room", "host": "192.0.2.15"}]))
    fleet = start([{"name": "Living Room", "host": "192.0.2.5"}])
    assert not (tmp_path / "tvs" / key).exists() and "kept" not in fleet.registry


async def test_home_assistant_app_still_accepts_only_the_internal_token(tmp_path, aiohttp_client):
    fleet = receivers(tmp_path)
    client = await aiohttp_client(fleet.app())
    response = await client.post("/api/tool/tv_status", json={}, headers={"Authorization": "Bearer " + TOKEN})
    reply = await response.json()
    assert response.status == 401
    assert reply["error"]["remediation"] == "Use the token from Supervisor discovery."
    headers = {"Authorization": "Bearer " + fleet.controller.token}
    assert (await (await client.post("/api/tool/tv_status", json={}, headers=headers)).json())["ok"]


async def test_insecure_local_accepts_only_loopback_host_names(tmp_path, aiohttp_client):
    fleet = receivers(tmp_path)
    plan = settings(["--insecure-local", "--discover"], {})
    client = await aiohttp_client(plan.listeners(fleet)[0][0])
    for host in ("localhost:8765", "127.0.0.1:8765", "[::1]:8765", "localhost"):
        reply = await (await client.post("/api/tool/tv_status", json={}, headers={"Host": host})).json()
        assert reply["ok"], host
    for host in ("attacker.example:8765", "192.0.2.64:8765", "localhost.attacker.example"):
        response = await client.post("/api/tool/tv_status", json={}, headers={"Host": host})
        assert response.status == 401, host


EVIL = "https://evil.example"


def zipped():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("index.html", "<h1>page</h1>")
    return out.getvalue()


def routes(app, key):
    """Every route of the API, with its path parameters filled in."""
    for resource in app.router.resources():
        info = resource.get_info()
        path = (info.get("path") or info["formatter"]).format(tv=key, name="tv_status")
        for route in resource:
            if route.method != "HEAD":
                yield route.method, path


def body_type(path):
    return "application/zip" if path == "/api/bundle" else "application/json"


async def api(tmp_path, aiohttp_client, argv):
    fleet = receivers(tmp_path)
    client = await aiohttp_client(settings(argv + ["--discover"], {}).listeners(fleet)[0][0])
    return client, list(routes(client.server.app, next(iter(fleet.runtimes))))


async def test_every_route_needs_the_token_and_refuses_other_sites(tmp_path, aiohttp_client):
    client, table = await api(tmp_path, aiohttp_client, ["--token", TOKEN])
    assert {path for _, path in table} >= {"/mcp", "/api/tool/tv_status", "/api/bundle", "/api/events",
                                           "/api/tvs", "/api/health"}
    good = {"Authorization": "Bearer " + TOKEN}
    for method, path in table:
        typed = {"Content-Type": body_type(path)} if method == "POST" else {}
        for headers, status in [
            ({}, 401),
            ({"Authorization": "Bearer wrong-token-0123456789"}, 401),
            # The internal token from the volume does not work in standalone mode.
            ({"Authorization": "Bearer " + receivers(tmp_path).controller.token}, 401),
            (good | {"Origin": EVIL}, 403),
            # Sandboxed pages, including the bridge's own bundles, send Origin: null.
            (good | {"Origin": "null"}, 403),
            (good | {"Origin": "http://127.0.0.1.evil.example"}, 403),
        ]:
            response = await client.request(method, path, data=b"{}", headers=typed | headers)
            assert response.status == status, (method, path, headers)
            assert (await response.json())["ok"] is False
        if method == "POST":
            for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None):
                headers = good | ({"Content-Type": content_type} if content_type else {})
                response = await client.request(method, path, data=b"{}", headers=headers)
                assert response.status == 415, (method, path, content_type)
                assert (await response.json())["error"]["code"] == "unsupported_media_type"


async def test_insecure_local_refuses_other_sites_on_every_route(tmp_path, aiohttp_client):
    client, table = await api(tmp_path, aiohttp_client, ["--insecure-local"])
    local = {"Host": "127.0.0.1:8765"}
    for method, path in table:
        typed = {"Content-Type": body_type(path)} if method == "POST" else {}
        for headers, status in [
            ({"Host": "attacker.example:8765"}, 401),
            (local | {"Origin": EVIL}, 403),
            (local | {"Origin": "null"}, 403),
        ]:
            response = await client.request(method, path, data=b"{}", headers=typed | headers)
            assert response.status == status, (method, path, headers)
        if method == "POST":
            response = await client.request(method, path, data=b"{}", headers=local | {"Content-Type": "text/plain"})
            assert response.status == 415, (method, path)


@pytest.mark.parametrize("argv", [["--token", TOKEN], ["--insecure-local"]])
async def test_programs_and_same_origin_pages_are_accepted(tmp_path, aiohttp_client, argv):
    client, _ = await api(tmp_path, aiohttp_client, argv)
    headers = {"Host": "127.0.0.1:8765"} | ({"Authorization": "Bearer " + TOKEN} if "--token" in argv else {})
    for origin in (None, "http://127.0.0.1:8765", "http://localhost:3000", "http://[::1]:8765"):
        sent = headers | ({"Origin": origin} if origin else {})
        reply = await (await client.post("/api/tool/tv_status", json={}, headers=sent)).json()
        assert reply["ok"], origin
        tvs = await client.get("/api/tvs", headers=sent)
        assert tvs.status == 200 and (await tvs.json())["tvs"][0]["name"] == "Living Room"
        health = await client.get("/api/health", headers=sent)
        assert health.status == 200 and "pending_pairing" in await health.json()
    bundle = await client.post("/api/bundle", data=zipped(), headers=headers | {"Content-Type": "application/zip"})
    assert re.fullmatch("[0-9a-f]{32}", (await bundle.json())["bundle"])
    socket = await client.ws_connect("/api/events", headers=headers | {"Origin": "http://localhost:8765"})
    await socket.close()


@pytest.mark.parametrize("argv", [["--token", TOKEN], ["--insecure-local"]])
async def test_event_socket_refuses_other_sites(tmp_path, aiohttp_client, argv):
    client, _ = await api(tmp_path, aiohttp_client, argv)
    headers = {"Host": "127.0.0.1:8765"} | ({"Authorization": "Bearer " + TOKEN} if "--token" in argv else {})
    for path in ("/api/events", f"/api/tvs/{receivers(tmp_path).select().metadata['id']}/events"):
        for sent, status in [(headers | {"Origin": EVIL}, 403), (headers | {"Origin": "null"}, 403)]:
            with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
                await client.ws_connect(path, headers=sent)
            assert refused.value.status == status
    if "--token" in argv:
        with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
            await client.ws_connect("/api/events", headers={"Origin": "http://127.0.0.1:8765"})
        assert refused.value.status == 401


@pytest.mark.parametrize("stage_host", ["127.0.0.1", None])
async def test_stage_listener_address(tmp_path, stage_host):
    fleet = receivers(tmp_path, **({"stage_host": stage_host} if stage_host else {}))
    await fleet.start()
    try:
        assert fleet.runners[fleet.primary].addresses[0][0] == (stage_host or "0.0.0.0")
    finally:
        await fleet.close()


async def test_stage_requires_the_process_key_in_standalone_mode(tmp_path, aiohttp_client):
    stage = Stage(tmp_path)
    stage.key = "k" * 32
    client = await aiohttp_client(stage.app())
    stage.origin = f"http://127.0.0.1:{client.port}"
    assert stage.url == stage.origin + "/stage?key=" + "k" * 32
    for query in ("", "?key=wrong", "?key="):
        assert (await client.get("/stage" + query)).status == 403
        with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
            await client.ws_connect("/stage/ws" + query, headers={"Origin": stage.origin})
        assert refused.value.status == 403
    page = await client.get("/stage?key=" + "k" * 32)
    assert page.status == 200
    # No other page can frame the stage, and the stage's pages cannot read the key from Referer.
    assert page.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert "location.search" in await page.text()
    await stage.show("a" * 32, "/c/" + "a" * 32 + "/index.html")
    # The key alone is not enough. The socket still needs the stage's own Origin.
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await client.ws_connect("/stage/ws?key=" + "k" * 32, headers={"Origin": EVIL})
    socket = await client.ws_connect("/stage/ws?key=" + "k" * 32, headers={"Origin": stage.origin})
    assert (await socket.receive_json())["page_id"] == "a" * 32
    await socket.close()


async def test_home_assistant_stage_is_unchanged(tmp_path, aiohttp_client):
    stage = Stage(tmp_path)
    assert stage.key is None and stage.url == "http://127.0.0.1:8766/stage"
    client = await aiohttp_client(stage.app())
    page = await client.get("/stage")
    assert page.status == 200 and "Content-Security-Policy" not in page.headers


async def test_fleet_gives_every_stage_the_key(tmp_path):
    fleet = Fleet(tmp_path, DEFAULTS | {"apple_tvs": [{"name": "Living Room", "host": "192.0.2.15"},
                                                     {"name": "Den", "host": "192.0.2.16"}]},
                  factory=fake_runtime, stage_port=0, stage_key="k" * 32)
    await fleet.start()
    try:
        for rt in (fleet.primary, *fleet.runtimes.values()):
            assert rt.stage.key == "k" * 32
            assert rt.operations.live_mirror.stage_url == rt.stage.origin + "/stage?key=" + "k" * 32
        async with aiohttp.ClientSession() as http:
            for rt in fleet.runtimes.values():
                async with http.get(rt.stage.origin + "/stage") as refused:
                    assert refused.status == 403
                async with http.get(rt.stage.url) as page:
                    assert page.status == 200
    finally:
        await fleet.close()
    home = Fleet(tmp_path / "ha", DEFAULTS | {"apple_tvs": [{"host": "192.0.2.15"}]}, factory=fake_runtime, stage_port=0)
    await home.start()
    try:
        assert home.select().operations.live_mirror.stage_url == home.primary.stage.origin + "/stage"
    finally:
        await home.close()


def test_child_processes_never_inherit_the_tokens(monkeypatch):
    monkeypatch.setenv("TV_USE_TOKEN", TOKEN)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-secret")
    monkeypatch.setenv("TV_USE_TVS", "192.0.2.15")
    env = processes.child_env(EXTRA="1")
    assert "TV_USE_TOKEN" not in env and "SUPERVISOR_TOKEN" not in env
    assert env["TV_USE_TVS"] == "192.0.2.15" and env["EXTRA"] == "1"


def ha_strings():
    """Remediation text in the app that names Home Assistant or gives an `ha` command."""
    naming = re.compile(r"Home Assistant|\bHA\b|\bha [a-z]+ ")
    found = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            values = []
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Fault" and len(node.args) > 2:
                values.append(node.args[2])
            if isinstance(node, ast.Dict):
                values += [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == "fix"]
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "").endswith("_FIX") or getattr(t, "id", "") == "fix"
                                                    for t in node.targets):
                values.append(node.value)
            for value in values:
                for text in ast.walk(value):
                    if isinstance(text, ast.Constant) and isinstance(text.value, str) and naming.search(text.value):
                        found.add(text.value)
    return found


def test_standalone_rewords_every_home_assistant_fix():
    found = ha_strings()
    assert "Pair AirPlay in Home Assistant." in found and len(found) >= 10
    assert found <= set(REMEDIATION)
    for text in REMEDIATION.values():
        assert not re.search(r"Home Assistant|\bHA\b|\bha [a-z]+ |Configure", text), text


async def test_standalone_fixes_name_no_home_assistant_steps(tmp_path, monkeypatch):
    from atv_bridge_app import live

    rt = fake_runtime("192.0.2.15", state.State(tmp_path))
    rt.stage.dns_status = {"hostname": "box.tail1234.ts.net", "resolved": False}
    monkeypatch.setattr(live, "resolve_tailnet", AsyncMock(return_value=False))
    monkeypatch.setattr("atv_bridge_app.tools.resolve_tailnet", AsyncMock(return_value=False))
    monkeypatch.setattr("atv_bridge_app.tools.tailnet_dns", AsyncMock(return_value={"ok": False}))
    home = await rt.tools.tv_status()
    assert any(issue.get("fix") == live.TAILNET_DNS_FIX for issue in home["data"]["problems"])
    assert Fault("pairing_required", "AirPlay is unpaired.", "Pair AirPlay in Home Assistant.").remediation == (
        "Pair AirPlay in Home Assistant.")
    monkeypatch.setattr(state, "REWORDING", dict(REMEDIATION))
    fixes = [issue.get("fix", "") for issue in (await rt.tools.tv_status())["data"]["problems"]]
    health = await rt.operations.tv_health()
    fixes += [issue["fix"] for issue in health["problems"]]
    assert fixes and not any("Home Assistant" in fix or "ha dns" in fix for fix in fixes), fixes
    assert REMEDIATION[live.TAILNET_DNS_FIX] in fixes
    fault = Fault("pairing_required", "AirPlay is unpaired.", "Pair AirPlay in Home Assistant.")
    assert fault.remediation == 'Run tv_pair with kind "control" for this TV.'
    # The DNS failure check still recognises the reworded tailnet fix.
    assert live.page_failure("box.tail1234.ts.net", OSError("ERR_NAME_NOT_RESOLVED")).remediation == (
        REMEDIATION[live.TAILNET_DNS_FIX])


@pytest.fixture
def run_main(monkeypatch):
    """Run __main__.main with a recording Fleet and listener, then stop it with SIGTERM."""
    calls = {"binds": []}

    class RecordingFleet:
        def __init__(self, root, options, **kwargs):
            calls.update(root=root, options=options, kwargs=kwargs)

        def app(self, authorize=None):
            calls["authorize"] = authorize
            return web.Application()

    class RecordingSite:
        def __init__(self, runner, address, port):
            self.bind = (address, port)

        async def start(self):
            calls["binds"].append(self.bind)
            os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(entry, "Fleet", RecordingFleet)
    monkeypatch.setattr(web, "TCPSite", RecordingSite)
    monkeypatch.setattr(processes, "_supervisor_token", None)
    umask = os.umask(0o022)
    os.umask(umask)
    wording = dict(state.REWORDING)

    async def run(root, argv=()):
        monkeypatch.setenv("ATV_BRIDGE_DATA", str(root))
        try:
            await entry.main(list(argv))
        finally:
            os.umask(umask)
        return calls

    yield run
    state.REWORDING.clear()
    state.REWORDING.update(wording)


async def test_home_assistant_mode_reads_options_json_as_before(tmp_path, run_main, monkeypatch):
    options = {"apple_tvs": [{"name": "Den", "host": "192.0.2.30"}], "mirror_fps": 12,
               "log_level": "info", "unknown_future_option": {"kept": True}}
    (tmp_path / "options.json").write_text(json.dumps(options))
    for name in [n for n in os.environ if n.startswith("TV_USE_")]:
        monkeypatch.delenv(name)
    calls = await run_main(tmp_path)
    assert calls["options"] == options
    assert calls["kwargs"] == {}
    assert calls["authorize"] is None
    assert calls["binds"] == [("127.0.0.1", 8765), ("127.0.0.1", 8099)]
    assert json.loads((tmp_path / "options.json").read_text()) == options
    assert state.REWORDING == {}


async def test_home_assistant_mode_under_supervisor(tmp_path, run_main, monkeypatch):
    (tmp_path / "options.json").write_text(json.dumps({"apple_tvs": []}))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-secret")
    # A stray standalone variable does not switch a Supervisor install to standalone.
    monkeypatch.setenv("TV_USE_TOKEN", TOKEN)
    calls = await run_main(tmp_path)
    assert calls["options"] == {"apple_tvs": []}
    assert calls["binds"] == [("127.0.0.1", 8765), ("172.30.32.1", 8099)]
    assert "SUPERVISOR_TOKEN" not in os.environ
    assert processes.supervisor_token() == "supervisor-secret"


async def test_standalone_main_uses_environment_and_one_listener(tmp_path, run_main, monkeypatch, caplog):
    monkeypatch.setenv("TV_USE_TOKEN", TOKEN)
    monkeypatch.setenv("TV_USE_TVS", "host=192.0.2.15,id=AA:01")
    monkeypatch.setenv("TV_USE_MIRROR_FSP", "30")
    caplog.set_level(logging.INFO)
    calls = await run_main(tmp_path, ["--mirror-fps", "30", "--port", "9200"])
    assert calls["options"] == DEFAULTS | {"apple_tvs": [{"host": "192.0.2.15", "id": "AA:01"}], "mirror_fps": 30}
    assert calls["kwargs"] == {"stage_host": "127.0.0.1", "stage_key": calls["kwargs"]["stage_key"],
                               "keep_unlisted": True}
    assert callable(calls["authorize"])
    assert calls["binds"] == [("0.0.0.0", 9200)]
    assert not (tmp_path / "options.json").exists()
    assert "Starting in standalone mode because TV_USE_MIRROR_FSP is set" in caplog.text
    assert any(r.levelno == logging.WARNING and "TV_USE_MIRROR_FSP is not a tv-use setting" in r.message
               for r in caplog.records)
    # Playwright's driver and other children inherit os.environ.
    assert "TV_USE_TOKEN" not in os.environ
    assert state.REWORDING == REMEDIATION


async def test_standalone_flag_ignores_a_stray_supervisor_token(tmp_path, run_main, monkeypatch, caplog):
    (tmp_path / "options.json").write_text(json.dumps({"apple_tvs": []}))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-secret")
    monkeypatch.setenv("TV_USE_TOKEN", TOKEN)
    caplog.set_level(logging.INFO)
    calls = await run_main(tmp_path, ["--standalone", "--tv", "192.0.2.15"])
    assert calls["binds"] == [("0.0.0.0", 8765)]
    # Supervisor discovery and the Supervisor DNS lookup both read this.
    assert processes.supervisor_token() is None and "SUPERVISOR_TOKEN" not in os.environ
    assert "Starting in standalone mode because the --standalone flag" in caplog.text
    assert "Ignoring SUPERVISOR_TOKEN" in caplog.text


async def test_standalone_never_announces_to_supervisor(tmp_path, aiohttp_client, monkeypatch):
    from atv_bridge_app import server

    monkeypatch.setattr(processes, "_supervisor_token", "stray")
    processes.forget_supervisor_token()
    post = AsyncMock()
    monkeypatch.setattr(server.ClientSession, "post", post)
    await server.discover("token")
    post.assert_not_called()
