import json
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from aiohttp import web

from atv_bridge_app import __main__ as entry
from atv_bridge_app import processes
from atv_bridge_app.fleet import Fleet
from atv_bridge_app.server import TVRuntime
from atv_bridge_app.standalone import DEFAULTS, configure, mode, settings
from conftest import FakeAdapter

TOKEN = "standalone-test-token-0123456789"
CONFIG = Path(__file__).parents[1] / "config.yaml"


def standalone(argv=(), **env):
    return settings(list(argv), {"TV_USE_TOKEN": TOKEN} | env)


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
    assert plan.fleet == {"stage_host": "0.0.0.0"}


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
        settings([], {})
    assert "A bearer token is required" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        settings([], {"TV_USE_TOKEN": "   "})
    assert "A bearer token is required" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        settings([], {"TV_USE_TOKEN": "short"})
    assert "at least 16 characters" in capsys.readouterr().err
    secret = tmp_path / "token"
    secret.write_text(TOKEN + "\n")
    assert settings([], {"TV_USE_TOKEN_FILE": str(secret)}).token == TOKEN
    assert settings(["--token-file", str(secret)], {}).token == TOKEN
    assert settings(["--token", TOKEN], {}).token == TOKEN
    with pytest.raises(SystemExit):
        settings([], {"TV_USE_TOKEN": TOKEN, "TV_USE_TOKEN_FILE": str(secret)})
    assert "not both" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        settings([], {"TV_USE_TOKEN_FILE": str(tmp_path / "missing")})
    assert "Cannot read the token file" in capsys.readouterr().err


def test_insecure_local_needs_no_token_and_stays_on_loopback(capsys):
    plan = settings(["--insecure-local"], {})
    assert (plan.token, plan.listen) == (None, "127.0.0.1")
    assert settings([], {"TV_USE_INSECURE_LOCAL": "1", "TV_USE_LISTEN": "::1"}).listen == "::1"
    for argv, env, message in [
        (["--insecure-local", "--listen", "0.0.0.0"], {}, "loopback address"),
        (["--insecure-local"], {"TV_USE_TOKEN": TOKEN}, "not both"),
    ]:
        with pytest.raises(SystemExit):
            settings(argv, env)
        assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, env, options_json, supervisor, expected",
    [
        ([], {}, True, None, "ha"),
        ([], {}, False, "supervisor-token", "ha"),
        ([], {}, True, "supervisor-token", "ha"),
        ([], {"TV_USE_MODE": "ha"}, False, None, "ha"),
        ([], {}, False, None, "standalone"),
        (["--standalone"], {}, True, "supervisor-token", "standalone"),
        ([], {"TV_USE_MODE": "Standalone"}, True, None, "standalone"),
    ],
)
def test_mode_detection(tmp_path, argv, env, options_json, supervisor, expected):
    if options_json:
        (tmp_path / "options.json").write_text("{}")
    assert mode(argv, env, tmp_path, supervisor) == expected


def test_mode_rejects_unknown_values_and_flags_in_home_assistant_mode(tmp_path):
    with pytest.raises(SystemExit, match="TV_USE_MODE must be"):
        mode([], {"TV_USE_MODE": "docker"}, tmp_path, None)
    (tmp_path / "options.json").write_text("{}")
    assert configure([], {}, tmp_path, None) is None
    with pytest.raises(SystemExit, match="only in standalone mode"):
        configure(["--tv", "192.0.2.1"], {}, tmp_path, None)


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
    plan = settings(["--insecure-local"], {})
    client = await aiohttp_client(plan.listeners(fleet)[0][0])
    for host in ("localhost:8765", "127.0.0.1:8765", "[::1]:8765", "localhost"):
        reply = await (await client.post("/api/tool/tv_status", json={}, headers={"Host": host})).json()
        assert reply["ok"], host
    for host in ("attacker.example:8765", "192.0.2.64:8765", "localhost.attacker.example"):
        response = await client.post("/api/tool/tv_status", json={}, headers={"Host": host})
        assert response.status == 401, host


@pytest.mark.parametrize("stage_host", ["127.0.0.1", None])
async def test_stage_listener_address(tmp_path, stage_host):
    fleet = receivers(tmp_path, **({"stage_host": stage_host} if stage_host else {}))
    await fleet.start()
    try:
        assert fleet.runners[fleet.primary].addresses[0][0] == (stage_host or "0.0.0.0")
    finally:
        await fleet.close()


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

    async def run(root, argv=()):
        monkeypatch.setenv("ATV_BRIDGE_DATA", str(root))
        try:
            await entry.main(list(argv))
        finally:
            os.umask(umask)
        return calls

    return run


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


async def test_standalone_main_uses_environment_and_one_listener(tmp_path, run_main, monkeypatch):
    monkeypatch.setenv("TV_USE_TOKEN", TOKEN)
    monkeypatch.setenv("TV_USE_TVS", "host=192.0.2.15,id=AA:01")
    calls = await run_main(tmp_path, ["--mirror-fps", "30", "--port", "9200"])
    assert calls["options"] == DEFAULTS | {"apple_tvs": [{"host": "192.0.2.15", "id": "AA:01"}], "mirror_fps": 30}
    assert calls["kwargs"] == {"stage_host": "127.0.0.1"}
    assert callable(calls["authorize"])
    assert calls["binds"] == [("0.0.0.0", 9200)]
    assert not (tmp_path / "options.json").exists()
