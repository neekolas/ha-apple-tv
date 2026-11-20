import asyncio
import base64
import json
import logging
import os
import plistlib
import re
import time
from pathlib import Path

import pyatv
from atv_bridge_app.atv import Adapter, credentials
from atv_bridge_app.engine import Engine
from atv_bridge_app.state import State, write_private
from pyatv.support.http import http_connect

HOST = os.getenv("ATV_BRIDGE_HOST") or "192.0.2.15"
PIN_FILE = Path("/tmp/atvlab/pin")
log = logging.getLogger("atvlab")


def json_value(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def dump(value):
    return json.dumps(value, default=json_value, indent=2, ensure_ascii=False)


def error(exc):
    return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


async def attempt(fn, *args):
    start = time.monotonic()
    try:
        detail = await fn(*args)
        row = {"ok": True, **detail}
    except Exception as exc:  # noqa: BLE001 - Keep independent probe results on driver failures.
        row = error(exc)
    row["elapsed_s"] = round(time.monotonic() - start, 3)
    log.info("%s %s (%.3fs)", fn.__name__, "pass" if row["ok"] else "fail", row["elapsed_s"])
    return row


def prepare_pin(path):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.unlink(missing_ok=True)


async def read_pin(protocol, path, timeout=180):
    print("PIN_REQUIRED", protocol, flush=True)
    async with asyncio.timeout(timeout):
        while True:
            try:
                value = path.read_text().strip()
            except FileNotFoundError:
                await asyncio.sleep(0.15)
                continue
            path.unlink()
            if not re.fullmatch(r"[0-9]{4,8}", value):
                raise ValueError("PIN file must contain 4–8 ASCII digits")
            return value


class Lab:
    def __init__(self, directory, state_root, host=HOST, airplay_port=7000, pin=PIN_FILE):
        self.directory = directory
        self.host = host
        self.airplay_port = airplay_port
        self.pin = pin
        self.state = State(state_root)
        if self.state.identity["name"] != "Agent Bridge":
            self.state.identity["name"] = "Agent Bridge"
            write_private(self.state.root / "identity.json", dump(self.state.identity))
        self.adapter = Adapter(host, self.state)
        self.engine = Engine(self.adapter, lambda event: log.info("engine %s", event))

    def airplay_credentials(self):
        record = self.state.pairings.get("airplay")
        if not record:
            raise ValueError("Pair AirPlay first")
        return credentials(self.state.identity, record)

    async def info(self):
        async with asyncio.timeout(10):
            conn = await http_connect(self.host, self.airplay_port)
            try:
                reply = await conn.send_and_receive(
                    "GET", "/info", user_agent="AirPlay/980.71.1", headers={"CSeq": "1"}
                )
                return plistlib.loads(reply.body)
            finally:
                conn.close()


async def inspect_info(lab):
    async def plist():
        info = await lab.info()
        return {"plist": info, "model": info.get("model"), "build": info.get("osBuildVersion")}

    async def scan():
        configs = await pyatv.scan(loop=asyncio.get_running_loop(), hosts=[lab.host])
        devices = []
        for config in configs:
            if str(config.address) != lab.host:
                continue
            info = config.device_info
            devices.append(
                {
                    "name": config.name,
                    "address": lab.host,
                    "id": config.identifier,
                    "model": info.raw_model,
                    "version": info.version,
                    "build": info.build_number,
                    "services": [
                        {
                            "protocol": s.protocol.name,
                            "port": s.port,
                            "id": s.identifier,
                            "properties": dict(s.properties),
                            "pairing": s.pairing.name,
                        }
                        for s in config.services
                    ],
                }
            )
        return {"ok": bool(devices), "devices": devices}

    info, discovery = await asyncio.gather(attempt(plist), attempt(scan))
    return {"ok": info["ok"] and discovery["ok"], "info": info, "scan": discovery}


async def pair(lab, protocol):
    if protocol == "companion":
        print(
            "WARNING Repeated Companion failures trigger BackOff. A TV restart may be required. One attempt.",
            flush=True,
        )
    prepare_pin(lab.pin)
    handler = await lab.adapter.begin_pair(protocol)
    try:
        pin = await read_pin(protocol, lab.pin)
        if len(pin) != 4:
            raise ValueError("Expected a four-digit HAP PIN")
        record = await lab.adapter.finish_pair(handler, pin)
        lab.state.save_pairings(**{protocol: record})
        return {"ok": True, "protocol": protocol, "client_name": lab.state.identity["name"]}
    finally:
        await handler.close()
