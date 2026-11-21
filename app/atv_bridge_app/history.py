"""Watch history: a per-TV log of played media, built from now-playing state the TV already pushes."""

import asyncio
import json
import logging
import math
import os
import re
import time
import uuid
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from .state import Fault, write_private
from .tools import youtube_id
from .tools.contract import outcome

_LOG = logging.getLogger(__name__)
# Shorter plays are skips and previews.
MIN_WATCH_S = 10
# An item that returns within this time continues its earlier entry.
RESUME_GAP_S = 4 * 3600
# Untitled playback has no identity, so only a short break continues it.
UNTITLED_GAP_S = 15 * 60
# How many recent entries a returning item is matched against.
RECENT = 10
SLACK_S = 5
# A reading in the first tenth of an item is a restart. A restart is a rewatch, and a new entry, when
# the entry had got past half and there was a break, or had reached its last tenth without a break.
RESTART = 0.1
REWATCH_AFTER_BREAK = 0.5
REWATCH_IN_PLACE = 0.9
# A late title takes an untitled entry when the item's position covers the entry's playing time.
# The slack allows for buffering and for the two seconds between the engine's reads.
ADOPT_SLACK_S = 10
# A stored content identifier is a plain token. It has no character that a link needs.
CONTENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")
SHELL_APPS = {
    "com.apple.HeadBoard",
    "com.apple.PineBoard",
    "com.apple.IdleScreen",
    "com.apple.TVSettings",
    "com.apple.TVSystemUIService",
    "com.apple.TVAirPlay",
}
YOUTUBE = "com.google.ios.youtube"
ACTIVE = ("playing", "paused", "seeking", "loading")
# pyatv reports any rate other than 0 or 1 as seeking. That covers 1.5x playback.
PLAYING = ("playing", "seeking")
IDENTITY = ("app_id", "content_id", "title", "artist", "album", "series", "season", "episode")
FIELDS = (*IDENTITY, "app", "kind", "genre", "store_id", "item_id", "duration_s", "position_s")
NUMBER = (int, float)
# Types of the stored fields. A line with another type is skipped on load.
TYPES = {
    "id": str, "start": NUMBER, "end": NUMBER, "watched_s": NUMBER,
    "app": str, "app_id": str, "kind": str, "title": str, "artist": str, "album": str, "genre": str,
    "series": str, "season": int, "episode": int, "episode_title": str, "content_id": str,
    "store_id": int, "item_id": str, "url": str, "duration_s": NUMBER, "position_s": NUMBER, "meta": dict,
}
REQUIRED = ("id", "start", "end", "watched_s")
# Title formats that apps use: "Series | S1 E2 - Episode" (Hulu) and artist "S1:E2 Episode" (Disney+).
HULU_TITLE = re.compile(r"^(?P<series>.+?) \| S(?P<season>\d+) E(?P<episode>\d+) - (?P<name>.+)$")
DISNEY_ARTIST = re.compile(r"^S(?P<season>\d+):E(?P<episode>\d+) (?P<name>.+)$")
# HBO Max puts the episode in the title and the series in the artist.
SERIES_IN_ARTIST = {"com.wbd.stream"}
AGE = re.compile(r"^(\d{1,5})([mhdw])$")
AGE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
MAX_TIME_TEXT = 40
# Times outside 1970 to 2100 are mistakes.
MAX_TIME = 4_102_444_800


def named(item):
    return bool(item.get("title") or item.get("content_id"))


def derive(entry):
    """Fill series, season and episode from the title formats that apps use."""
    title, artist = entry.get("title") or "", entry.get("artist") or ""
    found = {}
    if match := HULU_TITLE.match(title):
        found = match.groupdict()
    elif match := DISNEY_ARTIST.match(artist):
        found = match.groupdict() | {"series": title}
    elif entry.get("app_id") in SERIES_IN_ARTIST and title and artist:
        found = {"series": artist, "name": title}
    for key, value in found.items():
        key = "episode_title" if key == "name" else key
        if not entry.get(key):
            entry[key] = int(value) if key in ("season", "episode") else value
    video = youtube_id(entry.get("content_id") or "") if entry.get("app_id") == YOUTUBE else None
    if video:
        # The link holds the checked video ID and no other stored text.
        entry["url"] = "https://www.youtube.com/watch?v=" + video


