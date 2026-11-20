import asyncio
import shutil
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import aiohttp
import deploy
import pytest
from aiohttp import web
from yarl import URL


def args(*items):
    return deploy.parser().parse_args(items)


@asynccontextmanager
async def server(app):
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield URL(f"http://127.0.0.1:{runner.addresses[0][1]}")
    finally:
        await runner.cleanup()


def test_check_over_mock_http_and_ws(capsys):
    async def scenario():
        calls = []
        token = "test-private-HA-token"
        apps = [
            {"slug": slug, "version": "1", "state": "started", "name": slug}
            for slug in [deploy.SSH, "a0d7b954_tailscale"]
        ]
        results = {
            "/supervisor/info": {"version": "2026.11.0", "arch": "aarch64"},
            "/addons": {"addons": apps},
            "/addons/core_ssh/info": {"network": {"22/tcp": 2222}, "protected": True},
            "/addons/a0d7b954_tailscale/info": {
                "options": {
                    "accept_routes": True,
                    "auth_key": "tskey-secret",
                    "services": [{"path": "private-path"}],
                    "login_server": "https://someone:private-login@control.example/a?private-query",
                    "taildrive": {"config": False},
                }
            },
            "/host/info": {"disk_free": 8.1},
            "/network/info": {
                "interfaces": [
                    {
                        "interface": "eth0",
                        "ipv4": {"address": ["192.0.2.1/24"]},
                        "wifi": {"psk": "wifi-secret"},
                    }
                ]
            },
        }

        async def http(request):
            assert request.headers["Authorization"] == f"Bearer {token}"
            calls.append("/api/")
            return web.json_response({"message": "API running."})

        async def ws(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.send_json({"type": "auth_required"})
            assert await socket.receive_json() == {
                "type": "auth",
                "access_token": token,
            }
            await socket.send_json({"type": "auth_ok", "ha_version": "2026.11.2"})
            msg = await socket.receive_json()
            assert msg["type"] == "supervisor/api" and msg["method"] == "get"
            assert "data" not in msg
            calls.append(msg["endpoint"])
            await socket.send_json(
                {
                    "id": msg["id"],
                    "type": "result",
                    "success": True,
                    "result": results[msg["endpoint"]],
                }
            )
            await socket.close()
            return socket

        app = web.Application()
        app.add_routes([web.get("/api/", http), web.get("/api/websocket", ws)])
        async with server(app) as origin, aiohttp.ClientSession() as session:
            ha = deploy.HA(session, str(origin), token)
            await deploy.check(ha, True)
            assert calls == ["/api/", "/supervisor/info", "/addons"]
            await deploy.check(ha, False)
        output = capsys.readouterr().out
        for expected in [
            "2026.11.2",
            "2026.11.0",
            "8.1",
            "2222",
            "eth0",
            '"accept_routes": true',
            '"config": false',
            "control.example",
            "[redacted]",
        ]:
            assert expected in output
        for secret in [
            token,
            "tskey-secret",
            "private-path",
            "private-login",
            "private-query",
            "wifi-secret",
        ]:
            assert secret not in output

    asyncio.run(scenario())


def test_http_logs_and_watchdog_credentials(capsys):
    async def scenario():
        requests = []

        async def handle(request):
            requests.append((request.path, request.headers.get("Authorization")))
            if request.path.endswith("/logs"):
                return web.Response(text="first\nsecret-token\n")
            return web.json_response({"ok": True, "state": "running"})

        app = web.Application()
        app.router.add_get("/{tail:.*}", handle)
        async with server(app) as origin, aiohttp.ClientSession() as session:
            ha = deploy.HA(session, str(origin), "secret-token")
            await ha.logs(False)
            # Redirect the watchdog to this test server while checking the exact requested origin.
            get = ha.get_json

            async def health_get(url, *, auth=False):
                assert url == origin.with_port(8766) / "api/health" and not auth
                return await get(origin / "api/health", auth=auth)

            ha.get_json = health_get
            assert await ha.health()
        assert requests == [
            ("/api/hassio/addons/local_atv_bridge/logs", "Bearer secret-token"),
            ("/api/health", None),
        ]
        output = capsys.readouterr().out
        assert "secret-token" not in output and "[redacted]" in output

    asyncio.run(scenario())


def test_token_file(tmp_path):
    f = tmp_path / "ha_token"
    with pytest.raises(deploy.DeployError, match="Missing admin"):
        deploy.read_token(f)
    f.write_text("private\n")
    f.chmod(0o644)
    with pytest.raises(deploy.DeployError, match="mode 600"):
        deploy.read_token(f)
    f.chmod(0o600)
    assert deploy.read_token(f) == "private"


def test_dedicated_key_generation_is_idempotent(tmp_path):
    key = deploy.key_path(tmp_path)
    assert key.name == "atv_bridge_ha"
    first = asyncio.run(deploy.public_key(Mock(), key))
    assert first.startswith("ssh-ed25519 ")
    assert asyncio.run(deploy.public_key(Mock(), key)) == first
    key.rename(tmp_path / "id_ed25519")
    key.with_suffix(".pub").rename(tmp_path / "id_ed25519.pub")
    assert deploy.key_path(tmp_path).name == "id_ed25519"


def test_ssh_setup_preserves_other_keys_then_runs_without_writes(monkeypatch):
    ha = Mock()
    ha.apps = AsyncMock(return_value=[])
    info = {
        "state": "stopped",
        "options": {
            "authorized_keys": ["ssh-ed25519 other"],
            "server": {"tcp_forwarding": True},
            "apks": ["vim"],
        },
        "network": {"22/tcp": None},
    }
    ha.info = AsyncMock(return_value=info)
    ha.sup = AsyncMock()
    remote = Mock(shell=AsyncMock(return_value=(0, "", "")))
    monkeypatch.setattr(
        deploy, "public_key", AsyncMock(return_value="ssh-ed25519 new deploy")
    )
    monkeypatch.setattr(deploy, "choose_port", AsyncMock(return_value=2223))
    monkeypatch.setattr(deploy, "Remote", Mock(return_value=remote))
    asyncio.run(deploy.ssh_setup(ha))
    install, options, start = ha.sup.await_args_list
    assert install.args == ("/store/addons/core_ssh/install", "post")
    assert start.args == ("/addons/core_ssh/start", "post")
    assert options.kwargs["data"] == {
        "options": {
            "authorized_keys": ["ssh-ed25519 other", "ssh-ed25519 new deploy"],
            "server": {"tcp_forwarding": False},
            "apks": ["vim"],
        },
        "network": {"22/tcp": 2223},
    }
    info.update(state="started", **options.kwargs["data"])
    ha.apps.return_value = [{"slug": deploy.SSH}]
    ha.sup.reset_mock()
    asyncio.run(deploy.ssh_setup(ha))
    ha.sup.assert_not_called()


def test_choose_port_requires_refusal_on_every_address(monkeypatch):
    ha = Mock(base=URL("http://localhost"))
    refused = ConnectionRefusedError()
    probe = AsyncMock(side_effect=[
        TimeoutError(), ExceptionGroup("mixed", [refused, TimeoutError()]),
        OSError("no route"), ExceptionGroup("refused", [refused, refused]),
    ])
    monkeypatch.setattr(asyncio, "open_connection", probe)
    assert asyncio.run(deploy.choose_port(ha, [], {"state": "stopped"})) == 2225
    assert all(call.kwargs["all_errors"] for call in probe.await_args_list)


def test_choose_port_skips_reserved_and_listening(monkeypatch):
    ha = Mock(base=URL("http://localhost"))
    ha.info = AsyncMock(return_value={"network": {"500/tcp": 2222}})
    writer = Mock(wait_closed=AsyncMock())
    probe = AsyncMock(side_effect=[(None, writer), ConnectionRefusedError()])
    monkeypatch.setattr(asyncio, "open_connection", probe)
    assert (
        asyncio.run(deploy.choose_port(ha, [{"slug": "other"}], {"state": "stopped"}))
        == 2224
    )
    assert [call.args[1] for call in probe.await_args_list] == [2223, 2224]
    writer.close.assert_called_once()


@pytest.mark.parametrize(
    "existing, version, expected",
    [
        (False, None, "/store/addons/local_atv_bridge/install"),
        (True, "1", "/addons/local_atv_bridge/rebuild"),
        (True, "2", "/store/addons/local_atv_bridge/update"),
    ],
)
def test_push_app_stages_then_builds_with_deadline(
    tmp_path, monkeypatch, existing, version, expected
):
    events = []
    ha = Mock(base=URL("http://localhost"))
    installed = (
        [{"slug": deploy.APP, "version": "1", "version_latest": version}]
        if existing
        else []
    )
    ha.apps = AsyncMock(return_value=installed)
    ha.info = AsyncMock(
        return_value={
            "options": {"apple_tv_host": "192.0.2.1", "log_level": "info"},
            "state": "started" if existing else "stopped",
        }
    )
    ha.sup = AsyncMock(side_effect=lambda path, *a, **kw: events.append(path))
    remote = Mock(copy_tree=AsyncMock(side_effect=lambda *a: events.append("copy")))
    monkeypatch.setattr(deploy, "remote_for_push", AsyncMock(return_value=remote))
    monkeypatch.setattr(deploy, "ROOT", tmp_path)

    async def stage(script):
        assert script == tmp_path / "app/build/stage-context.sh"
        fork = tmp_path / "app/vendor/pyatv/pyproject.toml"
        fork.parent.mkdir(parents=True)
        fork.touch()
        events.append("stage")

    monkeypatch.setattr(deploy, "run", stage)
    monkeypatch.setattr(
        deploy, "wait_app", AsyncMock(side_effect=lambda *_: events.append("health"))
    )
    asyncio.run(deploy.push_app(ha, args("push-app", "--log-level", "debug")))
    action = "restart" if existing else "start"
    assert events == [
        "stage",
        "copy",
        "/store/reload",
        expected,
        f"/addons/{deploy.APP}/options",
        f"/addons/{deploy.APP}/{action}",
        "health",
    ]
    assert ha.sup.await_args_list[1].kwargs["timeout"] == 900
    assert ha.sup.await_args_list[2].kwargs["data"]["options"] == {
        "apple_tv_host": "192.0.2.1",
        "log_level": "debug",
    }


def test_wait_app_keeps_streaming_until_watchdog(monkeypatch):
    events = []
    ha = Mock(base=URL("http://localhost"))
    ha.health = AsyncMock(side_effect=[False, True])
    sleep = asyncio.sleep

    async def stream(follow):
        assert follow
        events.append("open")
        try:
            await asyncio.Future()
        finally:
            events.append("close")

    ha.logs = stream
    monkeypatch.setattr(asyncio, "sleep", lambda _: sleep(0))
    asyncio.run(deploy.wait_app(ha))
    assert events == ["open", "close"]


@pytest.mark.parametrize("check_ok", [True, False])
def test_integration_checks_before_restart(monkeypatch, check_ok):
    ha = Mock(base=URL("http://localhost"))
    ha.sup = AsyncMock(
        side_effect=[{}, deploy.LostReply("closed")]
        if check_ok
        else [deploy.DeployError("invalid configuration")]
    )
    remote = Mock(copy_tree=AsyncMock())
    monkeypatch.setattr(deploy, "remote_for_push", AsyncMock(return_value=remote))
    ha.get_json = AsyncMock(side_effect=[{"state": "STARTING"}, {"state": "RUNNING"}])
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    if check_ok:
        asyncio.run(deploy.push_integration(ha))
        assert [c.args for c in ha.sup.await_args_list] == [
            ("/homeassistant/check", "post"),
            ("/core/restart", "post"),
        ]
        assert ha.get_json.await_count == 2
    else:
        with pytest.raises(deploy.DeployError, match="invalid configuration"):
            asyncio.run(deploy.push_integration(ha))
        assert ha.sup.await_count == 1
        ha.get_json.assert_not_called()
    remote.copy_tree.assert_awaited_once_with(
        deploy.ROOT / "custom_components/atv_bridge",
        ("/homeassistant", "/config"),
        "/custom_components",
    )


def test_upload_replaces_stale_tree_and_excludes_caches(tmp_path, monkeypatch):
    # Exercise the exact remote shell commands on a local temporary directory.
    root = tmp_path / "repo"
    source = root / "app"
    for filename in [
        "config.yaml",
        "vendor/pyatv/pyproject.toml",
        "vendor/pyatv/.venv/secret",
        ".ruff_cache/index",
        "lib/__pycache__/stale.pyc",
    ]:
        file = source / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(filename)
    mount = tmp_path / "addons"
    dest = mount / "atv_bridge"
    dest.mkdir(parents=True)
    (dest / "stale-file").touch()
    commands = deploy.run

    async def local_command(*argv, check=True):
        if argv[0] == "ssh":
            return await commands("sh", "-c", argv[-1], check=check)
        assert argv[0] == "scp"
        shutil.copyfile(argv[-2], argv[-1].split(":", 1)[1])
        return 0, "", ""

    key = tmp_path / "key"
    key.touch()
    monkeypatch.setattr(deploy, "ROOT", root)
    monkeypatch.setattr(deploy, "key_path", lambda *_: key)
    monkeypatch.setattr(deploy, "run", local_command)
    remote = deploy.Remote(Mock(base=URL("http://localhost")), 2222)
    asyncio.run(remote.copy_tree(source, (str(tmp_path / "missing"), str(mount))))
    assert sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()) == [
        "config.yaml",
        "vendor/pyatv/pyproject.toml",
    ]
    assert list(mount.iterdir()) == [dest]


