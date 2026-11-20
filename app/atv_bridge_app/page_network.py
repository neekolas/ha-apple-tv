"""Authorize numeric TCP peers for untrusted pages, including the app's URL probe."""

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass

import ifaddr

from .state import Fault

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")
ULA = ipaddress.ip_network("fc00::/7")
PRIVATE_V4 = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
# IPv4 translation and tunneling addresses can represent a different network's peer.
TRANSITION = tuple(map(ipaddress.ip_network, (
    "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16", "192.88.99.0/24",
)))
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class BlockedAddress(Fault):
    def __init__(self, kind, address=None):
        self.kind = kind
        self.address = address
        target = f"{kind} address {address}" if address is not None else kind
        super().__init__(
            "blocked_address",
            f"Page network access blocked ({target}).",
            "Use a public or tailnet URL, or configure page_network_allow for a trusted LAN service. "
            "The bridge host is protected.",
        )


def _address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        # Chromium accepts legacy IPv4 numbers. getaddrinfo interprets leading
        # zeroes differently across platforms, so canonicalize before DNS.
        if not re.fullmatch(r"[0-9][0-9a-fA-FxX.]*", value):
            raise
        try:
            return ipaddress.IPv4Address(socket.inet_aton(value.removesuffix(".")))
        except OSError as exc:
            raise ValueError("Invalid numeric IPv4 address") from exc
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _hostname(value):
    name = value.removesuffix(".").encode("idna").decode("ascii").lower()
    if len(name) > 253 or not all(HOST_LABEL.fullmatch(label) for label in name.split(".")):
        raise ValueError("Invalid hostname")
    return name


@dataclass(frozen=True)
class _Allow:
    host: str | None = None
    network: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None
    port: int | None = None

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str) or not value or "%" in value:
            raise ValueError("Expected a hostname, IP, CIDR, or host:port")
        if "/" in value:
            return cls(network=ipaddress.ip_network(value))
        port = None
        host = value
        if value.startswith("["):
            match = re.fullmatch(r"\[([^\]]+)\](?::([0-9]+))?", value)
            if not match:
                raise ValueError("Invalid bracketed IPv6 host or port")
            host, port = match.groups()
            ipaddress.IPv6Address(host)
        elif value.count(":") == 1:
            host, port = value.split(":")
        if port is not None:
            if not re.fullmatch("[0-9]+", port) or not 1 <= int(port) <= 65535:
                raise ValueError("Invalid port")
            port = int(port)
        try:
            address = _address(host)
        except ValueError:
            return cls(host=_hostname(host), port=port)
        return cls(network=ipaddress.ip_network(address), port=port)

    def matches(self, hostname, address, port):
        if self.port is not None and self.port != port:
            return False
        return address in self.network if self.network is not None else self.host == hostname


def _local_interfaces():
    addresses, networks = set(), set()
    for adapter in ifaddr.get_adapters():
        for item in adapter.ips:
            address = _address(item.ip[0] if item.is_IPv6 else item.ip)
            addresses.add(address)
            if item.network_prefix:
                networks.add(ipaddress.ip_network((address, item.network_prefix), strict=False))
    return addresses, networks


def _classification(address):
    if address.is_unspecified:
        return "unspecified"
    if address.is_loopback:
        return "loopback"
    if address.is_multicast:
        return "multicast"
    if address.is_link_local:
        return "link-local"
    if address in TAILNET_V4 or address in TAILNET_V6:
        return "tailnet"
    if address in ULA:
        return "unique-local"
    if any(address in network for network in PRIVATE_V4):
        return "private"
    if any(address in network for network in TRANSITION):
        return "transition"
    if address.version == 6 and address not in GLOBAL_UNICAST_V6:
        return "reserved"
    if not address.is_global or address.is_reserved:
        return "reserved"
    return "public"


class PageNetworkPolicy:
    def __init__(self, allow=(), stage_ports=()):
        if not isinstance(allow, (tuple, list)):
            raise ValueError("page_network_allow must be a list")
        try:
            self.allow = tuple(_Allow.parse(entry) for entry in allow)
        except ValueError as exc:
            raise ValueError(f"Invalid page_network_allow entry: {exc}") from exc
        # Fleet updates this set as receiver-specific stage listeners start and stop.
        self.stage_ports = stage_ports

    async def resolve(self, host: str, port: int, *, stage_allowed: bool = True) -> list[str]:
        """Validate *every* answer. Callers must dial a returned IP, keeping the name for TLS/HTTP."""
        if not 1 <= port <= 65535 or not host:
            raise ValueError("Invalid destination")
        if stage_allowed and host == "127.0.0.1" and port in self.stage_ports:
            return [host]
        if "%" in host:
            raise BlockedAddress("scoped address")
        try:
            literal = _address(host)
        except ValueError:
            name = _hostname(host)
            if name == "local" or name.endswith(".local"):
                raise BlockedAddress("mDNS name")
            if name == "localhost" or name.endswith(".localhost"):
                raise BlockedAddress("loopback name")
            answers = await asyncio.get_running_loop().getaddrinfo(
                name, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
            )
            addresses = list(dict.fromkeys(_address(row[4][0]) for row in answers))
            if not addresses:
                raise socket.gaierror(socket.EAI_NONAME, "No addresses for page destination")
        else:
            name = str(literal)
            addresses = [literal]

        local_addresses, local_networks = await asyncio.to_thread(_local_interfaces)
        for address in addresses:
            kind = _classification(address)
            if kind in {"loopback", "unspecified", "multicast", "transition"}:
                raise BlockedAddress(kind, address)
            if address in local_addresses:
                raise BlockedAddress("bridge host", address)
            if kind == "tailnet":
                continue
            if kind == "public":
                if not any(address in network for network in local_networks):
                    continue
                kind = "local network"
            if not any(rule.matches(name, address, port) for rule in self.allow):
                raise BlockedAddress(kind, address)
        return [str(address) for address in addresses]
