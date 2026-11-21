"""Record RSD developer streams while remote presses happen, to find a press signal.

Hardware probe. It opens the paired RemotePairing tunnel with the bridge's
observer code and records every message from the selected streams to JSONL.
Filters only change what the terminal shows.

Runs three ways:
  uv run atvlab press-probe --seconds 60                    # lab state
  uv run python -m atvlab.press_probe --host IP --data DIR  # explicit record
  python press_probe.py --host IP --data /data/tvs/<id>     # in the HA app container (cwd /opt/app)
  ... --analyze FILE --press-times 1763650000.1,1763650003.4  # offline

DIR holds observer/remote.plist. Checked against pymobiledevice3 11.61.0.
"""

import argparse
import asyncio
import collections
import contextlib
import json
import os
import re
import struct
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

STREAMS = ("syslog", "activity", "ax", "notify", "kdebug")
# Terminal filter only. Names are guesses until the first hardware run.
INTEREST = (
    r"backboardd|PineBoard|TVAirPlay|AirPlay|rapportd|tvremoted|TVRemote|mediaremoted|bluetoothd|BTLEServer"
    r"|HID|button|press|select|arrow|keyboard|focus|gesture|remote"
)
ACTIVITY_PREDICATE = (
    "(messageType == info OR messageType == debug OR messageType == default OR "
    "messageType == error OR messageType == fault)"
)
# sys/kdebug.h: DBG_IOKIT 5, DBG_IOHID 35. Filter format is (class << 24) | (subclass << 16).
KDEBUG_IOHID = (5 << 24) | (35 << 16)
KD_BUF = struct.Struct("<Q5QII8x")  # timestamp, arg1-4, thread, debugid, cpu


def add_arguments(cli):
    cli.add_argument("--seconds", type=float, default=60, help="capture duration")
    cli.add_argument("--streams", default="syslog,activity", help=f"comma list of {', '.join(STREAMS)}")
    cli.add_argument("--out", type=Path, help="JSONL path")
    cli.add_argument("--process", help="terminal filter: regex on the process name")
    cli.add_argument("--match", help="terminal filter: regex on subsystem, category and message")
    cli.add_argument("--print-all", action="store_true", help="print every record")
    cli.add_argument("--quiet", action="store_true", help="print only the summary")
    cli.add_argument("--markers", action="store_true", help="stamp each line typed on stdin as a marker record")
    cli.add_argument(
        "--syslog-flags",
        type=lambda v: int(v, 0),
        default=0x124,
        help="syslog: os_activity_stream flags. Default 0x124 PAYLOAD|DEBUG|INFO. Console's sniffed set is 0x3c",
    )
    cli.add_argument("--syslog-pid", type=int, default=-1, help="syslog: device-side PID filter")
    cli.add_argument("--ur", type=int, default=100, help="activity and kdebug: update rate in ms")
    cli.add_argument("--predicate", default=ACTIVITY_PREDICATE, help="activity: NSPredicate on the device")
    cli.add_argument("--ax-event-type", type=int, default=2, help="ax: monitored event mask")
    cli.add_argument("--notify", action="append", default=[], metavar="NAME", help="Darwin notification to observe")
    cli.add_argument("--kdebug-all", action="store_true", help="kdebug: all classes instead of IOHID only")


def parser():
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--host", default=os.getenv("ATV_BRIDGE_HOST"), help="TV IPv4 address")
    cli.add_argument("--data", type=Path, help="directory holding observer/remote.plist")
    cli.add_argument("--controller-id", help="defaults to identity.json in --data or two levels up")
    cli.add_argument("--analyze", type=Path, help="rank signals in an existing JSONL and exit")
    cli.add_argument("--press-times", default="", help="analyze: comma list of host epoch seconds")
    cli.add_argument("--window", type=float, default=1.0, help="analyze: seconds after each press")
    add_arguments(cli)
    return cli


