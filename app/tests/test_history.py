"""Watch history tests. Every title, channel and ID here is made up."""

import asyncio
import json
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from jsonschema import Draft202012Validator
from pyatv.protocols.mrp import protobuf as pb

from atv_bridge_app.atv import raw_metadata
from atv_bridge_app.fleet import Fleet
from atv_bridge_app.history import HistoryStore, Recorder, derive, reading
from atv_bridge_app.server import TVRuntime
from atv_bridge_app.tools.contract import ROUTED_SCHEMAS
from conftest import FakeAdapter

HULU = {"name": "Hulu", "bundle_id": "com.hulu.plus"}
YOUTUBE = {"name": "YouTube", "bundle_id": "com.google.ios.youtube"}
NETFLIX = {"name": "Netflix", "bundle_id": "com.netflix.Netflix"}
MUSIC = {"name": "Music", "bundle_id": "com.apple.TVMusic"}
START = 1_800_000_000
VIDEO_ID = "AAAAAAAAAAA"


class Clock:
    """Wall time and monotonic time that tests move by hand."""

    def __init__(self):
        self.wall = START
        self.mono = 5000.0

    def attach(self, recorder):
        recorder.clock, recorder.ticks = (lambda: self.wall), (lambda: self.mono)
        return self

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


@pytest.fixture
async def watch(service):
    """Drive the recorder by hand: move the clocks, set the fake TV's state, publish and sample."""
    recorder = service.history
    clock = Clock().attach(recorder)
    tv = await service.engine.ensure()

    async def step(seconds, app=None, **media):
        clock.advance(seconds)
        tv.app, tv.media = app, media or None
        # The engine publishes each new snapshot before the recorder samples.
        recorder.notice({"connected": True, **await tv.status()})
        await recorder.sample()
        return recorder.entries()

    step.clock = clock
    return service, step


def episode(position, state="playing", number=2):
    return {
        "title": f"Example Show | S1 E{number} - Sample Episode",
        "state": state,
        "position_s": position,
        "duration_s": 1290,
    }


def lines(service):
    return (service.state.root / "history.jsonl").read_text().splitlines()


async def test_samples_of_one_item_extend_one_entry_and_pauses_add_no_time(watch):
    service, step = watch
    await step(0, HULU, **episode(100))
    await step(15, HULU, **episode(115))
    rows = await step(300, HULU, **episode(415))
    assert len(rows) == 1 and rows[0]["watched_s"] == 315
    # Ten minutes paused. A scrub during the pause moves the position by 500 s.
    await step(5, HULU, **episode(420, "paused"))
    await step(600, HULU, **episode(920, "paused"))
    await step(2, HULU, **episode(920))
    rows = await step(30, HULU, **episode(950))
    assert len(rows) == 1
    entry = rows[0]
    assert entry["watched_s"] == 350
    assert entry["start"] == START and entry["end"] == START + 952
    assert entry["app"] == "Hulu" and entry["app_id"] == "com.hulu.plus"
    assert (entry["series"], entry["season"], entry["episode"]) == ("Example Show", 1, 2)
    assert entry["episode_title"] == "Sample Episode" and entry["position_s"] == 950
    # The file holds the same entry after a reload.
    await service.history.stop()
    store = HistoryStore(service.state.root / "history.jsonl", 2000)
    store.load()
    assert store.entries == {entry["id"]: entry}


async def test_a_pause_near_the_end_then_another_item_adds_no_time(watch):
    _, step = watch
    await step(0, HULU, **episode(1200))
    await step(20, HULU, **episode(1220))
    await step(5, HULU, **episode(1225, "paused"))
    other = episode(0, number=3)
    rows = await step(200, HULU, **other)
    # The 65 s left in the paused episode were not played.
    assert [row["watched_s"] for row in rows] == [25]
    rows = await step(30, HULU, **episode(30, number=3))
    assert [(row["episode"], row["watched_s"]) for row in rows] == [(2, 25), (3, 30)]


