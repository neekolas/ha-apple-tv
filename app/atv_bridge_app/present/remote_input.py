"""Forward Siri Remote presses to the mirrored page as trusted key events."""

import asyncio
import logging
import time
from contextlib import suppress

from ..state import classify, now

_LOG = logging.getLogger(__name__)

MODES = ("off", "keys", "focus")
KEYS = {
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "select": "Enter",
}
STALE_S = 2.0
QUEUE_LIMIT = 8
RETRY_MAX_S = 30
# Reasons for state "unavailable" that start in the app. The observer adds its own
# (atv_bridge_observer/buttons.py, class Unavailable).
REASONS = {"pairing_required": "screen_not_paired", "screen_pairing_invalid": "screen_pairing_invalid"}

# Runs in the stage document. Key events go to the focused frame.
FOCUS_STAGE = """() => {
  const frame = document.querySelector('iframe.visible');
  if (frame && document.activeElement !== frame) frame.focus();
}"""

# Runs in the content document before every key. It counts trusted key presses in
# window.__tvRemote and returns the document's id and count.
# Hardware finding: after a few idle minutes the sandboxed frame drops a key unless a
# script ran in it just before. This evaluation is that script. Do not remove it.
PRESS_LOG = """() => {
  if (!window.__tvRemote) {
    const log = window.__tvRemote = {id: Math.random().toString(36).slice(2), count: 0, last: null};
    addEventListener('keydown', event => {
      if (event.isTrusted) { log.count += 1; log.last = event.key; }
    }, true);
  }
  return [window.__tvRemote.id, window.__tvRemote.count];
}"""

# Runs in the content document. Safe to run before every key.
FOCUS_HELPER = r"""() => {
  if (window.__tvRemoteFocus) return;
  window.__tvRemoteFocus = true;
  try {
    // A constructed sheet is not blocked by the page's style-src policy.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(':focus{outline:6px solid #0a84ff!important;outline-offset:4px!important;' +
      'box-shadow:0 0 0 12px rgba(10,132,255,.35)!important}');
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  } catch {}
  const FOCUSABLE = 'a[href],button,input,select,textarea,summary,[tabindex],[contenteditable=""],[contenteditable="true"]';
  // A trusted Enter already activates these elements.
  const NATIVE = 'a[href],button,select,textarea,summary,input:not([type=checkbox]):not([type=radio])';
  const DIRECTIONS = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
  const usable = el => !el.disabled && el.tabIndex >= 0 && !el.closest('[inert],[aria-hidden=true]')
    && el.checkVisibility({visibilityProperty: true});
  // The bubble phase lets the page handle the key first.
  addEventListener('keydown', event => {
    if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
    let active = document.activeElement;
    if (!active || active === document.body || active === document.documentElement) active = null;
    if (event.key === 'Enter') {
      if (active && !active.matches(NATIVE)) {
        event.preventDefault();
        active.click();
      }
      return;
    }
    const dir = DIRECTIONS[event.key];
    if (!dir) return;
    // These controls use the arrow on that axis themselves.
    if (active && ((active.localName === 'select' && dir[1])
        || (active.localName === 'input' && active.type === 'range' && dir[0]))) return;
    const items = [...document.querySelectorAll(FOCUSABLE)].filter(el => el !== active && usable(el))
      .map(el => [el, el.getBoundingClientRect()]).filter(([, r]) => r.width > 0 && r.height > 0);
    let best = null, bestScore = Infinity;
    const from = active?.getBoundingClientRect();
    for (const [el, r] of items) {
      let score;
      if (!from) {
        // No focus yet. Start at the first item on screen in reading order.
        const visible = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
        score = (visible ? 0 : 1e7) + Math.abs(r.top) * 4 + Math.abs(r.left);
      } else {
        const dx = r.left + r.width / 2 - (from.left + from.width / 2);
        const dy = r.top + r.height / 2 - (from.top + from.height / 2);
        const along = dx * dir[0] + dy * dir[1];
        // Distance between the two boxes on the other axis. Zero when they overlap.
        const cross = dir[0]
          ? Math.max(0, r.top - from.bottom, from.top - r.bottom)
          : Math.max(0, r.left - from.right, from.left - r.right);
        // The item must lie mostly in the pressed direction, not in the next row or column.
        if (along <= 1 || along <= cross) continue;
        score = along + cross * 4;
      }
      if (score < bestScore) [best, bestScore] = [el, score];
    }
    if (!best) return;  // Nothing that way. The page scrolls as usual.
    event.preventDefault();
    best.focus({preventScroll: true});
    best.scrollIntoView({block: 'nearest', inline: 'nearest'});
  });
}"""