class Recorder:
    def __init__(self, path, args):
        self.file = path.open("a")
        self.args = args
        self.counts = collections.Counter()
        self.process = re.compile(args.process, re.I) if args.process else None
        self.match = re.compile(args.match, re.I) if args.match else None
        self.interest = re.compile(INTEREST, re.I)

    def write(self, stream, **fields):
        wall = time.time()
        row = {
            "wall": round(wall, 6),
            "wall_iso": datetime.fromtimestamp(wall, UTC).isoformat(timespec="milliseconds"),
            "mono": round(time.monotonic(), 6),
            "stream": stream,
            **fields,
        }
        self.file.write(json.dumps(row, default=repr, ensure_ascii=False) + "\n")
        self.file.flush()
        self.counts[stream] += 1
        if not self.args.quiet and self.shown(stream, row):
            text = " ".join(
                str(row[k]) for k in ("process", "pid", "level", "subsystem", "category", "message") if row.get(k) not in (None, "")
            )
            print(f"{row['wall_iso'][11:23]} {stream:8} {text[:300]}", flush=True)

    def shown(self, stream, row):
        if stream in ("marker", "meta", "ax", "notify", "kdebug") or self.args.print_all:
            return True
        text = " ".join(str(row.get(k) or "") for k in ("subsystem", "category", "message"))
        name = str(row.get("process") or "")
        if self.process or self.match:
            return bool((not self.process or self.process.search(name)) and (not self.match or self.match.search(text)))
        return bool(self.interest.search(name + " " + text))


async def syslog(rsd, rec, args):
    from pymobiledevice3.services.os_trace import OsTraceService

    # Upstream's default (0x3c, sniffed from Console) has HISTORICAL and CALLSTACK and no INFO bit.
    # Dropping HISTORICAL to avoid a backlog replay is inferred from the flag name. Not verified.
    rec.write("meta", message=f"syslog flags {args.syslog_flags:#x}")
    async with OsTraceService(lockdown=rsd) as service:
        async for entry in service.syslog(pid=args.syslog_pid, stream_flags=args.syslog_flags):
            rec.write(
                "syslog",
                device_time=entry.timestamp.timestamp(),
                mach=entry.mach_timestamp,
                pid=entry.pid,
                process=os.path.basename(entry.filename),
                image=os.path.basename(entry.image_name),
                level=getattr(entry.level, "name", str(entry.level)),
                subsystem=entry.label.subsystem if entry.label else None,
                category=entry.label.category if entry.label else None,
                message=entry.message,
            )


async def pid_names(rsd):
    from pymobiledevice3.services.os_trace import OsTraceService

    async with asyncio.timeout(10), OsTraceService(lockdown=rsd) as service:
        payload = (await service.get_pid_list()).get("Payload", {})
    return {int(pid): info.get("ProcessName") for pid, info in payload.items()}


async def activity(rsd, rec, args):
    from pymobiledevice3.services.dvt.instruments.activity_trace_tap import (
        ActivityTraceTap,
        decode_message_format,
    )
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider

    try:
        names = await pid_names(rsd)
    except Exception as exc:  # noqa: BLE001 - Names are optional.
        rec.write("meta", message=f"pid list unavailable: {type(exc).__name__}: {exc}")
        names = {}
    async with DvtProvider(rsd) as dvt:
        tap = ActivityTraceTap(dvt)
        # 11.61 hardcodes ur=500. Its kdebug tap documents ur as the flush interval in ms.
        tap._config.update(ur=args.ur, predicate=args.predicate)
        async with tap:
            async for row in tap:
                text = row.message
                with contextlib.suppress(Exception):
                    text = decode_message_format(row.message, getattr(row, "format_string", None)) if row.message else row.name
                rec.write(
                    "activity",
                    device_time=row.device_time.timestamp() if row.device_time else None,
                    mach=int.from_bytes(row.time, "little") if isinstance(row.time, bytes) else None,
                    pid=row.process,
                    process=names.get(row.process),
                    image=os.path.basename(row.sender_image_path or ""),
                    level=getattr(row, "message_type", None) or getattr(row, "event_type", None),
                    subsystem=row.subsystem,
                    category=row.category,
                    format=getattr(row, "format_string", None),
                    message=text,
                )


