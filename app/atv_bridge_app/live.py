"""Check the effective framing policy without proxying the live application."""

import asyncio
import errno
import socket
import ssl
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import (
    ClientConnectorDNSError,
    ClientConnectorError,
    ClientError,
    ClientSession,
    ClientSSLError,
    ClientTimeout,
    TCPConnector,
)

from .page_network import BlockedAddress
from .processes import supervisor_token
from .state import Fault, reworded

ORIGIN = "http://127.0.0.1:8766"


def accepts_frame(headers, url, origin=ORIGIN):
    same = str(url.origin()) == origin
    policies = headers.getall("Content-Security-Policy", [])
    ancestors = []
    for policy in policies:
        for directive in policy.split(";"):
            tokens = directive.split()
            if tokens and tokens[0].lower() == "frame-ancestors":
                ancestors.append(tokens[1:])
                break
    if ancestors:
        return all(
            any(
                s in ("*", "http:", origin, "http://127.0.0.1:*") or (s == "'self'" and same)
                for s in sources
            )
            for sources in ancestors
        )
    return all(
        v.lower().strip() != "deny" and (v.lower().strip() != "sameorigin" or same)
        for v in headers.getall("X-Frame-Options", [])
    )


async def resolve_tailnet(stage, hostname):
    try:
        async with asyncio.timeout(4):
            await asyncio.get_running_loop().getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        resolved = True
    except (OSError, TimeoutError):
        resolved = False
    stage.dns_status = {"hostname": hostname, "resolved": resolved}
    return resolved


SUPERVISOR_URL = "http://supervisor"
TAILNET_DNS_FIX = "Set HA DNS to Tailscale: ha dns options --servers dns://100.100.100.100"
REFUSED_FIX = "Is the agent's server and tailscale serve running?"


def page_failure(host, exc):
    detail = str(exc) or type(exc).__name__
    cause = exc.os_error if isinstance(exc, ClientConnectorError) else exc
    if (
        isinstance(exc, ClientConnectorDNSError)
        or isinstance(cause, socket.gaierror)
        or "ERR_NAME_NOT_RESOLVED" in detail
    ):
        fix = (
            TAILNET_DNS_FIX
            if host.rstrip(".").lower().endswith(".ts.net")
            else "Check the hostname and the HA DNS resolver."
        )
    elif (
        isinstance(cause, ConnectionRefusedError)
        or getattr(cause, "errno", None) == errno.ECONNREFUSED
        or "ERR_CONNECTION_REFUSED" in detail
    ):
        fix = REFUSED_FIX
    elif (
        isinstance(exc, (ssl.SSLError, ClientSSLError)) or "ERR_CERT_" in detail or "ERR_SSL_" in detail
    ):
        fix = "Check the server's TLS certificate, hostname and trust chain."
    elif isinstance(cause, TimeoutError) or "TIMED_OUT" in detail:
        fix = "Check the route to the server and whether it responds within 8 seconds."
    else:
        fix = "Check the server and the network route from Home Assistant."
    return Fault("page_unreachable", f"Could not load {host}: {detail}", fix, True)


class ProbeConnector(TCPConnector):
    """Give aiohttp only approved numeric peers, retaining the URL for Host, SNI and redirects."""

    def __init__(self, policy):
        super().__init__(force_close=True, use_dns_cache=False)
        self.policy = policy
        self.stage_allowed = ContextVar("probe_stage_allowed", default=False)

    async def connect(self, req, traces, timeout):
        token = self.stage_allowed.set(req.url.scheme == "http")
        try:
            connection = await super().connect(req, traces, timeout)
        finally:
            self.stage_allowed.reset(token)
        # Removing a receiver can revoke its stage port while TCP is connecting.
        if req.url.raw_host == "127.0.0.1" and req.port not in self.policy.stage_ports:
            connection.close()
            raise BlockedAddress("loopback", req.url.raw_host)
        return connection

    async def _resolve_host(self, host, port, traces=None):
        # An aiohttp resolver alone would never see IP literals. This connector
        # hook runs for both literals and names, including on redirect hops.
        addresses = await self.policy.resolve(host, port, stage_allowed=self.stage_allowed.get())
        return [
            {
                "hostname": host, "host": ip, "port": port,
                "family": socket.AF_INET6 if ":" in ip else socket.AF_INET,
                "proto": socket.IPPROTO_TCP, "flags": socket.AI_NUMERICHOST,
            }
            for ip in addresses
        ]