def content_id(value, app_id):
    """The content identifier that may be stored, or None. A link can hold account tokens."""
    if not isinstance(value, str):
        return None
    if app_id == YOUTUBE:
        # A video ID, or a link that holds one. Only the checked ID is kept.
        return youtube_id(value)
    return value if CONTENT_ID.fullmatch(value) else None


def rewatch(entry, sample, reached):
    """The entry had got to the given share of the item and the sample is back in its first tenth.

    The check needs a duration and both positions. Without them it is False.
    """
    length, last, position = entry.get("duration_s"), entry.get("position_s"), sample.get("position_s")
    if not length or last is None or position is None:
        return False
    return last >= reached * length and position <= RESTART * length


def adopts(entry, sample, played):
    """A titled sample can take an untitled entry that has played for the given time."""
    if named(entry) or not named(sample):
        return True
    if entry.get("item_id") and entry["item_id"] == sample.get("item_id"):
        return True
    # An item that just started cannot be what played before it. Without a position there is no check.
    position = sample.get("position_s")
    return position is None or position >= played - ADOPT_SLACK_S


def fits(entry, sample):
    """The sample can belong to the entry: no field that both report differs."""
    keys = IDENTITY
    if not (named(entry) and named(sample)):
        # Without a title, the queue item is the only way to tell two items apart.
        keys += ("item_id",)
    if any(entry.get(k) and sample.get(k) and entry[k] != sample[k] for k in keys):
        return False
    lengths = entry.get("duration_s"), sample.get("duration_s")
    if all(lengths) and abs(lengths[0] - lengths[1]) > SLACK_S:
        return False
    if named(entry) and not named(sample):
        # An untitled reading may be anything. Only the same queue item ties it to a titled entry.
        return bool(entry.get("item_id")) and entry["item_id"] == sample.get("item_id")
    return True


def reading(status, details):
    """Flatten one status snapshot into history fields. Empty values are dropped."""
    media = (status.get("now_playing") or {}) | (details or {})
    app = status.get("app") or {}
    sample = {
        "app": app.get("name"),
        "app_id": app.get("bundle_id"),
        "kind": media.get("media_type") if media.get("media_type") != "unknown" else None,
        "title": media.get("title"),
        "artist": media.get("artist"),
        "album": media.get("album"),
        "genre": media.get("genre"),
        "series": media.get("series_name"),
        "season": media.get("season_number"),
        "episode": media.get("episode_number"),
        "content_id": content_id(media.get("content_identifier"), app.get("bundle_id")),
        "store_id": media.get("itunes_store_identifier"),
        "item_id": media.get("item_id"),
        "duration_s": media.get("duration_s"),
        "position_s": media.get("position_s"),
        "meta": media.get("raw"),
    }
    sample = {k: v for k, v in sample.items() if v not in (None, "", {})}
    return sample | {"state": media.get("state") or "idle", "power": status.get("power")}


def valid(entry):
    if not isinstance(entry, dict) or any(key not in entry for key in REQUIRED):
        return False
    for key, kind in TYPES.items():
        value = entry.get(key)
        if key in entry and (not isinstance(value, kind) or isinstance(value, bool)):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
    return True


async def unbroken(work):
    """Run a coroutine to its end even when the caller is cancelled. Then pass the cancellation on."""
    task = asyncio.ensure_future(work)
    cancel = None
    while not task.done():
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError as exc:
            cancel = exc
    if cancel:
        if not task.cancelled() and task.exception():
            _LOG.error("Watch history work failed after its caller was cancelled", exc_info=task.exception())
        raise cancel
    return task.result()


class HistoryStore:
    """Append-only JSON lines. A later line with the same id replaces the earlier one.

    load, append and rewrite touch the disk. Call them from a worker thread.
    """

    def __init__(self, path, limit):
        self.path, self.limit = path, limit
        self.entries = {}
        self.lines = 0
        self.loaded = False

    def load(self):
        self.loaded = True
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            _LOG.warning("Cannot read the watch history", exc_info=True)
            return
        for line in data.splitlines():
            self.lines += 1
            try:
                entry = json.loads(line.decode())
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("drop") is True:
                self.entries.pop(entry.get("id"), None)
            elif valid(entry):
                self.entries[entry["id"]] = entry
        self.trim()

    def trim(self):
        for key in list(self.entries)[: max(0, len(self.entries) - self.limit)]:
            del self.entries[key]

    def append(self, text):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)

    def rewrite(self, text):
        write_private(self.path, text)


