import asyncio
import json
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager, suppress

from .state import Fault

observer_log = logging.getLogger("atv_bridge.observer")


async def forward_observer_errors(stream):
    async for line in stream:
        message = line.decode(errors="replace").rstrip()
        level = message.partition(":")[0]
        observer_log.log(logging.getLevelNamesMapping().get(level, logging.WARNING), "%s", message)


# Playwright starts its driver internally using the parent environment.
_supervisor_token = None


def isolate_supervisor_token():
    global _supervisor_token
    if "SUPERVISOR_TOKEN" in os.environ:
        _supervisor_token = os.environ.pop("SUPERVISOR_TOKEN")


def forget_supervisor_token():
    """Standalone mode never talks to Supervisor, even when a stray token is present."""
    global _supervisor_token
    _supervisor_token = None
    os.environ.pop("SUPERVISOR_TOKEN", None)


def supervisor_token():
    return _supervisor_token or os.getenv("SUPERVISOR_TOKEN")


# Child processes never inherit these secrets.
SECRETS = ("SUPERVISOR_TOKEN", "TV_USE_TOKEN")


def child_env(**values):
    return {key: value for key, value in os.environ.items() if key not in SECRETS} | values


async def reap(proc, *, group=False, sweep=None):
    def send(sig):
        with suppress(ProcessLookupError):
            if group:
                os.killpg(proc.pid, sig)
            elif proc.returncode is None:
                proc.send_signal(sig)

    send(signal.SIGTERM)
    if group:
        # wait() can block on an inherited stdout even after the leader dies.
        # returncode follows waitpid, independent of the pipe's lifetime.
        deadline = asyncio.get_running_loop().time() + 3
        while proc.returncode is None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        send(signal.SIGKILL)
        if sweep:
            await sweep()
    else:
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            send(signal.SIGKILL)
    await proc.wait()


class ObserverClient:
    def __init__(self, host, state, command=None):
        self.command = command or [sys.executable, "-m", "atv_bridge_observer"]
        self.env = child_env(
            ATV_BRIDGE_HOST=host,
            ATV_BRIDGE_DATA=str(state.root),
            ATV_CONTROLLER_ID=state.identity["controller_id"],
            XDG_DATA_HOME=str(state.root),
        )
        self.proc = None
        self.stderr_task = None
        self.reader = None
        self.pending = None
        self.listeners = []
        self.lock = asyncio.Lock()

    def subscribe(self, listener):
        """Receive the observer's unsolicited events. A listener must return at once."""
        self.listeners.append(listener)

    def _emit(self, event):
        for listener in tuple(self.listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - A listener must not stop the pipe reader.
                observer_log.exception("Observer event listener failed")

    async def _read(self, proc):
        """Split stdout into event lines and the response to the single request in flight."""
        try:
            while line := await proc.stdout.readline():
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("Not a JSON object")
                except ValueError:
                    # Keep reading, so later requests still get their responses.
                    # A request in flight fails now instead of waiting for its deadline.
                    observer_log.warning("Observer wrote a line that is not a JSON object")
                    if self.pending and not self.pending.done():
                        self.pending.set_exception(ValueError("Unreadable observer response"))
                    continue
                if "event" in message:
                    self._emit(message)
                elif self.pending and not self.pending.done():
                    self.pending.set_result(message)
        except Exception:  # noqa: BLE001 - A broken pipe ends the request below.
            observer_log.warning("Observer output could not be read")
        finally:
            if self.pending and not self.pending.done():
                self.pending.set_exception(ConnectionError("Observer exited"))
            self._emit({"event": "closed"})

    async def call(self, method, **params):
        async with self.lock:
            return await self._call_locked(method, params)

    @asynccontextmanager
    async def app_switch(self):
        """Release the source app's AX subscription and exclude competing status reads."""
        async with self.lock:
            if self.proc and self.proc.returncode is None:
                try:
                    await self._call_locked("ax_release", {})
                except Fault:
                    observer_log.warning("Closing an unresponsive observer before app switch")
                    await self.close()
            yield

    async def _call_locked(self, method, params):
        try:
            if not self.proc or self.proc.returncode is not None:
                if self.stderr_task:
                    await self.stderr_task
                self.proc = await asyncio.create_subprocess_exec(
                    *self.command,
                    env=self.env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # A detailed 4K PNG is transferred as a single base64 JSON line.
                    limit=64 * 1024**2,
                )
                self.stderr_task = asyncio.create_task(forward_observer_errors(self.proc.stderr))
                self.reader = asyncio.create_task(self._read(self.proc))
            async with asyncio.timeout(35):
                self.pending = asyncio.get_running_loop().create_future()
                self.proc.stdin.write(json.dumps({"method": method, **params}).encode() + b"\n")
                await self.proc.stdin.drain()
                response = await self.pending
        except BaseException as exc:
            await self.close()
            if not isinstance(exc, Exception):
                raise
            raise Fault(
                "observer_unavailable",
                "Observer did not respond.",
                "Check Remote Pairing and the network.",
                True,
            ) from exc
        if not response.get("ok"):
            raise Fault(
                response.get("code", "observer_failed"),
                response.get("message", "Observer failed."),
                "Open Remotes and Devices → Remote App and Devices, then pair the observer.",
                True,
            )
        return response

    async def close(self):
        if self.proc:
            proc, self.proc = self.proc, None
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except TimeoutError:
                await reap(proc)
            finally:
                if self.reader:
                    reader, self.reader = self.reader, None
                    await asyncio.gather(reader, return_exceptions=True)
                if self.stderr_task:
                    await self.stderr_task
                    self.stderr_task = None
