"""Run an admin request in the exact HTTP(S) content document captured by tvpipe."""

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error

from ..state import Fault

SCRIPT_LIMIT = 65536
RUN_SCRIPT = r"""async ({script, arg, uri, limit}) => {
  // Chromium can report the requested frame URL for an internal error document.
  // Check the committed URI in the same evaluation that runs the caller's code.
  if (document.documentURI !== uri) return {changed: document.documentURI};

  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    let run;
    try {
      run = new AsyncFunction('arg', '"use strict"; return (\n' + script.trimEnd().replace(/;$/, '') + '\n)');
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      run = new AsyncFunction('arg', '"use strict";\n' + script);
    }
    let value = await run(arg);
    if (typeof value === 'function') value = await value(arg);
    const json = JSON.stringify(value === undefined ? null : value);
    if (json === undefined) throw new TypeError("The result is not JSON-serializable");
    const encoded = new TextEncoder().encode(json);
    if (encoded.length <= limit) return {json, truncated: false};
    const preview = new TextDecoder().decode(encoded.subarray(0, limit), {stream: true});
    return {preview, bytes: encoded.length, truncated: true};
  } catch (error) {
    return {error: String(error?.message ?? error).slice(0, 512),
      stack: String(error?.stack ?? '').split('\n').slice(0, 5).join('\n').slice(0, 1024)};
  }
}"""


def stale():
    return Fault("stale_view", "The presented document changed during the call.", "Inspect the current document with tv_page_run before retrying.")


def unverified():
    return Fault("stale_view", "The mirror no longer has the verified presentation.",
                 "Present the intended page again with tv_show.")


def local_address(host):
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_unspecified:
        return True
    # The primary stage also listens on the host's LAN interfaces.
    with socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET) as sock:
        try:
            sock.bind((str(address), 0))
        except OSError:
            return False
    return True


class PageInteraction:
    def __init__(self, ops):
        self.ops = ops
        self.debug_ports = set
        self.last_run = float("-inf")

    async def binding(self):
        stage = self.ops.stage
        if not stage.page:
            raise Fault("nothing_showing", "No bridge page is showing.", "Use tv_show first.")
        presenter = self.ops.presenter
        sender = getattr(presenter, "active", presenter)
        if sender.mode != "mirror":
            raise Fault("page_not_live", "The presentation is a recording.", "Show a mirrored page.")
        direct = stage.direct_browser is sender
        if not sender.started or (not direct and not stage.ready.is_set()):
            raise unverified()
        page = stage.page
        try:
            frame = await sender.presented_frame(page["page_id"], page["revision"], direct)
        except (Fault, Error) as exc:
            raise unverified() from exc
        if not frame:
            raise unverified()
        uri = frame.url
        await self.allow_document(uri, direct, frame)
        return frame, uri, direct

    async def allow_document(self, uri, direct, frame):
        stage = self.ops.stage
        page = stage.page
        sender = getattr(self.ops.presenter, "active", self.ops.presenter)
        current = urlsplit(uri)
        if current.scheme not in ("http", "https"):
            raise Fault("target_missing", "The browser is displaying an internal or error document.",
                        "Use tv_show to present an HTTP(S) page.")
        ports = stage.stage_ports | self.debug_ports() | {8765, 8099, urlsplit(stage.origin).port, sender.debug_port}
        if (current.port or (443 if current.scheme == "https" else 80)) not in ports:
            return
        # An inline bundle lives on the stage listener in an opaque-origin iframe.
        bundle = urlsplit(urljoin(stage.origin, "/c/" + page["page_id"] + "/"))
        if (not direct and not page.get("url") and current.netloc == bundle.netloc
                and current.scheme == bundle.scheme and current.path.startswith(bundle.path)):
            return
        local = local_address(current.hostname or "")
        if local is None:
            peer = await sender.document_peer(frame, uri)
            local = local_address(peer) if peer else None
        if local is not False:
            raise Fault("target_missing", "Page control cannot operate a bridge listener.",
                        "Use tv_show to present your content URL or bundle.")

    async def run(self, script, arg=None, timeout_ms=5000):
        now = asyncio.get_running_loop().time()
        if now - self.last_run < .25:
            raise Fault("busy", "Page scripts are arriving too quickly.", "Wait briefly and retry.")
        self.last_run = now
        try:
            async with asyncio.timeout(timeout_ms / 1000), self.ops.stage.lock:
                frame, uri, direct = await self.binding()
                result = await frame.evaluate(RUN_SCRIPT, {"script": script, "arg": arg, "uri": uri, "limit": SCRIPT_LIMIT})
                if "changed" in result:
                    await self.allow_document(result["changed"], direct, frame)
                    raise stale()
                if "error" in result:
                    raise Fault("script_error", result["error"] or "The script threw an error.",
                                "Inspect the stack excerpt and correct the script.", stack=result["stack"])
                if result["truncated"]:
                    return result
                return {"result": json.loads(result["json"]), "truncated": False}
        except TimeoutError as exc:
            raise Fault("script_error", f"The script exceeded the {timeout_ms} ms deadline.",
                        "Read the page before retrying. The script may still have effects.") from exc
        except Error as exc:
            raise stale() from exc