class Recorder:
    """Sample one TV's now-playing state and merge the samples into history entries.

    A sample reads state that pyatv keeps from the TV's push messages. It sends nothing to the
    TV and takes no TV lock. The engine reports each change, so the timer is only a heartbeat.
    Wall time is used for timestamps only. Watched time comes from the monotonic clock.
    """

    def __init__(self, root, engine, stage, *, enabled=True, interval=300, fast_interval=15,
                 max_entries=2000, clock=time.time, ticks=time.monotonic):
        self.engine, self.stage = engine, stage
        self.enabled, self.interval = enabled, interval
        self.fast = min(fast_interval, interval)
        self.clock, self.ticks = clock, ticks
        # With the option off, the file is neither read nor written.
        self.store = HistoryStore(root / "history.jsonl", max_entries) if enabled else None
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.task = None
        self.signature = None
        self.noticed = None
        self.live = None
        self.before = None
        self.chain = False
        self.current = None
        self.saved = None
        self.wrote = 0
        self.seen = {}

    def start(self):
        if self.enabled and not self.task:
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.store and self.store.loaded:
            async with self.lock:
                await unbroken(self.flush(force=True))

    @staticmethod
    def sign(status):
        media = status.get("now_playing") or {}
        return (
            status.get("power"),
            (status.get("app") or {}).get("bundle_id"),
            *(media.get(k) for k in ("state", "title", "artist", "content_identifier")),
        )

    def notice(self, change):
        """Wake the sampler when the engine publishes a different item or state."""
        if not self.enabled or "connected" not in change:
            return
        if not change["connected"] or self.sign(change) != self.signature:
            if self.noticed is None:
                # Playback is credited up to here when the next sample finds it stopped.
                self.noticed = self.ticks()
            self.wake.set()

    async def run(self):
        delay = self.fast
        while True:
            self.wake.clear()
            try:
                changed = await self.sample()
            except Exception:  # noqa: BLE001 - A bad sample must not end the recorder.
                _LOG.exception("Watch history sample failed")
                changed = False
            # Sample fast after a change, then double the wait up to the idle interval.
            delay = self.fast if changed else min(self.interval, delay * 2)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), delay)

    async def read(self):
        client = self.engine.client
        if client is None:
            return None
        try:
            status = await client.status()
        except Exception:  # noqa: BLE001 - A closed session reads as no sample.
            return None
        details = None
        if hasattr(client, "media_details"):
            try:
                details = await client.media_details()
            except Exception:  # noqa: BLE001 - The basic fields in status still make an entry.
                _LOG.warning("Extra now-playing fields are unavailable", exc_info=True)
        self.signature, before = self.sign(status), self.signature
        sample = reading(status, details)
        sample["changed"] = self.signature != before
        # The bridge's own pages and AirPlay clips are not something the user chose to watch.
        sample["own"] = bool(self.stage.page or status.get("airplay_playing"))
        return sample

    async def sample(self):
        """Take one reading. Return True when the item or its state changed.

        The read only looks at memory and can be cancelled. The work after it runs to its end,
        also when the caller is cancelled. A file write runs on a thread that cannot be stopped,
        so the lock stays held until the write ends. Work that stopped at a write would also
        leave playing time to be counted again.
        """
        async with self.lock:
            if not self.enabled:
                return False
            if not self.store.loaded:
                await unbroken(asyncio.to_thread(self.store.load))
            sample = await self.read()
            return await unbroken(self.merge(sample))

    async def merge(self, sample):
        """Merge one reading into the entries. The caller holds the lock."""
        now, tick = self.clock(), self.ticks()
        noticed, self.noticed = self.noticed, None
        if sample is None:
            self.live = self.before = None
            self.chain = False
            await self.flush(force=True)
            return False
        changed, own = sample.pop("changed"), sample.pop("own")
        state, power = sample.pop("state"), sample.pop("power")
        visible = power != "off" and not own
        self.live = sample | {"state": state} if visible and state in ("playing", "paused") else None
        content = named(sample) or (sample.get("app_id") and sample["app_id"] not in SHELL_APPS)
        playing = state in PLAYING and visible and bool(content)
        entry, before = self.current, self.before
        # The time since the previous reading, if that reading was playing.
        pending = tick - before["tick"] if before and before["playing"] else 0
        # The previous reading was this entry and this one can be too.
        attached = bool(
            entry
            and self.chain
            and fits(entry, sample)
            and not rewatch(entry, sample, REWATCH_IN_PLACE)
            and adopts(entry, sample, entry["watched_s"] + pending)
            and self.open(entry, now, tick)
        )
        if entry and pending:
            if attached and playing:
                elapsed = pending
            elif noticed is not None:
                # Playback stopped or changed. Count up to the engine's report of that change.
                elapsed = noticed - before["tick"]
            else:
                # No report. Only an item that restarted without a break is known to have played on.
                elapsed = pending if playing and not changed else 0
            # A long gap means the bridge was stalled. Count nothing for it.
            if 0 < elapsed <= 2 * self.interval + 60:
                entry["watched_s"] += elapsed
                if not (attached and playing):
                    entry["end"] = int(entry["end"] + elapsed)
                    if "position_s" in entry:
                        # The item played on after its position was last read. Move the position with it.
                        reached = entry["position_s"] + elapsed
                        entry["position_s"] = round(min(reached, entry.get("duration_s") or reached))
        if playing and not attached:
            await self.flush(force=True)
            self.saved = self.recent(sample, now, tick)
            entry = self.current = (
                dict(self.saved)
                if self.saved
                else {"id": uuid.uuid4().hex[:12], "start": int(now), "end": int(now), "watched_s": 0}
            )
            attached = True
        if attached and state in ACTIVE:
            # A title can arrive after playback starts, or only at the first pause.
            untitled = not named(entry)
            meta = entry.get("meta", {}) | sample.get("meta", {})
            entry.update({k: sample[k] for k in FIELDS if k in sample})
            if meta:
                entry["meta"] = meta
            derive(entry)
            if playing:
                entry["end"] = int(now)
                if len(self.seen) > 4 * RECENT:
                    self.seen = dict(list(self.seen.items())[-RECENT:])
                self.seen[entry["id"]] = tick
            if untitled and named(entry):
                await self.absorb(entry, now, tick)
        self.chain = attached and state in ACTIVE
        self.before = {"tick": tick, "playing": playing}
        await self.flush(force=not playing)
        return changed

    def open(self, entry, now, tick):
        """The entry played recently enough to continue. Monotonic when this process saw it play."""
        gap = tick - self.seen[entry["id"]] if entry["id"] in self.seen else now - entry["end"]
        return 0 <= gap <= (RESUME_GAP_S if named(entry) else UNTITLED_GAP_S)

    def recent(self, sample, now, tick, skip=None):
        """The stored entry that this reading continues after a break, if any."""
        for entry in list(self.store.entries.values())[: -RECENT - 1 : -1]:
            if entry["id"] == skip or named(entry) != named(sample) or not fits(entry, sample):
                continue
            if not rewatch(entry, sample, REWATCH_AFTER_BREAK) and self.open(entry, now, tick):
                return entry
        return None

    async def absorb(self, entry, now, tick):
        """A provisional entry got its title. Fold it into the entry of that item if one is open."""
        target = self.recent(entry, now, tick, skip=entry["id"])
        if not target:
            return
        merged = target | {k: entry[k] for k in ("end", "position_s") if k in entry}
        merged["watched_s"] = target["watched_s"] + entry["watched_s"]
        if self.store.entries.pop(entry["id"], None) and self.saved:
            await self.write(json.dumps({"id": entry["id"], "drop": True}) + "\n")
        self.current, self.saved = merged, target
        self.seen[merged["id"]] = tick
        # Write the merged entry with this sample, since the provisional one is gone from the file.
        self.wrote = tick - self.interval

    async def flush(self, force=False):
        """Keep the open entry current in memory. Write it at most once per interval, or when it closes."""
        entry = self.current
        if not entry or entry["watched_s"] < MIN_WATCH_S:
            return
        stored = entry | {"watched_s": round(entry["watched_s"])}
        self.store.entries[stored["id"]] = stored
        self.store.trim()
        if stored == self.saved:
            return
        tick = self.ticks()
        if force or self.saved is None or tick - self.wrote >= self.interval:
            self.saved, self.wrote = stored, tick
            await self.write(json.dumps(stored) + "\n")

    async def write(self, text):
        store = self.store
        store.lines += 1
        if store.lines >= 2 * store.limit:
            # Compact rarely: only when half of the file is superseded lines.
            text = "".join(json.dumps(entry) + "\n" for entry in store.entries.values())
            store.lines = len(store.entries)
            await asyncio.to_thread(store.rewrite, text)
        else:
            await asyncio.to_thread(store.append, text)

    def entries(self):
        return list(self.store.entries.values()) if self.store else []


