"""Read and operate the sender's committed Chromium document under the TV and stage locks."""

import asyncio
import ipaddress
import json
import re
from itertools import islice
from dataclasses import dataclass
from secrets import token_urlsafe
from urllib.parse import urldefrag, urljoin, urlsplit

import yaml
from playwright.async_api import ElementHandle, Error

from ..state import Fault

CONTROLS = set("button checkbox combobox link listbox menuitem menuitemcheckbox menuitemradio "
               "option radio searchbox slider spinbutton switch tab textbox treeitem".split())
REGIONS = set("banner complementary contentinfo dialog form main navigation region search".split())
FIELDS = {"textbox", "searchbox", "combobox", "listbox", "spinbutton", "slider"}
NODE = re.compile(r'^(\w+)(?: ("(?:[^"\\]|\\.)*"))?((?: \[[^\]]+\])*)$')
REF = re.compile(r'\[ref=((?:f\d+)?e\d+)\]')
STATES = ("disabled", "checked", "selected", "expanded")
TIMEOUT = 2000
# YAML excludes surrogates and most control characters. Unicode reserves the
# last two code points of each plane and U+FDD0..FDEF as noncharacters.
INVALID_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f\ud800-\udfff\ufdd0-\ufdef"
                          + "".join(chr(plane * 65536 + end) for plane in range(17)
                                    for end in (65534, 65535)) + "]")


def stale():
    return Fault("stale_view", "The page view or element has changed.", "Call tv_page_look again.")


def unverified():
    return Fault("stale_view", "The mirror no longer has the verified presentation.",
                 "Present the intended page again with tv_show.")


def missing():
    return Fault("target_missing", "This target is unavailable for the requested operation.",
                 "Read tv_page_look and choose a supported control.")


def form_key_blocked():
    return Fault("target_missing", "Enter and Space can submit a form.",
                 "Choose a control outside the form for this key.")


def uncertain():
    # Native TV operations already translate delivery_uncertain to the v1 'uncertain' code.
    return Fault("page_delivery_uncertain", "Chromium could not confirm dispatch.",
                 "Call tv_page_look to check the result before sending further input.")


def private_listener(url, ports):
    port = url.port or (443 if url.scheme == "https" else 80)
    if port not in ports:
        return False
    host = (url.hostname or "").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def quote(value):
    return json.dumps(INVALID_TEXT.sub("�", " ".join(str(value).split())[:96]), ensure_ascii=False)


@dataclass
class Row:
    role: str
    name: str
    states: list
    browser_ref: str | None
    ref: str | None = None

    def line(self):
        return " ".join(part for part in (self.ref, self.role, quote(self.name), *self.states) if part)


def snapshot_rows(tree, *, scoped=False):
    """Project Playwright's YAML. Discard URLs, frame subtrees and raw editable values."""
    for entry in tree:
        key, children = next(iter(entry.items())) if isinstance(entry, dict) else (entry, None)
        match = NODE.fullmatch(key)
        if not match:
            continue
        role, encoded_name, attributes = match.groups()
        if role == "iframe":
            continue
        name = json.loads(encoded_name) if encoded_name else ""
        identity = REF.search(attributes)
        browser_ref = identity[1] if identity else None
        states = [s for s in STATES if f"[{s}]" in attributes]
        if "[active]" in attributes:
            states.append("focused")
        if role in CONTROLS | REGIONS or role in ("heading", "status", "alert"):
            if role in ("status", "alert") and isinstance(children, str):
                name = " ".join(filter(None, (name, children)))
            yield Row(role, name, states, browser_ref)
        elif isinstance(children, str) and children.strip():
            yield Row("text", children, [], browser_ref)
        elif role == "generic" and browser_ref:
            # Inspect unnamed containers for live status and independent scrolling.
            yield Row("generic", name, [], browser_ref)
        if isinstance(children, list) and role not in CONTROLS:
            yield from snapshot_rows(children, scoped=scoped)
        elif scoped and isinstance(children, list) and role in ("combobox", "listbox", "treeitem"):
            yield from (row for row in snapshot_rows(children, scoped=True)
                        if row.role in ("option", "treeitem"))


