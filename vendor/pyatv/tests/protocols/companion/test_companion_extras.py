"""Public extras over the encrypted fake-device connection."""

import asyncio

import pytest

from pyatv import exceptions
from pyatv.const import Protocol
from pyatv.protocols.companion import MediaControlFlags
from pyatv.protocols.companion.connection import FrameType
from pyatv.protocols.companion.extras import CompanionExtras

from tests.utils import until

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "method, args, name, content, low_priority",
    [
        (
            "fetch_current_now_playing_info",
            (),
            "FetchCurrentNowPlayingInfoEvent",
            {},
            False,
        ),
        ("fetch_supported_actions", (), "FetchSupportedActionsEvent", {}, False),
        (
            "fetch_up_next_info",
            ("page2",),
            "FetchUpNextInfoEvent",
            {"PaginationTokenKey": "page2"},
            False,
        ),
        (
            "fetch_current_top_shelf_items",
            (),
            "FetchCurrentTopShelfItemsEvent",
            {},
            True,
        ),
        ("toggle_captions", (), "ToggleCaptions", {}, False),
    ],
)
async def test_wire_requests(
    companion_client,
    companion_state,
    monkeypatch,
    method,
    args,
    name,
    content,
    low_priority,
):
    peer = companion_state.clients[0]
    seen = []

    def respond(message):
        seen.append(message)
        peer.send_response(message, {"result": 1})

    monkeypatch.setattr(peer, "handle_" + name.lower(), respond, raising=False)
    extras = CompanionExtras(companion_client)
    reply = await getattr(extras, method)(*args)
    assert len(seen) == 1
    assert (seen[0]["_i"], seen[0]["_t"], seen[0]["_c"]) == (name, 2, content)
    assert seen[0].get("_btHP") is (False if low_priority else None)
    if reply is not None:
        assert reply["_c"] == {"result": 1}


async def test_underlying_error_only(companion_client, companion_state, monkeypatch):
    peer = companion_state.clients[0]

    def reject(message):
        peer.send_to_client(
            FrameType.E_OPACK, {"_x": message["_x"], "_t": 3, "_eu": {"_ec": 7}}
        )

    monkeypatch.setattr(peer, "handle_togglecaptions", reject, raising=False)
    with pytest.raises(exceptions.ProtocolError, match="underlying error"):
        await CompanionExtras(companion_client).toggle_captions()


async def test_media_flags_and_siri(companion_client, companion_state):
    extras = CompanionExtras(companion_client)
    with pytest.raises(exceptions.NotSupportedError):
        await extras.rewind_begin()
    flags = MediaControlFlags.FastForward | MediaControlFlags.Rewind
    companion_state.clients[0].send_event("_iMC", 123, {"_mcF": int(flags)})
    await until(
        lambda: companion_client.features.get(Protocol.Companion)._control_flags
        == flags
    )
    for name in (
        "fast_forward_begin",
        "fast_forward_end",
        "rewind_begin",
        "rewind_end",
    ):
        await getattr(extras, name)()
        assert companion_state.latest_button == name
    await extras.siri_press()
    assert companion_state.latest_button == "siri"


async def test_subscription_lifetime(companion_client, companion_state):
    extras = CompanionExtras(companion_client)
    one, two = [], []
    peer = companion_state.clients[0]
    async with extras.subscribe("CustomEvent", one.append):
        async with extras.subscribe("CustomEvent", two.append):
            await until(lambda: "CustomEvent" in companion_state.interests)
            peer.send_event("CustomEvent", 12, {"n": 1})
            await until(lambda: one == two == [{"n": 1}])
        peer.send_event("CustomEvent", 13, {"n": 2})
        await until(lambda: len(one) == 2)
        assert two == [{"n": 1}]
        assert "CustomEvent" in companion_state.interests
    await until(lambda: "CustomEvent" not in companion_state.interests)
    async with extras.subscribe("_iMC", one.append):
        pass
    assert "_iMC" in companion_state.interests


async def test_closed_session_guard(companion_client):
    extras = CompanionExtras(companion_client)
    await asyncio.gather(*companion_client.close())
    with pytest.raises(exceptions.BlockedStateError):
        await extras.toggle_captions()
