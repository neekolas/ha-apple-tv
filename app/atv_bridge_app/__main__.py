import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

from .processes import forget_supervisor_token, isolate_supervisor_token, supervisor_token
from .fleet import Fleet
from .ingress import panel
from .standalone import REMEDIATION, configure
from .state import REWORDING

LAG_LIMIT = 1.0


async def watch_event_loop():
    """Warn when the event loop stalls, which delays every tool call at once."""
    log = logging.getLogger(__name__)
    loop = asyncio.get_running_loop()
    while True:
        before = loop.time()
        await asyncio.sleep(0.5)
        if (lag := loop.time() - before - 0.5) > LAG_LIMIT:
            log.warning("Event loop stalled for %d ms", lag * 1000)


async def main(argv=None):
    os.umask(0o077)
    isolate_supervisor_token()
    root = Path(os.environ.get("ATV_BRIDGE_DATA", "/data")).resolve()
    # Standalone settings, or None when Home Assistant's Supervisor supplies options.json.
    stray = supervisor_token()
    plan, reason = configure(sys.argv[1:] if argv is None else argv, os.environ, root, stray)
    # Playwright's driver and other children inherit os.environ. Settings hold the token now.
    os.environ.pop("TV_USE_TOKEN", None)
    if plan:
        forget_supervisor_token()
        REWORDING.update(REMEDIATION)
    options = plan.options if plan else json.loads((root / "options.json").read_text())
    logging.basicConfig(level=options.get("log_level", "info").upper())
    log = logging.getLogger(__name__)
    log.info("Starting in %s mode because %s", "standalone" if plan else "Home Assistant", reason)
    if plan and stray:
        log.warning("Ignoring SUPERVISOR_TOKEN. Standalone mode never contacts Supervisor.")
    for warning in plan.warnings if plan else ():
        log.warning("%s", warning)
    # pyatv's wire-level debug logs include keys, PINs and keyboard contents.
    logging.getLogger("pyatv").setLevel(logging.WARNING)
    # Status-only AirPlay traces omit credentials, headers, URLs and payloads.
    logging.getLogger("pyatv.airplay.url_status").setLevel(max(logging.INFO, logging.getLogger().level))
    # Stateless MCP logs one "Terminating session: None" line per request. The bridge logs requests itself.
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
    watchdog = asyncio.create_task(watch_event_loop())
    service = Fleet(root, options, **(plan.fleet if plan else {}))
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    runners = []
    try:
        listeners = plan.listeners(service) if plan else [
            (service.app(), "127.0.0.1", 8765),
            (panel(service), "172.30.32.1" if supervisor_token() else "127.0.0.1", 8099),
        ]
        for app, address, port in listeners:
            runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
            await runner.setup()
            runners.append(runner)
            await web.TCPSite(runner, address, port).start()
        if plan:
            logging.getLogger(__name__).log(
                logging.INFO if plan.token else logging.WARNING, "%s", plan.describe()
            )
        else:
            logging.getLogger(__name__).info(
                "Apple TV Agent Bridge listening on loopback 8765 and stage 8766"
            )
        await stopped.wait()
    finally:
        watchdog.cancel()
        for runner in reversed(runners):
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