async def test_a_clock_step_changes_timestamps_but_not_watched_time(watch):
    _, step = watch
    await step(0, HULU, **episode(0))
    await step(60, HULU, **episode(60))
    # NTP sets the wall clock a day forward, then an hour back. Playback goes on.
    step.clock.wall += 86400
    rows = await step(60, HULU, **episode(120))
    assert rows[0]["watched_s"] == 120 and rows[0]["end"] == START + 86400 + 120
    step.clock.wall -= 90000
    rows = await step(60, HULU, **episode(180))
    assert len(rows) == 1 and rows[0]["watched_s"] == 180
    # A stalled bridge counts nothing for the gap.
    rows = await step(3600, HULU, **episode(190))
    assert rows[0]["watched_s"] == 180


async def test_a_stop_without_an_engine_report_counts_nothing(watch):
    service, step = watch
    tv = service.engine.client
    await step(0, HULU, **episode(0))
    await step(100, HULU, **episode(100))
    # The timer finds the pause 300 s later. Nothing shows how long playback went on.
    step.clock.advance(300)
    tv.media = episode(130, "paused")
    await service.history.sample()
    assert service.history.entries()[0]["watched_s"] == 100


async def test_idle_metadata_short_plays_and_shell_apps_are_not_recorded(watch):
    service, step = watch
    # A finished item can stay in now-playing for hours with state idle, paused or stopped.
    stale = {"title": "Sample Song", "artist": "Sample Artist"}
    for state in ("idle", "paused", "stopped"):
        assert await step(300, MUSIC, state=state, **stale) == []
    # Untitled playback in a system app is the bridge or a screen saver, not content.
    airplay = {"name": "AirPlay", "bundle_id": "com.apple.TVAirPlay"}
    assert await step(300, airplay, state="playing", title="") == []
    assert await step(300, airplay, state="playing", title="") == []
    # A five-second skip stays out of the log.
    await step(0, YOUTUBE, state="playing", title="Skipped Video", position_s=0)
    assert await step(5, YOUTUBE, state="paused", title="Skipped Video", position_s=5) == []
    assert not (service.state.root / "history.jsonl").exists()
    # The TV reports playing while it is off.
    service.engine.client.power_state = "off"
    await step(0, HULU, **episode(0))
    assert await step(300, HULU, **episode(300)) == []


async def test_title_arrives_late_and_untitled_apps_keep_an_app_entry(watch):
    _, step = watch
    await step(0, YOUTUBE, state="playing", title="", artist="")
    await step(20, YOUTUBE, state="playing", title="", artist="")
    rows = await step(
        4, YOUTUBE, state="paused", title="Sample Video", artist="Sample Channel", content_identifier=VIDEO_ID
    )
    assert len(rows) == 1
    assert (rows[0]["title"], rows[0]["artist"], rows[0]["watched_s"]) == ("Sample Video", "Sample Channel", 24)
    assert rows[0]["url"] == "https://www.youtube.com/watch?v=" + VIDEO_ID
    # Some apps report the playing state and nothing else.
    await step(60, NETFLIX, state="playing")
    rows = await step(300, NETFLIX, state="playing")
    assert rows[-1]["app"] == "Netflix" and "title" not in rows[-1] and rows[-1]["watched_s"] == 300
    # A stale title from another app must not land on that entry.
    rows = await step(5, YOUTUBE, state="paused", title="Sample Video", artist="Sample Channel")
    assert "title" not in rows[-1] and rows[-1]["watched_s"] == 305
    # An untitled session hours later is a new entry.
    await step(3 * 3600, NETFLIX, state="playing")
    rows = await step(60, NETFLIX, state="playing")
    assert [(row["app"], row["watched_s"]) for row in rows] == [("YouTube", 24), ("Netflix", 305), ("Netflix", 60)]
    # A short break continues the untitled entry.
    await step(5, NETFLIX, state="paused")
    await step(120, None)
    await step(0, NETFLIX, state="playing")
    rows = await step(40, NETFLIX, state="playing")
    # The five seconds before the pause count. The two minutes away do not.
    assert [row["watched_s"] for row in rows] == [24, 305, 105]


