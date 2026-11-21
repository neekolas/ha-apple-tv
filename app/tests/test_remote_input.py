"""Remote presses: log matching, the observer watch, event demux, lifecycle and key dispatch."""

import asyncio
import json
import plistlib
import struct
import sys
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from atv_bridge_app.present import remote_input
from atv_bridge_app.present.remote_input import FOCUS_HELPER, FOCUS_STAGE, PRESS_LOG
from atv_bridge_app.processes import ObserverClient
from atv_bridge_app.state import Fault, State
from atv_bridge_app.tools.contract import ROUTED_SCHEMAS, SCHEMAS
from atv_bridge_observer import __main__ as observer_module
from atv_bridge_observer import buttons

LEFT = "Unknown page:0x1 usage:0x8B downEvent:1 source:externalAccessory (MediaRemote) senderID:100003D02 down"


async def until(condition, timeout=2):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


# --- Matcher ---------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        (LEFT, ("left", True)),
        (LEFT.replace("0x8B", "0x8A"), ("right", True)),
        (LEFT.replace("0x8B", "0x8C"), ("up", True)),
        (LEFT.replace("0x8B", "0x8D"), ("down", True)),
        ("Select page:0xC usage:0x80 downEvent:1 source:externalAccessory (MediaRemote) senderID:100003D02 down", ("select", True)),
        # A physical remote reports another source and sender. The match ignores both.
        ("Unknown page:0x1 usage:0x8b downEvent:1 source:HIDDevice senderID:F00D down", ("left", True)),
        ("Select page:0x0C usage:0x80 downEvent:1", ("select", True)),
        # Lines logged by a physical clickpad Siri Remote on tvOS 27.2.
        ("Left page:0xC usage:0x44 downEvent:1 source:verifiedExternalAccessory senderID:100000597 down", ("left", True)),
        ("Right page:0xC usage:0x45 downEvent:1 source:verifiedExternalAccessory senderID:100000597 down", ("right", True)),
        ("Up page:0xC usage:0x42 downEvent:1 source:verifiedExternalAccessory senderID:100000597 down", ("up", True)),
        ("Down page:0xC usage:0x43 downEvent:1 source:verifiedExternalAccessory senderID:100000597 down", ("down", True)),
        # Each press also logs an up line and two follow-up lines.
        (LEFT.replace("downEvent:1", "downEvent:0").replace("D02 down", "D02 up"), (None, True)),
        (" 0x1/0x8B/100003D02 began: firstDown:0s ago b0e0u0 destinations:<keyboardFocus>", (None, True)),
        (" 0x1/0x8B/100003D02 finished: firstDown:0.00454s ago b0e0u1", (None, True)),
        # Unknown keys and unknown line shapes are reported for diagnosis, never as a key.
        ("TV page:0xC usage:0x60 downEvent:1 source:externalAccessory (MediaRemote) senderID:1 down", (None, False)),
        ("Unknown page:0xC usage:0x60 downEvent:0 source:x senderID:1 up", (None, True)),
        ("A new line shape", (None, False)),
    ],
)
def test_press_matcher_uses_page_and_usage_only(message, expected):
    assert buttons.match_press("backboardd", "Button", message) == expected


def test_press_matcher_ignores_other_processes_and_categories():
    assert buttons.match_press("rapportd", "Button", LEFT) == (None, True)
    assert buttons.match_press("backboardd", "SendHIDEvent", LEFT) == (None, True)
    assert buttons.match_press("backboardd", None, LEFT) == (None, True)


def test_stream_flags_never_replay_history():
    assert buttons.STREAM_FLAGS == 0x4
    assert not buttons.STREAM_FLAGS & buttons.OsActivityStreamFlag.HISTORICAL


def test_stale_lines_are_dropped():
    # now is the host's monotonic clock. device is the TV's wall clock.
    fresh = buttons.Freshness(now=1000.0)
    # Right after a connect nothing counts as a press.
    assert fresh.stale(device=950.0, now=1000.2)
    assert not fresh.stale(device=951.5, now=1001.6)
    # A line that took 3 s longer than the baseline arrives too late.
    assert fresh.stale(device=952.0, now=1005.1)
    # A line from before the newest one seen is a replay.
    assert not fresh.stale(device=960.0, now=1010.1)
    assert fresh.stale(device=957.0, now=1010.1)


def test_a_replayed_backlog_after_a_reconnect_is_dropped():
    first = buttons.Freshness(now=1000.0)
    # On the first connection, a short burst of old lines falls in the grace period.
    assert all(first.stale(device=900.0 + step * 0.3, now=1000.0 + step * 0.01) for step in range(60))
    assert not first.stale(device=1003.0, now=1003.4)
    assert first.stale(device=990.0, now=1003.5)
    # A reconnect keeps the lag baseline. A long replay is then late from its first line to its last.
    again = buttons.Freshness(now=2000.0, lag=first.lag)
    assert all(again.stale(device=1900.0 + step * 0.3, now=2000.0 + step * 0.02) for step in range(300))
    assert not again.stale(device=2010.0, now=2010.4)


@pytest.mark.parametrize("step", [5.0, -5.0, 3600.0, -3600.0])
def test_presses_resume_after_a_clock_step(step):
    """Reviewer case: after a clock step every press was dropped while the state stayed active."""
    fresh = buttons.Freshness(now=1000.0)
    assert not fresh.stale(device=1009.9, now=1010.0)
    # The TV clock steps. One press logs several lines within a few milliseconds.
    first = [fresh.stale(device=1020.0 - 0.1 + offset - step, now=1020.0 + offset) for offset in (0, 0.004, 0.008)]
    later = [fresh.stale(device=at - 0.1 - step, now=at) for at in (1030.0, 1100.0, 5000.0)]
    if step < 0:
        # The TV clock moved forward: the lag only shrinks, which is never stale.
        assert first == [False] * 3 and later == [False] * 3
    else:
        # The first press may be lost. A steady new lag is then accepted for good.
        assert first == [True] * 3 and later == [False] * 3
    # A late line is still dropped after the new baseline.
    assert fresh.stale(device=5001.0 - step - 4.0, now=5001.0)


