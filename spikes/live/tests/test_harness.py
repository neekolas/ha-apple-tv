import asyncio
import io
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from PIL import Image
from pyatv.auth.server_auth import PIN_CODE
from pyatv.conf import AppleTV, ManualService
from pyatv.const import Protocol
from pyatv.protocols.companion.api import SystemStatus

from atvlab import ROOT, common, control, observer, playback
from atvlab.cli import parser

sys.path.insert(0, str(ROOT.parents[1] / "vendor/pyatv"))
from tests.fake_device import FakeAppleTV
from tests.fake_device.airplay import DEVICE_PIN


@pytest.fixture
async def receiver(tmp_path, monkeypatch):
    fake = FakeAppleTV(asyncio.get_running_loop(), test_mode=False)
    for protocol in (Protocol.AirPlay, Protocol.Companion, Protocol.RAOP):
        fake.add_service(protocol)
    # The upstream RAOP receiver handles RTP but has no HAP route. Share the AirPlay
    # receiver's real HAP handler, exactly as the physical TV shares its receiver key.
    fake.services[Protocol.RAOP].service.add_route(
        "POST", "/pair-verify", fake.services[Protocol.AirPlay].service.handle_pair_verify
    )
    await fake.start()
    result = tmp_path / "result"
    result.mkdir()
    lab = common.Lab(
        result, tmp_path / "state", "127.0.0.1", fake.get_port(Protocol.AirPlay), tmp_path / "pins/pin"
    )

    async def config(selected=None, failed_host=None):
        cfg = AppleTV(lab.host, "fake")
        for proto in fake.services:
            props = {"features": "0x00000000,0x00010000"} if proto == Protocol.AirPlay else {}
            cfg.add_service(ManualService(proto.name, proto, fake.get_port(proto), props))
        return cfg, "configured"

    monkeypatch.setattr(lab.adapter.resolver, "resolve", config)
    try:
        yield fake, lab
    finally:
        await lab.engine.stop()
        await fake.stop()


async def simulate_pin(lab, protocol, value, monkeypatch):
    real_read = common.read_pin

    async def submit(name, path):
        assert name == protocol and not path.exists()
        path.write_text(str(value))
        return await real_read(name, path)

    with monkeypatch.context() as patch:
        patch.setattr(common, "read_pin", submit)
        result = await common.pair(lab, protocol)
    assert not lab.pin.exists()
    return result


async def test_pair_reuse_control(receiver, monkeypatch):
    fake, lab = receiver
    assert (await simulate_pin(lab, "airplay", DEVICE_PIN, monkeypatch))["ok"]
    names = fake.get_state(Protocol.AirPlay).pairing_client_names
    assert len(names) == 4 and all(v == "Agent Bridge" for _, v in names)
    companion = fake.get_usecase(Protocol.Companion)
    companion.set_installed_apps({"com.google.ios.youtube": "YouTube"})
    result = await control.reuse(lab)
    assert result["ok"], result
    assert fake.get_state(Protocol.RAOP).audio_packets
    assert (await lab.engine.status())["power"] == "on"
    assert (await control.press(lab, ["menu", "select"]))["ok"]
    assert fake.get_state(Protocol.Companion).latest_button == "select"
    assert (await control.launch(lab, "com.google.ios.youtube"))["ok"]
    rejected = await control.launch(lab, "bogus.bundle")
    assert rejected["companion_errors"][0]["_ec"] == 4
    matrix = await control.matrix(lab, parser().parse_args(["s4", "--text", "test keyboard"]))
    assert matrix["ok"], matrix
    assert matrix["trials"][0]["readback"] == "test keyboard"
    await lab.engine.close_client()
    assert (await simulate_pin(lab, "companion", PIN_CODE, monkeypatch))["ok"]


async def test_wake_from_sleep(receiver, monkeypatch):
    fake, lab = receiver
    await simulate_pin(lab, "companion", PIN_CODE, monkeypatch)
    state = fake.get_state(Protocol.Companion)
    usecase = fake.get_usecase(Protocol.Companion)

    async def attention_events():
        previous = state.powered_on
        while True:
            if state.powered_on != previous:
                previous = state.powered_on
                usecase.set_system_status(SystemStatus.Awake if previous else SystemStatus.Asleep)
            await asyncio.sleep(0.01)

    events = asyncio.create_task(attention_events())
    try:
        result = await control.matrix(lab, parser().parse_args(["s4", "--wake-trials", "2"]))
        assert result["ok"], result
    finally:
        events.cancel()
        await asyncio.gather(events, return_exceptions=True)