async def test_an_untitled_reading_does_not_extend_a_titled_entry(watch):
    service, step = watch
    video = {"title": "Sample Video", "artist": "Sample Channel", "duration_s": 600}
    await step(0, YOUTUBE, state="playing", position_s=0, **video)
    await step(60, YOUTUBE, state="playing", position_s=60, **video)
    # The next video starts without a title. It gets a provisional entry of its own.
    await step(1, YOUTUBE, state="playing", title="")
    rows = await step(30, YOUTUBE, state="playing", title="")
    assert [(row.get("title"), row["watched_s"]) for row in rows] == [("Sample Video", 61), (None, 30)]
    rows = await step(30, YOUTUBE, state="playing", title="Other Video", artist="Sample Channel")
    assert [(row.get("title"), row["watched_s"]) for row in rows] == [("Sample Video", 61), ("Other Video", 60)]
    # When the late title is the item already open, the provisional entry folds into it.
    await step(1, YOUTUBE, state="playing", title="")
    await step(20, YOUTUBE, state="playing", title="")
    rows = await step(20, YOUTUBE, state="playing", title="Other Video", artist="Sample Channel")
    assert [(row.get("title"), row["watched_s"]) for row in rows] == [("Sample Video", 61), ("Other Video", 101)]
    await service.history.stop()
    store = HistoryStore(service.state.root / "history.jsonl", 2000)
    store.load()
    assert list(store.entries.values()) == rows
    # The same queue item ties an untitled reading to its titled entry.
    tv = service.engine.client
    tv.media_details = AsyncMock(return_value={"item_id": "queue-1"})
    await step(600, MUSIC, state="playing", title="Sample Song")
    await step(30, MUSIC, state="playing", title="")
    rows = await step(30, MUSIC, state="playing", title="Sample Song")
    assert [(row.get("title"), row["watched_s"]) for row in rows][2:] == [("Sample Song", 60)]


async def test_a_returning_item_continues_and_a_rewatch_starts_again(watch):
    _, step = watch
    first, second = episode(0, number=1), episode(0, number=2)
    await step(0, HULU, **first)
    await step(600, HULU, **(first | {"position_s": 600}))
    await step(1, HULU, **second)
    await step(60, HULU, **(second | {"position_s": 60}))
    # Back to the first episode within four hours: its entry continues.
    await step(1, HULU, **(first | {"position_s": 600}))
    rows = await step(600, HULU, **(first | {"position_s": 1200}))
    assert [(row["episode"], row["watched_s"]) for row in rows] == [(1, 1201), (2, 61)]
    # It had reached its last tenth. Starting it again from the beginning is a new viewing.
    await step(60, HULU, **(first | {"position_s": 5}))
    rows = await step(100, HULU, **(first | {"position_s": 105}))
    assert [(row["episode"], row["watched_s"]) for row in rows] == [(1, 1261), (2, 61), (1, 100)]
    # After more than four hours the item is a new entry too.
    await step(1, HULU, **(first | {"position_s": 105, "state": "paused"}))
    await step(5 * 3600, HULU, **(first | {"position_s": 105}))
    rows = await step(50, HULU, **(first | {"position_s": 155}))
    assert [row["watched_s"] for row in rows] == [1261, 61, 101, 50]


