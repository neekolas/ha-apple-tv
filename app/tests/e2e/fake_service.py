"""Run the production bridge against loopback AirPlay and Companion protocol servers."""

import asyncio
import ipaddress
import json
import sys
from pathlib import Path

from aiohttp import web
from pyatv import conf
from pyatv.const import Protocol
from tests.fake_device import FakeAppleTV

from atv_bridge_app.atv import Adapter
from atv_bridge_app.server import Service
from atv_bridge_app.fleet import Fleet


async def main(data, discover=False):
    host = "127.0.0.1"
    tv = FakeAppleTV(asyncio.get_running_loop(), test_mode=False)
    buttons, companion = tv.add_service(Protocol.Companion)
    companion.set_installed_apps({"com.example.player": "Example Player"})
    airplay, _ = tv.add_service(Protocol.AirPlay)
    await tv.start()

    async def scan(address=None):
        # Replace only DNS-SD. Pairing and control use real protocol connections.
        config = conf.AppleTV(ipaddress.IPv4Address(host), "Loopback TV")
        for protocol, properties in (
            (Protocol.Companion, {}),
            (Protocol.AirPlay, {"features": "0x00000000,0x00010000"}),
        ):
            config.add_service(
                conf.ManualService(protocol.name, protocol, tv.get_port(protocol), properties)
            )
        return [config]

    def runtime(address, state, **options):
        adapter = Adapter(address, state, options.get("apple_tv_id", ""))
        adapter.resolver.scan = scan
        return Service(address, state, adapter, **options)

    service = Fleet(data, {"apple_tv_host": "" if discover else host}, factory=runtime, stage_port=0)
    runner = web.AppRunner(service.app())
    try:
        await runner.setup()
        await web.TCPSite(runner, host, 0).start()
        print(json.dumps({"port": runner.addresses[0][1]}), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.readline)
        assert buttons.system_info["name"] == "Agent Bridge", buttons.system_info["name"]
        assert airplay.pairing_client_names and all(
            name == "Agent Bridge" for _, name in airplay.pairing_client_names
        ), airplay.pairing_client_names
        (data / "last_button").write_text(str(buttons.latest_button))
    finally:
        await runner.cleanup()
        await tv.stop()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), "--discover" in sys.argv[2:]))