async def test_pin_timeout(tmp_path):
    with pytest.raises(TimeoutError):
        await common.read_pin("airplay", tmp_path / "missing", timeout=0.05)


async def test_clock_and_range(tmp_path):
    path = tmp_path / "clock.mp4"
    await asyncio.to_thread(playback.clock_movie, path, 3)
    result = await asyncio.to_thread(
        subprocess.run,
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
        capture_output=True,
        check=True,
    )
    video, audio = json.loads(result.stdout)["streams"]
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1920, 1080)
    assert float(video["duration"]) == 3 and audio["codec_name"] == "aac"
    async with (
        playback.serve_file("127.0.0.1", path) as url,
        aiohttp.ClientSession() as client,
        client.get(url, headers={"Range": "bytes=10-50"}) as reply,
    ):
        assert reply.status == 206 and await reply.read() == path.read_bytes()[10:51]


@pytest.mark.parametrize("cutoff,expected", [(290, False), (0.01, True)])
async def test_playback_evidence(cutoff, expected):
    class ProtocolDouble:
        supports_playback_state = True
        playback_state = "idle"
        url_fetcher = AsyncMock()

        def _handle_event(self, event):
            if event["type"] == "playbackState":
                self.playback_state = event["name"]

    class Stream:
        def create_airplay_protocol(self):
            return ProtocolDouble()

        async def play_url(self, url):
            proto = self.create_airplay_protocol()
            proto._handle_event(
                {"type": "unhandledURL", "messageID": 3, "request": {"FCUP_Response_URL": url}}
            )
            proto._handle_event({"type": "playbackState", "name": "playing"})
            await asyncio.sleep(0.05)
            proto._handle_event({"type": "playbackState", "name": "stopped"})

    client = SimpleNamespace(facade=SimpleNamespace(stream={Protocol.AirPlay: Stream()}))
    row = await playback.playback_trial(
        client, "http://127.0.0.1/movie", cutoff, timeout=1, interval=0.01
    )
    assert row["ok"] is expected and row["early_cut"] is not expected
    assert row["fcup_events"][0]["message_id"] == 3 and len(row["samples"]) >= 2


async def test_observer_paths(tmp_path, monkeypatch):
    from atv_bridge_observer import __main__ as driver

    lab = common.Lab(tmp_path / "run", tmp_path / "state", "127.0.0.1", pin=tmp_path / "pins/pin")
    lab.directory.mkdir()
    png = io.BytesIO()
    Image.new("RGB", (80, 40)).save(png, "PNG")
    rsd = SimpleNamespace(peer_info={"Services": {"fake.ax": {}}})
    tunnel = SimpleNamespace(aopen=AsyncMock(return_value=rsd), aclose=AsyncMock())
    monkeypatch.setattr(driver, "UserspaceRsdTunnel", lambda provider_factory: tunnel)

    @asynccontextmanager
    async def dvt(_):
        yield SimpleNamespace(get_screenshot=AsyncMock(return_value=png.getvalue()))

    monkeypatch.setattr(driver, "DvtProvider", dvt)
    monkeypatch.setattr(driver, "Screenshot", dvt)

    class Manual:
        def __init__(self, identifier, host, port):
            assert host == "127.0.0.1"

        async def _request_pair_consent(self):
            from collections import namedtuple

            return namedtuple("Consent", "pin")(driver.tunnel_service.input("PIN"))

        async def connect(self):
            result = await self._request_pair_consent()
            assert result.pin == "012345"
            self.pair_record_path.write_bytes(b"fake")

        async def close(self):
            pass

    monkeypatch.setattr(driver.tunnel_service, "RemotePairingManualPairingService", Manual)
    monkeypatch.setattr(driver, "endpoint", AsyncMock(return_value=("test", 123)))

    async def pin(protocol, path):
        path.write_text("012345")
        return await common.read_pin(protocol, path)

    monkeypatch.setattr(observer, "read_pin", pin)
    assert (await observer.observe(lab, "pair"))["ok"]
    assert lab.state.pairings["remote_pairing"]["path"] == "/data/observer/remote.plist"
    shots = await observer.observe(lab, "screenshot", 2)
    assert len(shots["captures"]) == 2 and shots["ok"]
    assert (await observer.observe(lab, "screen"))["ok"]
    assert (tmp_path / "latest.png").read_bytes() == (lab.directory / "screen-001.png").read_bytes()

    class Audit:
        async def capabilities(self):
            return ["focus"]

        async def iter_elements(self):
            yield SimpleNamespace(to_dict=lambda: {"caption": "Fake search field"})

    @asynccontextmanager
    async def audit(service):
        assert service is rsd
        yield Audit()

    monkeypatch.setattr(observer, "AccessibilityAudit", audit)
    result = await observer.observe(lab, "ax-probe")
    assert result["ok"] and result["list_items"]["items"][0]["caption"] == "Fake search field"