# --- Observer watch --------------------------------------------------------


def entry(message, device=None, process="backboardd", category="Button"):
    return SimpleNamespace(
        timestamp=datetime.fromtimestamp(time.time() if device is None else device),
        filename="/usr/libexec/" + process,
        label=SimpleNamespace(category=category, subsystem="com.apple.BackBoard") if category else None,
        message=message,
    )


def start_activity_answer(status, fmt=plistlib.FMT_BINARY):
    """The three byte strings that the device sends in answer to StartActivity."""
    body = plistlib.dumps({"Status": status}, fmt=fmt)
    count = (len(body).bit_length() + 7) // 8
    return struct.pack("<I", count), len(body).to_bytes(count, "little"), body


class WireConnection:
    """Feeds the real OsTraceService.syslog() from a byte string, then stays silent."""

    def __init__(self, data):
        self.data, self.position = data, 0

    async def send_plist(self, *_, **__):
        pass

    async def recvall(self, size):
        if self.position + size > len(self.data):
            await asyncio.Event().wait()
        self.position += size
        return self.data[self.position - size:self.position]

    async def close(self):
        pass


@pytest.mark.parametrize("status,fmt,expected", [
    ("RequestSuccessful", plistlib.FMT_BINARY, True),
    ("RequestSuccessful", plistlib.FMT_XML, True),
    # A refusal with a short body has a 1-byte length. That read must not count as accepted.
    ("RequestFailed", plistlib.FMT_BINARY, False),
    ("RequestFailed", plistlib.FMT_XML, False),
])
async def test_stream_counts_as_established_only_after_an_accepting_status(status, fmt, expected):
    """Runs the real pymobiledevice3 syslog() read sequence (reviewer repro established.py)."""
    answer = b"".join(start_activity_answer(status, fmt))
    connection = WireConnection(answer)
    service = buttons.OsTraceService(lockdown=None, service=connection)
    established = asyncio.Event()
    seen = []
    buttons.mark_established(service.service, established)
    recvall = connection.recvall

    async def spy(size):
        data = await recvall(size)
        seen.append((connection.position, established.is_set()))
        return data

    connection.recvall = spy
    first = asyncio.create_task(service.syslog(pid=1, stream_flags=4).__anext__())
    await asyncio.sleep(0.05)
    # Never set before the whole status plist was read.
    assert all(not was_set for position, was_set in seen if position < len(answer))
    assert established.is_set() is expected
    assert first.done() is not expected  # An accepted idle stream waits. A refused one raises.
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


class FakeTrace:
    """Stand-in for OsTraceService. Each instance is one connection."""

    RSD_SERVICE_NAME = buttons.OsTraceService.RSD_SERVICE_NAME
    pids = {"68": {"ProcessName": "backboardd"}, "70": {"ProcessName": "rapportd"}}
    streams = []
    pid_error = None

    accept = True
    status = "RequestSuccessful"

    def __init__(self, lockdown):
        self.rsd = lockdown
        # The answer to StartActivity, then an endless run of 1-byte record markers.
        self.service = SimpleNamespace(recvall=AsyncMock(side_effect=[*start_activity_answer(self.status), *[b"\x02"] * 999]))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get_pid_list(self):
        if FakeTrace.pid_error:
            raise FakeTrace.pid_error
        return {"Payload": dict(FakeTrace.pids)}

    async def syslog(self, pid, stream_flags):
        lines = asyncio.Queue()
        FakeTrace.streams.append(SimpleNamespace(pid=pid, flags=stream_flags, lines=lines))
        if not FakeTrace.accept:
            await asyncio.Event().wait()  # The device never answers StartActivity.
        # The same reads as pymobiledevice3 11.61 syslog(): count, length, status plist.
        (count,) = struct.unpack("<I", await self.service.recvall(4))
        length = int.from_bytes(await self.service.recvall(count), "little")
        if plistlib.loads(await self.service.recvall(length)).get("Status") != "RequestSuccessful":
            await asyncio.sleep(0.03)  # The refusal reaches the caller a moment later.
            raise ConnectionError("The device refused to start the log stream")
        while True:
            await self.service.recvall(1)
            item = await lines.get()
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.fixture
def watch(monkeypatch, tmp_path):
    FakeTrace.streams, FakeTrace.pid_error, FakeTrace.accept = [], None, True
    FakeTrace.status = "RequestSuccessful"
    FakeTrace.pids = {"68": {"ProcessName": "backboardd"}, "70": {"ProcessName": "rapportd"}}
    monkeypatch.setattr(buttons, "OsTraceService", FakeTrace)
    monkeypatch.setattr(buttons, "CONNECT_GRACE_S", 0)
    monkeypatch.setattr(buttons, "RETRY_MIN_S", 0.01)
    record = tmp_path / "remote.plist"
    record.touch()
    rsd = SimpleNamespace(peer_info={"Services": {FakeTrace.RSD_SERVICE_NAME: {}}})
    tunnel = SimpleNamespace(rsd=rsd)
    observer = SimpleNamespace(
        tunnel=tunnel, record=record, busy=asyncio.Lock(), close_channels=AsyncMock(), pairing=False,
        open_tunnel=AsyncMock(return_value=rsd),
    )
    observer.pairing_active = lambda: observer.pairing
    events = []
    return buttons.ButtonWatch(observer, events.append), observer, events