async def probe(stage, url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Fault(
            "invalid_arguments", "Page URLs require HTTP or HTTPS.", "Supply a complete live URL."
        )
    host = parts.hostname
    # The total timeout includes policy checks, DNS, TLS and every redirect.
    try:
        async with ClientSession(
            timeout=ClientTimeout(total=8), connector=ProbeConnector(stage.page_network)
        ) as http:
            async with http.get(url, allow_redirects=True) as response:
                host = response.url.host
                for hop in (*response.history, response):
                    if hop.url.host.endswith(".ts.net"):
                        stage.dns_status = {"hostname": hop.url.host, "resolved": True}
                if response.status >= 500:
                    raise Fault(
                        "page_unreachable",
                        f"Could not load {host}: HTTP {response.status} {response.reason}.",
                        "Check the agent's server logs and retry.",
                        True,
                    )
                return accepts_frame(response.headers, response.url, getattr(stage, "origin", ORIGIN))
    except (TimeoutError, ClientError, OSError) as exc:
        host = getattr(exc, "host", None) or host
        fault = page_failure(host, exc)
        if fault.remediation == reworded(TAILNET_DNS_FIX):
            stage.dns_status = {"hostname": host, "resolved": False}
        raise fault from exc


def resolver_config(path):
    servers = []
    suffix = None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        tokens = line.partition("#")[0].partition(";")[0].lower().split()
        if len(tokens) < 2:
            continue
        if tokens[0] == "nameserver":
            servers.append(tokens[1])
        elif tokens[0] in ("search", "domain"):
            suffix = next(
                (n.rstrip(".") for n in tokens[1:] if n.rstrip(".").endswith(".ts.net")), suffix
            )
    return servers, suffix


async def supervisor_info(http, path):
    try:
        async with http.get(SUPERVISOR_URL + "/" + path, allow_redirects=False) as response:
            response.raise_for_status()
            result = await response.json()
            return result.get("data", {}) if result.get("result") == "ok" else {}
    except (TimeoutError, ClientError, ValueError):
        return {}


def tailnet_hostname(hostname, suffix):
    hostname = hostname.lower().rstrip(".")
    if hostname.endswith(".ts.net"):
        return hostname
    if hostname and suffix:
        return f"{hostname.split('.')[0]}.{suffix}"
    return None


async def tailnet_dns(resolv_conf=Path("/etc/resolv.conf")):
    servers, suffix = resolver_config(resolv_conf)
    hostname = socket.gethostname()
    token = supervisor_token()
    fqdn = tailnet_hostname(hostname, None if token else suffix)
    upstream = []
    if token and not fqdn:
        async with ClientSession(
            headers={"Authorization": "Bearer " + token}, timeout=ClientTimeout(total=3)
        ) as http:
            # The app's short hostname identifies the container. Ask for the HA host.
            host_info = await supervisor_info(http, "host/info")
            fqdn = tailnet_hostname(host_info.get("hostname") or "", suffix)
            if not fqdn and "100.100.100.100" not in servers:
                dns = await supervisor_info(http, "dns/info")
                upstream = [
                    urlsplit(s if "://" in s else "//" + s).hostname
                    for s in (*dns.get("servers", []), *dns.get("locals", []))
                    if isinstance(s, str)
                ]
    if fqdn:
        try:
            async with asyncio.timeout(3):
                await asyncio.get_running_loop().getaddrinfo(fqdn, None, type=socket.SOCK_STREAM)
            return {"ok": True, "source": "system_resolver", "hostname": fqdn}
        except (OSError, TimeoutError) as exc:
            return {
                "ok": False,
                "source": "system_resolver",
                "hostname": fqdn,
                "detail": str(exc) or type(exc).__name__,
            }
    if "100.100.100.100" in servers:
        return {"ok": True, "source": "resolv.conf"}
    return {"ok": "100.100.100.100" in upstream, "source": "supervisor" if token else "resolv.conf"}
