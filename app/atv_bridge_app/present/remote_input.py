"""Forward Siri Remote presses to the mirrored page as trusted key events."""

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import suppress

from ..state import classify, now

_LOG = logging.getLogger(__name__)

MODES = ("off", "keys", "focus", "tvos")
KEYS = {
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "select": "Enter",
}
# Input.dispatchKeyEvent needs the Windows key code of each key.
KEY_CODES = {"ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40, "Enter": 13}
STALE_S = 2.0
QUEUE_LIMIT = 8
RETRY_MAX_S = 30
# A key needs no new wake script when one ran in the content frame less than this long ago.
# The frame dropped keys only after idle minutes. Set to 0 to wake the frame before every key.
WAKE_FRESH_S = 0.5
TOUCH_LOG_LIMIT = 20  # Touch reports logged per TOUCH_LOG_WINDOW_S. A finger can touch the remote many times a second.
TOUCH_LOG_WINDOW_S = 10
# Reasons for state "unavailable" that start in the app. The observer adds its own
# (atv_bridge_observer/buttons.py, class Unavailable).
REASONS = {"pairing_required": "screen_not_paired", "screen_pairing_invalid": "screen_pairing_invalid"}

# Runs in the stage document. Key events go to the focused frame.
FOCUS_STAGE = """() => {
  const frame = document.querySelector('iframe.visible');
  if (frame && document.activeElement !== frame) frame.focus();
}"""

# Runs in the content document before a key and after it. It counts trusted key presses in
# window.__tvRemote and returns the document's id, the count, and whether a helper is installed.
# Hardware finding: after a few idle minutes the sandboxed frame drops a key unless a
# script ran in it just before. This evaluation is that script. Do not remove it.
# The bridge waits for its result before it sends the key. A key that follows another one
# within WAKE_FRESH_S uses the run after that key as its wake.
PRESS_LOG = """() => {
  if (!window.__tvRemote) {
    const log = window.__tvRemote = {id: Math.random().toString(36).slice(2), count: 0, last: null};
    addEventListener('keydown', event => {
      if (event.isTrusted) { log.count += 1; log.last = event.key; }
    }, true);
  }
  return [window.__tvRemote.id, window.__tvRemote.count, Boolean(window.__tvRemoteFocus)];
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
        // Two items of one row are at the same distance. The one that lines up better with the
        // focused box wins. Without this, the first one in the document won, which can be a
        // diagonal move when rows are scrolled differently.
        const overlap = dir[0] ? Math.min(r.bottom, from.bottom) - Math.max(r.top, from.top)
          : Math.min(r.right, from.right) - Math.max(r.left, from.left);
        const size = dir[0] ? Math.min(r.height, from.height) : Math.min(r.width, from.width);
        score = along + cross * 4 - Math.max(0, overlap) / Math.max(1, size);
      }
      if (score < bestScore) [best, bestScore] = [el, score];
    }
    if (!best) return;  // Nothing that way. The page scrolls as usual.
    event.preventDefault();
    best.focus({preventScroll: true});
    best.scrollIntoView({block: 'nearest', inline: 'nearest'});
  });
}"""


# The "tvos" helper. It extends the focus helper with a tvOS look and a search closer to UIKit:
# a lift (scale, shadow, white ring) and a beam-first search. See docs/research/tvos-native-feel.md,
# section 4. Every duration and scale is in CONFIG at the top, for tuning on the TV.
# The values suit a mirror of about 10 frames per second, where one frame is 100 ms: most of a
# change lands in the first frame, and no state is shorter than about 200 ms. Only the scale of
# the focused item animates. Retune CONFIG when the frame rate rises.
# Hardware finding: a row must not remember its last focused item. Up and down then go
# diagonally, which reads as a fault. They take the item straight above or below on screen.
TVOS_HELPER = r"""() => {
  if (window.__tvRemoteFocus) return;
  window.__tvRemoteFocus = 'tvos';
  const CONFIG = window.__tvRemoteConfig = {
    scale: 1.06,            // Lift of the focused item.
    wideScale: 1.03,        // Lift of an item wider than wideRatio of the screen.
    wideRatio: 0.5,
    // With this ease-out curve, 96% of the lift (180 ms) and 89% of the drop (260 ms) are done
    // after 100 ms. The first mirrored frame shows nearly the whole change.
    scaleInMs: 180,
    scaleOutMs: 260,
    easing: 'cubic-bezier(.2,.8,.2,1)',
    shadow: '0 16px 40px rgba(0,0,0,.45)',  // Applied at once, never animated.
    ringPx: 3,
    ringOffsetPx: 3,
    ringColor: '#fff',
    // How long Select holds the item at scale 1. The press needs about 100 ms to show. It then
    // stays for two frames, so one late frame cannot hide it.
    pressedMs: 300,
    // Keys closer together than this are a run: ring only, no lift. A lift that lasts less
    // than this shows for one frame or for none, which looks like flicker.
    fastRunMs: 250,
    // 'instant' or 'smooth'. A smooth scroll is three to five mirrored frames that each repaint
    // a whole row, and it starts slowly. Use 'smooth' only at a higher frame rate.
    scrollBehavior: 'instant',
    scrollMarginY: 60,      // Room kept above and below the focused item when it scrolls into view.
    scrollMarginX: 80,
  };
  const FOCUSABLE = 'a[href],button,input,select,textarea,summary,[tabindex],[contenteditable=""],[contenteditable="true"]';
  // A trusted Enter already activates these elements.
  const NATIVE = 'a[href],button,select,textarea,summary,input:not([type=checkbox]):not([type=radio])';
  const DIRECTIONS = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
  const root = document.documentElement;
  try {
    // A constructed sheet is not blocked by the page's style-src policy.
    // The scale property leaves a transform that the page set on the element alone.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(`
      :is(${FOCUSABLE}) { transition: scale ${CONFIG.scaleOutMs}ms ${CONFIG.easing};
        scroll-margin: ${CONFIG.scrollMarginY}px ${CONFIG.scrollMarginX}px; }
      :focus { outline: ${CONFIG.ringPx}px solid ${CONFIG.ringColor} !important;
        outline-offset: ${CONFIG.ringOffsetPx}px !important;
        scale: var(--tv-remote-scale, ${CONFIG.scale}) !important; z-index: 1;
        box-shadow: ${CONFIG.shadow} !important;
        transition: scale ${CONFIG.scaleInMs}ms ${CONFIG.easing}; }
      :root.tv-remote-fast :focus { scale: none !important; box-shadow: none !important; }
      :root.tv-remote-pressed :focus { scale: 1 !important; }
      /* A scrollbar has no use without a pointer. This sheet exists only in tvos mode. */
      * { scrollbar-width: none !important; }
      ::-webkit-scrollbar { display: none !important; }
    `);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  } catch {}
  const usable = el => !el.disabled && el.tabIndex >= 0 && !el.closest('[inert],[aria-hidden=true]')
    && el.checkVisibility({visibilityProperty: true});
  // Items that had focus. One can still be shrinking when a later key arrives.
  const lifted = new WeakSet();
  // The box of an element without its lift. A scaled box would make the beam too wide.
  const box = el => {
    const r = el.getBoundingClientRect();
    const s = parseFloat(getComputedStyle(el).scale);
    if (!(s > 0) || s === 1) {
      if (el !== document.activeElement) lifted.delete(el);
      return r;
    }
    const w = r.width / s, h = r.height / s, x = r.left + (r.width - w) / 2, y = r.top + (r.height - h) / 2;
    return {left: x, top: y, right: x + w, bottom: y + h, width: w, height: h};
  };
  // The row of an item: the nearest [data-tv-group] or sideways scroll container.
  const rowOf = el => {
    for (let node = el.parentElement; node && node !== document.body && node !== root; node = node.parentElement) {
      if (node.hasAttribute('data-tv-group') || /auto|scroll/.test(getComputedStyle(node).overflowX)) return node;
    }
    return null;
  };
  addEventListener('focusin', event => {
    const el = event.target;
    if (!(el instanceof Element)) return;
    lifted.add(el);
    root.style.setProperty('--tv-remote-scale',
      el.offsetWidth > innerWidth * CONFIG.wideRatio ? CONFIG.wideScale : CONFIG.scale);
  }, true);
  let lastKey = -Infinity, fastTimer = 0, pressedTimer = 0;
  // The bubble phase lets the page handle the key first.
  addEventListener('keydown', event => {
    if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
    let active = document.activeElement;
    if (!active || active === document.body || active === root) active = null;
    if (event.key === 'Enter') {
      if (active) {
        root.classList.add('tv-remote-pressed');
        clearTimeout(pressedTimer);
        pressedTimer = setTimeout(() => root.classList.remove('tv-remote-pressed'), CONFIG.pressedMs);
      }
      if (active && !active.matches(NATIVE)) {
        event.preventDefault();
        active.click();
      }
      return;
    }
    const dir = DIRECTIONS[event.key];
    if (!dir) return;
    // In a run of quick keys, the items passed show only the ring. The last one lifts.
    if (event.timeStamp - lastKey < CONFIG.fastRunMs) root.classList.add('tv-remote-fast');
    lastKey = event.timeStamp;
    clearTimeout(fastTimer);
    fastTimer = setTimeout(() => root.classList.remove('tv-remote-fast'), CONFIG.fastRunMs);
    // These controls use the arrow on that axis themselves.
    if (active && ((active.localName === 'select' && dir[1])
        || (active.localName === 'input' && active.type === 'range' && dir[0]))) return;
    // Read every box before anything is written. Boxes are positions on screen, so each
    // row counts where its own scroll offset has put it.
    const items = [...document.querySelectorAll(FOCUSABLE)].filter(el => el !== active && usable(el))
      .map(el => [el, lifted.has(el) ? box(el) : el.getBoundingClientRect()])
      .filter(([, r]) => r.width > 0 && r.height > 0);
    const from = active && box(active);
    const row = active && dir[0] ? rowOf(active) : null;
    const beam = [];
    let best = null, bestScore = Infinity;
    for (const [el, r] of items) {
      if (!from) {
        // No focus yet. Start at the first item on screen in reading order.
        const visible = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
        const score = (visible ? 0 : 1e7) + Math.abs(r.top) * 4 + Math.abs(r.left);
        if (score < bestScore) [best, bestScore] = [el, score];
        continue;
      }
      const dx = r.left + r.width / 2 - (from.left + from.width / 2);
      const dy = r.top + r.height / 2 - (from.top + from.height / 2);
      const along = dx * dir[0] + dy * dir[1];
      if (along <= 1) continue;
      // How far the two boxes overlap on the other axis. Below zero: the distance between them.
      const overlap = dir[0] ? Math.min(r.bottom, from.bottom) - Math.max(r.top, from.top)
        : Math.min(r.right, from.right) - Math.max(r.left, from.left);
      if (overlap > 1) {
        // In the beam: the focused box projected in the pressed direction.
        const gap = Math.max(0, dir[0] ? (dir[0] > 0 ? r.left - from.right : from.left - r.right)
          : (dir[1] > 0 ? r.top - from.bottom : from.top - r.bottom));
        const size = dir[0] ? Math.min(r.height, from.height) : Math.min(r.width, from.width);
        beam.push({el, gap, end: gap + (dir[0] ? r.width : r.height),
          share: Math.round(100 * overlap / size), off: Math.abs(dir[0] ? dy : dx)});
        continue;
      }
      // Outside the beam. Used only when the beam is empty. Left and right stay in their row:
      // at the end of a row, a tile of another row that is scrolled differently is not "right".
      const cross = Math.max(0, -overlap);
      if (along > cross && along + cross * 4 < bestScore && (!dir[0] || rowOf(el) === row)) {
        [best, bestScore] = [el, along + cross * 4];
      }
    }
    if (beam.length) {
      // The nearest row or column of the beam is every item that starts before the nearest one ends.
      // In it, the item that lines up best with the focused box wins, then the nearest center.
      const near = beam.reduce((a, b) => (b.gap < a.gap ? b : a));
      best = beam.filter(item => item.gap < near.end)
        .reduce((a, b) => ((b.share - a.share || a.off - b.off || a.gap - b.gap) > 0 ? b : a)).el;
    }
    if (!best) return;  // Nothing that way. The page scrolls as usual.
    event.preventDefault();
    best.focus({preventScroll: true});
    best.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: CONFIG.scrollBehavior});
  });
}"""

HELPERS = {"focus": FOCUS_HELPER, "tvos": TVOS_HELPER}


def key_events(key):
    """The key-down and key-up parameters of one press, as Playwright's keyboard.press sends them."""
    base = {"key": key, "code": key, "windowsVirtualKeyCode": KEY_CODES[key]}
    # Enter carries its text. The character event is what activates a button or a link.
    down = {"type": "keyDown", "text": "\r", "unmodifiedText": "\r"} if key == "Enter" else {"type": "rawKeyDown"}
    return base | down, base | {"type": "keyUp"}


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
        self.touch_logged = deque()
        self.touch_skipped = 0
        self.cdp = None  # (page, CDP session) for key events.
        self.awake = None  # (frame, press log state, time) after a key that left the document ready for the next one.
        self.timing = {}  # Seconds that each step of the last send took.
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
            # A top-level page keeps its frame object across shows, so the frame alone does not tell a new page.
            self.revision, self.presses, self.last, self.awake = revision, 0, None, None
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
        elif kind == "gesture":
            self.log_touch(event)
        elif kind == "touch_unmatched":
            _LOG.info("Remote touch line with an unknown shape: %s", event.get("message"))
        elif kind == "button_unmatched":
            _LOG.debug("Unmatched button log line: %s", event.get("message"))

    def log_touch(self, report):
        """Log what the observer made of one finger contact. The values tune atv_bridge_observer/touch.py."""
        now = time.monotonic()
        while self.touch_logged and now - self.touch_logged[0] >= TOUCH_LOG_WINDOW_S:
            self.touch_logged.popleft()
        if len(self.touch_logged) >= TOUCH_LOG_LIMIT:
            self.touch_skipped += 1
            return
        self.touch_logged.append(now)
        skipped, self.touch_skipped = self.touch_skipped, 0
        _LOG.info(
            "Remote touch: %s%s",
            json.dumps({key: value for key, value in report.items() if key != "event"}, separators=(",", ":")),
            f" ({skipped} earlier ones not logged)" if skipped else "",
        )

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
            batch = [await self.queue.get()]
            # Keys that arrived while the last ones went out leave together, as the arrows after a flick do.
            # Select ends a batch: it can start a navigation, and the next key must find the new document.
            while not self.queue.empty() and batch[-1][0] != "select":
                batch.append(self.queue.get_nowait())
            now = time.monotonic()
            batch = [item for item in batch if now - item[1] <= STALE_S] if self.wanted() else []
            if not batch:
                continue
            keys = [key for key, _, _ in batch]
            names = " + ".join(f"{key} ({event['gesture']})" if event.get("gesture") else key for key, _, event in batch)
            started = time.time()
            try:
                async with asyncio.timeout(STALE_S):
                    arrived = await self.send(*keys)
            except Exception as exc:  # noqa: BLE001 - A lost key must not stop the next one.
                self.awake = None
                _LOG.info("Remote key %s was not delivered (%s)", names, type(exc).__name__)
                continue
            if arrived < len(keys):
                self.missed += len(keys) - arrived
                _LOG.warning("Remote key %s was sent but the page did not get it (%d of %d arrived)", names, arrived, len(keys))
                continue
            event = batch[0][2]
            received = event.get("received") or started
            step = {name: seconds * 1000 for name, seconds in self.timing.items()}
            # The key is in the page when its events are acknowledged. The press log read comes after that.
            _LOG.debug(
                "Remote key %s reached the page %d ms after the observer read it "
                "(queue %d, bind %d, wake %d, keys %d ms; press log read %d ms more; TV log to observer %d ms)",
                names,
                (started - received) * 1000 + step["bind"] + step["wake"] + step["keys"],
                (started - received) * 1000,
                step["bind"],
                step["wake"],
                step["keys"],
                step["log"],
                (received - event.get("device_time", received)) * 1000,
            )

    async def session(self, page):
        """Return the CDP session that carries key events to a page. One is made for each page."""
        if not self.cdp or self.cdp[0] is not page:
            self.cdp = (page, await page.context.new_cdp_session(page))
        return self.cdp[1]

    async def prepare(self):
        """Give the current content document keyboard focus and, in focus and tvos mode, the helper.

        Return the frame, its press log state and the CDP session of its page.
        """
        started = time.monotonic()
        frame, _, direct = await self.interaction.binding()
        session = await self.session(frame.page)
        bound = time.monotonic()
        awake = self.awake
        if awake and awake[0] is frame and bound - awake[2] < WAKE_FRESH_S:
            # A script ran in this document a moment ago, after the last key. Focus and helper are in place.
            state = awake[1]
        else:
            # The stage document and the content document are two frames, so the two scripts run side by side.
            focus = [] if direct else [frame.page.evaluate(FOCUS_STAGE)]
            state, *_ = await asyncio.gather(frame.evaluate(PRESS_LOG), *focus)
            helper = HELPERS.get(self.mode)
            # The helper source is large. Send it only to a document that does not have it.
            if helper and not state[2]:
                await frame.evaluate(helper)
        self.timing = {"bind": bound - started, "wake": time.monotonic() - bound, "keys": 0.0, "log": 0.0}
        return frame, state, session

    async def send(self, *keys):
        """Press keys in the current content document, in order. Return how many of them the page got."""
        # A mirror restart or a navigation loses focus and the helper. Restore both, unless a key just went out.
        frame, before, session = await self.prepare()
        timing, self.awake = self.timing, None
        woke = time.monotonic()
        try:
            # Nothing waits between the events. One CDP session handles its messages in the order sent,
            # so every key-down still comes before its key-up and before the next key.
            await asyncio.gather(*(
                session.send("Input.dispatchKeyEvent", event) for key in keys for event in key_events(KEYS[key])
            ))
        except Exception:
            self.cdp = None
            raise
        pressed = time.monotonic()
        timing["keys"] = pressed - woke
        self.timing = timing
        try:
            after = await frame.evaluate(PRESS_LOG)
        except Exception:  # noqa: BLE001 - The key may have started a navigation.
            return len(keys)
        done = time.monotonic()
        timing["log"] = done - pressed
        # A new id means a new document, so the count cannot be compared.
        if after[0] != before[0]:
            return len(keys)
        arrived = min(len(keys), after[1] - before[1])
        # This read is the wake for a key that follows at once. Not after Select, which can start a navigation,
        # and not for a document that lost its helper.
        if arrived == len(keys) and keys[-1] != "select" and (after[2] or self.mode not in HELPERS):
            self.awake = (frame, after, done)
        return arrived

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