async def test_watch_reports_one_key_per_press_with_a_pid_filter(watch):
    watch, observer, events = watch
    watch.start()
    try:
        await until(lambda: {"event": "buttons", "state": "active"} in events)
        stream = FakeTrace.streams[0]
        assert (stream.pid, stream.flags) == (68, 0x4)
        up = LEFT.replace("downEvent:1", "downEvent:0")
        for line in (entry(LEFT), entry(up), entry(" 0x1/0x8B/1 began: x"), entry(" 0x1/0x8B/1 finished: x"),
                     entry(LEFT, process="rapportd"), entry("TV page:0xC usage:0x60 downEvent:1 x"),
                     entry(LEFT, device=time.time() - 30)):
            stream.lines.put_nowait(line)
        await until(lambda: any(e["event"] == "button_unmatched" for e in events))
        await asyncio.sleep(0.02)
        presses = [e for e in events if e["event"] == "button"]
        assert [e["key"] for e in presses] == ["left"]
        assert presses[0]["received"] >= presses[0]["device_time"]
        assert [e["message"] for e in events if e["event"] == "button_unmatched"] == ["TV page:0xC usage:0x60 downEvent:1 x"]
    finally:
        await watch.stop()


async def test_watch_restarts_after_a_channel_reset_and_reads_the_pid_again(watch):
    watch, observer, events = watch
    watch.start()
    try:
        await until(lambda: len(FakeTrace.streams) == 1 and events)
        FakeTrace.pids = {"91": {"ProcessName": "backboardd"}}
        FakeTrace.streams[0].lines.put_nowait(ConnectionResetError())
        await until(lambda: len(FakeTrace.streams) == 2)
        assert FakeTrace.streams[1].pid == 91
        await until(lambda: [e["state"] for e in events if e["event"] == "buttons"] == ["active", "unavailable", "active"])
        assert events[1]["reason"] == "stream_failed"
        FakeTrace.streams[1].lines.put_nowait(entry(LEFT))
        await until(lambda: events[-1].get("key") == "left")
        # The watch never closes a tunnel that requests may be using.
        observer.close_channels.assert_not_awaited()
    finally:
        await watch.stop()


async def test_watch_probes_liveness_without_log_traffic(watch, monkeypatch):
    watch, observer, events = watch
    monkeypatch.setattr(buttons, "PROBE_INTERVAL_S", 0.02)
    monkeypatch.setattr(buttons, "PROBE_RETRY_S", 0.02)
    watch.start()
    try:
        await until(lambda: events == [{"event": "buttons", "state": "active"}])
        await asyncio.sleep(0.1)  # Silence is normal. Probes pass and nothing changes.
        assert len(events) == 1 and len(FakeTrace.streams) == 1
        FakeTrace.pid_error = TimeoutError()
        async with observer.busy:  # A request in flight delays the tunnel reset.
            await asyncio.sleep(0.1)
            observer.close_channels.assert_not_awaited()
        await until(lambda: observer.close_channels.await_count == 1)
        await until(lambda: events[-1].get("state") == "unavailable")
    finally:
        await watch.stop()


class ScreenPairingInvalid(Exception):
    pass


async def hang():
    await asyncio.sleep(5)




@pytest.mark.parametrize("case,reason", [
    ("unpaired", "screen_not_paired"), ("pairing", "pairing_in_progress"), ("no_service", "service_missing"),
    ("no_process", "process_not_found"), ("unreachable", "tv_unreachable"), ("slow", "tv_unreachable"),
    ("rejected", "screen_pairing_invalid"), ("refused", "stream_failed"), ("silent", "stream_failed"),
])
async def test_watch_reports_why_it_cannot_run(watch, monkeypatch, case, reason):
    watch, observer, events = watch
    if case == "unpaired":
        observer.record.unlink()
    elif case == "pairing":
        observer.pairing = True
    elif case == "no_service":
        observer.open_tunnel.return_value.peer_info["Services"].clear()
    elif case == "no_process":
        FakeTrace.pids = {}
    elif case == "unreachable":
        observer.open_tunnel.side_effect = ConnectionError("no route")
    elif case == "slow":
        monkeypatch.setattr(buttons, "OPEN_TIMEOUT_S", 0.02)
        observer.open_tunnel.side_effect = hang
    elif case == "rejected":
        observer.open_tunnel.side_effect = ScreenPairingInvalid()
    elif case == "refused":
        # The device answers StartActivity with a failure status. "active" must never show.
        monkeypatch.setattr(FakeTrace, "status", "RequestFailed")
    else:
        # The device never answers the stream request. The watch must not report "active".
        FakeTrace.accept = False
        monkeypatch.setattr(buttons, "ESTABLISH_TIMEOUT_S", 0.05)
    watch.start()
    try:
        await until(lambda: events)
        await asyncio.sleep(0.05)
        assert events == [{"event": "buttons", "state": "unavailable", "reason": reason}]
        if case in ("unpaired", "pairing"):
            observer.open_tunnel.assert_not_awaited()
    finally:
        await watch.stop()


async def test_observer_requests_work_while_the_watch_fails(monkeypatch, tmp_path, capsys):
    for key, value in {"ATV_BRIDGE_HOST": "192.0.2.1", "ATV_CONTROLLER_ID": "test", "ATV_BRIDGE_DATA": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)

    class Tunnel:
        def __init__(self, provider_factory):
            self.rsd = None

        async def aopen(self):
            raise ConnectionError("no route")

        async def aclose(self):
            pass

    monkeypatch.setattr(observer_module, "UserspaceRsdTunnel", Tunnel)
    monkeypatch.setattr(buttons, "RETRY_MIN_S", 0.01)
    observer = observer_module.Observer()
    observer.record.touch()
    try:
        started = time.monotonic()
        assert await observer.call({"method": "buttons_start"}) == {}
        assert await observer.call({"method": "buttons_start"}) == {}  # Idempotent.
        assert time.monotonic() - started < 0.5
        await until(lambda: "unavailable" in capsys.readouterr().out)
        assert await observer.call({"method": "health"}) == {"paired": True, "tunnel": False}
        task = observer.buttons.task
        assert await observer.call({"method": "buttons_stop"}) == {}
        assert task.cancelled() and observer.buttons.task is None
    finally:
        await observer.close()


