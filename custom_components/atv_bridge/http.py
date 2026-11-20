"""Admin-only streaming proxy to the app."""

import logging

from aiohttp import ClientError, ClientTimeout, web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.http.decorators import require_admin

from .const import DOMAIN

REQUEST_HEADERS = (
    "Accept",
    "Content-Type",
    "Mcp-Session-Id",
    "MCP-Protocol-Version",
    "Mcp-Method",
    "Mcp-Name",
    "Last-Event-ID",
)
RESPONSE_HEADERS = (
    "Content-Type",
    "Mcp-Session-Id",
    "MCP-Protocol-Version",
    "Cache-Control",
    "Allow",
)
CHUNK_SIZE = 64 * 1024
# The app stops a tool call after 150 s, so a silent upstream past this is a hang. GET streams stay open.
POST_READ_TIMEOUT = 200
_LOGGER = logging.getLogger(__name__)


class BridgeView(HomeAssistantView):
    requires_auth = True
    upstream_path: str

    @require_admin
    async def forward(self, request: web.Request, **route_args) -> web.StreamResponse:
        entries = request.app[KEY_HASS].config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise web.HTTPServiceUnavailable(text="Apple TV Agent Bridge is offline")
        client = entries[0].runtime_data.client
        headers = {
            key: request.headers[key]
            for key in REQUEST_HEADERS
            if key in request.headers
        }
        headers.update(client.headers)
        path = self.upstream_path
        if self.name == "api:atv_bridge:tool":
            tool = request.match_info["name"]
            if not tool.isidentifier():
                raise web.HTTPNotFound()
            path += "/" + tool
        url = client.base_url.with_path(path).with_query(request.query)
        try:
            upstream = await client.session.request(
                request.method,
                url,
                headers=headers,
                data=request.content.iter_chunked(CHUNK_SIZE)
                if request.can_read_body
                else None,
                timeout=ClientTimeout(
                    total=None,
                    connect=10,
                    sock_read=POST_READ_TIMEOUT if request.method == "POST" else None,
                ),
                allow_redirects=False,
            )
        except TimeoutError as err:
            _LOGGER.warning("Apple TV Agent Bridge did not answer %s %s in time", request.method, path)
            raise web.HTTPGatewayTimeout(text="Apple TV Agent Bridge did not answer in time") from err
        except ClientError as err:
            _LOGGER.warning("Cannot reach Apple TV Agent Bridge for %s %s: %s", request.method, path, err)
            raise web.HTTPBadGateway(text="Cannot reach Apple TV Agent Bridge") from err

        async with upstream:
            response = web.StreamResponse(
                status=upstream.status,
                headers={
                    key: upstream.headers[key]
                    for key in RESPONSE_HEADERS
                    if key in upstream.headers
                },
            )
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                await response.write(chunk)
            await response.write_eof()
            return response


class MCPView(BridgeView):
    url = "/api/atv_bridge/mcp"
    name = "api:atv_bridge:mcp"
    upstream_path = "/mcp"
    get = post = delete = BridgeView.forward


class BundleView(BridgeView):
    url = "/api/atv_bridge/bundle"
    name = "api:atv_bridge:bundle"
    upstream_path = "/api/bundle"
    post = BridgeView.forward


class ToolView(BridgeView):
    url = "/api/atv_bridge/tool/{name}"
    name = "api:atv_bridge:tool"
    upstream_path = "/api/tool"
    post = BridgeView.forward
