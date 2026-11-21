import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import unquote

from aiohttp import web
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import MCPError

from . import __version__
from .stage import MAX_UPLOAD
from .tools.contract import SCHEMAS, ROUTED_SCHEMAS, INSTRUCTIONS, PAGE_GUIDE
from .tools.public import PublicTools

_LOG = logging.getLogger(__name__)
PEEK_LIMIT = 1 << 20


def describe(method, body):
    """Name the JSON-RPC method and tool without logging arguments."""
    if method != "POST":
        return method
    if len(body) > PEEK_LIMIT:
        return f"POST {len(body)} bytes"
    try:
        message = json.loads(body)
    except ValueError:
        return "POST unparsed"
    if not isinstance(message, dict):
        return "POST batch"
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    return " ".join(str(part) for part in ("POST", message.get("method"), params.get("name")) if part)


class MCP:
    def __init__(self, tools):
        fleet = hasattr(tools, "inventory")
        schemas = ROUTED_SCHEMAS if fleet else SCHEMAS

        resource_help = {
            "screen": "Accessible focus, app and screen state for the default TV, or per-TV text summaries when selection is ambiguous.",
            "apps": "Installed app names and bundle IDs. With several paired TVs, lists are keyed by receiver ID.",
            "page-guide": "Page sizing, bundle layout, live URLs and calling page functions with tv_page_run.",
            "tvs": "Registered receiver names, IDs, identifiers and pairing status. Use the ID as the tv selector.",
        }
        scoped_help = {
            "screen": "Read this receiver's current accessible items, focus and screen state.",
            "apps": "List this receiver's installed app names and bundle IDs for tv_open and tv_play.",
        }

        async def resources(_ctx, _params):
            names = [
                ("screen", "application/json"),
                ("apps", "application/json"),
                ("page-guide", "text/plain"),
            ]
            if fleet:
                names.append(("tvs", "application/json"))
                for tv in tools.inventory():
                    names.extend((kind + "/" + tv["id"], "application/json") for kind in ("screen", "apps"))
            return types.ListResourcesResult(resources=[
                types.Resource(
                    uri=f"tv://{name}", name=name, mime_type=mime,
                    description=scoped_help[name.split("/")[0]] if "/" in name else resource_help[name],
                )
                for name, mime in names
            ])

        async def read_resource(_ctx, params):
            uri = params.uri
            address = str(uri).rstrip("/")
            if address == "tv://page-guide":
                text, mime_type = PAGE_GUIDE, "text/plain"
            elif fleet:
                name, _, selector = address.removeprefix("tv://").partition("/")
                tv = unquote(selector) or None
                if name == "tvs":
                    data = tools.inventory()
                elif name == "screen":
                    data = await tools.call("tv_look", {"tv": tv} if tv else {})
                elif name == "apps":
                    if tv or sum(r["paired"] for r in tools.inventory()) == 1:
                        data = await tools.apps(tv)
                    else:
                        data = {
                            r["id"]: await tools.apps(r["id"])
                            for r in tools.inventory()
                            if r["paired"]
                        }
                else:
                    raise MCPError(code=types.INVALID_PARAMS, message="Unknown resource")
                text, mime_type = json.dumps(data), "application/json"
            elif address == "tv://apps":
                text, mime_type = json.dumps(await tools.apps()), "application/json"
            elif address == "tv://screen":
                data = tools.last_look or await tools.call("tv_look", {})
                text, mime_type = json.dumps(data), "application/json"
            else:
                raise MCPError(code=types.INVALID_PARAMS, message="Unknown resource")
            return types.ReadResourceResult(contents=[
                types.TextResourceContents(uri=uri, text=text, mime_type=mime_type)
            ])

        async def prompts(_ctx, _params):
            return types.ListPromptsResult(prompts=[
                types.Prompt(
                    name="present",
                    description="Get page design guidance and an HTML starter for a topic. Use tv_show to display the finished page.",
                    arguments=[types.PromptArgument(name="topic", required=True, description="Subject or purpose of the page to design.")],
                )
            ])

        async def prompt(_ctx, params):
            if params.name != "present" or not (params.arguments or {}).get("topic"):
                raise MCPError(code=types.INVALID_PARAMS, message="Supply a topic to the present prompt.")
            scaffold = """<!doctype html><meta name="viewport" content="width=device-width">
<style>body{margin:5vw;background:#111;color:white;font:36px system-ui}h1{font-size:64px}</style>
<h1></h1><main></main><script>window.render = text => {document.querySelector('main').textContent=text};</script>"""
            return types.GetPromptResult(
                messages=[
                    types.PromptMessage(
                        role="user",
                        content=types.TextContent(
                            type="text",
                            text=f"Create and present an HTML page about {params.arguments['topic']}.\n{PAGE_GUIDE}\nStart from this scaffold:\n{scaffold}",
                        ),
                    )
                ]
            )

        async def list_tools(_ctx, _params):
            return types.ListToolsResult(tools=[
                types.Tool(
                    name=name,
                    description=" ".join(getattr(PublicTools, name).__doc__.split()),
                    input_schema=schema,
                )
                for name, schema in schemas.items()
            ])

        async def call(_ctx, params):
            result = await tools.call(params.name, params.arguments or {})
            image = result.pop("image", None)
            content = [types.TextContent(type="text", text=json.dumps(result))]
            if image:
                content.append(types.ImageContent(type="image", **image))
            return types.CallToolResult(
                content=content, structured_content=result, is_error=not result["ok"]
            )

        server = Server(
            "Apple TV Agent Bridge",
            version=__version__,
            instructions=INSTRUCTIONS,
            get_tool_input_schema=schemas.get,
            on_list_resources=resources,
            on_read_resource=read_resource,
            on_list_prompts=prompts,
            on_get_prompt=prompt,
            on_list_tools=list_tools,
            on_call_tool=call,
        )
        self.manager = StreamableHTTPSessionManager(
            server, stateless=True, json_response=True, max_request_body_size=MAX_UPLOAD
        )

    @asynccontextmanager
    async def running(self):
        # aiohttp can run startup and cleanup in different tasks. AnyIO needs
        # its cancel scope entered and exited by the same task.
        ready, stopped = asyncio.Event(), asyncio.Event()

        async def lifetime():
            async with self.manager.run():
                ready.set()
                await stopped.wait()

        task = asyncio.create_task(lifetime())
        started = asyncio.create_task(ready.wait())
        try:
            await asyncio.wait((task, started), return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                await task
            yield
        finally:
            stopped.set()
            started.cancel()
            await asyncio.gather(started, return_exceptions=True)
            await task

    async def handle(self, request):
        """Adapt the SDK's ASGI streamable HTTP response to the existing aiohttp listener."""
        started = time.monotonic()
        body = await request.read()
        label = describe(request.method, body)
        _LOG.info("MCP %s received after %d ms of upload", label, (time.monotonic() - started) * 1000)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": request.scheme,
            "path": request.path,
            "raw_path": request.raw_path.encode(),
            "query_string": request.query_string.encode(),
            "headers": [(k.lower(), v) for k, v in request.raw_headers],
            "client": (request.remote, 0),
            "server": ("127.0.0.1", 8765),
        }
        response = None

        async def receive():
            nonlocal body
            if body is not None:
                chunk, body = body, None
                return {"type": "http.request", "body": chunk, "more_body": False}
            while request.transport and not request.transport.is_closing():
                await asyncio.sleep(0.25)
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response
            if message["type"] == "http.response.start":
                response = web.StreamResponse(status=message["status"])
                for key, value in message["headers"]:
                    response.headers.add(key.decode("latin1"), value.decode("latin1"))
                await response.prepare(request)
            elif message["type"] == "http.response.body":
                await response.write(message.get("body", b""))
                if not message.get("more_body"):
                    await response.write_eof()

        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            _LOG.info(
                "MCP %s answered %s in %d ms",
                label,
                response.status if response is not None else "nothing",
                (time.monotonic() - started) * 1000,
            )
        return response