@pytest.mark.parametrize("count", [0, 2])
def test_disable_requires_exact_unique_entry(count):
    entry = {
        "entry_id": "one",
        "domain": "apple_tv",
        "title": "Living Room",
        "disabled_by": None,
    }
    ha = Mock(
        ws=AsyncMock(return_value=[entry] * count + [dict(entry, domain="atv_bridge")])
    )
    with pytest.raises(deploy.DeployError, match="Expected one"):
        asyncio.run(deploy.disable_builtin(ha, "Living Room", True))
    assert ha.ws.await_count == 1


@pytest.mark.parametrize(
    "flags,disabled,writes",
    [
        ([], None, False),
        (["--dry-run"], None, False),
        (["--yes"], None, True),
        (["--yes"], "user", False),
    ],
)
def test_disable_preview_and_confirmation(flags, disabled, writes):
    entry = {
        "domain": "apple_tv",
        "title": "Living Room",
        "entry_id": "target",
        "disabled_by": disabled,
    }
    others = [dict(entry, domain="atv_bridge"), dict(entry, title="Bedroom")]
    ha = Mock(
        ws=AsyncMock(
            side_effect=[
                [entry, *others],
                {"require_restart": False},
                [dict(entry, disabled_by="user")],
            ]
        )
    )
    asyncio.run(
        deploy.dispatch(
            ha, args("disable-builtin", "--entry-title", "Living Room", *flags)
        )
    )
    assert ha.ws.await_args_list[0].kwargs == {"domain": "apple_tv"}
    assert ha.ws.await_count == (3 if writes else 1)
    if writes:
        call = ha.ws.await_args_list[1]
        assert call.args == ("config_entries/disable",)
        assert call.kwargs == {"entry_id": "target", "disabled_by": "user"}


