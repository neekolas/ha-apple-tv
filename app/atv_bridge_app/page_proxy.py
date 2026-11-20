"""Loopback HTTP/1.1 forward proxy for the presentation browsers."""

import asyncio
import re
import socket
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import h11

from .page_network import BlockedAddress, PageNetworkPolicy

_CHUNK = 32 * 1024
_HOP_HEADERS = {
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"proxy-connection", b"te", b"trailer", b"upgrade",
}


def _destination(request):
    target = request.target.decode("ascii")
    tunnel = request.method == b"CONNECT"
    parts = urlsplit("//" + target if tunnel else target)
    host = parts.hostname
    port = parts.port
    if not host or parts.username is not None or parts.fragment:
        raise ValueError("Invalid proxy authority")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    if parts.netloc.lower() != authority or port == 0:
        raise ValueError("Invalid proxy authority")
    if tunnel:
        if parts.path or parts.query or port is None:
            raise ValueError("CONNECT requires host and port")
        if any(key == b"transfer-encoding" or (key == b"content-length" and value != b"0")
               for key, value in request.headers):
            raise ValueError("CONNECT cannot carry an HTTP request body")
    elif parts.scheme not in {"http", "ws"}:
        raise ValueError("Expected an absolute HTTP request target")
    path = (parts.path or "/") + ("?" + parts.query if "?" in target else "")
    return host, port or 80, authority.encode(), path.encode()


def _headers(headers, upgrade):
    hop = _HOP_HEADERS.copy()
    for key, value in headers:
        if key == b"connection":
            hop.update(token.strip().lower() for token in value.split(b","))
    # h11 decodes incoming framing and encodes it again at the next hop.
    hop.difference_update({b"content-length", b"transfer-encoding"})
    result = [(key, value) for key, value in headers if key not in hop]
    if upgrade:
        result.extend([(b"connection", b"Upgrade"), (b"upgrade", b"websocket")])
    else:
        result.append((b"connection", b"close"))
    return result


class _Stream:
    def __init__(self, reader, writer, role):
        self.reader = reader
        self.writer = writer
        self.http = h11.Connection(role, max_incomplete_event_size=64 * 1024)

    async def event(self):
        while (event := self.http.next_event()) is h11.NEED_DATA:
            self.http.receive_data(await self.reader.read(_CHUNK))
        return event

    async def send(self, event):
        self.writer.write(self.http.send(event))
        await self.writer.drain()


async def _copy(source, destination):
    destination.writer.write(source.http.trailing_data[0])
    while data := await source.reader.read(_CHUNK):
        destination.writer.write(data)
        await destination.writer.drain()
    destination.writer.write_eof()


async def _tunnel(client, origin):
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_copy(client, origin))
        tasks.create_task(_copy(origin, client))


async def _upload(client, origin):
    while isinstance(event := await client.event(), h11.Data):
        await origin.send(event)
    if not isinstance(event, h11.EndOfMessage):
        raise ValueError("Incomplete request")
    await origin.send(h11.EndOfMessage())


async def _http(client, origin, request, authority, path):
    upgrade = any(key == b"upgrade" and value.lower() == b"websocket" for key, value in request.headers)
    headers = [(k, v) for k, v in _headers(request.headers, upgrade) if k != b"host"]
    headers.append((b"host", authority))
    await origin.send(h11.Request(method=request.method, target=path, headers=headers))
    async with asyncio.TaskGroup() as tasks:
        upload = tasks.create_task(_upload(client, origin))
        while True:
            event = await origin.event()
            if isinstance(event, (h11.InformationalResponse, h11.Response)):
                switching = event.status_code == 101
                if switching and not upgrade:
                    raise ValueError("Unexpected upgrade")
                await client.send(type(event)(
                    status_code=event.status_code, reason=event.reason,
                    headers=_headers(event.headers, switching),
                ))
                if switching:
                    await upload
                    await _tunnel(client, origin)
                    break
            elif isinstance(event, h11.Data):
                await client.send(event)
            elif isinstance(event, h11.EndOfMessage):
                await client.send(h11.EndOfMessage())
                break
            else:
                raise ValueError("Incomplete proxy response")
        upload.cancel()  # The origin can send a final response before reading the upload.


async def _connect(addresses, port):
    last_error = None
    for address in addresses:
        try:
            async with asyncio.timeout(3):
                return await asyncio.open_connection(address, port, flags=socket.AI_NUMERICHOST)
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("No usable addresses")


