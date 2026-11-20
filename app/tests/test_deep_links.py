from unittest.mock import AsyncMock

import pytest
from atv_bridge_app.state import Fault
from atv_bridge_app.tools.deep_link import confirm_custom_link, link_targets


@pytest.mark.parametrize(
    "labels,landed",
    [
        (["Hulu", "START WATCHING - : Season 1 Episode 1", "Episodes"], "title_page"),
        (["Home", "Trending Now", "Play"], "app_home"),
    ],
)
async def test_target_foreground_despite_other_paused_media(service, labels, landed):
    tools = service.tools
    client = await service.engine.ensure()
    client.app = {"name": "TV", "bundle_id": "com.apple.TVWatchList"}
    client.media = {"title": "Old movie", "state": "paused"}
    service.state.save_pairings(remote_pairing=True)
    items = [{"label": s} for s in labels]
    view = {
        "app": {"name": "Hulu", "bundle_id": "com.hulu.plus"},
        "focused": items[0],
        "items": items,
    }
    tools.read_observation = AsyncMock(return_value=view)
    service.operations.launch = AsyncMock()
    result = await tools.tv_play(
        link="https://tv.apple.com/us/show/only-murders-in-the-building/umc.cmc.7kd9yvyu5ecz1snkt4a35o12g"
    )
    assert result["verified"] and result["data"]["landed"] == landed
    assert result["screen"]["app"]["id"] == "com.hulu.plus"


async def test_refused_link_nonretryable_fix(service):
    service.operations.launch = AsyncMock(
        side_effect=Fault("command_rejected", "Refused", "Check command.")
    )
    result = await service.tools.call("tv_play", {"link": "hulu://series/id"})
    assert (
        result["error"]["code"] == "launch_refused" and not result["error"]["retryable"]
    )
    assert (
        "try another link form or tv_open + tv_navigate"
        in result["error"]["fix"].lower()
    )


@pytest.mark.parametrize("focus,press", [("Open", True), ("Cancel", False)])
async def test_only_confirm_named_system_dialog_with_open_focused(
    service, focus, press
):
    tools = service.tools
    service.state.save_pairings(remote_pairing=True)
    tools.read_observation = AsyncMock(
        return_value={
            "app": {"bundle_id": "com.apple.TVSystemUIService"},
            "items": [{"label": s} for s in ("Open in “Hulu”?", "Open", "Cancel")],
            "focused": {"label": focus},
        }
    )
    tools.press = AsyncMock()
    await confirm_custom_link(tools, link_targets("hulu://watch/id", {}))
    if press:
        tools.press.assert_awaited_once_with("select", switching_app=True)
        assert "Select" in tools.action_evidence[-1]
    else:
        tools.press.assert_not_awaited()


async def test_wrong_app_cannot_verify_from_stale_or_background_playback(service):
    tools = service.tools
    client = await service.engine.ensure()
    service.state.save_pairings(remote_pairing=True)
    tools.read_observation = AsyncMock(
        return_value={
            "app": {"name": "Home", "bundle_id": "com.apple.HeadBoard"},
            "focused": {"label": "Music"},
            "items": [],
        }
    )

    async def open_link(*args):
        client.media = {"title": "Background song", "state": "playing"}
        client.app = {"name": "Music", "bundle_id": "com.apple.TVMusic"}

    service.operations.launch = open_link
    result = await tools.call(
        "tv_play", {"link": "https://music.apple.com/us/album/abc/1234"}
    )
    assert not result["ok"] and result["error"]["code"] == "uncertain"


def test_netflix_featured_play_button_is_home():
    from atv_bridge_app.tools.deep_link import title_action

    assert (
        title_action(
            {
                "focused": {"label": "Play"},
                "items": [
                    {"label": name} for name in ("Play", "More Info", "Search", "Home")
                ],
            }
        )
        is None
    )


async def test_confirmation_uses_one_screenshot_and_one_select(service, monkeypatch):
    import base64
    from atv_bridge_app.tools import deep_link

    service.state.save_pairings(remote_pairing=True)
    tools = service.tools
    tools.read_observation = AsyncMock(
        return_value={"app": None, "focused": None, "items": []}
    )
    tools.ops.observer.call = AsyncMock(
        return_value={"png": base64.b64encode(b"image").decode()}
    )
    readings = []

    def recognize(png, names):
        readings.append((png, names))
        return True

    monkeypatch.setattr(deep_link, "confirmed_open_image", recognize)
    tools.press = AsyncMock()
    await confirm_custom_link(tools, {"com.netflix.Netflix": "Netflix"})
    assert readings == [(b"image", {"Netflix"})]
    tools.ops.observer.call.assert_awaited_once_with("screenshot", max_width=1280)
    tools.press.assert_awaited_once_with("select", switching_app=True)
    assert len(tools.action_evidence) == 2