def test_cli_rejects_conflicting_disable_flags():
    with pytest.raises(SystemExit):
        args("disable-builtin", "--entry-title", "Living Room", "--yes", "--dry-run")


def test_supervisor_option_envelope_and_error(capsys):
    async def scenario():
        requests = []
        payload = {
            "options": {"authorized_keys": ["test-public-key"]},
            "network": {"22/tcp": 2222},
        }

        async def ws(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.send_json({"type": "auth_required"})
            await socket.receive_json()
            await socket.send_json({"type": "auth_ok", "ha_version": "2026.11.2"})
            requests.append(await socket.receive_json())
            await socket.send_json(
                {
                    "type": "result",
                    "id": 1,
                    "success": False,
                    "error": {"code": "unknown_error", "message": "invalid options"},
                }
            )
            await socket.close()
            return socket

        app = web.Application()
        app.router.add_get("/api/websocket", ws)
        async with server(app) as origin, aiohttp.ClientSession() as session:
            ha = deploy.HA(session, str(origin), "mock-secret")
            with pytest.raises(deploy.DeployError, match="invalid options"):
                await ha.sup(
                    "/addons/core_ssh/options", "post", data=payload, timeout=900
                )
        assert requests == [
            {
                "id": 1,
                "type": "supervisor/api",
                "method": "post",
                "endpoint": "/addons/core_ssh/options",
                "timeout": 900,
                "data": payload,
            }
        ]
        assert "test-public-key" not in capsys.readouterr().out

    asyncio.run(scenario())


def test_wait_for_slow_build_has_overall_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    fork = tmp_path / "app/vendor/pyatv/pyproject.toml"
    fork.parent.mkdir(parents=True)
    fork.touch()
    monkeypatch.setattr(deploy, "run", AsyncMock())
    monkeypatch.setattr(
        deploy, "remote_for_push", AsyncMock(return_value=Mock(copy_tree=AsyncMock()))
    )
    monkeypatch.setattr(deploy, "BUILD_TIMEOUT", 0.01)
    observed = []

    async def supervisor(path, *a, **kw):
        observed.append(path)
        if path.endswith("/install"):
            await asyncio.Future()

    ha = Mock(sup=supervisor, apps=AsyncMock(return_value=[]))
    with pytest.raises(deploy.DeployError, match="deadline"):
        asyncio.run(deploy.push_app(ha, args("push-app")))
    assert observed == ["/store/reload", "/store/addons/local_atv_bridge/install"]


def test_restart_and_status(monkeypatch):
    ha = Mock(
        sup=AsyncMock(),
        info=AsyncMock(
            return_value={"state": "started", "version": "1", "slug": deploy.APP}
        ),
        apps=AsyncMock(return_value=[{"slug": deploy.APP}]),
        health=AsyncMock(return_value=True),
    )
    wait = AsyncMock()
    monkeypatch.setattr(deploy, "wait_app", wait)
    asyncio.run(deploy.dispatch(ha, args("restart-app")))
    ha.sup.assert_awaited_once_with(
        "/addons/local_atv_bridge/restart", "post", timeout=180
    )
    wait.assert_awaited_once_with(ha)
    asyncio.run(deploy.dispatch(ha, args("status")))
    ha.health.assert_awaited_once()
    assert '"watchdog_running": true' in ha.say.call_args.args[0]
