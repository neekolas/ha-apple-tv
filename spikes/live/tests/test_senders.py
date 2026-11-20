"""Opt-in to the real sender binaries and an OrbStack-local diagnostic receiver."""

import asyncio
import os
import re

import pytest
from build_receiver import build_receiver
from pyatv.auth import convert
from pyatv.conf import AppleTV, ManualService
from pyatv.const import Protocol
from pyatv.support.http import HttpConnection

from atvlab import common, mirror

pytestmark = pytest.mark.skipif(
    os.getenv("ATVLAB_SENDER_TEST") != "1", reason="opt in to the container receiver and Chromium"
)


async def test_mirror_to_local_receiver(tmp_path, monkeypatch):
    # This diagnostic receiver only parses RTSP request lines. The TV also accepts
    # HTTP/1.1 for these HAP exchanges. Preserve the pairing messages and keys.
    send = HttpConnection.send_and_receive

    async def rtsp(self, method, uri, **kwargs):
        kwargs["protocol"] = "RTSP/1.0"
        kwargs["headers"] = {"CSeq": "1", **(kwargs.get("headers") or {})}
        if uri == "/pair-setup":
            kwargs["headers"]["X-Apple-HKP"] = "5"  # diagnostic modern profile selects screen pairing

        return await send(self, method, uri, **kwargs)

    monkeypatch.setattr(HttpConnection, "send_and_receive", rtsp)
    binary = await asyncio.to_thread(build_receiver)
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-listen",
        "0.0.0.0:0",
        "-profile",
        "modern",
        "-auth",
        "pin",
        "-code",
        "2271",
        "-stats-interval",
        "1s",
        "-debug",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    receiver_log = []
    started = asyncio.Event()

    async def read_log():
        async for line in proc.stdout:
            receiver_log.append(line.decode(errors="replace"))
            if b"test receiver listening" in line:
                started.set()

    reader = asyncio.create_task(read_log())
    try:
        await asyncio.wait_for(started.wait(), 10)
        host = "127.0.0.1"
        port = int(re.search(r"listening on .*?:(\d+) \(profile=", "".join(receiver_log))[1])
        lab = common.Lab(tmp_path, tmp_path / "state", host, port, pin=tmp_path / "pin")

        async def config(selected=None, failed_host=None):
            conf = AppleTV(host, "diagnostic receiver")
            conf.add_service(
                ManualService("fake", Protocol.AirPlay, port, {"features": "0x00000000,0x00010000"})
            )
            return conf, "configured"

        async def submit(protocol, path):
            assert protocol == "airplay"
            return "2271"

        monkeypatch.setattr(lab.adapter.resolver, "resolve", config)
        monkeypatch.setattr(common, "read_pin", submit)
        assert (await asyncio.wait_for(common.pair(lab, "airplay"), 15))["ok"]
        info = await lab.info()
        value = lab.airplay_credentials()
        common.write_private(
            lab.state.root / "tvpipe/credentials.txt",
            convert.to_tvpipe(value, addr=f"{host}:{port}", device_id=info["deviceID"]),
        )
        common.write_private(
            lab.state.root / "doubletake/credentials.json",
            convert.to_doubletake(value, device_id=info["deviceID"]),
        )

        result = await mirror.canvas(lab, info)
        assert result["teardown_status"] == 200 and result["displays"], result
        result = await mirror.tvpipe(lab, f"airplay:{host}:{port}", 4)
        assert result["ok"] and result["canvas_lines"] and result["video_frames"] > 0, result

        def packets():
            stats = re.findall(r"video=(\d+)/\d+B audio=(\d+)/", "".join(receiver_log))
            return tuple(map(int, stats[-1]))

        first = packets()
        assert all(first)
        lab.host = "host.docker.internal"  # the same Mac receiver from inside the X11 container
        result = await mirror.doubletake(lab, info["deviceID"], 4)
        assert result["ok"], result
        second = packets()
        assert second[0] > first[0] and second[1] > first[1], (first, second)
    finally:
        if proc.returncode is None:
            proc.terminate()
        await proc.wait()
        await reader
        (tmp_path / "receiver.log").write_text("".join(receiver_log))
