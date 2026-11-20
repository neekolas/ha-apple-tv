import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import unquote

from aiohttp import web
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .tools.contract import SCHEMAS, ROUTED_SCHEMAS, INSTRUCTIONS, PAGE_GUIDE
from .tools.public import PublicTools


class MCP:
    def __init__(self, tools):
        server = Server("Apple TV Agent Bridge", instructions=INSTRUCTIONS)

        fleet = hasattr(tools, "inventory")

        resource_help = {
            "screen": "Accessible focus, app and screen state for the default TV, or per-TV text summaries when selection is ambiguous.",
            "apps": "Installed app names and bundle IDs. With several paired TVs, lists are keyed by receiver ID.",
            "page-guide": "Page sizing, bundle layout, live URLs and the JavaScript update interface for tv_show.",
            "tvs": "Registered receiver names, IDs, identifiers and pairing status. Use the ID as the tv selector.",
        }
        scoped_help = {
            "screen": "Read this receiver's current accessible items, focus and screen state.",
            "apps": "List this receiver's installed app names and bundle IDs for tv_open and tv_play.",
        }

        @server.list_resources()
        async def resources():
            names = [
                ("screen", "application/json"),
                ("apps", "application/json"),
                ("page-guide", "text/plain"),
            ]
            if fleet:
                names.append(("tvs", "application/json"))
                for tv in tools.inventory():
                    names.extend((kind + "/" + tv["id"], "application/json") for kind in ("screen", "apps"))
            return [
                types.Resource(
                    uri=f"tv://{name}", name=name, mimeType=mime,
                    description=scoped_help[name.split("/")[0]] if "/" in name else resource_help[name],
                )
                for name, mime in names
            ]

        @server.read_resource()
        async def read_resource(uri):
            address = str(uri).rstrip("/")
            if address == "tv://page-guide":
                return [ReadResourceContents(PAGE_GUIDE, mime_type="text/plain")]
            if fleet:
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
                    raise ValueError("Unknown resource")
                return [ReadResourceContents(json.dumps(data), mime_type="application/json")]
            if address == "tv://apps":
                return [
                    ReadResourceContents(json.dumps(await tools.apps()), mime_type="application/json")
                ]
            if address == "tv://screen":
                return [
                    ReadResourceContents(
                        json.dumps(tools.last_look or await tools.call("tv_look", {})),
                        mime_type="application/json",
                    )
                ]
            raise ValueError("Unknown resource")

        @server.list_prompts()
        async def prompts():
            return [
                types.Prompt(
                    name="present",
                    description="Get page design guidance and an HTML starter for a topic. Use tv_show to display the finished page.",
                    arguments=[types.PromptArgument(name="topic", required=True, description="Subject or purpose of the page to design.")],
                )
            ]

        @server.get_prompt()
        async def prompt(name, arguments):
            if name != "present" or not (arguments or {}).get("topic"):
                raise ValueError("Supply a topic to the present prompt.")
            scaffold = """<!doctype html><meta name="viewport" content="width=device-width">
<style>body{margin:5vw;background:#111;color:white;font:36px system-ui}h1{font-size:64px}</style>
<h1></h1><main></main><script>tv.on('update', data => {document.querySelector('main').textContent=data.text; tv.state=data}); tv.ready();</script>"""
            return types.GetPromptResult(
                messages=[
                    types.PromptMessage(
                        role="user",
                        content=types.TextContent(
                            type="text",
                            text=f"Create and present an HTML page about {arguments['topic']}.\n{PAGE_GUIDE}\nStart from this scaffold:\n{scaffold}",
                        ),
                    )
                ]
            )

        @server.list_tools()
        async def list_tools():
            return [
                types.Tool(
                    name=name,
                    description=" ".join(getattr(PublicTools, name).__doc__.split()),
                    inputSchema=schema,
                )
                for name, schema in (ROUTED_SCHEMAS if fleet else SCHEMAS).items()
            ]

        @server.call_tool(validate_input=False)
        async def call(name, arguments):
            result = await tools.call(name, arguments)
            image = result.pop("image", None)
            content = [types.TextContent(type="text", text=json.dumps(result))]
            if image:
                content.append(types.ImageContent(type="image", **image))
            return types.CallToolResult(
                content=content, structuredContent=result, isError=not result["ok"]
            )

        self.manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)

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
        body = await request.read()
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

        await self.manager.handle_request(scope, receive, send)
        return response
