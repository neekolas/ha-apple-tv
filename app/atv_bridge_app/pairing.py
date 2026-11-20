import asyncio
import re
import uuid
from time import monotonic
from contextlib import suppress

from .state import Fault, success


PAIR_WINDOW = 180
SCREEN_POLL_INTERVAL = 2
SCREEN_WAIT = (
    "Please open Settings › Remotes and Devices › Remote App and Devices on the TV."
)
SCREEN_CODE = "Please read me the 6-digit screen pairing code on the TV."
SCREEN_TRANSIENT = {
    "pairing_screen_unavailable",
    "observer_unavailable",
    "unreachable",
    "tv_unreachable",
}


class Pairing:
    def __init__(self, engine, observer):
        self.engine = engine
        self.adapter = engine.adapter
        self.state = self.adapter.state
        self.observer = observer
        self.pending = self.preparation = None
        self.deadline = 0
        self.id = self.protocol = self.handler = self.expiry = None

    async def _clear(self):
        try:
            if self.preparation:
                self.preparation.cancel()
                await asyncio.gather(self.preparation, return_exceptions=True)
                self.preparation = None
            if self.handler:
                await self.handler.close()
            elif self.protocol == "remote_pairing":
                await self.observer.close()
        finally:
            self.protocol = self.handler = self.id = self.pending = None
            self.engine.pairing = False
            self.engine.problem = None
            self.engine.reset()

    async def _expire(self, pairing_id):
        await asyncio.sleep(PAIR_WINDOW)
        async with self.engine.lock:
            if self.id == pairing_id:
                await self._clear()

    async def stop(self):
        if self.expiry:
            self.expiry.cancel()
            with suppress(asyncio.CancelledError):
                await self.expiry
        async with self.engine.lock:
            await self._clear()

    def prompt(self):
        if not self.id or not self.pending:
            return None
        return dict(
            self.pending,
            pairing_id=self.id,
            expires_in_s=max(0, int(self.deadline - monotonic())),
        )

    async def inspect(self, protocol, pairing_id, notify=False):
        async with self.engine.lock:
            self.check_session(protocol, pairing_id)
            if not self.pending:
                raise Fault(
                    "busy",
                    "A Configure pairing is in progress.",
                    "Finish that exchange in Home Assistant.",
                )
            if self.preparation and self.preparation.done():
                self.preparation.result()
            if notify:
                self.pending["notify"] = True
                self.engine.publish({"pairing": self.pending["state"]})
            return self.prompt()

    async def cancel(self, protocol, pairing_id):
        async with self.engine.lock:
            self.check_session(protocol, pairing_id)
            if self.expiry:
                self.expiry.cancel()
                await asyncio.gather(self.expiry, return_exceptions=True)
            await self._clear()

    def check_session(self, protocol, pairing_id):
        if (
            not self.id
            or monotonic() >= self.deadline
            or self.id != pairing_id
            or (protocol and self.protocol != protocol)
        ):
            raise Fault(
                "invalid_pairing_id",
                "This pairing session is missing or expired.",
                "Start tv_pair again for the same TV and kind.",
            )

    async def screen_start(self, **options):
        response = await self.observer.call("pair_start", **options)
        if response.get("already_paired"):
            raise Fault(
                "already_paired",
                "The observer already has a verified pairing.",
                "Check the screen pairing with tv_status.",
            )

    async def wait_for_settings(self):
        while True:
            await asyncio.sleep(SCREEN_POLL_INTERVAL)
            try:
                await self.screen_start(retry_discovery=True)
            except Fault as exc:
                if exc.code in SCREEN_TRANSIENT:
                    continue
                self.engine.publish({"pairing": "failed"})
                raise
            self.pending.update(state="code_on_screen", ask_user=SCREEN_CODE)
            self.engine.publish({"pairing": "code_on_screen"})
            return

    async def start(
        self,
        protocol,
        selected=None,
        *,
        replace=False,
        prompt=None,
    ):
        if protocol not in ("airplay", "companion", "remote_pairing"):
            raise Fault(
                "invalid_protocol",
                "Unknown pairing protocol.",
                "Choose airplay, companion or remote_pairing.",
            )
        async with self.engine.lock:
            if prompt and self.id:
                if self.protocol != protocol or not self.pending:
                    raise Fault(
                        "busy",
                        "Another pairing is in progress.",
                        "Finish or cancel the current pairing first.",
                    )
                if self.preparation and self.preparation.done():
                    self.preparation.result()
                if prompt.get("notify"):
                    self.pending["notify"] = True
                return success("pin_required", pairing_id=self.id)
            if (
                protocol == "companion"
                and "companion" not in self.state.pairing_needed()
            ):
                if not self.state.pairings.get("airplay"):
                    raise Fault(
                        "pairing_required",
                        "AirPlay must be paired first.",
                        "Start AirPlay pairing.",
                    )
                raise Fault(
                    "already_paired",
                    "Companion uses the saved controller identity.",
                    "Use tv_status to check the connection.",
                )
            if self.expiry:
                self.expiry.cancel()
                await asyncio.gather(self.expiry, return_exceptions=True)
            await self._clear()
            await self.engine.close_client()
            self.engine.pairing = True
            self.protocol = protocol
            try:
                if protocol == "remote_pairing":
                    self.handler = None
                    if replace:
                        self.state.save_pairings(remote_pairing=None)
                    try:
                        await self.screen_start(
                            **({"replace": True} if replace else {})
                        )
                    except Fault as exc:
                        if not prompt or exc.code not in SCREEN_TRANSIENT:
                            raise
                        prompt.update(
                            state="waiting_for_pairing_screen", ask_user=SCREEN_WAIT
                        )
                        self.preparation = asyncio.create_task(self.wait_for_settings())
                else:
                    if replace and protocol == "airplay":
                        self.state.save_pairings(
                            airplay=None, companion=None, raop=None, credential_reuse={}
                        )
                        self.state.write_tvpipe(self.adapter.host)
                    self.handler = await self.adapter.begin_pair(protocol, selected)
                self.id = uuid.uuid4().hex
                self.pending = prompt
                self.deadline = monotonic() + PAIR_WINDOW
                self.expiry = asyncio.create_task(self._expire(self.id))
                return success("pin_required", pairing_id=self.id)
            except BaseException:
                await self._clear()
                raise
            finally:
                if not self.id and self.engine.pairing:
                    await self._clear()

    async def finish(self, pairing_id, pin, protocol=None):
        async with self.engine.lock:
            self.check_session(protocol, pairing_id)
            if self.preparation:
                if not self.preparation.done():
                    raise Fault(
                        "busy",
                        "Waiting for the TV to offer a screen PIN.",
                        "Open Remote App and Devices and wait for the code.",
                    )
                await self.preparation
            if not re.fullmatch(r"[0-9]{4,6}", str(pin)):
                raise Fault(
                    "invalid_pin",
                    "Expected a four to six digit PIN.",
                    "Enter the PIN shown on the TV.",
                )
            try:
                evidence = []
                if self.protocol == "remote_pairing":
                    result = await self.observer.call("pair_finish", pin=str(pin))
                    self.state.save_pairings(remote_pairing={"path": result["path"]})
                else:
                    record = await self.adapter.finish_pair(self.handler, pin)
                    receiver = getattr(self.handler, "bridge_receiver", None)
                    await self.handler.close()
                    self.handler = None
                    if self.protocol == "airplay":
                        # Replace the receiver records together. The controller identity persists.
                        # A crash during verification leaves a usable AirPlay record.
                        updates = dict(
                            airplay=record,
                            companion=None,
                            raop=record,
                            credential_reuse={},
                        )
                    else:
                        updates = {"companion": record}
                    if receiver:
                        config, source, selected = receiver
                        self.adapter.resolver.remember(
                            config, source, selected=selected, **updates
                        )
                    else:
                        self.state.save_pairings(**updates)
                    if self.protocol == "airplay":
                        self.state.write_tvpipe(self.adapter.host)
                        reuse = {}
                        for protocol in ("companion", "raop"):
                            try:
                                async with asyncio.timeout(15):
                                    await self.adapter.verify_reuse(protocol, record)
                                reuse[protocol] = True
                            except Exception as exc:  # noqa: BLE001 - Each optional protocol probe has an independent outcome.
                                # No reply cannot establish credential rejection.
                                unavailable = isinstance(exc, OSError) or (
                                    isinstance(exc, Fault)
                                    and exc.code == "tv_unreachable"
                                )
                                reuse[protocol] = None if unavailable else False
                                evidence.append(
                                    f"{protocol} reuse did not verify ({type(exc).__name__})"
                                )
                        self.state.save_pairings(
                            credential_reuse=reuse,
                            companion=record if reuse["companion"] else None,
                        )
                        return success(
                            "paired",
                            evidence,
                            credential_reuse=reuse,
                            pairing_needed=self.state.pairing_needed(),
                        )
                return success("paired", evidence)
            finally:
                if self.expiry:
                    self.expiry.cancel()
                    await asyncio.gather(self.expiry, return_exceptions=True)
                    self.expiry = None
                await self._clear()
