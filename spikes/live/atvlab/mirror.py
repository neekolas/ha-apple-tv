import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import uuid

from pyatv.auth import convert
from pyatv.auth.hap_pairing import parse_credentials
from pyatv.protocols.airplay.auth import verify_connection
from pyatv.support.http import decode_bplist_from_body, http_connect
from pyatv.support.rtsp import RtspSession

from . import ROOT
from .common import dump, log

IMAGE = "atvlab-doubletake:0.6.0"


async def screen_handshake(rtsp, helper):
    proc = await asyncio.create_subprocess_exec(
        helper,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        for request_size, response_size in ((16, 142), (164, 32)):
            data = await proc.stdout.readexactly(request_size)
            reply = await rtsp.exchange(
                "POST",
                "/fp-setup",
                headers={"X-Apple-ET": "32"},
                content_type="application/octet-stream",
                body=data,
                allow_error=True,
            )
            if reply.code == 404:
                return "unavailable"
            if reply.code != 200 or len(reply.body) != response_size:
                raise ValueError(f"Screen handshake failed (HTTP {reply.code}, {len(reply.body)} bytes)")
            proc.stdin.write(reply.body)
            await proc.stdin.drain()
        if await proc.wait() != 0:
            raise ValueError("Screen handshake failed receiver confirmation")
        return "verified"
    finally:
        proc.stdin.close()
        await proc.wait()


async def canvas(lab, info):
    creds = parse_credentials(lab.airplay_credentials())
    helper = ROOT / "bin/cargo/release/atvlab-canvas-handshake"
    if info.get("features", 0) & (1 << 14) and not helper.exists():
        await asyncio.to_thread(
            subprocess.run,
            [
                "cargo",
                "build",
                "--locked",
                "--release",
                "--manifest-path",
                ROOT / "canvas/Cargo.toml",
                "--target-dir",
                ROOT / "bin/cargo",
            ],
            check=True,
        )
    conn = await http_connect(lab.host, lab.airplay_port)
    try:
        await verify_connection(creds, conn)
        rtsp = RtspSession(conn)
        handshake = (
            await screen_handshake(rtsp, helper)
            if info.get("features", 0) & (1 << 14)
            else "unadvertised"
        )
        mac = bytearray(hashlib.sha256(creds.client_id).digest()[:6])
        mac[0] = (mac[0] | 2) & 254
        mac = mac.hex(":").upper()
        body = {
            "deviceID": mac,
            "macAddress": mac,
            "sessionUUID": str(uuid.uuid4()).upper(),
            "sourceVersion": "980.71.1",
            "isScreenMirroringSession": True,
            "osBuildVersion": "13F69",
            "model": "Linux",
            "name": "Agent Bridge",
            "updateSessionRequest": False,
            "combinedGetInfoWithControlSetup": True,
        }
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as timing:
            if info.get("features", 0) & (1 << 41):
                peer = {
                    "ID": str(uuid.uuid4()),
                    "Addresses": [conn.local_ip],
                    "DeviceType": 0,
                    "SupportsClockPortMatchingOverride": True,
                }
                body.update(timingProtocol="PTP", timingPeerInfo=peer, timingPeerList=[peer])
            else:
                timing.bind((conn.local_ip, 0))
                body.update(timingProtocol="NTP", timingPort=timing.getsockname()[1])
            uri = f"rtsp://{lab.host}:{lab.airplay_port}/{rtsp.session_id}"
            response = await rtsp.exchange("SETUP", uri, body=body)
            try:
                combined = decode_bplist_from_body(response).get("info", {})
                refreshed = (
                    combined
                    if "displays" in combined
                    else decode_bplist_from_body(await rtsp.exchange("GET", "/info"))
                )
                result = {
                    "setup_status": response.code,
                    "screen_handshake": handshake,
                    "timing": body["timingProtocol"],
                    "initial_displays": info.get("displays"),
                    "displays": refreshed.get("displays"),
                }
                print(dump(result), flush=True)
            finally:
                teardown = await rtsp.exchange("TEARDOWN", uri)
        return {"ok": True, **result, "teardown_status": teardown.code}
    finally:
        conn.close()


async def tvpipe(lab, target, seconds, visible=False):
    binary = ROOT / "bin/tvpipe"
    if not binary.is_file():
        await asyncio.to_thread(subprocess.run, [ROOT / "install-tvpipe.sh"], check=True)
    cmd = [binary, "--machine", "-v", "share", (ROOT / "test.html").as_uri(), "--to", target]
    if visible:
        cmd.append("--visible")
    env = os.environ | {"TVPIPE_CONFIG_DIR": str(lab.state.root / "tvpipe")}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    started = asyncio.Event()
    rows = {
        "drops": 0,
        "reconnects": 0,
        "frame_drops": 0,
        "frames_pushed": 0,
        "receiver_up_at_stop": False,
        "video_frames": 0,
        "canvas_lines": [],
        "receiver_events": [],
        "end": None,
    }

    async def consume():
        with (lab.directory / "tvpipe.log").open("w", buffering=1) as output:
            async for data in proc.stdout:
                line = data.decode(errors="replace")
                output.write(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "started":
                    rows["video"] = event.get("video")
                    started.set()
                elif kind == "receiver":
                    state = event["state"]
                    rows["receiver_events"].append(state)
                    if state != "stopped":
                        rows["receiver_up_at_stop"] = state in ("connected", "reconnected")
                    rows["drops"] += state in ("lost", "failed", "ended")
                    rows["reconnects"] += state == "reconnected"
                elif kind == "log" and re.search(r"canvas \d+x\d+|session ready", event["message"]):
                    rows["canvas_lines"].append(event["message"])
                elif kind == "stats":
                    rows["video_frames"] = event.get("video_frames", 0)
                    rows["frame_drops"] = sum(r.get("dropped", 0) for r in event.get("receivers", []))
                elif kind == "end":
                    rows["end"] = event["reason"]
                    rows["frames_pushed"] = sum(r.get("pushed", 0) for r in event.get("receivers", []))
                    rows["frame_drops"] = sum(r.get("dropped", 0) for r in event.get("receivers", []))
                elif kind == "error":
                    rows["sender_error"] = event.get("message")

    reader = asyncio.create_task(consume())
    wait = asyncio.create_task(proc.wait())
    ready = asyncio.create_task(started.wait())
    full = False
    duration = 0
    try:
        await asyncio.wait([wait, ready, reader], timeout=90, return_when=asyncio.FIRST_COMPLETED)
        if started.is_set() and not wait.done():
            start = time.monotonic()
            await asyncio.wait([wait, reader], timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            duration = time.monotonic() - start
            full = duration >= seconds
    finally:
        proc.stdin.close()  # machine EOF sends the stop command, including AirPlay TEARDOWN
        if not (await asyncio.wait([wait], timeout=10))[0]:
            proc.kill()
        await wait
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
        await reader
    return {
        "ok": full
        and proc.returncode == 0
        and rows["end"] in ("stopped", "eof")
        and "sender_error" not in rows
        and rows["frames_pushed"] > 0
        and rows["receiver_up_at_stop"],
        "exit_code": proc.returncode,
        "duration_s": round(duration, 3),
        **rows,
    }


async def doubletake(lab, device_id, seconds):
    data = (lab.state.root / "doubletake/credentials.json").read_text()
    if convert.from_doubletake(data, device_id=device_id) != lab.airplay_credentials():
        raise ValueError("Refresh credentials with s3")
    build_log = lab.directory / "doubletake-build.log"
    with build_log.open("w") as out:
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "build", "-t", IMAGE, ROOT / "doubletake"],
            stdout=out,
            stderr=subprocess.STDOUT,
            check=True,
        )
    name = "atvlab-" + uuid.uuid4().hex[:12]
    cmd = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--network",
        "host",
        "-i",
        "--name",
        name,
        "-v",
        f"{ROOT / 'test.html'}:/tmp/test.html:ro",
        IMAGE,
        "-target",
        lab.host,
        "-port",
        str(lab.airplay_port),
        "-device-id",
        device_id,
        "-creds",
        "/tmp/credentials.json",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    started = asyncio.Event()
    rows = {
        "ready_lines": [],
        "drops": 0,
        "reconnects": 0,
        "stream_errors": [],
        "target_latency_ms": 900,
        "capture": None,
    }

    async def consume():
        with (lab.directory / "doubletake.log").open("w", buffering=1) as out:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                out.write(line)
                if "screen capture started" in line:
                    rows["capture"] = line.strip()
                if "streaming error:" in line:
                    rows["stream_errors"].append(line.strip())
                if "mirror session ready" in line:
                    rows["ready_lines"].append(line.strip())
                    started.set()
                if "connection lost" in line.lower():
                    rows["drops"] += 1
                if "reconnected" in line.lower():
                    rows["reconnects"] += 1

    wait = asyncio.create_task(proc.wait())
    reader = asyncio.create_task(consume())
    ready = asyncio.create_task(started.wait())
    full = False
    duration = 0
    try:
        proc.stdin.write(data.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        await asyncio.wait([wait, ready], timeout=90, return_when=asyncio.FIRST_COMPLETED)
        if started.is_set() and not wait.done():
            start = time.monotonic()
            await asyncio.wait([wait], timeout=seconds)
            duration = time.monotonic() - start
            full = duration >= seconds
    finally:
        if not wait.done():
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "stop", "--signal", "SIGINT", "-t", "8", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        await wait
        await reader
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
    return {
        "ok": full
        and proc.returncode in (0, 130)
        and bool(rows["capture"])
        and not rows["stream_errors"],
        "exit_code": proc.returncode,
        "duration_s": round(duration, 3),
        **rows,
    }


async def mirror(lab, args):
    info = await lab.info()
    if args.canvas_only:
        return await asyncio.wait_for(canvas(lab, info), 180)
    if args.sender == "doubletake":
        result = await doubletake(lab, info["deviceID"], args.minutes * 60)
    else:
        text = (lab.state.root / "tvpipe/credentials.txt").read_text()
        if convert.from_tvpipe(text) != lab.airplay_credentials():
            raise ValueError("Refresh credentials with s3")
        result = await tvpipe(
            lab, f"airplay:{lab.host}:{lab.airplay_port}", args.minutes * 60, args.visible
        )
    log.info("pre-session displays %s", info.get("displays"))
    return result | {"sender": args.sender, "displays": info.get("displays")}