# Only fixed, bridge-owned browser functions are evaluated. Field values in the ARIA
# serialization are never copied to the response. Read them from the pinned node here.
INSPECT = r"""(el, name) => {
  const hints = [name, el.type, el.name, el.id, el.autocomplete, el.placeholder,
    el.getAttribute('aria-label'), ...(el.labels || [])].map(x => x?.textContent ?? x).join(' ');
  const field = el.matches('input,textarea,select,[role=textbox],[role=combobox],[role=searchbox]') || el.isContentEditable;
  const secret = el.type === 'file' || field && /passw|passcode|secret|token|api.?key|private.?key|auth.?key|pincode|\bpwd\b|\botp\b|one.time|verification.?code|credit.?card|cc-|\bcvc\b|\bcvv\b|\bpin\b/i.test(hints);
  const restricted = 'a[href],area[href],[download],input[type=file],iframe,object,embed';
  const blocked = el.closest(restricted) ||
    el.closest('label')?.control?.type === 'file' || el.formTarget || el.form?.target ||
    (el.form && ['submit','image'].includes(el.type));
  const textField = el.matches('textarea,input:is([type=text],[type=search],[type=email],[type=tel],[type=url],:not([type]))') || el.isContentEditable;
  return {blocked:Boolean(blocked || secret), disabled:el.matches(':disabled,[aria-disabled=true]'),
    restrictedChild:Boolean(el.querySelector(restricted)), focused:el.matches(':focus'),
    formControl:Boolean(el.form || el.closest('form') || el.closest('label')?.control?.form),
    secret, textField, select:el.tagName === 'SELECT', live:el.matches('[aria-live=polite],[aria-live=assertive]'),
    scroll: el.clientHeight > 0 && el.scrollHeight > el.clientHeight && /auto|scroll|overlay/.test(getComputedStyle(el).overflowY),
    value:secret ? '[masked]' : typeof el.value === 'string' ? el.value.slice(0,160) : el.isContentEditable ? el.innerText.slice(0,160) : null};
}"""

OBSERVE = """(root, target) => {
  const focus = document.activeElement;
  const status = [...root.querySelectorAll('[role=status],[role=alert],[aria-live=polite],[aria-live=assertive]')]
    .filter(el => el.getClientRects().length && !el.closest('[hidden],[aria-hidden=true]') &&
       !el.matches('input,textarea,select,[contenteditable]') &&
       !el.querySelector('input,textarea,select,[contenteditable]'))
    .slice(0,3).map(el => el.innerText.slice(0,160)).join(' ');
  const label = focus?.getAttribute('aria-label') || focus?.labels?.[0]?.innerText ||
      (focus?.matches('button') ? focus.innerText : '') || focus?.localName;
  return {focused:focus === target, focus:label?.slice(0,120), status};
}"""


@dataclass
class Target:
    handle: ElementHandle
    row: Row


