import asyncio
from contextlib import asynccontextmanager, suppress

from .state import Fault, classify, success


class Engine:
    """Serialize connection ownership, including pairing and wake retries."""

    def __init__(self, adapter, publish, wake_timeout=5):
        self.adapter = adapter
        self.publish = publish
        self.wake_timeout = wake_timeout
        self.woke_at = 0.0
        self.lock = asyncio.Lock()
        self.client = None
        self.problem = None
        self.monitor = None
        self.last_power = "unknown"
        self.rebuild = False
        self.pairing = False
        self.signal = asyncio.Event()
        self.delay = 1
        self.next_connect = 0

    def start(self):
        self.monitor = asyncio.create_task(self._monitor())

    def power_changed(self, previous, new):
        if previous == "off" and new in ("on", "screensaver"):
            self.rebuild = True
        if new != "unknown":
            self.last_power = new
        self.signal.set()

    async def close_client(self):
        if self.client:
            client, self.client = self.client, None
            await client.close()

    async def stop(self):
        if self.monitor:
            self.monitor.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor
        async with self.lock:
            await self.close_client()

    def reset(self):
        self.delay = 1
        self.next_connect = 0
        self.signal.set()

    async def failed(self, exc):
        self.problem = classify(exc)
        await self.close_client()
        self.next_connect = asyncio.get_running_loop().time() + self.delay
        self.delay = min(60, self.delay * 2)
        self.publish({"connected": False, "health": self.problem.code})

    async def ensure(self, fresh=False, companion_only=False):
        if self.pairing:
            raise Fault(
                "pairing_in_progress",
                "Pairing is in progress.",
                "Finish the PIN prompt.",
            )
        if fresh or self.rebuild:
            self.rebuild = False
            await self.close_client()
        if self.client is None:
            if (
                not fresh
                and self.problem
                and self.next_connect > asyncio.get_running_loop().time()
            ):
                raise self.problem
            try:
                self.client = await self.adapter.connect(
                    self.power_changed, companion_only=companion_only
                )
            except Exception as exc:
                await self.failed(exc)
                if self.problem is exc:
                    raise
                raise self.problem from exc
            self.problem = None
            self.publish({"connected": True})
        return self.client

    @asynccontextmanager
    async def session(self, fresh=False):
        async with self.lock:
            client = await self.ensure(fresh=fresh)
            try:
                yield client
                self.delay = 1
            except Fault:
                raise
            except Exception as exc:
                fault = classify(exc)
                if fault.code == "command_rejected":
                    raise fault from exc
                await self.failed(exc)
                raise self.problem from exc

    async def status(self):
        try:
            async with self.session() as client:
                try:
                    snapshot = await client.status()
                except Exception as exc:
                    if classify(exc).code != "unreachable":
                        raise
                    # A lease change can leave a stale Companion session. Retry the read
                    # once on a freshly resolved connection while holding the control lock.
                    client = await self.ensure(fresh=True)
                    snapshot = await client.status()
                self.power_changed_if_transition(snapshot["power"])
                return success(
                    "connected", ["Session responded"], connected=True, **snapshot
                )
        except Fault as exc:
            return exc.result() | {
                "connected": False,
                "power": "unknown",
                "app": None,
                "now_playing": None,
                "keyboard_focused": False,
                "volume_supported": False,
                "tvos": "unknown",
            }

    def power_changed_if_transition(self, power):
        if self.last_power == "off" and power in ("on", "screensaver"):
            self.power_changed(self.last_power, power)
        self.last_power = power

    def health(self):
        fields = {
            "connected": self.client is not None,
            "pairings": {
                p: bool(self.adapter.state.receiver(p))
                for p in ("airplay", "companion", "remote_pairing")
            },
        }
        fields["pairing_needed"] = self.adapter.state.pairing_needed()
        issue = self.problem
        if not issue and not fields["pairings"]["airplay"]:
            issue = Fault(
                "pairing_required",
                "AirPlay is unpaired.",
                "Pair AirPlay in Home Assistant.",
            )
        if not issue and not fields["pairings"]["companion"]:
            issue = Fault(
                "pairing_required",
                "Companion verification failed.",
                "Complete the fallback Companion pairing.",
            )
        if getattr(self.adapter, "access_restricted", False) and (
            not issue or issue.code not in ("unreachable", "tv_unreachable")
        ):
            issue = Fault(
                "allow_access_changed",
                "AirPlay reports home-only access.",
                "Set AirPlay and HomeKit → Allow Access to Anyone on the Same Network.",
            )
        if issue:
            return issue.result(state=issue.code) | fields
        return success("healthy" if self.client else "connecting", **fields)

    async def ready(self, force=False):
        if not force:
            status = await self.status()
            if status.get("error", {}).get("code") in (
                "pairing_required",
                "pairing_removed",
                "allow_access_changed",
            ):
                return status
            if status["power"] == "on":
                async with self.session():
                    pass  # ensure() rebuilds an Off→On session flagged by status().
                return success("on", ["TV already awake"])
        return await self.wake()

    async def wake(self):
        async with self.lock:
            evidence = []
            for attempt in range(1, 3):
                try:
                    client = await self.ensure(fresh=True, companion_only=True)
                    await client.hid("wake", 0)
                    evidence.append(f"Attempt {attempt}: HID Wake acknowledged")
                    async with asyncio.timeout(self.wake_timeout):
                        while (power := await client.power()) not in (
                            "on",
                            "screensaver",
                        ):
                            await asyncio.sleep(0.2)
                    self.power_changed_if_transition(power)
                    await self.ensure(fresh=True)
                    self.delay = 1
                    self.woke_at = asyncio.get_running_loop().time()
                    return success("on", evidence + [f"Power reported {power}"])
                except Exception as exc:  # noqa: BLE001 - A failed wake attempt always releases its session.
                    evidence.append(f"Attempt {attempt}: {type(exc).__name__}")
                    if self.client is not None:
                        await self.failed(exc)
                    fault = classify(exc)
                    if fault.code in (
                        "pairing_required",
                        "pairing_removed",
                        "allow_access_changed",
                    ):
                        return fault.result(evidence=evidence)
            if fault.code == "tv_unreachable":
                return fault.result(evidence=evidence)
            return Fault(
                "wake_failed",
                "Power-on could not be confirmed after two fresh sessions.",
                "Check pairing, Allow Access and the TV network.",
                True,
                **fault.fields,
            ).result(evidence=evidence)

    async def sleep_verified(self):
        loop = asyncio.get_running_loop()
        async with self.lock:
            for attempt in range(2):
                client = await self.ensure(fresh=bool(attempt))
                if await client.power() == "off":
                    return
                while loop.time() - self.woke_at < 2:
                    await asyncio.sleep(0.2)
                    if await client.power() == "off":
                        return
                try:
                    await client.hid("sleep", 0)
                    deadline = loop.time() + (5 if attempt else 2)
                    while loop.time() < deadline:
                        if await client.power() == "off":
                            self.power_changed_if_transition("off")
                            return
                        await asyncio.sleep(0.2)
                except Exception as exc:
                    fault = classify(exc)
                    if (
                        fault.code
                        in (
                            "pairing_required",
                            "pairing_removed",
                            "allow_access_changed",
                        )
                        or attempt
                    ):
                        raise fault from exc
                    await asyncio.sleep(2)
            raise Fault(
                "uncertain",
                "The TV did not confirm sleep after a fresh-session retry.",
                "Check tv_status before retrying.",
            )

    async def _monitor(self):
        last = None
        loop = asyncio.get_running_loop()
        while True:
            self.signal.clear()
            async with self.lock:
                if not self.pairing and loop.time() >= self.next_connect:
                    try:
                        client = await self.ensure()
                        snapshot = await client.status()
                        self.power_changed_if_transition(snapshot["power"])
                        if snapshot != last:
                            self.publish({"connected": True, **snapshot})
                            last = snapshot
                        self.delay = 1
                    except Exception as exc:  # noqa: BLE001 - Transport failures drive the reconnect loop.
                        if self.client:
                            await self.failed(exc)
                        last = None
            timeout = (
                max(0.1, self.next_connect - loop.time())
                if self.next_connect > loop.time()
                else 2
            )
            with suppress(TimeoutError):
                await asyncio.wait_for(self.signal.wait(), timeout)
