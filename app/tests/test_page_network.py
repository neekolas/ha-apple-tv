import asyncio
import ipaddress
import socket

import pytest

from atv_bridge_app import page_network
from atv_bridge_app.page_network import BlockedAddress, PageNetworkPolicy


@pytest.fixture(autouse=True)
def isolated_interfaces(monkeypatch):
    # Never depend on the machine running pytest or inspect its network.
    monkeypatch.setattr(page_network, "_local_interfaces", lambda: (set(), set()))


def dns_answers(monkeypatch, answers):
    seen = []

    async def resolve(name, port, **kwargs):
        seen.append(name)
        rows = []
        for text in next(answers):
            address = ipaddress.ip_address(text)
            if address.version == 4:
                family, peer = socket.AF_INET, (text, port)
            else:
                family, peer = socket.AF_INET6, (text, port, 0, 0)
            rows.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", peer))
        return rows

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    return seen


@pytest.mark.parametrize(("host", "kind"), [
    ("0.0.0.0", "unspecified"),
    ("0.0.0.1", "reserved"),
    ("10.0.0.0", "private"),
    ("172.16.0.0", "private"),
    ("172.31.255.255", "private"),
    ("172.30.32.0", "private"),
    ("192.168.0.0", "private"),
    ("127.0.0.1", "loopback"),
    ("127.255.255.255", "loopback"),
    ("169.254.0.0", "link-local"),
    ("224.0.0.0", "multicast"),
    ("240.0.0.0", "reserved"),
    ("255.255.255.255", "reserved"),
    ("192.0.2.0", "reserved"),
    ("192.88.99.0", "transition"),
    ("198.18.0.0", "reserved"),
    ("::", "unspecified"),
    ("::1", "loopback"),
    ("fe80::1", "link-local"),
    ("fc00::1", "unique-local"),
    ("fd7a:115c:a1e1::1", "unique-local"),
    ("fec0::1", "reserved"),
    ("ff02::1", "multicast"),
    ("::ffff:192.168.0.1", "private"),
    ("::ffff:7f00:1", "loopback"),
    ("::127.0.0.1", "reserved"),
    ("64:ff9b::7f00:1", "transition"),
    ("2002:7f00:0001::1", "transition"),
    ("2001:db8::1", "reserved"),
    ("4000::1", "reserved"),
    ("fe80::1%1", "scoped"),
    ("service.LOCAL.", "mDNS"),
    ("service.localhost", "loopback"),
    ("2130706433", "loopback"),
    ("0177.0.0.1", "loopback"),
    ("0x7f000001", "loopback"),
    ("127.1", "loopback"),
])
async def test_blocked_by_default(host, kind):
    with pytest.raises(BlockedAddress) as caught:
        await PageNetworkPolicy().resolve(host, 8765)
    assert caught.value.code == "blocked_address" and kind in str(caught.value)
    assert "page_network_allow" in caught.value.remediation


@pytest.mark.parametrize(("host", "peer"), [
    ("1.1.1.1", "1.1.1.1"),
    ("2606:4700:4700::1111", "2606:4700:4700::1111"),
    ("100.64.0.0", "100.64.0.0"),
    ("100.127.255.255", "100.127.255.255"),
    ("fd7a:115c:a1e0::1", "fd7a:115c:a1e0::1"),
    ("::ffff:100.64.0.1", "100.64.0.1"),
])
async def test_allow_public_and_tailnet(host, peer):
    assert await PageNetworkPolicy().resolve(host, 443) == [peer]


async def test_system_dns_returns_only_pinned_ips(monkeypatch):
    seen = dns_answers(monkeypatch, iter([
        ["100.64.0.1", "fd7a:115c:a1e0::1", "100.64.0.1"], ["172.30.32.1"],
        ["1.1.1.1", "::ffff:127.0.0.1"],
    ]))
    policy = PageNetworkPolicy()
    assert await policy.resolve("Agent.TS.NET.", 443) == ["100.64.0.1", "fd7a:115c:a1e0::1"]
    with pytest.raises(BlockedAddress, match="private"):
        await policy.resolve("agent.ts.net", 443)
    with pytest.raises(BlockedAddress, match="loopback"):
        await policy.resolve("mixed.example", 443)
    assert seen == ["agent.ts.net", "agent.ts.net", "mixed.example"]


async def test_stage_exception_tracks_listeners_and_literal_origin(monkeypatch):
    dns_answers(monkeypatch, iter([["127.0.0.1"]]))
    ports = {8766}
    policy = PageNetworkPolicy(stage_ports=ports)
    assert await policy.resolve("127.0.0.1", 8766) == ["127.0.0.1"]
    ports.add(9123)
    assert await policy.resolve("127.0.0.1", 9123) == ["127.0.0.1"]
    ports.remove(9123)
    for host, port in [
        ("127.0.0.1", 9123), ("::1", 8766), ("127.0.0.2", 8766), ("::ffff:127.0.0.1", 8766),
        ("localhost", 8766), ("alias.example", 8766),
    ]:
        with pytest.raises(BlockedAddress, match="loopback"):
            await policy.resolve(host, port)
    with pytest.raises(BlockedAddress, match="loopback"):
        await policy.resolve("127.0.0.1", 8766, stage_allowed=False)


async def test_extra_destinations_and_ports(monkeypatch):
    peer = "192.0.2.9"
    dns_answers(monkeypatch, iter([[peer], [peer], [peer], [peer]]))
    policy = PageNetworkPolicy(allow=[
        "dashboard.example:8000", "docs.example.", "198.51.100.0/27", "[2001:db8::1]:8080",
    ])
    assert await policy.resolve("Dashboard.Example.", 8000) == [peer]
    assert await policy.resolve("docs.example", 80) == [peer]
    assert await policy.resolve("198.51.100.31", 80) == ["198.51.100.31"]
    assert await policy.resolve("2001:db8::1", 8080) == ["2001:db8::1"]
    for host, port in [
        ("dashboard.example", 8001), ("other.example", 8000),
        ("198.51.100.32", 80), ("2001:db8::1", 8081),
    ]:
        with pytest.raises(BlockedAddress):
            await policy.resolve(host, port)


async def test_own_host_and_local_network_checked_on_each_connect(monkeypatch):
    peer = ipaddress.ip_address("2606:4700:4700::1111")
    local = set()
    networks = set()
    monkeypatch.setattr(page_network, "_local_interfaces", lambda: (local, networks))
    policy = PageNetworkPolicy()
    assert await policy.resolve(str(peer), 443) == [str(peer)]
    networks.add(ipaddress.ip_network("2606:4700:4700::/64"))
    with pytest.raises(BlockedAddress, match="local network"):
        await policy.resolve(str(peer), 443)
    allow = PageNetworkPolicy(allow=[f"[{peer}]:443", "100.64.0.0/10", "127.0.0.1"])
    assert await allow.resolve(str(peer), 443) == [str(peer)]
    local.update([peer, ipaddress.ip_address("100.64.0.1")])
    for host in [str(peer), "100.64.0.1", "127.0.0.1"]:
        with pytest.raises(BlockedAddress):
            await allow.resolve(host, 443)


@pytest.mark.parametrize("allow", [
    "192.0.2.1", ["*"], ["http://dashboard.example"], ["bad host"],
    ["dashboard.example:"], ["dashboard.example:65536"], ["[2001:db8::1"],
    ["192.0.2.0/33"], [None],
])
def test_invalid_option(allow):
    with pytest.raises(ValueError, match="page_network_allow"):
        PageNetworkPolicy(allow=allow)
