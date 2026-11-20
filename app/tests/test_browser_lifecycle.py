import asyncio
import os
import signal
import sys

import pytest

from atv_bridge_app.present import browsers
from atv_bridge_app.present.sender import STAGE_URL
from atv_bridge_app.present.tvpipe import TvpipeSender


def process(root, pid, name, parent, rss):
    path = root / str(pid)
    path.mkdir()
    fields = ["S", str(parent), *(["0"] * 17), str(pid * 11), "0"]
    (path / "stat").write_text(f"{pid} ({name}) " + " ".join(fields))
    (path / "status").write_text(f"VmRSS:\t{rss} kB\n")


async def test_sweep_keeps_live_tree_and_accounts_for_detached_processes(
    tmp_path, monkeypatch, caplog
):
    import shutil

    process(tmp_path, 13, "tvpipe", 1, 0)
    process(tmp_path, 14, "chromium", 13, 2048)
    process(tmp_path, 15, "chromium", 14, 3072)
    process(tmp_path, 17, "chrome_crashpad", 1, 1024)
    process(tmp_path, 19, "other", 1, 1024)
    monkeypatch.setattr(browsers, "proc_root", lambda: tmp_path)
    monkeypatch.setattr(os, "pidfd_open", lambda pid: pid, raising=False)
    monkeypatch.setattr(os, "close", lambda fd: None)
    killed = []

    def kill(pid, sig):
        assert sig == signal.SIGKILL
        killed.append(pid)
        shutil.rmtree(tmp_path / str(pid))

    monkeypatch.setattr(signal, "pidfd_send_signal", kill, raising=False)
    assert browsers.browser_status() == {"processes": 3, "rss_mib": 6}
    with caplog.at_level("INFO"):
        await browsers.sweep(13)
    assert killed == [17] and "chrome_crashpad PID 17" in caplog.text
    assert browsers.browser_status() == {"processes": 2, "rss_mib": 5}
    await browsers.sweep()
    assert set(killed) == {14, 15, 17}
    assert browsers.browser_status() == {"processes": 0, "rss_mib": 0}


async def test_crash_reaps_detached_pipe_holder_before_restart(service, tmp_path, monkeypatch):
    # Detached grandchild holds the dead leader's stdout open. The fake sweep
    # targets only its recorded PID, never the developer machine's browsers.
    script = tmp_path / "fake-tvpipe"
    pid_file, exited = tmp_path / "orphan", tmp_path / "cleaned"
    script.write_text(f"""#!{sys.executable}
import os, sys, subprocess
from pathlib import Path
pid_file, cleaned = Path({str(pid_file)!r}), Path({str(exited)!r})
if not pid_file.exists():
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    pid_file.write_text(str(orphan.pid))
elif not cleaned.exists():
    sys.exit(2)
print('{{"type":"started"}}', flush=True)
sys.stdin.buffer.read()
""")
    script.chmod(0o700)

    async def sweep(owner=None):
        if owner is None and pid_file.exists() and not exited.exists():
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
            exited.touch()

    monkeypatch.setattr(browsers, "sweep", sweep)
    sender = TvpipeSender("192.0.2.1", service.state, str(script), restart_delay=0.01)
    try:
        await sender.start(STAGE_URL)
        first = sender.proc
        first.kill()
        async with asyncio.timeout(6):
            while sender.proc is first or not sender.started:
                await asyncio.sleep(0.05)
        assert exited.exists() and first.returncode == -signal.SIGKILL
    finally:
        await sender.stop()
        if pid_file.exists() and not exited.exists():
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


@pytest.mark.parametrize("enabled", [False, True])
async def test_crash_probe_is_test_only_and_requires_internal_token(service, aiohttp_client, monkeypatch, enabled):
    from unittest.mock import Mock
    monkeypatch.setenv("ATV_BRIDGE_LIFECYCLE_TEST", "1" if enabled else "0")
    proc = Mock(returncode=None, pid=3)
    service.operations.live_mirror.proc = proc
    client = await aiohttp_client(service.app())
    path = "/api/tool/test_crash_tvpipe"
    assert (await client.post(path,json={"action":"crash"})).status == 401
    headers = {"Authorization":"Bearer " + service.state.token}
    reply = await (await client.post(path,headers=headers,json={"action":"crash"})).json()
    assert reply["ok"] is enabled and proc.kill.call_count == int(enabled)
    service.operations.live_mirror.proc = None
