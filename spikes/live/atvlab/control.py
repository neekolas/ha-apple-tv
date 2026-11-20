import asyncio
import copy
import wave
from unittest.mock import patch

import pyatv
import yaml
from pyatv.auth import convert
from pyatv.conf import AppleTV
from pyatv.const import Protocol
from pyatv.protocols.companion.api import SystemStatus

from . import APP
from .common import attempt, error, write_private


async def reuse(lab):
    value = lab.airplay_credentials()
    record = lab.state.pairings["airplay"]

    async def companion():
        try:
            client = await lab.adapter.connect(lambda *_: None, record, companion_only=True)
        except Exception as exc:  # noqa: BLE001 - Preserve transport failures alongside trial evidence.
            return error(exc) | {"session_established": False}
        try:
            # A missing feature is independent of an accepted HAP pairing.
            lab.state.save_pairings(companion=record)

            async def app_list():
                return {"values": await client.apps()}

            async def attention():
                value = await client.api.fetch_attention_state()
                return {"ok": value != SystemStatus.Unknown, "value": value.name}

            async def power():
                value = await client.power()
                return {"ok": value != "unknown", "value": value}

            checks = {
                "apps": await attempt(app_list),
                "attention": await attempt(attention),
                "power": await attempt(power),
            }
            return {"ok": all(r["ok"] for r in checks.values()), "session_established": True, **checks}
        finally:
            await client.close()

    async def raop():
        await lab.adapter.verify_reuse("raop", record)
        config = (await lab.adapter.resolver.resolve())[0]
        service = copy.deepcopy(config.get_service(Protocol.RAOP))
        service.credentials = value
        target = AppleTV(config.address, config.name)
        target.add_service(service)
        path = lab.directory / "silence.wav"
        with wave.open(str(path), "wb") as output:
            output.setparams((2, 2, 44100, 0, "NONE", ""))
            output.writeframes(bytes(2 * 2 * 44100 * 2))
        atv = await pyatv.connect(target, asyncio.get_running_loop())
        try:
            await asyncio.wait_for(atv.stream.stream_file(str(path)), 30)
            lab.state.save_pairings(raop=record)
            return {"file": path.name, "seconds": 2, "hap_verified": True}
        finally:
            await asyncio.gather(*atv.close())

    async def export(sender):
        info = await lab.info()
        device_id = (
            info.get("deviceID") or ((await lab.adapter.resolver.resolve())[0]).get_service(Protocol.AirPlay).identifier
        )
        if not device_id:
            raise ValueError("AirPlay deviceID unavailable")
        if sender == "tvpipe":
            data = convert.to_tvpipe(
                value,
                device_id=device_id,
                name=info.get("name", "Apple TV"),
                addr=f"{lab.host}:{lab.airplay_port}",
            )
            restored = convert.from_tvpipe(data)
            name = "credentials.txt"
        else:
            data = convert.to_doubletake(value, device_id=device_id)
            restored = convert.from_doubletake(data, device_id=device_id)
            name = "credentials.json"
        if restored != value:
            raise ValueError("Credential round trip failed")
        write_private(lab.state.root / sender / name, data)
        return {"path": f"{sender}/{name}", "device_id": device_id, "round_trip": True}

    rows = {"companion": await attempt(companion), "raop": await attempt(raop)}
    for sender in ("tvpipe", "doubletake"):
        rows[sender] = await attempt(export, sender)
    return {"ok": all(r["ok"] for r in rows.values()), "checks": rows}


async def press(lab, keys):
    async with lab.engine.session() as client:
        for key in keys:
            await client.hid(key, 0)
            await asyncio.sleep(0.6)
    return {"ok": True, "keys": keys}


async def sleep(lab):
    async with lab.engine.session() as client:
        await client.hid("sleep", 0)
        async with asyncio.timeout(8):
            while await client.power() != "off":
                await asyncio.sleep(0.2)
    return {"ok": True, "power": "off"}


async def launch(lab, target, expected=None):
    replies = []
    async with lab.engine.session() as client:
        # Observe decoded rejection fields before the fork converts them to ProtocolError.
        before = await client.status() if expected else None
        protocol = client.api._protocol
        original = protocol._handle_opack

        def capture(frame, reply):
            if any(k in reply for k in ("_em", "_ec", "_eu")):
                replies.append({k: reply[k] for k in ("_em", "_ec", "_eu", "_ed") if k in reply})
            return original(frame, reply)

        try:
            with patch.object(protocol, "_handle_opack", capture):
                await client.open(target)
        except Exception as exc:  # noqa: BLE001 - Preserve transport failures alongside trial evidence.
            return error(exc) | {"companion_errors": replies}
        snapshot = await client.status()

        def verified(current):
            if not expected:
                return True
            return (current["app"] or {}).get("bundle_id") == expected and (
                before["app"] != current["app"] or before["now_playing"] != current["now_playing"]
            )

        if expected:
            until = asyncio.get_running_loop().time() + 8
            while not verified(snapshot):
                if asyncio.get_running_loop().time() >= until:
                    break
                await asyncio.sleep(0.5)
                snapshot = await client.status()
        return {
            "ok": verified(snapshot),
            "before": before,
            "expected_bundle": expected,
            "acknowledged": True,
            "observed": snapshot,
            "companion_errors": replies,
        }


async def matrix(lab, args):
    services = yaml.safe_load((APP / "atv_bridge_app/data/deeplinks.yaml").read_text())
    launches = []
    for item in args.launch:
        service, sep, url = item.partition("=")
        if not sep or not url or service not in services and "." not in service:
            raise ValueError("Use --launch service=url or expected.bundle.id=url")
        launches.append((services.get(service, {}).get("bundle_id", service), url))
    rows = []
    for trial in range(1, args.wake_trials + 1):
        off = await attempt(sleep, lab)
        wake = await attempt(lab.engine.wake)
        rows.append(
            {"kind": "wake", "trial": trial, "ok": off["ok"] and wake["ok"], "sleep": off, "wake": wake}
        )
    for bundle, url in launches:
        for trial in range(1, args.times + 1):
            home = await attempt(press, lab, ["home"])
            row = await attempt(launch, lab, url, bundle) if home["ok"] else {"ok": False}
            rows.append(row | {"kind": "launch", "trial": trial, "home": home, "url": url})

    async def youtube_text():
        async with lab.engine.session() as client:
            await client.open("youtube://www.youtube.com/results?search_query=atvlab")
            await asyncio.sleep(2)
            # Depending on the installed YouTube version the link opens results or the sidebar.
            for key in args.search_keys.split(","):
                if (await client.status())["keyboard_focused"]:
                    break
                await client.hid(key, 0)
                await asyncio.sleep(0.6)
            matched = await client.type(args.text, True)
            actual = await client.facade.keyboard.text_get()
            return {"ok": matched and actual == args.text, "readback": actual}

    if args.text is not None:
        rows.append(await attempt(youtube_text) | {"kind": "text"})
    if not rows:
        raise ValueError("Choose --wake-trials, --launch or --text")
    return {"ok": all(r["ok"] for r in rows), "trials": rows}
