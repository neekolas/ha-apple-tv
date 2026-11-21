"""Remote presses: log matching, the observer watch, event demux, lifecycle and key dispatch."""

import asyncio
import json
import sys
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from atv_bridge_app.present import remote_input
from atv_bridge_app.present.remote_input import FOCUS_HELPER, FOCUS_STAGE
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
    fresh = buttons.Freshness(now=1000.0)
    # Right after a connect nothing counts as a press.
    assert fresh.stale(device=950.0, now=1000.2)
    # The TV clock is 50 s behind in this example. That alone is not stale.
    assert not fresh.stale(device=951.5, now=1001.6)
    # A line that took 3 s longer than the baseline arrives too late.
    assert fresh.stale(device=952.0, now=1005.1)
    # A line from before the newest one seen is a replay.
    assert not fresh.stale(device=960.0, now=1010.1)
    assert fresh.stale(device=957.0, now=1010.1)


# --- Observer watch --------------------------------------------------------


def entry(message, device=None, process="backboardd", category="Button"):
    return SimpleNamespace(
        timestamp=datetime.fromtimestamp(time.time() if device is None else device),
        filename="/usr/libexec/" + process,
        label=SimpleNamespace(category=category, subsystem="com.apple.BackBoard") if category else None,
        message=message,
    )


class FakeTrace:
    """Stand-in for OsTraceService. Each instance is one connection."""

    RSD_SERVICE_NAME = buttons.OsTraceService.RSD_SERVICE_NAME
    pids = {"68": {"ProcessName": "backboardd"}, "70": {"ProcessName": "rapportd"}}
    streams = []
    pid_error = None

    def __init__(self, lockdown):
        self.rsd = lockdown

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
        while True:
            item = await lines.get()
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.fixture
def watch(monkeypatch, tmp_path):
    FakeTrace.streams, FakeTrace.pid_error = [], None
    FakeTrace.pids = {"68": {"ProcessName": "backboardd"}, "70": {"ProcessName": "rapportd"}}
    monkeypatch.setattr(buttons, "OsTraceService", FakeTrace)
    monkeypatch.setattr(buttons, "CONNECT_GRACE_S", 0)
    monkeypatch.setattr(buttons, "RETRY_MIN_S", 0.01)
    record = tmp_path / "remote.plist"
    record.touch()
    rsd = SimpleNamespace(peer_info={"Services": {FakeTrace.RSD_SERVICE_NAME: {}}})
    tunnel = SimpleNamespace(rsd=rsd, aopen=AsyncMock(return_value=rsd))
    observer = SimpleNamespace(
        tunnel=tunnel, record=record, busy=asyncio.Lock(), close_channels=AsyncMock(), pairing=False,
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
        assert events[1]["reason"] == "ConnectionResetError"
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


@pytest.mark.parametrize("case,reason", [("unpaired", "screen_not_paired"), ("pairing", "pairing_in_progress"),
                                        ("no_service", "service_missing"), ("no_process", "process_not_found")])
async def test_watch_reports_why_it_cannot_run(watch, case, reason):
    watch, observer, events = watch
    if case == "unpaired":
        observer.record.unlink()
    elif case == "pairing":
        observer.pairing = True
    elif case == "no_service":
        observer.tunnel.aopen.return_value.peer_info["Services"].clear()
    else:
        FakeTrace.pids = {}
    watch.start()
    try:
        await until(lambda: events)
        assert events == [{"event": "buttons", "state": "unavailable", "reason": reason}]
        if case in ("unpaired", "pairing"):
            observer.tunnel.aopen.assert_not_awaited()
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
        await until(lambda: observer.count("buttons_stop") == observer.count("buttons_start"))


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
    service.operations.sender = SimpleNamespace(mode="play_url")
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await asyncio.sleep(0.02)
    assert observer.calls == [] and control.status()["reason"] == "not_mirrored"


def test_show_schema_accepts_the_remote_option():
    valid = Draft202012Validator(SCHEMAS["tv_show"]).is_valid
    assert SCHEMAS["tv_show"]["properties"]["remote"]["default"] == "off"
    assert all(valid({"html": "x", "remote": mode}) for mode in ("off", "keys", "focus"))
    assert not valid({"html": "x", "remote": "mouse"}) and not valid({"remote": "keys"})
    assert Draft202012Validator(ROUTED_SCHEMAS["tv_show"]).is_valid({"tv": "Bedroom", "html": "x", "remote": "focus"})


# --- Dispatch --------------------------------------------------------------


def fake_frame():
    page = SimpleNamespace(evaluate=AsyncMock(), keyboard=SimpleNamespace(press=AsyncMock()))
    return SimpleNamespace(page=page, evaluate=AsyncMock(), url="http://127.0.0.1:8766/c/a/index.html")


@pytest.mark.parametrize("mode,direct", [("keys", False), ("focus", False), ("keys", True), ("focus", True)])
async def test_key_goes_to_the_current_content_frame(remote, mode, direct):
    service, observer, control = remote
    frame = fake_frame()
    control.interaction.binding = AsyncMock(return_value=(frame, frame.url, direct))
    control.mode = mode
    for key, expected in remote_input.KEYS.items():
        await control.send(key)
        assert frame.page.keyboard.press.await_args.args == (expected,)
    assert set(remote_input.KEYS.values()) == {"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter"}
    # Keyboard focus is restored for every key: the stage iframe, then the helper.
    assert [c.args for c in frame.page.evaluate.await_args_list] == ([] if direct else [(FOCUS_STAGE,)] * 5)
    assert [c.args for c in frame.evaluate.await_args_list] == ([(FOCUS_HELPER,)] * 5 if mode == "focus" else [])


async def test_forwarding_needs_no_tool_lock_and_survives_a_lost_key(remote):
    service, observer, control = remote
    frame = fake_frame()
    failure = Fault("stale_view", "The mirror no longer has the verified presentation.", "Show again.")
    control.interaction.binding = AsyncMock(side_effect=[failure, (frame, frame.url, False), (frame, frame.url, False)])
    await service.stage.show("a", "/c/a/index.html", remote="keys")
    await until(lambda: control.state == "active")
    # tv_remote holds the tool lock and a screen read holds the observer lock.
    async with service.tools.lock, service.observer.lock:
        for key in ("left", "right", "select"):
            observer.emit({"event": "button", "key": key, "received": time.time(), "device_time": time.time()})
        await until(lambda: frame.page.keyboard.press.await_count == 2)
    assert [c.args[0] for c in frame.page.keyboard.press.await_args_list] == ["ArrowRight", "Enter"]
    assert control.presses == 3 and control.status()["last"]["key"] == "select"
    observer.emit({"event": "button", "key": "home"})
    assert control.presses == 3


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
    # The helper leaves a page that handles arrows alone.
    assert await press("up") == "b"
    assert await press("right") == "own"
    assert await press("left") == "own"
    assert await frame.evaluate("window.__tvRemoteFocus") is True