async def test_frozen_positions_fast_playback_and_queue_items(watch):
    service, step = watch
    tv = service.engine.client
    tv.media_details = AsyncMock(return_value={"item_id": "queue-1", "media_type": "video"})
    # Some apps report playing without advancing the position. Elapsed time still counts.
    await step(0, YOUTUBE, state="playing", title="", position_s=7)
    await step(60, YOUTUBE, state="playing", title="", position_s=7)
    # pyatv reports 1.5x playback as seeking.
    await step(60, YOUTUBE, state="seeking", title="", position_s=7)
    # The queue item stays the same when the title arrives, so the entry is filled, not split.
    rows = await step(2, YOUTUBE, state="paused", title="Sample Video", position_s=7)
    assert [(row["title"], row["watched_s"], row["item_id"], row["kind"]) for row in rows] == [
        ("Sample Video", 122, "queue-1", "video")
    ]
    # Another untitled queue item is another video.
    tv.media_details.return_value = {"item_id": "queue-2"}
    await step(10, YOUTUBE, state="playing", title="")
    rows = await step(30, YOUTUBE, state="playing", title="")
    assert [(row.get("title"), row["watched_s"]) for row in rows] == [("Sample Video", 122), (None, 30)]
    # A failing detail read still records the basic fields.
    tv.media_details.side_effect = RuntimeError("no MRP")
    rows = await step(30, YOUTUBE, state="playing", title="")
    assert rows[-1]["watched_s"] == 60


async def test_bridge_presentations_and_disconnects_add_nothing(watch):
    service, step = watch
    service.stage.page = {"page_id": "p", "title": "Agent page"}
    await step(0, HULU, **episode(0))
    assert await step(300, HULU, **episode(300)) == []
    # The bridge's own page is not listed as playing, and neither is a TV that is off.
    assert (await service.tools.call("tv_history", {}))["data"]["now"] == []
    service.stage.page = None
    assert len((await service.tools.call("tv_history", {}))["data"]["now"]) == 1
    service.engine.client.power_state = "off"
    assert (await service.tools.call("tv_history", {}))["data"]["now"] == []
    service.engine.client.power_state = "on"
    await step(0, HULU, **episode(300))
    rows = await step(60, HULU, **episode(360))
    assert rows[0]["watched_s"] == 60
    # No session: the next sample has no previous reading to count from.
    await service.engine.close_client()
    assert await service.history.sample() is False
    await service.engine.ensure()
    await step(200, HULU, **episode(560))
    assert service.history.entries()[0]["watched_s"] == 60


async def test_restart_resumes_the_entry_and_the_file_stays_small_and_private(service, tmp_path):
    clock = Clock()
    tv = await service.engine.ensure()
    tv.app = HULU

    def recorder(**options):
        made = Recorder(tmp_path, service.engine, service.stage, interval=300, **options)
        clock.attach(made)
        return made

    async def play(made, seconds, position):
        clock.advance(seconds)
        tv.media = episode(position)
        await made.sample()

    first = recorder()
    await play(first, 0, 0)
    await play(first, 100, 100)
    path = tmp_path / "history.jsonl"
    # The file is private from its first write.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    # The open entry is written at most once per interval, not on every sample.
    for position in (110, 120, 130):
        await play(first, 10, position)
    assert path.read_bytes() == before and first.entries()[0]["watched_s"] == 130
    await play(first, 300, 430)
    assert len(path.read_text().splitlines()) == 2
    # A restart reads the file, rewrites nothing and continues the stored entry.
    before = path.read_bytes()
    second = recorder()
    assert not second.store.loaded and path.read_bytes() == before
    await play(second, 100, 530)
    await play(second, 50, 580)
    assert path.read_bytes().startswith(before)
    await second.stop()
    assert [row["watched_s"] for row in second.entries()] == [480]

    # Bad lines are skipped: invalid UTF-8, broken JSON, missing fields and wrong types.
    good = second.entries()[0]
    with path.open("ab") as f:
        f.write(b"\xff\xfe not text\n")
        f.write(b"not json\n")
        f.write(b'{"id": "no-times"}\n')
        f.write(json.dumps(good | {"id": "bad-title", "title": ["a list"]}).encode() + b"\n")
        f.write(json.dumps(good | {"id": "bad-time", "start": "yesterday"}).encode() + b"\n")
        f.write(b'{"id": "nan", "start": NaN, "end": 1, "watched_s": 20}\n')
        f.write(b'[1, 2, 3]\n')
    third = recorder(max_entries=3)
    await third.sample()
    assert third.entries() == [good]

    # The cap drops the oldest entries. The file is compacted when half of it is superseded.
    for number in range(3, 9):
        tv.media = episode(0, number=number)
        await third.sample()
        clock.advance(20)
        tv.media = episode(20, number=number)
        await third.sample()
    await third.stop()
    assert [row["episode"] for row in third.entries()] == [6, 7, 8]
    assert len(path.read_text().splitlines()) < 6
    reloaded = HistoryStore(path, 3)
    reloaded.load()
    assert list(reloaded.entries.values()) == third.entries()