async def ax(rsd, rec, args):
    from pymobiledevice3.services.accessibilityaudit import AccessibilityAudit

    # This enables app monitoring on the legacy inspector. Use it on an idle lab TV.
    async with AccessibilityAudit(rsd) as audit:
        async for event in audit.iter_events(monitored_event_type=args.ax_event_type):
            rec.write("ax", message=event.name, data=event.data)


async def notify(rsd, rec, args):
    from pymobiledevice3.services.notification_proxy import NotificationProxyService

    async with NotificationProxyService(lockdown=rsd, timeout=args.seconds + 30) as service:
        for name in args.notify:
            await service.notify_register_dispatch(name)
        async for event in service.receive_notification():
            rec.write("notify", message=event.get("Name"), data=dict(event))


async def kdebug(rsd, rec, args):
    from pymobiledevice3.services.dvt.instruments.core_profile_session_tap import CoreProfileSessionTap
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider

    async with DvtProvider(rsd) as dvt:
        codes = await CoreProfileSessionTap.get_trace_codes(dvt)
        hid = {code: name for code, name in codes.items() if "hid" in name.lower()}
        rec.write("meta", message="HID trace codes", data={hex(k): v for k, v in sorted(hid.items())})
        config = await CoreProfileSessionTap.get_time_config(dvt)
        filters = {0xFFFFFFFF} if args.kdebug_all else {KDEBUG_IOHID}
        tap = CoreProfileSessionTap(dvt, config, filters, sample_callstacks=False)
        tap._config["ur"] = args.ur
        raw = rec.file.name + ".kdbuf"
        async with tap:
            with open(raw, "ab") as out:
                while True:
                    data = await tap._next_message()
                    if not data or data.startswith(b"bplist"):
                        continue
                    out.write(data)
                    out.flush()
                    # Best effort: aligned 64-byte kd_buf records. The raw file is authoritative.
                    events = collections.Counter()
                    if len(data) % KD_BUF.size == 0:
                        for offset in range(0, len(data), KD_BUF.size):
                            debugid = KD_BUF.unpack_from(data, offset)[6]
                            if debugid >> 24 == 5 and (debugid >> 16) & 0xFF == 35:
                                events[codes.get(debugid & ~3, hex(debugid))] += 1
                    if events or not args.kdebug_all:
                        rec.write("kdebug", message=f"{len(data)} bytes", data=dict(events))


def start_markers(rec):
    # Type a note and press Return to stamp it. A line starting with "press" counts in --analyze.
    # A daemon thread, so a blocked readline cannot delay exit.
    def read():
        for line in sys.stdin:
            with contextlib.suppress(ValueError):  # The file closes when the capture ends.
                rec.write("marker", message=line.strip())

    threading.Thread(target=read, daemon=True).start()