class PageInteraction:
    def __init__(self, ops):
        self.ops = ops
        self.targets = {}
        self.issued = set()
        self.root = self.frame = self.revision = self.view = None
        self.pending = self.next_row = None
        self.cursor = None
        self.first_ref = self.next_ref = 1
        self.last = {}

    def throttle(self, op, interval):
        now = asyncio.get_running_loop().time()
        if now - self.last.get(op, float("-inf")) < interval:
            raise Fault("busy", "Page operations are arriving too quickly.", "Wait briefly and retry.")
        self.last[op] = now

    async def clear(self, *, keep_document=False):
        handles = [t.handle for t in self.targets.values()]
        if not keep_document:
            if self.root:
                handles.append(self.root)
            self.root = self.frame = self.revision = None
        self.view = self.cursor = None
        if self.pending is not None:
            await self.pending.aclose()
        self.pending = self.next_row = None
        self.first_ref = self.next_ref
        self.targets.clear()
        self.issued.clear()
        await asyncio.gather(*(handle.dispose() for handle in handles), return_exceptions=True)

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
        if page.get("url") and (sender.page is None or sender.page is not sender.verified_page):
            raise unverified()
        try:
            frame = await sender.presented_frame(page["page_id"], page["revision"], direct)
        except Fault as exc:
            raise unverified() from exc
        expected = sender.verified_url if page.get("url") else urljoin(stage.origin, page["src"])
        if not frame or not expected:
            raise unverified()
        current, wanted = urlsplit(frame.url), urlsplit(expected)
        if (current.scheme, current.netloc) != (wanted.scheme, wanted.netloc):
            raise unverified()
        if page.get("url"):
            # Single-page apps change the URL in place. Accept a changed URL only
            # while the frame holds the document last seen at the verified URL.
            # A reload at the verified URL becomes that document.
            root = sender.verified_root
            try:
                same = root is not None and await root.evaluate(
                    "el => el === document.documentElement && el.isConnected"
                )
            except Error:
                same = False
            if not same:
                if urldefrag(frame.url).url != urldefrag(expected).url:
                    raise unverified()
                sender.verified_root = await frame.evaluate_handle("document.documentElement")
                if root is not None:
                    await asyncio.gather(root.dispose(), return_exceptions=True)
        elif not current.path.startswith(wanted.path.rsplit("/", 1)[0] + "/"):
            raise unverified()
        if urlsplit(expected).scheme not in ("http", "https"):
            raise unverified()
        if page.get("url") and private_listener(current, stage.stage_ports | {urlsplit(stage.origin).port, sender.debug_port}):
            raise Fault("target_missing", "Page control cannot operate a bridge listener.",
                        "Use tv_show to present your content URL or bundle.")
        return frame, page["revision"], "direct" if direct else "iframe"

    async def check(self):
        frame, revision, _ = await self.binding()
        if frame != self.frame or revision != self.revision:
            raise stale()
        if not self.root or not await self.root.evaluate(
            "el => el === document.documentElement && el.isConnected && /^https?:/.test(document.documentURI)"
        ):
            raise stale()

    async def read_row(self, row):
        if not row.browser_ref:
            return True
        handles = await self.frame.locator("aria-ref=" + row.browser_ref).element_handles()
        if len(handles) != 1:
            for handle in handles:
                await handle.dispose()
            return False
        handle = handles[0]
        keep = False
        try:
            if await handle.owner_frame() != self.frame:
                return False
            info = await handle.evaluate(INSPECT, row.name)
            if info["focused"] and "focused" not in row.states:
                row.states.append("focused")
            if info["secret"] and row.role not in FIELDS:
                row.name = "[masked]"
            if row.role in FIELDS and info["value"] is not None:
                row.states.append("value=" + quote(info["value"]))
            if row.role == "generic" and info["scroll"]:
                row.role = "region"
            if row.role in ("text", "generic") and info["live"]:
                row.role = "status"
            addressable = row.role in REGIONS or info["scroll"] or (row.role in CONTROLS and not info["restrictedChild"])
            if addressable and not info["blocked"]:
                row.ref = f"e{self.next_ref}"
                self.next_ref += 1
                self.targets[row.ref] = Target(handle, row)
                keep = True
            return row.role != "generic"
        finally:
            if not keep:
                await handle.dispose()

    async def choices(self, row):
        """A scoped native select lists the exact values accepted by the action."""
        target = self.targets.get(row.ref)
        if not target or row.role not in ("combobox", "listbox"):
            return []
        options = await target.handle.evaluate("""el => el.tagName === 'SELECT'
          ? [...el.options].map(o => [o.label, o.value, o.selected, o.matches(':disabled')]) : []""")
        return [Row("option", label, ["value=" + quote(value)] +
                    (["selected"] if selected else []) + (["disabled"] if disabled else []), None)
                for label, value, selected, disabled in options]

    async def look(self, find=None, scope=None, cursor=None):
        self.throttle("look", 1)
        try:
            async with self.ops.stage.lock, asyncio.timeout(5):
                if cursor:
                    await self.check()
                    if cursor != self.cursor:
                        raise stale()
                    return await self.slice()
                frame, revision, mode = await self.binding()
                scope_ref = None
                options = []
                if scope:
                    if not self.view:
                        raise stale()
                    await self.check()
                    target = await self.target(scope)
                    scope_ref = target.row.browser_ref
                    options = await self.choices(target.row)
                await self.clear(keep_document=scope is not None)
                self.frame, self.revision, self.mode = frame, revision, mode
                if scope is None:
                    self.root = await frame.evaluate_handle("document.documentElement")
                await self.check()
                locator = frame.locator("aria-ref=" + scope_ref if scope_ref else "body")
                snapshot = await locator.aria_snapshot(mode="ai", timeout=TIMEOUT)
                if len(snapshot) > 512_000:
                    raise Fault("page_unreachable", "The page exceeds the snapshot limit.", "Show a smaller page.")
                loader = getattr(yaml, "CBaseLoader", yaml.BaseLoader)
                tree = yaml.load(INVALID_TEXT.sub("�", snapshot), Loader=loader) or []
                rows = list(snapshot_rows(tree, scoped=scope is not None))
                if options and rows and rows[0].browser_ref == scope_ref:
                    rows = [rows[0], *options]
                self.pending = self.visible_rows(rows, find)
                self.next_row = await anext(self.pending, None)
                self.title = await frame.title() or self.ops.stage.page.get("title") or ""
                await self.check()
                self.view = token_urlsafe(14)
                return await self.slice()
        except (yaml.YAMLError, ValueError, RecursionError) as exc:
            await self.clear()
            raise Fault("page_unreachable", "Chromium returned an unreadable snapshot.",
                        "Simplify the page and present it again with tv_show.") from exc
        except (Error, ExceptionGroup, TimeoutError) as exc:
            await self.clear()
            raise stale() from exc

    async def visible_rows(self, rows, find):
        """Resolve a small batch at a time. A cursor resumes this snapshot."""
        query = find.casefold() if find else ""
        inspected = {}

        async def read(index):
            if index not in inspected:
                row = rows[index]
                inspected[index] = row if await self.read_row(row) else None
            return inspected[index]

        def candidate(row):
            # Fields, live regions and scrolling containers gain searchable text
            # during inspection. Ordinary buttons already have their label and states.
            if row.role not in CONTROLS or row.role in FIELDS:
                return True
            if re.match(r"^e?\d", query):
                return True  # A public ref is assigned during inspection.
            masked = Row(row.role, "[masked]", row.states, None)
            return (query in row.name.casefold() or query in row.line().casefold() + " focused"
                    or query in masked.line().casefold() + " focused")

        indices = (i for i, row in enumerate(rows) if not query or candidate(row))
        last = -1
        while batch := list(islice(indices, 16)):
            async with asyncio.TaskGroup() as group:
                for index in batch:
                    group.create_task(read(index))
            for index in batch:
                row = inspected[index]
                if row is None:
                    continue
                if not query:
                    yield row
                    continue
                position = row.name.casefold().find(query)
                if position < 0 and query not in row.line().casefold():
                    continue
                if position > 64 and row.role == "text":
                    row.name = "…" + row.name[position - 24:]
                # Include the nearest visible row on either side, once, even
                # when a generic container disappears during inspection.
                before, after = index - 1, index + 1
                while before > last and await read(before) is None:
                    before -= 1
                while after < len(rows) and await read(after) is None:
                    after += 1
                for neighbor in (before, index, after):
                    if last < neighbor < len(rows):
                        yield inspected[neighbor]
                        last = neighbor

    async def slice(self):
        page_id = self.ops.stage.page["page_id"]
        header = f"page_id={page_id} view={self.view} title={quote(self.title)} more="
        lines = []
        size = len(header.encode()) + 5
        self.cursor = None
        while self.next_row is not None and len(lines) < 39:
            line = self.next_row.line()
            size += len(line.encode()) + 1
            if size > 2048:
                break
            if self.next_row.ref:
                self.issued.add(self.next_row.ref)
            lines.append(line)
            self.next_row = await anext(self.pending, None)
        await self.check()
        more = self.next_row is not None
        self.cursor = token_urlsafe(14) if more else None
        return {"page_id": page_id, "view": self.view, "mode": self.mode, "more": more,
                "snapshot": "\n".join([header + str(more).lower(), *lines]),
                **({"cursor": self.cursor} if more else {})}

    async def target(self, ref):
        target = self.targets.get(ref)
        if not target or ref not in self.issued:
            if re.fullmatch(r"e[1-9][0-9]*", ref) and int(ref[1:]) < self.first_ref:
                raise stale()
            raise missing()
        if not await target.handle.evaluate("el => el.isConnected && el.ownerDocument === document"):
            raise stale()
        return target

    async def act(self, view, ref, action, text=None, value=None, key=None, direction=None):
        self.throttle("act", .25)
        dispatched = False
        try:
            async with self.ops.stage.lock, asyncio.timeout(3):
                await self.binding()
                if not self.view or view != self.view:
                    raise stale()
                await self.check()
                name = ""
                old_value = None
                if ref == "document" and action == "scroll":
                    handle = self.root
                else:
                    target = await self.target(ref)
                    handle = target.handle
                    name = target.row.name
                    info = await handle.evaluate(INSPECT, name)
                    old_value = info["value"]
                    if info["blocked"] or info["disabled"] or not await handle.is_visible():
                        raise missing()
                    form_key = action == "press" and key in ("Enter", "Space")
                    if form_key and info["formControl"]:
                        raise form_key_blocked()
                    if action == "scroll" and not info["scroll"]:
                        raise missing()
                    if action != "scroll" and (target.row.role not in CONTROLS or info["restrictedChild"]):
                        raise missing()
                    if action == "fill" and (not info["textField"] or not await handle.is_editable()):
                        raise missing()
                    if action == "select" and (not info["select"] or not await handle.evaluate(
                        "(el,v) => [...el.options].filter(o => o.value === v && !o.matches(':disabled')).length === 1", value
                    )):
                        raise missing()
                before = await self.root.evaluate(OBSERVE, handle)
                await self.check()
                if not await handle.evaluate("el => el.isConnected && el.ownerDocument === document"):
                    raise stale()
                dispatched = True
                if action in ("press", "fill"):
                    # A page's focus handler can redirect focus to another element or frame.
                    await handle.focus()
                    await self.check()
                    if not await handle.evaluate("el => el.matches(':focus')"):
                        dispatched = False  # Focus settled elsewhere. No key or text was sent.
                        raise missing()
                    if form_key and (await handle.evaluate(INSPECT, name))["formControl"]:
                        dispatched = False
                        raise form_key_blocked()
                if action == "click":
                    await handle.click(timeout=TIMEOUT)
                elif action == "fill":
                    await handle.fill(text, timeout=TIMEOUT)
                elif action == "select":
                    await handle.select_option(value=value, timeout=TIMEOUT)
                elif action == "press":
                    await handle.press(key, timeout=TIMEOUT)
                else:
                    await handle.evaluate("""(el, sign) => {
                      const box = el === document.documentElement ? document.scrollingElement : el;
                      const height = el === document.documentElement ? innerHeight : box.clientHeight;
                      box.scrollBy({top:sign * height, behavior:"instant"});
                    }""", 1 if direction == "down" else -1)
                await self.check()
                after = await self.root.evaluate(OBSERVE, handle)
                changes = {k: v for k, v in after.items() if before[k] != v}
                new_value = (await handle.evaluate(INSPECT, name))["value"]
                if new_value != old_value:
                    changes["value"] = new_value
                await self.check()
                self.view = token_urlsafe(14)
                self.cursor = None
                return {"dispatched": True, "view": self.view, **changes}
        except (Fault, Error, TimeoutError) as exc:
            if dispatched:
                self.view = self.cursor = None
                raise uncertain() from exc
            if isinstance(exc, Fault):
                raise
            raise stale() from exc