def test_series_fields_from_app_title_formats():
    disney = {"title": "Example Kids Show", "artist": "S1:E2 Sample Episode", "app_id": "com.disney.disneyplus"}
    derive(disney)
    assert (disney["series"], disney["season"], disney["episode"], disney["episode_title"]) == (
        "Example Kids Show", 1, 2, "Sample Episode",
    )
    hbo = {"title": "Sample Episode", "artist": "Example Drama", "app_id": "com.wbd.stream"}
    derive(hbo)
    assert hbo["series"] == "Example Drama" and hbo["episode_title"] == "Sample Episode"
    video = {"title": "Sample Video", "artist": "Sample Channel", "app_id": "com.google.ios.youtube"}
    derive(video)
    assert "series" not in video and "url" not in video
    # Fields the TV reported win over the title format.
    reported = {"title": "A | S1 E2 - B", "series": "Real", "season": 9}
    derive(reported)
    assert (reported["series"], reported["season"], reported["episode"]) == ("Real", 9, 2)


def test_only_allowed_descriptive_fields_are_kept():
    item = pb.ContentItemMetadata(
        title="Sample Song", subtitle="Sub", serviceIdentifier="svc", mediaSubType=4, isAlwaysLive=True,
        releaseDate=float("nan"), directorName="https://example.org/?token=secret",
        # None of these may be stored.
        assetURLString="https://example.org/asset?token=secret", artworkURL="https://example.org/a.jpg",
        artworkFileURL="file:///private/a.jpg", lyricsURL="https://example.org/lyrics",
        artworkIdentifier="https://example.org/{w}x{h}.jpg", profileIdentifier="profile-1",
        iTunesStoreSubscriptionIdentifier=7, collectionIdentifier="playlist-1", userInfoData=b"1234",
        elapsedTime=3.0,
    )
    assert raw_metadata(item) == {
        "subtitle": "Sub",
        "serviceIdentifier": "svc",
        "mediaSubType": "Podcast",
        "isAlwaysLive": True,
    }
    # A content identifier that is a link is dropped, except for the video ID of a YouTube link.
    status = {"app": HULU, "now_playing": {"state": "playing", "content_identifier": "https://example.org/v?token=s"}}
    assert "content_id" not in reading(status, None)
    status = {"app": YOUTUBE, "now_playing": {"content_identifier": f"https://www.youtube.com/watch?v={VIDEO_ID}&t=s"}}
    assert reading(status, None)["content_id"] == VIDEO_ID
    status = {"app": HULU, "now_playing": {"content_identifier": "EP000123"}}
    assert reading(status, None)["content_id"] == "EP000123"


async def test_recorder_wakes_on_a_published_change_and_backs_off(service, monkeypatch):
    recorder = service.history
    recorder.fast, recorder.interval = 15, 300
    tv = await service.engine.ensure()
    waits = []
    real = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        waits.append(timeout)
        return await real(awaitable, 5)

    monkeypatch.setattr("atv_bridge_app.history.asyncio.wait_for", wait_for)
    recorder.start()
    try:
        async def sampled(count):
            for _ in range(200):
                if len(waits) >= count:
                    return
                await asyncio.sleep(0.005)
            raise AssertionError(waits)

        await sampled(1)
        # An unchanged snapshot, as the engine publishes while the position advances, wakes nothing.
        service.publish({"connected": True, **await tv.status()})
        service.publish({"presentation": {"state": "idle"}})
        assert not recorder.wake.is_set()
        tv.app, tv.media = HULU, episode(0)
        service.publish({"connected": True, **await tv.status()})
        assert recorder.wake.is_set()
        await sampled(2)
        for count in range(3, 9):
            recorder.wake.set()
            await sampled(count)
        # The first sample and each change wait the fast interval. Unchanged samples double it.
        assert waits == [15, 15, 30, 60, 120, 240, 300, 300]
    finally:
        await recorder.stop()


