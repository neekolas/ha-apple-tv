import asyncio
import json
import logging
import os
import signal
from pathlib import Path

from aiohttp import web

from .processes import isolate_supervisor_token, supervisor_token
from .fleet import Fleet
from .ingress import panel


async def main():
    os.umask(0o077)
    isolate_supervisor_token()
    root = Path(os.environ.get("ATV_BRIDGE_DATA", "/data")).resolve()
    options = json.loads((root / "options.json").read_text())
    logging.basicConfig(level=options.get("log_level", "info").upper())
    # pyatv's wire-level debug logs include keys, PINs and keyboard contents.
    logging.getLogger("pyatv").setLevel(logging.WARNING)
    # Status-only AirPlay traces omit credentials, headers, URLs and payloads.
    logging.getLogger("pyatv.airplay.url_status").setLevel(max(logging.INFO, logging.getLogger().level))
    service = Fleet(root, options)
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    runners = []
    try:
        for app, address, port in [
            (service.app(), "127.0.0.1", 8765),
            (panel(service), "172.30.32.1" if supervisor_token() else "127.0.0.1", 8099),
        ]:
            runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
            await runner.setup()
            runners.append(runner)
            await web.TCPSite(runner, address, port).start()
        logging.getLogger(__name__).info(
            "Apple TV Agent Bridge listening on loopback 8765 and stage 8766"
        )
        await stopped.wait()
    finally:
        for runner in reversed(runners):
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
