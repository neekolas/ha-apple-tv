"""Timing negotiation for URL control sessions."""

import plistlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyatv.protocols.raop.protocols import StreamContext
from pyatv.protocols.raop.protocols.airplayv2 import AirPlayV2
from pyatv.support.http import HttpResponse


@pytest.mark.asyncio
@pytest.mark.parametrize("ptp", [True, False])
async def test_url_setup_timing(ptp):
    rtsp = MagicMock()
    rtsp.connection.local_ip = "127.0.0.1"
    rtsp.setup = AsyncMock(
        return_value=HttpResponse(
            "RTSP", "1.0", 200, "OK", {}, plistlib.dumps({"eventPort": 1})
        )
    )
    stream = AirPlayV2(StreamContext(), rtsp, url_ptp=ptp)
    module = "pyatv.protocols.raop.protocols.airplayv2"
    with (
        patch(module + ".verify_connection", new_callable=AsyncMock),
        patch(
            module + ".setup_channel",
            new=AsyncMock(return_value=(MagicMock(), MagicMock())),
        ),
    ):
        await stream._setup_base(9876)
    body = rtsp.setup.await_args.kwargs["body"]
    if ptp:
        assert body["timingProtocol"] == "PTP"
        assert body["timingPeerList"] == [body["timingPeerInfo"]]
        assert body["timingPeerInfo"]["Addresses"] == ["127.0.0.1"]
        assert "timingPort" not in body
    else:
        assert (body["timingProtocol"], body["timingPort"]) == ("NTP", 9876)
    stream.teardown()


@pytest.mark.asyncio
async def test_control_socket_close_ends_playback():
    rtsp = MagicMock()
    rtsp.connection.transport = None
    stream = AirPlayV2(StreamContext(), rtsp)
    stream._handle_event({"type": "playbackState", "name": "playing"})
    assert await stream.wait_for_playback_state_change("playing", 1) == "stopped"
