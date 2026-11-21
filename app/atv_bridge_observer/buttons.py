# SPDX-License-Identifier: GPL-3.0-or-later
"""Watch remote presses in the receiver's log stream and report them as events."""

import asyncio
import logging
import os
import re
import time
from contextlib import suppress

from pymobiledevice3.services.os_trace import OsActivityStreamFlag, OsTraceService

# ---------------------------------------------------------------------------
# Press matcher. Change only this block when a remote logs a different line.
# The match uses the process, the log category and the HID page and usage.
# It must not depend on where the press came from.
# Verified on tvOS 26.6.1 with presses sent by tv_remote.
PROCESS = "backboardd"
CATEGORY = "Button"
PRESS = re.compile(r"\bpage:0x([0-9A-Fa-f]+) usage:0x([0-9A-Fa-f]+) downEvent:([01])\b")
# Other lines that the same press logs. They carry no new information.
FOLLOW_UP = re.compile(r"\b(began|finished): ")
USAGE_KEYS = {
    (0x01, 0x8B): "left",
    (0x01, 0x8A): "right",
    (0x01, 0x8C): "up",
    (0x01, 0x8D): "down",
    (0x0C, 0x80): "select",
}


def match_press(process, category, message):
    """Return (key, known). key names a key-down press. known is False for a line worth a diagnostic log."""
    if process != PROCESS or category != CATEGORY:
        return None, True
    found = PRESS.search(message or "")
    if not found:
        return None, bool(FOLLOW_UP.search(message or ""))
    key = USAGE_KEYS.get((int(found[1], 16), int(found[2], 16)))
    if found[3] != "1":
        return None, True
    return key, key is not None


# ---------------------------------------------------------------------------

# PAYLOAD only. Never add HISTORICAL: it replays old log lines as new presses.
STREAM_FLAGS = int(OsActivityStreamFlag.PAYLOAD)
STALE_S = 2.0
CONNECT_GRACE_S = 1.0
PROBE_INTERVAL_S = 30
PROBE_RETRY_S = 5
RETRY_MIN_S = 0.5
RETRY_MAX_S = 30
UNMATCHED_LIMIT = 6  # diagnostic lines per UNMATCHED_WINDOW_S
UNMATCHED_WINDOW_S = 10


class Freshness:
    """Decide whether a log line is live. Old lines must never become key presses."""

    def __init__(self, now):
        self.connected = now
        self.latest = float("-inf")
        self.lag = float("inf")

    def stale(self, device, now):
        # The smallest lag seen is the clock offset plus the normal delay.
        lag = now - device
        self.lag = min(self.lag, lag)
        self.latest = max(self.latest, device)
        return (
            now - self.connected < CONNECT_GRACE_S
            or self.latest - device > STALE_S
            or lag - self.lag > STALE_S
        )


class Unavailable(Exception):
    """The watch cannot run now. The message is the reason reported to the app."""


async def process_pid(rsd, name=PROCESS):
    async with asyncio.timeout(8), OsTraceService(lockdown=rsd) as service:
        payload = (await service.get_pid_list()).get("Payload", {})
    pids = [int(pid) for pid, info in payload.items() if info.get("ProcessName") == name]
    if len(pids) != 1:
        raise Unavailable("process_not_found")
    return pids[0]


class ButtonWatch:
    """Run the log stream on the observer's tunnel. Request handling never waits for it."""

    def __init__(self, observer, emit):
        self.observer = observer
        self.emit = emit
        self.task = None
        self.state = None
        self.delay = RETRY_MIN_S
        self.unmatched = []

    def start(self):
        if not self.task or self.task.done():
            self.state = None
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            task, self.task = self.task, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def report(self, state, reason=None):
        if (state, reason) != self.state:
            self.state = (state, reason)
            self.emit({"event": "buttons", "state": state, **({"reason": reason} if reason else {})})

    async def _run(self):
        self.delay = RETRY_MIN_S
        while True:
            try:
                await self._stream()
            except asyncio.CancelledError:
                raise
            except Unavailable as exc:
                self.report("unavailable", str(exc))
            except Exception as exc:  # noqa: BLE001 - Any stream failure leads to a retry.
                logging.info("Button watch stopped (%s). Retrying in %.1f s.", type(exc).__name__, self.delay)
                self.report("unavailable", type(exc).__name__)
            await asyncio.sleep(self.delay)
            self.delay = min(self.delay * 2, RETRY_MAX_S)

    async def _stream(self):
        observer = self.observer
        # A PIN exchange closes the tunnel on purpose. Do not reopen it.
        if observer.pairing_active():
            raise Unavailable("pairing_in_progress")
        if not observer.record.is_file():
            raise Unavailable("screen_not_paired")
        async with asyncio.timeout(20):
            rsd = await observer.tunnel.aopen()
        if OsTraceService.RSD_SERVICE_NAME not in rsd.peer_info.get("Services", {}):
            raise Unavailable("service_missing")
        pid = await process_pid(rsd)
        async with OsTraceService(lockdown=rsd) as service:
            fresh = Freshness(time.time())
            reader = asyncio.create_task(self._read(service.syslog(pid=pid, stream_flags=STREAM_FLAGS), fresh))
            try:
                # A refused stream fails at once. An idle stream is silent, which is normal.
                wait, failures = 1, 0
                while True:
                    done, _ = await asyncio.wait({reader}, timeout=wait)
                    if done:
                        reader.result()
                        raise ConnectionError("The log stream ended")
                    if self.state != ("active", None):
                        logging.info("Button watch active (pid %d, flags %#x)", pid, STREAM_FLAGS)
                        self.report("active")
                        self.delay = RETRY_MIN_S
                        wait = PROBE_INTERVAL_S
                        continue
                    # Liveness does not depend on log traffic. The probe also sees a new pid.
                    try:
                        current = await process_pid(rsd)
                    except Exception:  # noqa: BLE001 - Count every probe failure the same way.
                        failures += 1
                        if failures < 2:
                            wait = PROBE_RETRY_S
                            continue
                        await self._reset_tunnel(rsd)
                        raise
                    if current != pid:
                        raise ConnectionError("The watched process restarted")
                    wait, failures = PROBE_INTERVAL_S, 0
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)

    async def _reset_tunnel(self, rsd):
        # Wait for a request in flight. Its own failure handling may already have closed the tunnel.
        async with self.observer.busy:
            if self.observer.tunnel.rsd is rsd:
                logging.info("Button watch found a dead tunnel. Closing it.")
                with suppress(Exception):
                    await self.observer.close_channels()

    async def _read(self, stream, fresh):
        async for entry in stream:
            now = time.time()
            device = entry.timestamp.timestamp()
            stale = fresh.stale(device, now)
            key, known = match_press(
                os.path.basename(entry.filename), entry.label.category if entry.label else None, entry.message
            )
            if not known:
                self._unmatched(entry.message, now)
            if key and not stale:
                self.emit({"event": "button", "key": key, "device_time": device, "received": now})
            elif key:
                logging.info("Dropped a stale %s press (%.1f s behind)", key, now - device - fresh.lag)

    def _unmatched(self, message, now):
        self.unmatched = [at for at in self.unmatched if now - at < UNMATCHED_WINDOW_S]
        if len(self.unmatched) < UNMATCHED_LIMIT:
            self.unmatched.append(now)
            self.emit({"event": "button_unmatched", "message": str(message)[:300]})