async def test_tv_history_answers_while_the_tv_lock_is_held(watch):
    service, step = watch
    await step(0, HULU, **episode(0))
    await step(600, HULU, **episode(600))
    video = {"title": "Sample Video", "artist": "Sample Channel"}
    await step(0, YOUTUBE, state="playing", position_s=0, **video)
    await step(19, YOUTUBE, state="playing", position_s=19, **video)
    service.observer.call = AsyncMock(side_effect=AssertionError("history must not read the screen"))
    await service.tools.lock.acquire()
    try:
        result = await asyncio.wait_for(service.tools.call("tv_history", {}), 1)
    finally:
        service.tools.lock.release()
    assert result["ok"] and result["verified"] and "screen" not in result
    data = result["data"]
    assert data["recording"] is True
    assert [row["title"] for row in data["entries"]] == ["Sample Video", "Example Show | S1 E2 - Sample Episode"]
    assert data["entries"][0]["start"] == "2027-01-15T08:10:00Z"
    assert data["summary"] | {"top_apps": None} == {
        "entries": 2,
        "watched_s": 619,
        "first": "2027-01-15T08:00:00Z",
        "last": "2027-01-15T08:10:19Z",
        "top_apps": None,
        "top_series": [{"series": "Example Show", "watched_s": 600, "entries": 1}],
        "top_artists": [{"artist": "Sample Channel", "watched_s": 19, "entries": 1}],
    }
    assert [row["app"] for row in data["summary"]["top_apps"]] == ["Hulu", "YouTube"]
    assert data["now"] == [
        {"app": "YouTube", "app_id": "com.google.ios.youtube", "title": "Sample Video", "artist": "Sample Channel",
         "position_s": 19, "state": "playing"}
    ]
    call = service.tools.call
    assert [r["app"] for r in (await call("tv_history", {"app": "hulu"}))["data"]["entries"]] == ["Hulu"]
    assert (await call("tv_history", {"app": "com.google.ios.youtube"}))["data"]["summary"]["entries"] == 1
    assert (await call("tv_history", {"search": "sample CHANNEL"}))["data"]["summary"]["watched_s"] == 19
    summary_only = (await call("tv_history", {"limit": 0}))["data"]
    assert summary_only["entries"] == [] and summary_only["summary"]["entries"] == 2
    assert (await call("tv_history", {"until": "2027-01-15T08:05:00Z"}))["data"]["summary"]["entries"] == 1
    assert (await call("tv_history", {"since": "2027-01-15 08:10:10"}))["data"]["summary"]["entries"] == 1
    assert (await call("tv_history", {"since": "2027-02-01"}))["data"]["summary"]["entries"] == 0
    assert (await call("tv_history", {"since": "7d"}))["ok"]
    # Unreadable and huge times are refused, not raised.
    for bad in ("last week", "99999w", "9" * 30 + "d", "1" * 5000, "0001-01-01T00:00:00+23:59"):
        failed = await call("tv_history", {"since": bad})
        assert failed["error"]["code"] == "invalid_arguments" and "screen" not in failed, bad
        assert len(failed["summary"]) < 200
    audit = [json.loads(line) for line in (service.state.root / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["tool"] == "tv_history" and audit[-1]["arguments"] == ["since"]


def rooms(tmp_path, tvs=("Living Room", "Bedroom"), **options):
    def factory(host, state, **options):
        adapter = FakeAdapter(state)
        adapter.host = adapter.resolver.address = host
        return TVRuntime(host, state, adapter, AsyncMock(), **options)

    entries = [
        {"name": name, "host": f"192.0.2.{15 + index}", "id": f"AA:0{index}"} for index, name in enumerate(tvs)
    ]
    return Fleet(tmp_path, {**options, "apple_tvs": entries}, factory=factory, stage_port=0)


async def test_fleet_history_covers_every_tv_and_filters_by_tv(tmp_path):
    fleet = rooms(tmp_path, history_max_entries=5, history_interval=600, history_fast_interval=5)
    living, bedroom = fleet.runtimes.values()
    assert (living.history.store.limit, living.history.interval, living.history.fast) == (5, 600, 5)
    clock = Clock()
    for rt, app, minutes in ((living, HULU, 10), (bedroom, NETFLIX, 3)):
        clock.attach(rt.history)
        tv = await rt.engine.ensure()
        tv.app, tv.media = app, {"state": "playing"}
        await rt.history.sample()
        clock.advance(minutes * 60)
        await rt.history.sample()
        tv.media = None
    # Two paired TVs have no default. History still answers for all of them.
    result = await fleet.call("tv_history", {})
    rows = result["data"]["entries"]
    assert [(row["tv"], row["app"], row["watched_s"]) for row in rows] == [
        ("Bedroom", "Netflix", 180),
        ("Living Room", "Hulu", 600),
    ]
    assert rows[0]["tv_id"] == bedroom.metadata["id"] and "screen" not in result
    assert result["data"]["summary"]["watched_s"] == 780
    for selector in ("bedroom", bedroom.metadata["id"]):
        only = (await fleet.call("tv_history", {"tv": selector}))["data"]["entries"]
        assert [row["tv"] for row in only] == ["Bedroom"]
    assert (await fleet.call("tv_history", {"tv": "Attic"}))["error"]["code"] == "tv_not_found"
    assert (await fleet.call("tv_history", {"limit": 501}))["error"]["code"] == "invalid_arguments"
    Draft202012Validator(ROUTED_SCHEMAS["tv_history"]).validate({"tv": "Bedroom", "since": "7d", "limit": 0})
    # Each TV keeps its own file, and removing the TV removes it.
    path = fleet.controller.root / "tvs" / bedroom.metadata["id"] / "history.jsonl"
    assert path.exists()
    await fleet.remove(bedroom.metadata["id"])
    assert not path.exists()
    await fleet.close()


async def test_history_switched_off_touches_no_file(tmp_path):
    fleet = rooms(tmp_path, tvs=("Living Room",))
    living = next(iter(fleet.runtimes.values()))
    path = living.state.root / "history.jsonl"
    path.write_bytes(b"\xff not a history file\n")
    await fleet.close()
    fleet = rooms(tmp_path, tvs=("Living Room",), watch_history=False)
    living = next(iter(fleet.runtimes.values()))
    assert living.history.store is None
    tv = await living.engine.ensure()
    tv.app, tv.media = HULU, episode(0)
    living.history.start()
    living.publish({"connected": True, **await tv.status()})
    assert living.history.task is None and not living.history.wake.is_set()
    result = await fleet.call("tv_history", {})
    assert result["ok"] and result["data"]["recording"] is False and result["data"]["entries"] == []
    assert result["summary"] == "Watch history recording is off."
    assert path.read_bytes() == b"\xff not a history file\n"
    await fleet.close()


async def test_history_with_no_tv_registered(tmp_path):
    fleet = rooms(tmp_path, tvs=())
    result = await fleet.call("tv_history", {})
    assert result["ok"] and result["summary"] == "No Apple TV is registered."
    assert result["data"]["entries"] == [] and result["data"]["recording"] is False
    await fleet.close()


def test_app_options_declare_the_history_settings():
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    assert {k: config["options"][k] for k in config["options"] if "history" in k} == {
        "watch_history": True,
        "history_interval": 300,
        "history_fast_interval": 15,
        "history_max_entries": 2000,
    }
    assert set(config["options"]) == set(config["schema"])


async def test_media_details_reads_the_fields_pyatv_keeps_from_mrp_pushes():
    from pyatv.const import Protocol
    from pyatv.core.facade import FacadeMetadata
    from pyatv.protocols.mrp import MrpMetadata, messages
    from pyatv.protocols.mrp.player_state import PlayerStateManager

    from atv_bridge_app.atv import Session

    psm = PlayerStateManager(SimpleNamespace(listen_to=lambda *_: None))
    metadata = FacadeMetadata()
    metadata.register(MrpMetadata(None, psm, None, None), Protocol.MRP)
    session = SimpleNamespace(facade=SimpleNamespace(metadata=metadata))
    empty = await Session.media_details(session)
    # Without a queue item there is no item ID. pyatv's own hash would be a digest of the title.
    assert empty["media_type"] == "unknown" and empty["item_id"] is None

    client = messages.create(pb.SET_NOW_PLAYING_CLIENT_MESSAGE)
    pb.extract_inner(client).client.bundleIdentifier = "com.apple.TVWatchList"
    await psm._handle_set_now_playing_client(client)
    state = messages.create(pb.SET_STATE_MESSAGE)
    inner = pb.extract_inner(state)
    inner.playerPath.client.bundleIdentifier = "com.apple.TVWatchList"
    inner.playerPath.player.identifier = "MediaRemote-DefaultPlayer"
    inner.playbackState = pb.PlaybackState.Playing
    item = inner.playbackQueue.contentItems.add()
    item.identifier = "queue-item-1"
    item.metadata.MergeFrom(pb.ContentItemMetadata(
        title="Sample Episode", seriesName="Example Show", seasonNumber=1, episodeNumber=2, genre="Drama",
        mediaType=pb.ContentItemMetadata.Video, iTunesStoreIdentifier=42, contentIdentifier="tv.show.1",
        serviceIdentifier="svc", artworkURL="https://example.org/a.jpg", duration=3400.0,
    ))
    await psm._handle_set_state(state)
    assert await Session.media_details(session) == {
        "media_type": "video",
        "album": None,
        "genre": "Drama",
        "series_name": "Example Show",
        "season_number": 1,
        "episode_number": 2,
        "itunes_store_identifier": 42,
        "item_id": "queue-item-1",
        "raw": {"serviceIdentifier": "svc"},
    }


async def test_running_bridge_records_from_engine_events_and_serves_rest_and_mcp(tmp_path, aiohttp_client):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    fleet = rooms(tmp_path)
    living, bedroom = fleet.runtimes.values()
    client = await aiohttp_client(fleet.app())
    headers = {"Authorization": "Bearer " + fleet.controller.token}
    # The fleet starts each recorder with its engine. The discovery runtime records nothing.
    assert living.history.task and bedroom.history.task and fleet.primary.history.task is None
    clock = Clock().attach(living.history)
    for _ in range(200):
        if living.engine.client:
            break
        await asyncio.sleep(0.01)
    tv = living.engine.client
    tv.app = HULU

    async def recorded(position, state="playing"):
        # The engine monitor reads the TV every two seconds. Wake it instead of waiting.
        tv.media = episode(position, state)
        living.engine.signal.set()
        for _ in range(200):
            if living.history.before and living.history.live and living.history.live["position_s"] == position:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(living.history.live)

    await recorded(0)
    clock.advance(40)
    # A steady position change wakes nothing. The pause does, and playback counts up to it.
    await recorded(40, "paused")
    reply = await client.post(f"/api/tvs/{living.metadata['id']}/tool/tv_history", json={}, headers=headers)
    rows = (await reply.json())["data"]["entries"]
    assert [(row["tv"], row["series"], row["watched_s"]) for row in rows] == [("Living Room", "Example Show", 40)]
    reply = await client.post("/api/tool/tv_history", json={"tv": "Bedroom"}, headers=headers)
    assert (await reply.json())["data"]["entries"] == []
    async with (
        httpx2.AsyncClient(headers=headers) as http,
        streamable_http_client(str(client.make_url("/mcp")), http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("tv_history", {"since": "1h", "limit": 0})
        assert not result.is_error and result.structured_content["data"]["now"][0]["tv"] == "Living Room"
