"""Regressions reported by tvOS and the Green, exercised without either device."""

import asyncio
import json
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from atv_bridge_app import live
from atv_bridge_app.atv import RtspSession, hap, install_sender_identity
from atv_bridge_app.state import Fault, State


@pytest.mark.parametrize("status", [200, 401, 403, 404, 499, 500, 503])
async def test_probe_follows_redirect_and_accepts_client_errors(service, aiohttp_server, status):
    visits = []

    async def page(request):
        visits.append((request.method, request.path))
        if request.path == "/first":
            raise web.HTTPFound("/final")
        return web.Response(status=status, headers={"X-Frame-Options": "DENY"}, text="Landing document")

    app = web.Application()
    app.router.add_get("/{path}", page)
    server = await aiohttp_server(app)
    url = str(server.make_url("/first"))
    if status >= 500:
        with pytest.raises(Fault, match=f"HTTP {status}") as caught:
            await live.probe(service.stage, url)
        assert caught.value.code == "page_unreachable"
    else:
        assert await live.probe(service.stage, url) is False
    assert visits == [("GET", "/first"), ("GET", "/final")]


async def test_probe_refused_port_and_tls_diagnostics(service):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    with pytest.raises(Fault) as caught:
        await live.probe(service.stage, f"http://127.0.0.1:{port}/")
    assert caught.value.code == "page_unreachable"
    assert "connect" in str(caught.value).lower() and caught.value.remediation == live.REFUSED_FIX
    failure = live.page_failure("example.ts.net", ssl.SSLCertVerificationError("certificate expired"))
    assert "certificate expired" in str(failure) and "TLS certificate" in failure.remediation


async def test_tailnet_dns_system_lookup_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "ha-green")
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 100.100.100.100\nsearch local test.ts.net\n")
    resolve = AsyncMock(return_value=[])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    assert await live.tailnet_dns(resolv) == {
        "ok": True,
        "source": "system_resolver",
        "hostname": "ha-green.test.ts.net",
    }
    resolve.side_effect = socket.gaierror("no record")
    assert (await live.tailnet_dns(resolv))["ok"] is False
    resolve.assert_awaited_with("ha-green.test.ts.net", None, type=socket.SOCK_STREAM)


@pytest.mark.parametrize(
    "forwarders,expected",
    [
        ([], False),
        (["dns://100.100.100.100"], True),
        (["dns://100.100.100.100:53"], True),
        (["dns://100.100.100.100.example.test"], False),
    ],
)
async def test_tailnet_dns_supervisor_forwarders(
    monkeypatch, tmp_path, aiohttp_server, forwarders, expected
):
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 127.0.0.11\n# nameserver 100.100.100.100\n")
    monkeypatch.setattr(socket, "gethostname", lambda: "addon-slug")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    seen = []

    async def supervisor(request):
        seen.append(request.path)
        assert request.headers["Authorization"] == "Bearer test-token"
        data = {"hostname": "ha-green"} if request.path == "/host/info" else {"servers": forwarders}
        return web.json_response({"result": "ok", "data": data})

    app = web.Application()
    app.router.add_get("/{service}/info", supervisor)
    server = await aiohttp_server(app)
    monkeypatch.setattr(live, "SUPERVISOR_URL", str(server.make_url("/")).rstrip("/"))
    assert await live.tailnet_dns(resolv) == {"ok": expected, "source": "supervisor"}
    assert seen == ["/host/info", "/dns/info"]
    monkeypatch.delenv("SUPERVISOR_TOKEN")
    resolv.write_text("nameserver 100.100.100.100 # tailnet resolver\n")
    assert await live.tailnet_dns(resolv) == {"ok": True, "source": "resolv.conf"}


async def test_health_reports_missing_dns_before_first_show(service, monkeypatch):
    from atv_bridge_app import tools

    monkeypatch.setattr(
        tools, "tailnet_dns", AsyncMock(return_value={"ok": False, "source": "resolv.conf"})
    )
    assert service.stage.page is None
    h = await service.operations.tv_health()
    r = (await service.tools.call("tv_status", {}))["data"]
    for data in (h, r):
        assert data["tailnet_dns"]["ok"] is False
        assert {"code": "tailnet_dns_missing", "fix": live.TAILNET_DNS_FIX} in data["problems"]