async def guarded(name, fn, rsd, rec, args):
    try:
        await fn(rsd, rec, args)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - One refused service must not stop the others.
        rec.write("meta", message=f"{name} stopped: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    return {"ok": True}


async def run(host, data, controller, args, out):
    chosen = [s.strip() for s in args.streams.split(",") if s.strip()]
    if unknown := set(chosen) - set(STREAMS):
        raise ValueError(f"Unknown streams: {sorted(unknown)}")
    if "notify" in chosen and not args.notify:
        raise ValueError("The notify stream needs at least one --notify NAME")
    env = {"ATV_BRIDGE_HOST": host, "ATV_BRIDGE_DATA": str(data), "ATV_CONTROLLER_ID": controller}
    with patch.dict(os.environ, env):
        from atv_bridge_observer.__main__ import Observer

        observer = Observer()
    if not observer.record.is_file():
        raise FileNotFoundError(f"No RemotePairing record at {observer.record}")
    rec = Recorder(out, args)
    functions = {"syslog": syslog, "activity": activity, "ax": ax, "notify": notify, "kdebug": kdebug}
    tasks = {}
    try:
        rsd = await asyncio.wait_for(observer.tunnel.aopen(), 30)
        services = sorted(rsd.peer_info["Services"])
        rec.write("meta", message="rsd services", data=services, product_version=rsd.product_version)
        tasks = {name: asyncio.create_task(guarded(name, functions[name], rsd, rec, args)) for name in chosen}
        if args.markers:
            start_markers(rec)
        print(f"RECORDING {out} for {args.seconds:g}s streams={','.join(chosen)}", flush=True)
        rec.write("meta", message="recording")
        await asyncio.wait([tasks[name] for name in chosen], timeout=args.seconds)
    finally:
        for task in tasks.values():
            task.cancel()
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        rec.write("meta", message="stopped", data=dict(rec.counts))
        rec.file.close()
        await observer.close()
    streams = {
        name: result if isinstance(result, dict) else {"ok": True, "ran_until_stop": True}
        for name, result in zip(tasks, results, strict=True)
    }
    return {
        "ok": bool(streams) and all(s["ok"] for s in streams.values()),
        "jsonl": str(out),
        "rsd_services": services,
        "streams": streams,
        "counts": dict(rec.counts),
    }


def signature(row):
    # Digits vary between presses. The format string is stable when the tap supplies it.
    text = row.get("format") or re.sub(r"0x[0-9a-f]+|\d+", "#", str(row.get("message") or ""), flags=re.I)
    return (row["stream"], row.get("process") or row.get("pid"), row.get("subsystem"), row.get("category"), text[:120])


def analyze(path, presses, window):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    presses = sorted(
        presses + [r["wall"] for r in rows if r["stream"] == "marker" and str(r.get("message", "")).lower().startswith("press")]
    )
    if not presses:
        raise ValueError("No press times. Pass --press-times or type 'press ...' markers during capture.")
    hits = collections.defaultdict(lambda: [set(), 0, []])
    for row in rows:
        if row["stream"] in ("marker", "meta"):
            continue
        entry = hits[signature(row)]
        entry[1] += 1
        for index, press in enumerate(presses):
            if 0 <= row["wall"] - press <= window and index not in entry[0]:
                entry[0].add(index)
                entry[2].append(row["wall"] - press)
    ranked = sorted(hits.items(), key=lambda item: (-len(item[1][0]), item[1][1]))
    print(f"{len(presses)} presses, {len(rows)} records, window {window:g}s")
    print("presses_hit  total  extra  first_delay_ms(min/median/max)  signature")
    for sig, (hit, total, delays) in ranked[:40]:
        if not hit:
            break
        delays.sort()
        ms = [round(d * 1000) for d in (delays[0], delays[len(delays) // 2], delays[-1])]
        print(f"{len(hit):>4}/{len(presses):<6} {total:>5} {total - len(hit):>6}  {ms[0]}/{ms[1]}/{ms[2]:<14} {' | '.join(str(s) for s in sig)}")


def controller_id(data):
    for path in (data / "identity.json", data.parent.parent / "identity.json"):
        if path.is_file():
            return json.loads(path.read_text())["controller_id"]
    raise ValueError("Pass --controller-id. No identity.json beside the pairing directory.")


def main():
    args = parser().parse_args()
    if args.analyze:
        analyze(args.analyze, [float(t) for t in args.press_times.split(",") if t], args.window)
        return
    if not args.host or not args.data:
        raise SystemExit("--host and --data are required to capture")
    os.umask(0o077)
    # The bridge gives its observer the same pymobiledevice3 home (app/atv_bridge_app/processes.py).
    os.environ.setdefault("XDG_DATA_HOME", str(args.data))
    out = args.out or Path(f"press-probe-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl")
    result = asyncio.run(run(args.host, args.data, args.controller_id or controller_id(args.data), args, out))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    if __package__ in (None, ""):
        # Standalone copy, for example inside the HA app container. The app code must be importable.
        sys.path.insert(0, os.getcwd())
    main()
