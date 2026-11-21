import base64
import binascii
import asyncio
import hmac
import io
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from aiohttp import WSMsgType, web

from .page_network import PageNetworkPolicy
from .state import Fault, success

MAX_UPLOAD = 50 * 1024**2


def bad_bundle(message):
    return Fault(
        "invalid_bundle", message, "Upload a ZIP with index.html at its root and relative file paths."
    )


class ContentStore:
    def __init__(self, root, capacity=500 * 1024**2):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        self.active = None
        self.pinned = {}
        self.lock = asyncio.Lock()
        for path in self.root.glob(".upload-*"):
            shutil.rmtree(path)

    def _put(self, html, archive):
        with tempfile.TemporaryDirectory(dir=self.root, prefix=".upload-") as temp:
            page = Path(temp) / "page"
            page.mkdir()
            if html is not None:
                (page / "index.html").write_text(html)
            else:
                self._extract(archive, page)
            size = self.size(page)
            existing = sorted(
                (p for p in self.root.iterdir() if not p.name.startswith(".")),
                key=lambda p: p.stat().st_mtime,
            )
            sizes = {p: self.size(p) for p in existing}
            protected = set(self.pinned.values()) | {self.active}
            if size + sum(n for p, n in sizes.items() if p.name in protected) > self.capacity:
                raise Fault(
                    "content_full",
                    "Page exceeds available content capacity.",
                    "Hide the current page or upload less.",
                )
            total = size + sum(sizes.values())
            for old in existing:
                if total <= self.capacity:
                    break
                if old.name not in protected:
                    shutil.rmtree(old)
                    total -= sizes[old]
            page_id = uuid.uuid4().hex
            page.rename(self.root / page_id)
            return page_id

    @staticmethod
    def size(path):
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())

    def _extract(self, archive, page):
        if len(archive) > MAX_UPLOAD:
            raise bad_bundle("ZIP exceeds 50 MiB.")
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                entries = z.infolist()
                if len(entries) > 8192 or sum(x.file_size for x in entries) > MAX_UPLOAD:
                    raise bad_bundle("Unpacked bundle exceeds the file or size limit.")
                for info in entries:
                    name = PurePosixPath(info.filename)
                    mode = stat.S_IFMT(info.external_attr >> 16)
                    if (
                        not name.parts
                        or name.is_absolute()
                        or ".." in name.parts
                        or "\\" in info.filename
                        or ":" in name.parts[0]
                        or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    ):
                        raise bad_bundle("Unsafe ZIP member.")
                    target = page.joinpath(*name.parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(info) as src, target.open("xb") as dst:
                            shutil.copyfileobj(src, dst)
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
            raise bad_bundle("Malformed ZIP or conflicting file names.") from exc
        if not (page / "index.html").is_file():
            raise bad_bundle("Missing index.html.")

    async def put(self, *, html=None, archive=None):
        async with self.lock:
            worker = asyncio.create_task(asyncio.to_thread(self._put, html, archive))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Extraction may still publish or evict. Hold the store lock until it finishes.
                await asyncio.gather(worker, return_exceptions=True)
                raise

    async def put_files(self, files):
        def archive():
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z:
                for path, value in files.items():
                    try:
                        data = (
                            base64.b64decode(value[7:], validate=True)
                            if value.startswith("base64:")
                            else value.encode()
                        )
                    except (binascii.Error, ValueError) as exc:
                        raise bad_bundle("Invalid base64 file.") from exc
                    z.writestr(path, data)
                    if out.tell() > MAX_UPLOAD:
                        raise bad_bundle("Inline files exceed 50 MiB.")
            return out.getvalue()

        return await self.put(archive=await asyncio.to_thread(archive))

    def file(self, page_id, relative):
        if len(page_id) != 32 or any(c not in "0123456789abcdef" for c in page_id):
            raise web.HTTPNotFound()
        root = self.root / page_id
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise web.HTTPNotFound()
        os.utime(root)
        return path


class Stage:
    def __init__(self, root, store=None):
        self.origin = "http://127.0.0.1:8766"
        # Standalone mode sets a per-process secret that the stage page and its socket require.
        self.key = None
        self.stage_ports = set()
        self.page_network = PageNetworkPolicy(stage_ports=self.stage_ports)
        self.store = store or ContentStore(root / "content")
        self.media_origin = self.media_prefix = None
        self.media = root / "media"
        self.media.mkdir(exist_ok=True)
        self.media_reads = {}
        self.page = None
        self.direct_browser = None
        self.dns_status = {"hostname": None, "resolved": None}
        self.ws = None
        self.ready = asyncio.Event()
        self.lock = asyncio.Lock()
        self.on_change = None  # Called after every show and hide.

    async def show(self, page_id, src, title=None, **options):
        self.direct_browser = None
        self.page = {
            "type": "show",
            "revision": uuid.uuid4().hex,
            "page_id": page_id,
            "src": src,
            "title": title,
            **options,
        }
        self.store.pinned[self] = page_id
        self.ready.clear()
        if self.on_change:
            self.on_change()
        if self.ws:
            await self.ws.send_json(self.page)

    async def hide(self):
        self.store.pinned.pop(self, None)
        self.page = self.direct_browser = None
        self.ready.clear()
        if self.on_change:
            self.on_change()
        if self.ws:
            await self.ws.close()

    @property
    def url(self):
        """The stage page that the bridge's browser loads."""
        return self.origin + "/stage" + (f"?key={self.key}" if self.key else "")

    def local(self, request):
        if request.remote not in ("127.0.0.1", "::1"):
            raise web.HTTPForbidden()
        # Loopback is not private when a container shares the host's network.
        if self.key and not hmac.compare_digest(request.query.get("key", "").encode(), self.key.encode()):
            raise web.HTTPForbidden()

    async def websocket(self, request):
        self.local(request)
        if request.headers.get("Origin") != self.origin:
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        if self.ws:
            await self.ws.close()
        self.ws = ws
        self.ready.clear()
        try:
            if self.page:
                await ws.send_json(self.page)
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    break
                event = msg.json()
                if not self.page or event.get("page_id") != self.page["page_id"]:
                    continue
                if event.get("type") == "ready" and event.get("revision") == self.page["revision"]:
                    self.ready.set()
        finally:
            if self.ws is ws:
                self.ws = None
                self.ready.clear()
        return ws

    def app(self, media_stage=None):
        async def index(request):
            self.local(request)
            # Keep other pages from framing the stage, and keep the key out of the Referer
            # header that the stage's own pages would otherwise read.
            headers = {"Content-Security-Policy": "frame-ancestors 'none'", "Referrer-Policy": "no-referrer"}
            return web.FileResponse(
                Path(__file__).parent / "data" / "stage.html", headers=headers if self.key else None
            )

        async def content(request):
            path = self.store.file(request.match_info["page_id"], request.match_info["file"])
            return web.FileResponse(
                path,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Security-Policy": "sandbox allow-scripts allow-forms",
                },
            )

        def media_owner(request):
            tv = request.match_info.get("receiver")
            if media_stage is None:
                return self
            owner = media_stage(tv)
            if owner is None:
                raise web.HTTPNotFound()
            return owner

        async def websocket(request):
            return await media_owner(request).websocket(request)

        async def media(request):
            owner = media_owner(request)
            path = (owner.media / request.match_info["file"]).resolve()
            if not path.is_relative_to(owner.media.resolve()) or not path.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(path)

        async def confirm_media_read(request, response):
            if (
                request.match_info.route.name in ("media", "receiver_media")
                and request.method == "GET"
                and response.status in (200, 206)
            ):
                event = media_owner(request).media_reads.get(
                    (request.match_info["file"], request.remote, request.query.get("session"))
                )
                if event:
                    event.set()

        async def live(request):
            return web.json_response(success("running"))

        app = web.Application()
        app.on_response_prepare.append(confirm_media_read)
        if media_stage is not None:
            app.router.add_get("/m/{receiver}/{file}", media, name="receiver_media")
        app.add_routes(
            [
                web.get("/stage", index),
                web.get("/stage/ws", websocket),
                web.get("/c/{page_id}/{file:.*}", content),
                web.get("/m/{file:.*}", media, name="media"),
                web.get("/api/health", live),
            ]
        )
        return app