class RemoteInput:
    """Keep the observer's button watch running while a page asks for remote input.

    Events arrive on the observer pipe reader. Dispatch uses no tool lock and no observer lock.
    """

    def __init__(self, ops, interaction):
        self.ops = ops
        self.interaction = interaction
        self.mode = "off"
        self.state = "off"
        self.reason = None
        self.revision = None
        self.presses = 0
        self.missed = 0
        self.last = None
        self.watching = False
        self.delay = 1
        self.subscribed = False
        self.tasks = []
        self.wake = asyncio.Event()
        self.changed = asyncio.Event()
        self.queue = asyncio.Queue()
        ops.stage.on_change = self.sync
        # The transport can change after the show: a mirror can fail or fall back to a recording.
        for sender in {id(s): s for s in (ops.sender, getattr(ops, "live_mirror", None)) if s}.values():
            if events := getattr(sender, "events", None):
                events.subscribe(lambda _status: self.wake.set())

    def transport_problem(self):
        """Return why the page cannot get keys, or None when a working mirror shows it."""
        presenter = getattr(self.ops, "presenter", None) or self.ops.sender
        active = getattr(presenter, "active", presenter)
        if getattr(active, "mode", None) != "mirror":
            return "not_mirrored"
        status = getattr(active, "status", None)
        if status and not status().get("ok", True):
            return "mirror_failed"
        return None

    def sync(self):
        """Follow the stage. Every way to dismiss or replace a page ends here."""
        page = self.ops.stage.page
        mode = (page or {}).get("remote") or "off"
        revision = page["revision"] if page else None
        if revision != self.revision:
            self.revision, self.presses, self.last = revision, 0, None
        self.mode = mode
        if mode == "off":
            self._set("off")
            while not self.queue.empty():
                self.queue.get_nowait()
        else:
            if not self.watching and self.state != "unavailable":
                self._set("starting")
            if not self.tasks:
                self._start()
        self.wake.set()

    def _start(self):
        subscribe = getattr(self.ops.observer, "subscribe", None)
        if subscribe is None:
            self._set("unavailable", "observer_unavailable")
            return
        if not self.subscribed:
            subscribe(self.on_event)
            self.subscribed = True
        self.tasks = [asyncio.create_task(self._reconcile()), asyncio.create_task(self._dispatch())]

    def _set(self, state, reason=None):
        self.state, self.reason = state, reason
        self.changed.set()

    def _retry(self):
        asyncio.get_running_loop().call_later(self.delay, self.wake.set)
        self.delay = min(self.delay * 2, RETRY_MAX_S)

    def wanted(self):
        """True while a working mirror shows a page that asked for remote input."""
        return self.mode != "off" and not self.transport_problem()

    def on_event(self, event):
        """Called by the pipe reader. It only records state and queues work."""
        kind = event.get("event")
        if kind == "closed":
            if self.watching:
                self.watching = False
                if self.wanted():
                    self._set("starting")
                    self._retry()
        elif not self.wanted():
            return
        elif kind == "buttons":
            self._set(event.get("state"), event.get("reason"))
            if self.state == "active":
                self.delay = 1
        elif kind == "button" and event.get("key") in KEYS:
            self.presses += 1
            self.last = {"key": event["key"], "at": now()}
            if self.queue.qsize() >= QUEUE_LIMIT:
                self.queue.get_nowait()
            self.queue.put_nowait((event["key"], time.monotonic(), event))
        elif kind == "button_unmatched":
            _LOG.debug("Unmatched button log line: %s", event.get("message"))

    async def _reconcile(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            if self.wanted() and not self.watching:
                # Set first: a "closed" event can arrive before the call returns.
                self.watching = True
                try:
                    await self.ops.observer.call("buttons_start")
                except Exception as exc:  # noqa: BLE001 - The page still shows. Retry later.
                    self.watching = False
                    self._set("unavailable", REASONS.get(classify(exc).code, "observer_unavailable"))
                    self._retry()
            elif not self.wanted() and self.watching:
                self.watching = False
                with suppress(Exception):
                    await self.ops.observer.call("buttons_stop")

    async def _dispatch(self):
        while True:
            key, queued, event = await self.queue.get()
            if not self.wanted() or time.monotonic() - queued > STALE_S:
                continue
            try:
                async with asyncio.timeout(STALE_S):
                    if not await self.send(key):
                        self.missed += 1
                        _LOG.warning("Remote key %s was sent but the page did not get it", key)
                        continue
            except Exception as exc:  # noqa: BLE001 - A lost key must not stop the next one.
                _LOG.info("Remote key %s was not delivered (%s)", key, type(exc).__name__)
                continue
            received = event.get("received") or time.time()
            _LOG.debug(
                "Remote key %s sent to the page %d ms after the observer read it (TV log to observer %d ms)",
                key,
                (time.time() - received) * 1000,
                (received - event.get("device_time", received)) * 1000,
            )

    async def prepare(self):
        """Give the current content document keyboard focus and, in focus mode, the helper."""
        frame, _, direct = await self.interaction.binding()
        if not direct:
            await frame.page.evaluate(FOCUS_STAGE)
        if self.mode == "focus":
            await frame.evaluate(FOCUS_HELPER)
        return frame

    async def send(self, key):
        """Press one key in the current content document. Return False if the page did not get it."""
        # A mirror restart or a navigation loses focus and the helper. Restore both for each key.
        frame = await self.prepare()
        before = await frame.evaluate(PRESS_LOG)
        await frame.page.keyboard.press(KEYS[key])
        try:
            after = await frame.evaluate(PRESS_LOG)
        except Exception:  # noqa: BLE001 - The key may have started a navigation.
            return True
        # A new id means a new document, so the count cannot be compared.
        return after[0] != before[0] or after[1] > before[1]

    async def prime(self):
        """Find the content document at show time, so the first press is not slow."""
        with suppress(Exception):
            async with asyncio.timeout(3):
                await self.prepare()

    async def settle(self, timeout=3):
        """Give the watch a short time to report its first state."""
        with suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                while self.wanted() and self.state == "starting":
                    self.changed.clear()
                    await self.changed.wait()
        return self.status()

    def status(self):
        if self.mode == "off":
            return None
        state, reason = self.state, self.reason
        # In auto mode the mirror can fall back to a recording after the show.
        if problem := self.transport_problem():
            state, reason = "unavailable", problem
        result = {"mode": self.mode, "state": state, "presses": self.presses, "last": self.last}
        if state == "unavailable" and reason:
            result["reason"] = reason
        return result

    async def close(self):
        tasks, self.tasks = self.tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