@pytest.mark.parametrize(
    "foreground,owner",
    [(False, {"pid": 31}), (False, {"bundle_id": "com.apple.TVSettings"}), (True, {})],
)
async def test_accessibility_foreground_wins_over_paused_hulu(service, foreground, owner):
    client = await service.engine.ensure()
    client.app = {"name": "Hulu", "bundle_id": "com.hulu"}
    client.media = {"title": "Paused episode", "state": "paused"}
    service.state.save_pairings(remote_pairing=True)

    async def observe(method, **kw):
        if method == "ax_focused":
            return {"focused": {"label": "General", **owner}}
        return {
            "apps": [
                client.app | {"pid": 26},
                {
                    "name": "Settings",
                    "bundle_id": "com.apple.TVSettings",
                    "pid": 31,
                    "foreground": foreground,
                },
            ]
        }

    service.observer.call = observe
    screen = await service.tools.snapshot()
    status = await service.operations.status()
    assert screen["app"] == {
        "id": "com.apple.TVSettings",
        "name": "Settings",
        "source": "accessibility",
    }
    assert status["app"] == {
        "bundle_id": "com.apple.TVSettings",
        "name": "Settings",
        "source": "accessibility",
    }
    assert screen["playing"]["title"] == status["now_playing"]["title"] == "Paused episode"
    service.state.save_pairings(remote_pairing=False)
    assert (await service.tools.snapshot())["app"] == {
        "id": "com.hulu",
        "name": "Hulu",
        "source": "now_playing",
    }


async def test_installed_identity_and_raop_setup_name(tmp_path):
    identity = {"name": "pyatv", "controller_id": "old-id", "ed25519_seed": "33" * 32}
    (tmp_path / "identity.json").write_text(json.dumps(identity))
    store = State(tmp_path)
    assert store.identity == identity | {"name": "Agent Bridge"}
    assert json.loads((tmp_path / "identity.json").read_text()) == store.identity
    install_sender_identity()
    assert hap._AIRPLAY_HEADERS["X-Apple-Client-Name"] == "Agent Bridge"
    session = SimpleNamespace(exchange=AsyncMock())
    body = {"name": "pyatv", "sessionUUID": "existing"}
    await RtspSession.setup(session, headers={"CSeq": "12"}, body=body)
    session.exchange.assert_awaited_once_with(
        "SETUP",
        headers={"CSeq": "12", "X-Apple-Client-Name": "Agent Bridge"},
        body=body | {"name": "Agent Bridge"},
    )
    assert body["name"] == "pyatv"


async def test_probe_rejects_untrusted_https_certificate(service, tmp_path):
    from datetime import UTC, datetime, timedelta
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    async with await asyncio.start_server(
        lambda reader, writer: writer.close(), "127.0.0.1", 0, ssl=context
    ) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(Fault) as caught:
            await live.probe(service.stage, f"https://127.0.0.1:{port}/")
    assert caught.value.code == "page_unreachable" and "certificate" in str(caught.value).lower()
    assert "TLS certificate" in caught.value.remediation


async def test_opening_settings_does_not_attribute_hulu_playback_to_settings(service):
    client = await service.engine.ensure()
    client.app = {"name": "Hulu", "bundle_id": "com.hulu"}
    client.media = {"title": "Hulu video", "state": "playing"}
    service.state.save_pairings(remote_pairing=True)
    service.observer.call = AsyncMock(
        return_value={
            "focused": None,
            "app": {"name": "Settings", "bundle_id": "com.apple.TVSettings"},
        }
    )
    result = await service.operations._verify(client, "com.apple.TVSettings", await client.status())
    assert result["state"] == "opened"
    assert result["evidence"] == ["App reported com.apple.TVSettings"]


@pytest.mark.parametrize(
    ("bundle", "name"), [("com.apple.HeadBoard", "Home"), ("com.apple.TVSettings", "Settings")]
)
async def test_single_foreground_snapshot_across_switch(service, bundle, name):
    client = await service.engine.ensure()
    client.app = {"name": "Hulu", "bundle_id": "com.hulu"}
    service.state.save_pairings(remote_pairing=True)
    requests = []

    async def observe(method, **kw):
        requests.append(method)
        assert method == "ax_items"
        return {
            "focused": {"uid": "f", "label": "Focused"},
            "items": [{"uid": "f", "label": "Focused"}],
            "app": {"bundle_id": bundle, "name": "internal process name"},
        }

    service.observer.call = observe
    result = await service.tools.tv_look()
    assert requests == ["ax_items"]
    assert result["screen"]["app"] == {"id": bundle, "name": name, "source": "accessibility"}
    assert result["data"]["items"][0]["focused"]


async def test_unavailable_paired_foreground_does_not_report_paused_app(service):
    client = await service.engine.ensure()
    client.app = {"name": "Hulu", "bundle_id": "com.hulu"}
    service.state.save_pairings(remote_pairing=True)

    async def observe(method, **kw):
        assert method == "ax_focused"
        return {"focused": None, "app": None}

    service.observer.call = observe
    assert (await service.tools.snapshot())["app"] is None
