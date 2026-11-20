"""Experimental Companion commands on the connection returned by pyatv.connect."""

from contextlib import asynccontextmanager
from typing import Any, Mapping, Optional

from pyatv import exceptions
from pyatv.const import Protocol
from pyatv.core.protocol import DispatchFunc
from pyatv.core.relayer import Relayer
from pyatv.interface import AppleTV
from pyatv.protocols.companion import (
    CompanionFeatures,
    CompanionRemoteControl,
    MediaControlFlags,
)
from pyatv.protocols.companion.api import CompanionAPI, HidCommand, MediaControlCommand


class CompanionExtras:
    """Use the authenticated Companion session of an existing device."""

    def __init__(self, atv: AppleTV):
        """Bind extras to the lifetime of this device."""
        self._atv = atv

    @property
    def _api(self) -> CompanionAPI:
        relay = self._atv.remote_control
        remote = relay.get(Protocol.Companion) if isinstance(relay, Relayer) else None
        if not isinstance(remote, CompanionRemoteControl):
            raise exceptions.NotSupportedError(
                "Authenticated Companion session required"
            )
        api = remote.api
        if not api._connection or not api._connection.connected:
            raise exceptions.ConnectionLostError("Companion connection closed")
        return api

    async def fetch_current_now_playing_info(self) -> Mapping[str, Any]:
        """Return the raw now-playing reply."""
        return await self._api._send_command("FetchCurrentNowPlayingInfoEvent", {})

    async def fetch_supported_actions(self) -> Mapping[str, Any]:
        """Return the raw supported-actions reply."""
        return await self._api._send_command("FetchSupportedActionsEvent", {})

    async def fetch_up_next_info(
        self, pagination_token: Optional[str] = None
    ) -> Mapping[str, Any]:
        """Return a page of Up Next items."""
        content = {"PaginationTokenKey": pagination_token} if pagination_token else {}
        return await self._api._send_command("FetchUpNextInfoEvent", content)

    async def fetch_current_top_shelf_items(self) -> Mapping[str, Any]:
        """Return the raw Top Shelf reply."""
        return await self._api._send_command(
            "FetchCurrentTopShelfItemsEvent", {}, low_priority=True
        )

    async def toggle_captions(self) -> None:
        """Toggle captions once."""
        await self._api._send_command("ToggleCaptions", {})

    async def _begin_scan(
        self, flag: MediaControlFlags, command: MediaControlCommand
    ) -> None:
        api = self._api
        features = self._atv.features.get(Protocol.Companion)
        if (
            not isinstance(features, CompanionFeatures)
            or not features._control_flags & flag
        ):
            raise exceptions.NotSupportedError(
                f"Companion did not advertise {flag.name}"
            )
        await api.mediacontrol_command(command)

    async def fast_forward_begin(self) -> None:
        """Start fast-forward when advertised by the active player."""
        await self._begin_scan(
            MediaControlFlags.FastForward, MediaControlCommand.FastForwardBegin
        )

    async def fast_forward_end(self) -> None:
        """Release fast-forward, even after the active player's capabilities change."""
        await self._api.mediacontrol_command(MediaControlCommand.FastForwardEnd)

    async def rewind_begin(self) -> None:
        """Start rewind when advertised by the active player."""
        await self._begin_scan(
            MediaControlFlags.Rewind, MediaControlCommand.RewindBegin
        )

    async def rewind_end(self) -> None:
        """Release rewind."""
        await self._api.mediacontrol_command(MediaControlCommand.RewindEnd)

    async def siri_press(self) -> None:
        """Send Siri HID down and up, without opening a microphone stream."""
        api = self._api
        await api.hid_command(True, HidCommand.Siri)
        await api.hid_command(False, HidCommand.Siri)

    @asynccontextmanager
    async def subscribe(self, event: str, callback: DispatchFunc[Mapping[str, Any]]):
        """Receive raw event content while inside this asynchronous context."""
        if not isinstance(event, str) or not event or not callable(callback):
            raise ValueError("Expected an event name and callback")
        api = self._api
        remove = api.listen_to(event, callback)
        try:
            await api.subscribe_event(event)
            try:
                yield
            finally:
                if api._connection and api._connection.connected:
                    await api.unsubscribe_event(event)
        finally:
            remove()
