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


def supervisor_token():
    return _supervisor_token or os.getenv("SUPERVISOR_TOKEN")


def child_env(**values):
    return {key: value for key, value in os.environ.items() if key != "SUPERVISOR_TOKEN"} | values


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
        self.lock = asyncio.Lock()

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
                    limit=32 * 1024**2,
                )
                self.stderr_task = asyncio.create_task(forward_observer_errors(self.proc.stderr))
            async with asyncio.timeout(35):
                self.proc.stdin.write(json.dumps({"method": method, **params}).encode() + b"\n")
                await self.proc.stdin.drain()
                response = json.loads(await self.proc.stdout.readline())
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
                if self.stderr_task:
                    await self.stderr_task
                    self.stderr_task = None
