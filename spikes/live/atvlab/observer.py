# Observer operations run in the short-lived atvlab CLI process.
import asyncio
import base64
import io
import os
import tempfile
from unittest.mock import patch

from atv_bridge_observer.__main__ import Observer
from PIL import Image
from pymobiledevice3.services.accessibilityaudit import AccessibilityAudit

from .common import attempt, prepare_pin, read_pin


async def ax_probe(observer):
    rsd = await asyncio.wait_for(observer.tunnel.aopen(), 30)
    items = []

    async def capabilities():
        async with asyncio.timeout(15), AccessibilityAudit(rsd) as service:
            return {"values": await service.capabilities()}

    async def list_items():
        async with asyncio.timeout(30), AccessibilityAudit(rsd) as service:
            async for item in service.iter_elements():
                items.append(item.to_dict())
                if len(items) >= 100:
                    return {"truncated": True}
        return {"truncated": False}

    caps = await attempt(capabilities)
    listing = await attempt(list_items)
    return {
        "ok": caps["ok"] and listing["ok"],
        "rsd_services": sorted(rsd.peer_info["Services"]),
        "capabilities": caps,
        "list_items": listing | {"items": items},
    }


async def observe(lab, action, repeat=1):
    env = {
        "ATV_BRIDGE_HOST": lab.host,
        "ATV_BRIDGE_DATA": str(lab.state.root),
        "ATV_CONTROLLER_ID": lab.state.identity["controller_id"],
    }
    with patch.dict(os.environ, env):
        observer = Observer()
    try:
        if action == "pair":
            prepare_pin(lab.pin)
            result = await asyncio.wait_for(observer.call({"method": "pair_start"}), 45)
            if not result.get("already_paired"):
                pin = await read_pin("remote_pairing", lab.pin)
                result = await asyncio.wait_for(observer.call({"method": "pair_finish", "pin": pin}), 60)
            if not observer.record.is_file():
                raise ValueError("RemotePairing completed without a record")
            # The Green mounts this directory at /data and the provider opens observer/remote.plist.
            lab.state.save_pairings(remote_pairing={"path": "/data/observer/remote.plist"})
            return {"ok": True, "record": str(observer.record.relative_to(lab.state.root))}
        if action == "ax-probe":
            return await ax_probe(observer)

        async def capture(index):
            reply = await observer.call({"method": "screenshot", "max_width": 16384})
            data = base64.b64decode(reply["png"], validate=True)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                if image.format != "PNG":
                    raise ValueError("Expected DVT PNG")
                size = image.size
            path = lab.directory / f"screen-{index:03}.png"
            path.write_bytes(data)
            if action == "screen":
                latest = lab.directory.parent / "latest.png"
                with tempfile.NamedTemporaryFile(dir=latest.parent, delete=False) as temp:
                    temp.write(data)
                os.replace(temp.name, latest)
            return {"file": path.name, "width": size[0], "height": size[1]}

        rows = [await attempt(capture, i + 1) for i in range(repeat)]
        return {"ok": all(r["ok"] for r in rows), "captures": rows}
    finally:
        await observer.close()