async def _local_websocket(client, origin, authority, *, capture):
    """A CONNECT to a stage or tvpipe can carry only a plaintext WebSocket."""
    inside = _Stream(client.reader, client.writer, h11.SERVER)
    early, eof = client.http.trailing_data
    if early or eof:
        inside.http.receive_data(early)
    async with asyncio.timeout(5):
        request = await inside.event()
    if not isinstance(request, h11.Request) or request.method != b"GET":
        raise ValueError("Expected WebSocket handshake")
    headers = dict(request.headers)
    connection = {v.strip().lower() for v in headers.get(b"connection", b"").split(b",")}
    if (
            not request.target.startswith(b"/")
            or (capture and not re.fullmatch(rb"/ws\?t=[a-fA-F0-9]{32}", request.target))
            or headers.get(b"host") != authority
            or headers.get(b"upgrade", b"").lower() != b"websocket"
            or b"upgrade" not in connection
            or b"content-length" in headers or b"transfer-encoding" in headers
    ):
        raise ValueError("Invalid local WebSocket handshake")
    await _http(inside, origin, request, authority, request.target)


class PageProxy:
    def __init__(self, policy: PageNetworkPolicy, *, open_capture=None):
        self.policy = policy
        self.open_capture = open_capture
        self.url = None
        self._server = None
        self._lock = asyncio.Lock()
        self._clients = set()
        self._denials = OrderedDict()

    async def start(self):
        async with self._lock:
            if self._server is None:
                self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
                port = self._server.sockets[0].getsockname()[1]
                self.url = f"http://127.0.0.1:{port}"
            return self.url

    def _accept(self, reader, writer):
        if self._server is None or len(self._clients) >= 128:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._clients.add(task)
        task.add_done_callback(self._clients.discard)

    def denial_for(self, url):
        """Find a recent denial for a failed document's exact destination."""
        try:
            parts = urlsplit(url)
            key = (parts.hostname, parts.port or (443 if parts.scheme in ("https", "wss") else 80))
        except ValueError:
            return None
        denied = self._denials.get(key)
        if denied and time.monotonic() - denied[0] <= 30:
            return BlockedAddress(*denied[1:])
        return None

    async def _serve(self, reader, writer):
        client = _Stream(reader, writer, h11.SERVER)
        upstream = None
        try:
            async with asyncio.timeout(10):
                request = await client.event()
                if not isinstance(request, h11.Request):
                    return
                host, port, authority, path = _destination(request)
                stage = host == "127.0.0.1" and port in self.policy.stage_ports
                capture = False
                try:
                    addresses = await self.policy.resolve(host, port, stage_allowed=True)
                except BlockedAddress as exc:
                    streams = None
                    if self.open_capture and host == "127.0.0.1" and request.method == b"CONNECT":
                        streams = await self.open_capture(host, port)
                    if streams is None:
                        self._denials[(host, port)] = (time.monotonic(), exc.kind, exc.address)
                        self._denials.move_to_end((host, port))
                        if len(self._denials) > 128:
                            self._denials.popitem(last=False)
                        # An HTTP error document counts as a successful no-cors fetch.
                        # Closing without a response gives every request a network error.
                        return
                    upstream_reader, upstream = streams
                    capture = True
                else:
                    upstream_reader, upstream = await _connect(addresses, port)
                    # A removed receiver's old port may already belong to another
                    # process. Recheck after connect, before forwarding any bytes.
                    if stage and port not in self.policy.stage_ports:
                        return
                self._denials.pop((host, port), None)
            origin = _Stream(upstream_reader, upstream, h11.CLIENT)
            if request.method == b"CONNECT":
                if not isinstance(await client.event(), h11.EndOfMessage):
                    raise ValueError("CONNECT has a request body")
                await client.send(h11.Response(status_code=200, reason=b"Connection Established", headers=[]))
                if capture or stage:
                    await _local_websocket(client, origin, authority, capture=capture)
                else:
                    await _tunnel(client, origin)
            else:
                await _http(client, origin, request, authority, path)
        except* (OSError, ValueError, h11.ProtocolError):
            pass  # A malformed request or failed upstream closes this connection.
        finally:
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    try:
                        async with asyncio.timeout(2):
                            await stream.wait_closed()
                    except (TimeoutError, OSError):
                        stream.transport.abort()

    async def close(self):
        async with self._lock:
            if self._server:
                server, self._server = self._server, None
                self.url = None
                server.close()
                server.abort_clients()
                clients = list(self._clients)
                for task in clients:
                    task.cancel()
                await asyncio.gather(*clients, return_exceptions=True)
                await server.wait_closed()
                self._denials.clear()