def instant(value, now):
    """Parse an ISO 8601 time or a relative age such as 7d. Times without a zone are UTC."""
    text = value.strip().lower()
    try:
        if len(text) > MAX_TIME_TEXT:
            raise ValueError("too long")
        if match := AGE.match(text):
            parsed = now - timedelta(**{AGE_UNITS[match[2]]: int(match[1])})
        else:
            parsed = datetime.fromisoformat(value.strip())
        stamp = (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()
        if not 0 <= stamp <= MAX_TIME:
            raise ValueError("out of range")
        return stamp
    except (ValueError, OverflowError) as exc:
        raise Fault(
            "invalid_arguments",
            f"Cannot read the time {value[:MAX_TIME_TEXT]!r}.",
            "Use ISO 8601 such as 2026-11-20 or 2026-11-20T18:00:00Z, or an age such as 24h, 7d or 4w.",
        ) from exc


def iso(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")


def top(rows, key, label):
    totals = defaultdict(lambda: [0, 0])
    for row in rows:
        if name := key(row):
            totals[name][0] += row["watched_s"]
            totals[name][1] += 1
    ranked = sorted(totals.items(), key=lambda item: -item[1][0])[:10]
    return [{label: name, "watched_s": seconds, "entries": count} for name, (seconds, count) in ranked]


async def report(sources, *, since=None, until=None, app=None, search=None, limit=50):
    """Answer tv_history for the given (tv fields, recorder) pairs."""
    now = datetime.now(UTC)
    start = instant(since, now) if since else None
    end = instant(until, now) if until else None
    rows, playing = [], []
    for tv, recorder in sources:
        # Bring the open entry up to date. This reads memory only.
        await recorder.sample()
        if recorder.live:
            playing.append(tv | recorder.live)
        rows.extend(tv | entry for entry in recorder.entries())
    wanted = (app or "").casefold()
    text = (search or "").casefold()
    rows = [
        row
        for row in rows
        if (start is None or row["end"] >= start)
        and (end is None or row["start"] <= end)
        and (not wanted or wanted in (row.get("app") or "").casefold() or wanted == (row.get("app_id") or "").casefold())
        and (
            not text
            or any(text in str(row.get(k) or "").casefold() for k in ("title", "episode_title", "series", "artist", "album"))
        )
    ]
    rows.sort(key=lambda row: row["start"], reverse=True)
    enabled = any(recorder.enabled for _, recorder in sources)
    summary = {
        "entries": len(rows),
        "watched_s": sum(row["watched_s"] for row in rows),
        "first": iso(min(row["start"] for row in rows)) if rows else None,
        "last": iso(max(row["end"] for row in rows)) if rows else None,
        "top_apps": top(rows, lambda row: row.get("app") or row.get("app_id"), "app"),
        "top_series": top(rows, lambda row: row.get("series"), "series"),
        # Without a series, the artist is a YouTube channel or a music artist.
        "top_artists": top(rows, lambda row: None if row.get("series") else row.get("artist"), "artist"),
    }
    shown = [row | {"start": iso(row["start"]), "end": iso(row["end"])} for row in rows[:limit]]
    text = f"{len(rows)} watch history entries, {summary['watched_s'] // 60} minutes watched."
    if not sources:
        text = "No Apple TV is registered."
    elif not enabled:
        text = "Watch history recording is off."
    return outcome(
        text,
        ["Read from the bridge's stored history"],
        {"recording": enabled, "summary": summary, "entries": shown, "now": playing},
    )
