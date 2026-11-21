import argparse
import asyncio
import fcntl
import logging
import math
import os
import time
from contextlib import ExitStack
from datetime import UTC, datetime

from . import ROOT, control
from .common import HOST, Lab, dump, error, inspect_info, log, pair


def count(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("expected a positive count")
    return n


def minutes(value):
    n = float(value)
    if not math.isfinite(n) or n <= 0:
        raise argparse.ArgumentTypeError("expected positive finite minutes")
    return n


def parser():
    cli = argparse.ArgumentParser(prog="atvlab", description=f"Apple TV hardware spikes for {HOST}. Set ATV_BRIDGE_HOST to your TV address.")
    sub = cli.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="directed scan and complete AirPlay info plist")
    sub.add_parser("pair", help="one HAP PIN exchange").add_argument(
        "protocol", choices=["airplay", "companion"]
    )
    sub.add_parser("s3", help="test Companion and RAOP credential reuse and export sender stores")
    s4 = sub.add_parser("s4", help="wake, deep link and YouTube keyboard trials")
    s4.add_argument("--wake-trials", nargs="?", const=1, default=0, type=count)
    s4.add_argument("--launch", metavar="SERVICE=URL", action="append", default=[])
    s4.add_argument("--times", type=count, default=1)
    s4.add_argument("--text", nargs="?", const="Agent Bridge keyboard test")
    s4.add_argument(
        "--search-keys", default="left,up,up,up,select", help="YouTube navigation until keyboard focus"
    )
    s6 = sub.add_parser("s6", help="five-minute URL playback trials")
    s6.add_argument("--repeat", "--times", type=count, default=1)
    s1 = sub.add_parser("s1", help="mirror the clock page or inspect the negotiated display")
    s1.add_argument("--sender", choices=["tvpipe", "doubletake"], default="tvpipe")
    s1.add_argument("--minutes", type=minutes, default=1)
    s1.add_argument("--canvas-only", action="store_true")
    s1.add_argument("--visible", action="store_true", help="show tvpipe's browser on the Mac")
    s5 = sub.add_parser("s5", help="RemotePairing, DVT screenshots and AccessibilityAudit")
    actions = s5.add_subparsers(dest="action")
    s5.set_defaults(action="pair")
    actions.add_parser("pair")
    actions.add_parser("screenshot").add_argument("--repeat", type=count, default=1)
    actions.add_parser("ax-probe")
    sub.add_parser("screenshot", help="DVT screenshot series").add_argument(
        "--repeat", type=count, default=1
    )
    sub.add_parser("screen", help="one DVT screenshot, also updates results/latest.png")
    sub.add_parser("ax-probe", help="enumerate RSD services and probe accessibility")
    from . import press_probe

    press_probe.add_arguments(sub.add_parser("press-probe", help="record RSD log streams during remote presses"))
    sub.add_parser("power", help="wake ladder or confirmed sleep").add_argument(
        "power", choices=["on", "off"]
    )
    sub.add_parser("press", help="deliver ordered remote keys once").add_argument("keys", nargs="+")
    sub.add_parser("launch", help="open a bundle ID or URL").add_argument("target")
    sub.add_parser("status", help="power, app, metadata and keyboard state")
    return cli


async def dispatch(lab, args):
    match args.cmd:
        case "info":
            return await inspect_info(lab)
        case "pair":
            return await pair(lab, args.protocol)
        case "s3":
            return await control.reuse(lab)
        case "s4":
            return await control.matrix(lab, args)
        case "s6":
            from .playback import playback

            return await playback(lab, args.repeat)
        case "s1":
            from .mirror import mirror

            return await mirror(lab, args)
        case "s5" | "screenshot" | "screen" | "ax-probe":
            from .observer import observe

            return await observe(lab, getattr(args, "action", args.cmd), getattr(args, "repeat", 1))
        case "press-probe":
            from . import press_probe

            return await press_probe.run(
                lab.host,
                lab.state.root,
                lab.state.identity["controller_id"],
                args,
                args.out or lab.directory / "press-probe.jsonl",
            )
        case "power":
            return await lab.engine.wake() if args.power == "on" else await control.sleep(lab)
        case "press":
            return await control.press(lab, args.keys)
        case "launch":
            return await control.launch(lab, args.target)
        case "status":
            return await lab.engine.status()


def lock(stack, path):
    file = stack.enter_context(path.open("a"))
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("Another atvlab run is using this device session") from exc


async def run(args):
    os.umask(0o077)
    start = datetime.now(UTC)
    directory = ROOT / "results" / f"{start:%Y%m%dT%H%M%S.%fZ}-{args.cmd}"
    directory.mkdir(parents=True)
    logging.basicConfig(
        filename=directory / "run.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    print(f"RESULT {directory / 'result.json'}", flush=True)
    state = ROOT / "state"
    state.mkdir(exist_ok=True)
    begin = time.monotonic()
    try:
        with ExitStack() as stack:
            observing = args.cmd in ("s5", "screen", "screenshot", "ax-probe", "press-probe")
            if args.cmd != "info":
                lock(stack, state / ("observer.lock" if observing else "control.lock"))
            if args.cmd == "pair" or args.cmd == "s5" and args.action == "pair" or args.cmd == "s3":
                lock(stack, state / "pairing.lock")
            with ExitStack() as init:
                lock(init, state / "init.lock")
                lab = Lab(directory, state)
            try:
                log.info("start %s target=%s", args.cmd, HOST)
                result = await dispatch(lab, args)
            finally:
                await lab.engine.stop()
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - Every run gets a result.
        result = error(exc)
    result.update(
        command=args.cmd,
        target=HOST,
        started_at=start.isoformat(),
        elapsed_s=round(time.monotonic() - begin, 3),
    )
    (directory / "result.json").write_text(dump(result) + "\n")
    log.info("finish %s ok=%s", args.cmd, result["ok"])
    print(dump(result), flush=True)
    return 0 if result["ok"] else 1


def main():
    raise SystemExit(asyncio.run(run(parser().parse_args())))
