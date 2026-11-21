import asyncio
from contextlib import asynccontextmanager

from .processes import ObserverClient
from .state import Fault


class ReceiverObserver:
    """Keep the pipe and the PIN exchange bound to the resolved TV."""

    def __init__(self, resolver, state):
        self.resolver = resolver
        self.state = state
        self.transport = ObserverClient(resolver.configured_host or "", state)
        self.lock = asyncio.Lock()
        self.invalid = False

    def subscribe(self, listener):
        self.transport.subscribe(listener)

    async def call(self, method, **params):
        async with self.lock:
            if method == "buttons_stop" and not self.transport.proc:
                return {"ok": True}  # Nothing is watching. Do not start a process to stop it.
            if method == "pair_finish":
                try:
                    result = await self.transport.call(method, **params)
                    self.invalid = False
                    return result
                finally:
                    await self.transport.close()
            if method == "health" and not params.get("verify"):
                if not self.transport.proc:
                    return {
                        "ok": True,
                        "paired": bool(self.state.receiver("remote_pairing")),
                        "tunnel": False,
                    }
                return await self.transport.call(method, **params)
            if method != "pair_start" and not self.state.receiver("remote_pairing"):
                raise Fault("pairing_required", "Remote Pairing is required.", "Pair the observer.")
            env = self.transport.env
            proc = self.transport.proc
            pinned = env["ATV_BRIDGE_HOST"]
            if proc and pinned and self.resolver.address != pinned:
                await self.transport.close()
            if method == "pair_start" and not params.pop("retry_discovery", False):
                await self.transport.close()
            if not self.transport.proc or self.transport.proc.returncode is not None:
                native = method != "pair_start" and not self.resolver.configured_host
                env["ATV_BRIDGE_BONJOUR"] = "1" if native else "0"
                env["ATV_BRIDGE_HOST"] = "" if native else await self.resolver.host()
            try:
                result = await self.transport.call(method, **params)
                if method == "health" and params.get("verify"):
                    self.invalid = False
                return result
            except Fault as exc:
                if exc.code == "screen_pairing_invalid":
                    self.invalid = True
                    raise
                if exc.code not in (
                    "observer_failed",
                    "observer_unavailable",
                    "screen_service_missing",
                ):
                    raise
                await self.transport.close()
                env["ATV_BRIDGE_BONJOUR"] = "0"
                env["ATV_BRIDGE_HOST"] = await self.resolver.host(env["ATV_BRIDGE_HOST"] or None)
                try:
                    result = await self.transport.call(method, **params)
                except Fault as retry:
                    if retry.code == "screen_service_missing":
                        self.invalid = True
                        raise Fault(
                            "screen_pairing_invalid",
                            "The resolved TV no longer advertises RemotePairing.",
                            "Open Remote App and Devices and choose Re-pair screen.",
                        ) from retry
                    if retry.code == "screen_pairing_invalid":
                        self.invalid = True
                    raise
                if method == "health" and params.get("verify"):
                    self.invalid = False
                return result

    @asynccontextmanager
    async def app_switch(self):
        async with self.lock, self.transport.app_switch():
            yield

    async def close(self):
        async with self.lock:
            await self.transport.close()