# --- Tunnel sharing with the real pymobiledevice3 lifecycle ------------------


@pytest.fixture
def real_tunnel(monkeypatch, tmp_path):
    """An Observer on the real UserspaceRsdTunnel. Only the network parts are replaced."""
    import contextlib

    import pymobiledevice3.remote.userspace_tunnel as userspace
    from pymobiledevice3.services.accessibilityaudit import AccessibilityAudit
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider

    for key, value in {"ATV_BRIDGE_HOST": "192.0.2.1", "ATV_CONTROLLER_ID": "test", "ATV_BRIDGE_DATA": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    link = SimpleNamespace(handshake=0.2, connects=0, hang=False)

    class Service:
        async def close(self):
            pass

        @contextlib.asynccontextmanager
        async def start_tcp_tunnel(self):
            link.connects += 1
            await asyncio.sleep(3600 if link.hang else link.handshake)
            tun = SimpleNamespace(set_peer=lambda address: None)
            yield SimpleNamespace(client=SimpleNamespace(tun=tun), address="fd00::1", port=1, auxiliary_metadata={})

    class Dial:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def dial(self, *_):
            pass

    class Rsd:
        peer_info = {"Services": {name: {} for name in (
            FakeTrace.RSD_SERVICE_NAME, DvtProvider.RSD_SERVICE_NAME, AccessibilityAudit.RSD_SERVICE_NAME)}}

        def __init__(self, *_, **__):
            pass

        async def connect(self):
            pass

        async def close(self):
            pass

    async def never(self, client):
        await asyncio.Event().wait()

    async def provider(self):
        return SimpleNamespace(service=Service(), lockdown=None)

    monkeypatch.setattr(userspace, "UserspaceDialPlane", Dial)
    monkeypatch.setattr(userspace, "RemoteServiceDiscoveryService", Rsd)
    monkeypatch.setattr(userspace.UserspaceRsdTunnel, "_watch_transport_closed", never)
    monkeypatch.setattr(observer_module.Observer, "provider", provider)
    FakeTrace.streams, FakeTrace.pid_error, FakeTrace.accept, FakeTrace.status = [], None, True, "RequestSuccessful"
    FakeTrace.pids = {"68": {"ProcessName": "backboardd"}}
    monkeypatch.setattr(buttons, "OsTraceService", FakeTrace)
    monkeypatch.setattr(buttons, "RETRY_MIN_S", 0.01)
    events = []
    monkeypatch.setattr(observer_module, "emit", events.append)
    observer = observer_module.Observer()
    observer.record.touch()
    return observer, link, events


async def test_two_plain_opens_collide_in_pymobiledevice3(real_tunnel):
    """The defect that open_tunnel() prevents: a second aopen() during the first one raises."""
    observer, link, _ = real_tunnel
    try:
        first = asyncio.create_task(observer.tunnel.aopen())
        await asyncio.sleep(0.05)
        with pytest.raises(Exception, match="already active"):
            await observer.tunnel.aopen()
        await first
    finally:
        await observer.close()


async def test_watch_and_requests_share_one_tunnel_open(real_tunnel):
    observer, link, events = real_tunnel
    try:
        # The watch starts the connect. Requests arrive while it runs.
        assert await observer.call({"method": "buttons_start"}) == {}
        await asyncio.sleep(0.05)
        assert link.connects == 1 and observer.tunnel.rsd is None
        started = time.monotonic()
        results = await asyncio.gather(
            observer.call({"method": "health", "verify": True}),
            observer.open_tunnel(),
            observer.open_tunnel(),
        )
        # They joined the running connect. Nobody failed and nobody connected again.
        assert results[0] == {"paired": True, "tunnel": True} and results[1] is results[2] is observer.tunnel.rsd
        assert link.connects == 1 and time.monotonic() - started < link.handshake + 0.2
        await until(lambda: {"event": "buttons", "state": "active"} in events)
        # After a channel reset both sides reopen through the same path.
        await observer.close_channels()
        assert observer.tunnel.rsd is None
        FakeTrace.streams[0].lines.put_nowait(ConnectionResetError())  # A closed tunnel ends the stream.
        await asyncio.sleep(0.03)  # The watch is reconnecting now.
        assert await observer.call({"method": "health", "verify": True}) == {"paired": True, "tunnel": True}
        await until(lambda: len(FakeTrace.streams) == 2 and events[-1] == {"event": "buttons", "state": "active"})
        assert link.connects == 2
    finally:
        await observer.close()
    assert observer.tunnel.rsd is None


async def test_slow_watch_connect_does_not_hold_requests_or_the_tunnel(real_tunnel, monkeypatch):
    """With an unreachable TV the watch gives up fast and its connect never blocks a close."""
    observer, link, events = real_tunnel
    link.hang = True
    monkeypatch.setattr(buttons, "OPEN_TIMEOUT_S", 0.3)
    try:
        await observer.call({"method": "buttons_start"})
        await asyncio.sleep(0.05)
        # A request fails by its own deadline, not the watch's.
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await observer.open_tunnel()
        # A close does not wait for the watch's connect.
        await observer.close_channels()
        assert time.monotonic() - started < 0.25
        # The watch survives the cancelled connect, reports it and tries again.
        await until(lambda: {"event": "buttons", "state": "unavailable", "reason": "tv_unreachable"} in events)
        await until(lambda: link.connects >= 2)
        assert not observer.buttons.task.done()
        # When nobody waits any more, the connect is cancelled and the tunnel is free again.
        await observer.buttons.stop()
        await asyncio.sleep(0.01)
        assert observer.opening[0].done()
        link.hang = False
        assert await observer.call({"method": "health", "verify": True}) == {"paired": True, "tunnel": True}
    finally:
        await observer.close()


async def test_request_does_not_join_a_cancelled_connect(monkeypatch, tmp_path):
    """Reviewer repro open_join.py: a request arrives while a cancelled connect still unwinds."""
    for key, value in {"ATV_BRIDGE_HOST": "192.0.2.1", "ATV_CONTROLLER_ID": "test", "ATV_BRIDGE_DATA": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    rsd = SimpleNamespace(peer_info={"Services": {}})

    class Tunnel:
        def __init__(self, provider_factory):
            self.rsd, self.opens, self.unwinding = None, 0, False

        async def aopen(self):
            self.opens += 1
            if self.opens > 1:
                assert not self.unwinding  # A new connect must wait for the old one to finish.
                self.rsd = rsd
                return rsd
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.unwinding = True
                await asyncio.sleep(0.2)  # Closing a half-open tunnel takes time.
                self.unwinding = False
                raise

        async def aclose(self):
            self.rsd = None

    monkeypatch.setattr(observer_module, "UserspaceRsdTunnel", Tunnel)
    observer = observer_module.Observer()
    observer.record.touch()
    try:
        # The watch is the only waiter and gives up.
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await observer.open_tunnel()
        assert observer.opening[0].cancelling() and not observer.opening[0].done()
        # Two requests arrive during the unwind. Both get a new connect and no error.
        assert await asyncio.gather(observer.open_tunnel(), observer.open_tunnel()) == [rsd, rsd]
        assert observer.tunnel.opens == 2
    finally:
        await observer.close()


# --- Pipe demux ------------------------------------------------------------

SCRIPT = """import json, sys
print(json.dumps({"event": "buttons", "state": "active"}), flush=True)
for line in sys.stdin:
    method = json.loads(line)["method"]
    if method == "die":
        sys.exit(0)
    print(json.dumps({"event": "button", "key": "left"}), flush=True)
    print(json.dumps({"ok": True, "method": method}), flush=True)
    print(json.dumps({"event": "button", "key": "select"}), flush=True)
"""


async def test_a_stray_output_line_does_not_stall_later_requests(tmp_path):
    script = """import json, sys
for line in sys.stdin:
    method = json.loads(line)["method"]
    if method == "garbled":
        print("not json", flush=True)
        continue
    print(json.dumps({"ok": True, "method": method}), flush=True)
    print("a stray line while idle", flush=True)
    print("[1, 2]", flush=True)
"""
    client = ObserverClient("192.0.2.1", State(tmp_path), [sys.executable, "-c", script])
    try:
        assert (await client.call("first"))["ok"]
        await asyncio.sleep(0.2)
        started = time.monotonic()
        assert (await client.call("second"))["method"] == "second"
        # An unreadable response fails its request at once, not after the 35 s deadline.
        with pytest.raises(Fault) as failure:
            await client.call("garbled")
        assert failure.value.code == "observer_unavailable" and time.monotonic() - started < 5
        assert (await client.call("third"))["method"] == "third"
    finally:
        await client.close()


async def test_client_separates_events_from_responses(tmp_path):
    client = ObserverClient("192.0.2.1", State(tmp_path), [sys.executable, "-c", SCRIPT])
    events = []
    client.subscribe(events.append)
    try:
        assert (await client.call("ax_focused"))["method"] == "ax_focused"
        assert (await client.call("screenshot"))["method"] == "screenshot"
        await until(lambda: len(events) == 5)
        assert [e.get("key") or e.get("state") for e in events] == ["active", "left", "select", "left", "select"]
        # A dead process fails the request at once and reports "closed".
        started = time.monotonic()
        with pytest.raises(Fault) as failure:
            await client.call("die")
        assert failure.value.code == "observer_unavailable" and time.monotonic() - started < 5
        assert events[-1] == {"event": "closed"}
        # The next call starts a new process.
        assert (await client.call("health"))["ok"]
    finally:
        await client.close()
    assert events[-1] == {"event": "closed"}


# --- App-side lifecycle ----------------------------------------------------


class FakeObserver:
    def __init__(self):
        self.calls = []
        self.listeners = []
        self.failure = None
        self.reply = {"event": "buttons", "state": "active"}

    def subscribe(self, listener):
        self.listeners.append(listener)

    def emit(self, event):
        for listener in self.listeners:
            listener(event)

    async def call(self, method, **params):
        self.calls.append(method)
        if method.startswith("buttons"):
            if self.failure:
                raise self.failure
            if method == "buttons_start" and self.reply:
                self.emit(self.reply)
            return {"ok": True}
        return {"ok": True, "focused": {"caption": "TV"}, "items": []}

    def count(self, method):
        return self.calls.count(method)


@pytest.fixture
async def remote(service, monkeypatch):
    observer = FakeObserver()
    service.operations.observer = observer
    sender = SimpleNamespace(mode="mirror", started=True, stop=AsyncMock())
    service.operations.presenter = sender
    yield service, observer, service.tools.remote_input
    await service.tools.remote_input.close()


async def test_watch_follows_the_presented_page(remote):
    service, observer, control = remote
    stage = service.stage
    await stage.show("a", "/c/a/index.html")
    await asyncio.sleep(0.02)
    assert observer.calls == [] and control.status() is None and not control.tasks

    await stage.show("b", "/c/b/index.html", remote="keys")
    await until(lambda: control.state == "active")
    assert observer.calls == ["buttons_start"]
    assert control.status() == {"mode": "keys", "state": "active", "presses": 0, "last": None}
    # In auto mode the mirror can fall back to a recording. Keys cannot reach a recording.
    service.operations.presenter = SimpleNamespace(mode="auto", active=SimpleNamespace(mode="play_url"))
    assert control.status()["reason"] == "not_mirrored"
    service.operations.presenter = SimpleNamespace(mode="mirror", stop=AsyncMock())

    # A second page with the option keeps the same watch.
    await stage.show("c", "/c/c/index.html", remote="focus")
    await asyncio.sleep(0.02)
    assert observer.calls == ["buttons_start"] and control.status()["mode"] == "focus"

    # A page without the option stops it.
    await stage.show("d", "/c/d/index.html")
    await until(lambda: observer.count("buttons_stop") == 1)
    assert control.status() is None

    await stage.show("e", "/c/e/index.html", remote="keys")
    await until(lambda: observer.count("buttons_start") == 2)
    await stage.hide()
    await until(lambda: observer.count("buttons_stop") == 2)
    observer.emit({"event": "button", "key": "left"})
    assert control.presses == 0 and control.queue.empty()


async def test_watch_restarts_when_the_observer_process_closes(remote, monkeypatch):
    service, observer, control = remote
    control.delay = 0.01
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await until(lambda: control.state == "active")
    observer.reply = None
    observer.emit({"event": "closed"})
    assert control.status()["state"] == "starting"
    await until(lambda: observer.count("buttons_start") == 2)
    observer.emit({"event": "buttons", "state": "active"})
    assert control.status()["state"] == "active"
    # The observer reports its own stream retries.
    observer.emit({"event": "buttons", "state": "unavailable", "reason": "ConnectionResetError"})
    assert control.status()["reason"] == "ConnectionResetError"
    assert observer.count("buttons_start") == 2


async def test_start_failure_retries_and_does_not_break_reads(remote):
    service, observer, control = remote
    control.delay = 0.01
    observer.failure = Fault("pairing_required", "Remote Pairing is required.", "Pair the observer.")
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await until(lambda: observer.count("buttons_start") >= 2)
    assert control.status() == {"mode": "keys", "state": "unavailable", "reason": "screen_not_paired", "presses": 0, "last": None}
    result = await service.tools.call("tv_look", {})
    assert result["ok"] and result["screen"]["presenting"]["remote"]["state"] == "unavailable"
    observer.failure = None
    await until(lambda: control.state == "active")


# --- tv_show ---------------------------------------------------------------


def present_as(service, mode):
    async def present(*, remote="off", **_):
        await service.stage.show("page", "/c/page/index.html", **({"remote": remote} if remote != "off" else {}))
        return {"page_id": "page", "mode": mode, "verified": True, "evidence": []}

    service.operations.present = present


async def test_show_reports_remote_input(remote):
    service, observer, control = remote
    present_as(service, "mirror")
    call = service.tools.call
    result = await call("tv_show", {"html": "<h1>x</h1>", "remote": "keys"})
    assert result["ok"] and result["data"]["remote"] == {"mode": "keys", "state": "active"}
    observer.emit({"event": "button", "key": "select", "received": time.time()})
    screen = (await call("tv_look", {}))["screen"]
    live = screen["presenting"]["remote"]
    assert live["presses"] == 1 and live["last"]["key"] == "select" and live["last"]["at"]

    # The option is off unless asked. Results keep their earlier shape.
    result = await call("tv_show", {"html": "<h1>x</h1>"})
    assert "remote" not in result["data"] and "remote" not in result["screen"]["presenting"]
    await until(lambda: observer.count("buttons_stop") == 1)

    result = await call("tv_show", {"html": "<h1>x</h1>", "remote": "focus"})
    assert result["data"]["remote"] == {"mode": "focus", "state": "active"}
    assert (await call("tv_show", {}))["screen"]["presenting"] is None
    await until(lambda: observer.count("buttons_stop") == 2)


@pytest.mark.parametrize("case,reason", [("unpaired", "screen_not_paired"), ("crash", "observer_unavailable"),
                                        ("stream", "service_missing"), ("video", "not_mirrored")])
async def test_show_succeeds_when_remote_input_cannot_start(remote, case, reason):
    service, observer, control = remote
    present_as(service, "play_url" if case == "video" else "mirror")
    if case == "video":
        service.operations.presenter = SimpleNamespace(mode="play_url", stop=AsyncMock())
    if case == "unpaired":
        observer.failure = Fault("pairing_required", "Remote Pairing is required.", "Pair the observer.")
    elif case == "crash":
        observer.failure = Fault("observer_unavailable", "Observer did not respond.", "Check.")
    elif case == "stream":
        observer.reply = {"event": "buttons", "state": "unavailable", "reason": reason}
    result = await service.tools.call("tv_show", {"html": "<h1>x</h1>", "remote": "keys"})
    assert result["ok"] and result["verified"]
    assert result["data"]["remote"] == {"mode": "keys", "state": "unavailable", "reason": reason}
    assert result["screen"]["presenting"]["remote"]["reason"] == reason
    if case == "video":
        assert observer.calls.count("buttons_start") == 0


async def test_show_does_not_wait_long_for_a_slow_watch(remote, monkeypatch):
    service, observer, control = remote
    present_as(service, "mirror")
    observer.reply = None
    settle = control.settle
    monkeypatch.setattr(control, "settle", lambda: settle(0.05))
    result = await service.tools.call("tv_show", {"html": "<h1>x</h1>", "remote": "keys"})
    assert result["ok"] and result["data"]["remote"] == {"mode": "keys", "state": "starting"}


async def test_video_only_bridge_never_starts_the_watch(remote):
    service, observer, control = remote
    service.operations.presenter = SimpleNamespace(mode="play_url")
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await asyncio.sleep(0.02)
    assert observer.calls == [] and control.status()["reason"] == "not_mirrored"


@pytest.mark.parametrize("case,reason", [("recording", "not_mirrored"), ("failed", "mirror_failed")])
async def test_watch_stops_when_the_mirror_falls_back_or_fails(remote, caplog, case, reason):
    """After the show, auto mode can fall back to a recording, or the mirror can fail for good."""
    service, observer, control = remote
    mirror = SimpleNamespace(mode="mirror", started=True, status=lambda: {"ok": True})
    presenter = service.operations.presenter = SimpleNamespace(mode="auto", active=mirror, stop=AsyncMock())
    control.interaction.binding = AsyncMock(side_effect=AssertionError("no dispatch without a mirror"))
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await until(lambda: control.state == "active")
    assert control.wanted()
    if case == "recording":
        presenter.active = SimpleNamespace(mode="play_url")
    else:
        mirror.status = lambda: {"ok": False, "error": {"code": "sender_failed"}}
    # Senders announce every status change.
    service.operations.sender.events.emit({"ok": True})
    await until(lambda: observer.count("buttons_stop") == 1)
    assert control.status() == {"mode": "keys", "state": "unavailable", "reason": reason, "presses": 0, "last": None}
    observer.emit({"event": "button", "key": "left"})
    await asyncio.sleep(0.02)
    assert control.presses == 0 and control.queue.empty() and "not delivered" not in caplog.text
    # The mirror comes back.
    presenter.active = SimpleNamespace(mode="mirror", started=True)
    service.operations.sender.events.emit({"ok": True})
    await until(lambda: observer.count("buttons_start") == 2 and control.status()["state"] == "active")


def test_show_schema_accepts_the_remote_option():
    valid = Draft202012Validator(SCHEMAS["tv_show"]).is_valid
    assert SCHEMAS["tv_show"]["properties"]["remote"]["default"] == "off"
    assert all(valid({"html": "x", "remote": mode}) for mode in ("off", "keys", "focus"))
    assert not valid({"html": "x", "remote": "mouse"}) and not valid({"remote": "keys"})
    assert Draft202012Validator(ROUTED_SCHEMAS["tv_show"]).is_valid({"tv": "Bedroom", "html": "x", "remote": "focus"})


# --- Dispatch --------------------------------------------------------------


def fake_frame(delivered=True, document="doc"):
    """A content frame whose press log counts each key the page got."""
    page = SimpleNamespace(evaluate=AsyncMock(), keyboard=SimpleNamespace(press=AsyncMock()))

    async def evaluate(script):
        if script == PRESS_LOG:
            return [frame.document, page.keyboard.press.await_count if delivered else 0]

    frame = SimpleNamespace(page=page, evaluate=AsyncMock(side_effect=evaluate), document=document,
                            url="http://127.0.0.1:8766/c/a/index.html")
    return frame


@pytest.mark.parametrize("mode,direct", [("keys", False), ("focus", False), ("keys", True), ("focus", True)])
async def test_key_goes_to_the_current_content_frame(remote, mode, direct):
    service, observer, control = remote
    frame = fake_frame()
    control.interaction.binding = AsyncMock(return_value=(frame, frame.url, direct))
    control.mode = mode
    for key, expected in remote_input.KEYS.items():
        assert await control.send(key)
        assert frame.page.keyboard.press.await_args.args == (expected,)
    assert set(remote_input.KEYS.values()) == {"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter"}
    # Keyboard focus is restored for every key: the stage iframe, then the helper.
    assert [c.args for c in frame.page.evaluate.await_args_list] == ([] if direct else [(FOCUS_STAGE,)] * 5)
    # A script runs in the content frame just before each key. Without it an idle frame drops the key.
    per_key = ([(FOCUS_HELPER,)] if mode == "focus" else []) + [(PRESS_LOG,), (PRESS_LOG,)]
    assert [c.args for c in frame.evaluate.await_args_list] == per_key * 5


async def test_a_key_the_page_did_not_get_is_reported(remote, caplog):
    service, observer, control = remote
    control.mode = "keys"
    lost = fake_frame(delivered=False)
    control.interaction.binding = AsyncMock(return_value=(lost, lost.url, False))
    assert not await control.send("left")
    # A key that starts a navigation gives a new document. That is not a lost key.
    lost.page.keyboard.press.side_effect = lambda key: setattr(lost, "document", "next")
    assert await control.send("select")
    lost.page.keyboard.press.side_effect = None
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await until(lambda: control.state == "active")
    observer.emit({"event": "button", "key": "left"})
    await until(lambda: control.missed == 1)
    assert "did not get it" in caplog.text


async def test_forwarding_needs_no_tool_lock_and_survives_a_lost_key(remote):
    service, observer, control = remote
    frame = fake_frame()
    failure = Fault("stale_view", "The mirror no longer has the verified presentation.", "Show again.")
    control.interaction.binding = AsyncMock(side_effect=[failure, (frame, frame.url, False), (frame, frame.url, False)])
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await until(lambda: control.state == "active")
    # tv_remote holds the tool lock for seconds.
    async with service.tools.lock:
        for key in ("left", "right", "select"):
            observer.emit({"event": "button", "key": key, "received": time.time(), "device_time": time.time()})
        await until(lambda: frame.page.keyboard.press.await_count == 2)
    assert [c.args[0] for c in frame.page.keyboard.press.await_args_list] == ["ArrowRight", "Enter"]
    assert control.presses == 3 and control.status()["last"]["key"] == "select"
    observer.emit({"event": "button", "key": "home"})
    assert control.presses == 3


EVENT_SCRIPT = """import json, sys, threading, time
def press():
    time.sleep(0.3)
    print(json.dumps({"event": "button", "key": "left"}), flush=True)
for line in sys.stdin:
    if json.loads(line)["method"] == "buttons_start":
        print(json.dumps({"event": "buttons", "state": "active"}), flush=True)
        threading.Thread(target=press, daemon=True).start()
    print(json.dumps({"ok": True}), flush=True)
"""


@pytest.mark.parametrize("wrapped", [False, True])
async def test_forwarding_does_not_wait_for_the_observer_request_lock(service, tmp_path, wrapped):
    """A screen read holds the observer lock for seconds. The event pipe must stay independent."""
    from atv_bridge_app.observer import ReceiverObserver

    client = ObserverClient("192.0.2.1", State(tmp_path), [sys.executable, "-c", EVENT_SCRIPT])
    observer = client
    if wrapped:
        # The production wrapper has its own lock on top of the client's.
        service.state.receiver = lambda kind: {"path": "x"}
        observer = ReceiverObserver(service.engine.adapter.resolver, service.state)
        observer.transport = client
    service.operations.observer = observer
    service.operations.presenter = SimpleNamespace(mode="mirror", started=True)
    control = service.tools.remote_input
    frame = fake_frame()
    control.interaction.binding = AsyncMock(return_value=(frame, frame.url, False))
    try:
        await service.stage.show("a", "/c/a/index.html", remote="keys")
        await until(lambda: control.state == "active")
        locks = [client.lock, service.tools.lock] + ([observer.lock] if wrapped else [])
        for lock in locks:
            await lock.acquire()
        try:
            await until(lambda: frame.page.keyboard.press.await_count == 1)
            assert all(lock.locked() for lock in locks)
        finally:
            for lock in locks:
                lock.release()
    finally:
        await control.close()
        await client.close()


async def test_queued_keys_expire(remote, monkeypatch):
    service, observer, control = remote
    frame = fake_frame()
    release = asyncio.Event()

    async def binding():
        await release.wait()
        return frame, frame.url, False

    control.interaction.binding = binding
    monkeypatch.setattr(remote_input, "STALE_S", 0.05)
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await until(lambda: control.state == "active")
    for _ in range(remote_input.QUEUE_LIMIT + 4):
        observer.emit({"event": "button", "key": "down"})
    assert control.queue.qsize() <= remote_input.QUEUE_LIMIT
    await asyncio.sleep(0.15)
    release.set()
    observer.emit({"event": "button", "key": "up"})
    await until(lambda: frame.page.keyboard.press.await_count == 1)
    await asyncio.sleep(0.02)
    assert [c.args[0] for c in frame.page.keyboard.press.await_args_list] == ["ArrowUp"]


# --- Real browser ----------------------------------------------------------

CONTENT = """<style>button,a,div[tabindex]{position:absolute;width:200px;height:80px}</style>
<button id=a style='left:0;top:0' onclick='hit(this)'>A</button>
<button id=b style='left:400px;top:0' onclick='hit(this)'>B</button>
<a id=c href='/next' style='left:0;top:300px' onclick='hit(this); return false'>C</a>
<div id=d tabindex=0 style='left:400px;top:300px' onclick='hit(this)'>D</div>
<div id=own tabindex=0 style='left:800px;top:0'>Own</div>
<button id=wide style='left:0;top:600px;width:1000px' onclick='hit(this)'>Wide</button>
<script>
window.clicks = {}; window.keys = [];
function hit(el) { clicks[el.id] = (clicks[el.id] || 0) + 1; }
addEventListener('keydown', e => keys.push([e.key, e.isTrusted]), true);
// This widget handles the arrows itself.
own.addEventListener('keydown', e => { if (e.key.startsWith('Arrow')) e.preventDefault(); });
</script>"""


@pytest.fixture
async def browser_frame():
    from playwright.async_api import async_playwright

    async with async_playwright() as driver:
        browser = None
        for options in ({}, {"channel": "chrome"}):  # Bundled Chromium, then an installed Chrome.
            try:
                browser = await driver.chromium.launch(**options)
                break
            except Exception:  # noqa: BLE001 - The browser is optional on a development host.
                continue
        if browser is None:
            pytest.skip("Chromium is unavailable")
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        # The stage shows bundles in a sandboxed iframe without allow-same-origin.
        await page.set_content(
            "<style>iframe{position:absolute;inset:0;width:100%;height:100%;border:0}</style>"
            "<iframe class=visible sandbox='allow-scripts allow-forms' srcdoc=\"" + CONTENT.replace('"', "&quot;") + '"></iframe>'
        )
        frame = page.frames[1]
        await frame.wait_for_selector("#a")
        yield frame
        await browser.close()


async def test_keys_reach_the_sandboxed_frame_as_trusted_events(remote, browser_frame):
    service, observer, control = remote
    frame = browser_frame
    control.interaction.binding = AsyncMock(return_value=(frame, frame.url, False))
    control.mode = "keys"
    for key in ("left", "right", "up", "down", "select"):
        await control.send(key)
    assert await frame.evaluate("keys") == [[k, True] for k in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter")]
    assert await frame.evaluate("document.hasFocus()") and await frame.evaluate("clicks") == {}
    # The page keeps a small press log that tv_page_run can read.
    log = await frame.evaluate("window.__tvRemote")
    assert (log["count"], log["last"]) == (5, "Enter")


async def test_focus_helper_moves_by_geometry_and_enter_clicks_once(remote, browser_frame):
    service, observer, control = remote
    frame = browser_frame
    control.interaction.binding = AsyncMock(return_value=(frame, frame.url, False))
    control.mode = "focus"

    async def press(key):
        await control.send(key)
        return await frame.evaluate("document.activeElement.id")

    assert await press("right") == "a"  # The first arrow picks the first item on screen.
    assert await press("right") == "b"
    assert await press("down") == "d"
    assert await press("left") == "c"
    assert await press("up") == "a"
    assert await press("left") == "a"  # Nothing that way.
    assert await press("select") == "a"
    assert await press("down") == "c"
    await press("select")
    await press("right")
    await press("select")
    # One click per Select: native for the button and link, from the helper for the div.
    assert await frame.evaluate("clicks") == {"a": 1, "c": 1, "d": 1}
    assert await frame.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid"
    # A wider item in the next row is not "right", but it is "down" from either column.
    assert await press("down") == "wide"
    assert await press("right") == "wide"
    assert await press("up") == "c"
    assert await press("right") == "d"
    assert await press("down") == "wide"
    assert await press("up") == "c"
    assert await press("up") == "a"
    assert await press("right") == "b"
    # The helper leaves a page that handles arrows alone.
    assert await press("right") == "own"
    assert await press("left") == "own"
    assert await frame.evaluate("window.__tvRemoteFocus") is True