async def test_play_url_on_pyatv_receiver(receiver, monkeypatch):
    fake, lab = receiver
    await simulate_pin(lab, "airplay", DEVICE_PIN, monkeypatch)
    lab.state.save_pairings(companion=lab.state.pairings["airplay"])
    # The upstream AirPlay HTTP receiver implements the AirPlay 1 URL flow.
    config = (await lab.adapter.resolver.resolve())[0]
    config.get_service(Protocol.AirPlay).properties["features"] = "0x1"
    monkeypatch.setattr(lab.adapter.resolver, "resolve", AsyncMock(return_value=(config, "configured")))
    movie = lab.directory / "clock.mp4"
    await asyncio.to_thread(playback.clock_movie, movie, 2)
    usecase = fake.get_usecase(Protocol.AirPlay)
    usecase.airplay_playback_playing()
    usecase.airplay_playback_idle()
    async with playback.serve_file(lab.host, movie) as url, lab.engine.session() as client:
        result = await playback.playback_trial(client, url, timeout=12, interval=0.25)
    assert fake.get_state(Protocol.AirPlay).last_airplay_content == movie.read_bytes()
    assert result["early_cut"] and 0.8 < result["cut_time_s"] < 2, result


async def test_matrix_verifies_launch_after_home(receiver, monkeypatch):
    from pyatv.protocols.companion.api import HidCommand
    from tests.fake_device.companion import FakeCompanionService

    fake, lab = receiver
    _, player = fake.add_service(Protocol.MRP)
    await fake.services[Protocol.MRP].service.start(True)
    await simulate_pin(lab, "companion", PIN_CODE, monkeypatch)
    launch_app = FakeCompanionService.handle__launchapp
    hid = FakeCompanionService.handle__hidc
    home_count = []

    def deep_link(service, msg):
        launch_app(service, msg)
        player.example_video(player="com.google.ios.youtube", app_name="YouTube", title="test")

    def home(service, msg):
        hid(service, msg)
        if msg["_c"]["_hBtS"] == 2 and msg["_c"]["_hidC"] == HidCommand.Home.value:
            home_count.append(1)
            player.nothing_playing()

    monkeypatch.setattr(FakeCompanionService, "handle__launchapp", deep_link)
    monkeypatch.setattr(FakeCompanionService, "handle__hidc", home)
    result = await control.matrix(
        lab, parser().parse_args(["s4", "--launch", "youtube=youtube://watch/test", "--times", "2"])
    )
    assert result["ok"] and len(home_count) == 2, result
    for row in result["trials"]:
        assert row["observed"]["app"]["bundle_id"] == "com.google.ios.youtube"
        assert not (row["before"]["app"] or {}).get("bundle_id") and row["companion_errors"] == []


async def test_observer_provider_uses_static_address(tmp_path, monkeypatch):
    from atv_bridge_observer import __main__ as driver

    monkeypatch.setenv("ATV_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("ATV_CONTROLLER_ID", "unit-controller")
    monkeypatch.setenv("ATV_BRIDGE_DATA", str(tmp_path))
    endpoint = AsyncMock(return_value=(None, 12345))
    monkeypatch.setattr(driver, "endpoint", endpoint)

    class Provider:
        def __init__(self, remote_id, host, port):
            assert (host, port) == ("127.0.0.1", 12345)
            self.connect = AsyncMock()

    monkeypatch.setattr(driver.tunnel_service, "RemotePairingTunnelService", Provider)
    obs = driver.Observer()
    try:
        provider = await obs.tunnel.provider_factory()
        endpoint.assert_awaited_once_with("127.0.0.1", False)
        provider.service.connect.assert_awaited_once_with(autopair=False)
        assert provider.service.identifier == "unit-controller"
        assert provider.service.pair_record_path == tmp_path / "observer/remote.plist"
    finally:
        await obs.close()
