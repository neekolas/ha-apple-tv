"""Account for Chromium in the app's dedicated Linux PID namespace."""

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from ..processes import reap

CHROMIUM_WRAPPER = "/usr/local/bin/chromium-wrapper"

_LOG = logging.getLogger(__name__)
_owners = set()
_spawn_lock = asyncio.Lock()
_NAMES = {
    "chromium",
    "chromium-browser",
    "chrome",
    "google-chrome",
    "crashpad_handle",
    "chrome_crashpad",
    "chrome_crashpad_handler",
    "crashpad_handler",
}


@dataclass(frozen=True)
class Process:
    parent: int
    birth: int
    name: str
    rss: int
    zombie: bool


def snapshot(root):
    processes = {}
    for entry in root.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            stat = (entry / "stat").read_text()
            comm, _, rest = stat.rpartition(") ")
            fields = rest.split()
            name = comm.partition("(")[2]
            if name not in _NAMES:
                try:
                    name = Path(os.readlink(entry / "exe")).name
                except OSError:
                    pass
            rss = 0
            if name in _NAMES:
                status = (entry / "status").read_text().splitlines()
                rss = next((int(s.split()[1]) for s in status if s.startswith("VmRSS:")), 0)
            processes[int(entry.name)] = Process(
                int(fields[1]), int(fields[19]), name, rss, fields[0] == "Z"
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
    return processes


def stray_processes(processes, owner, other_owners=()):
    protected = set(other_owners) | {owner}
    stray = {}
    for pid, process in processes.items():
        if process.name not in _NAMES:
            continue
        ancestor = pid
        while ancestor in processes and ancestor not in protected:
            ancestor = processes[ancestor].parent
        if ancestor not in protected:
            stray[pid] = process
    return stray


def proc_root():
    # The image opts in. A developer's Linux workstation shares its PID
    # namespace with unrelated browsers and must never be swept.
    return Path("/proc") if os.getenv("ATV_BRIDGE_BROWSER_NAMESPACE") == "1" else None


def browser_status():
    root = proc_root()
    browsers = stray_processes(snapshot(root), None) if root else {}
    return {
        "processes": len(browsers),
        "rss_mib": round(sum(p.rss for p in browsers.values()) / 1024, 2),
    }


def log_memory(phase):
    # Supervisor's container memory includes file pages after Chromium exits.
    # Sample each side of a presentation to distinguish that from retained heaps.
    if not proc_root():
        return
    try:
        lines = Path("/sys/fs/cgroup/memory.stat").read_text().splitlines()
        fields = dict(line.split() for line in lines)
        keys = ("anon", "file", "shmem", "active_file", "inactive_file")
        _LOG.info(
            "Browser %s, cgroup MiB: anon=%.2f file=%.2f shmem=%.2f active_file=%.2f inactive_file=%.2f",
            phase,
            *(int(fields[key]) / 2**20 for key in keys),
        )
    except (OSError, KeyError, ValueError):
        return  # Older kernels may not expose cgroup v2 accounting.


async def sweep(owner=None, *, root=None):
    root = root or proc_root()
    if root is None:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 4
    signalled = set()
    while True:
        async with _spawn_lock:
            _owners.difference_update({p for p in _owners if p.returncode is not None})
            stray = stray_processes(snapshot(root), owner, (p.pid for p in _owners))
        if not stray:
            return
        for pid, process in stray.items():
            key = (pid, process.birth)
            if process.zombie or key in signalled:
                continue
            try:
                fd = os.pidfd_open(pid)
                try:
                    # PID reuse between enumeration and pidfd_open must not
                    # turn a cleanup signal into a signal to another process.
                    fields = (root / str(pid) / "stat").read_text().rpartition(") ")[2].split()
                    if int(fields[19]) != process.birth:
                        continue
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                    _LOG.info("Reaped stray browser %s PID %s", process.name, pid)
                    signalled.add(key)
                finally:
                    os.close(fd)
            except (FileNotFoundError, ProcessLookupError):
                pass
        if loop.time() >= deadline:
            raise RuntimeError(f"Browser processes did not exit: {sorted(stray)}")
        await asyncio.sleep(0.05)


async def spawn(*args, **kwargs):
    """Start a process group without losing ownership if the caller is cancelled."""

    async def start():
        async with _spawn_lock:
            proc = await asyncio.create_subprocess_exec(*args, start_new_session=True, **kwargs)
            _owners.difference_update({p for p in _owners if p.returncode is not None})
            _owners.add(proc)
            return proc

    child = asyncio.create_task(start())
    try:
        return await asyncio.shield(child)
    except asyncio.CancelledError:
        await reap(await child, group=True, sweep=sweep)
        raise
