"""Apple TV remote control."""

import asyncio

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_HOLD_SECS,
    ATTR_NUM_REPEATS,
    RemoteEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import BridgeError
from . import receivers
from .coordinator import BridgeEntry
from .entity import BridgeEntity, PowerCommands


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BridgeEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for coordinator in receivers(entry):
        async_add_entities([BridgeRemote(coordinator, "remote")], config_subentry_id=coordinator.subentry_id)


class BridgeRemote(PowerCommands, BridgeEntity, RemoteEntity):
    @property
    def is_on(self) -> bool | None:
        power = self.status.get("power")
        return power != "off" if power in ("off", "on", "screensaver") else None

    async def async_send_command(self, command, **kwargs) -> None:
        keys = [command] if isinstance(command, str) else list(command)
        repeats = kwargs.get(ATTR_NUM_REPEATS, 1)
        if not keys or repeats < 1:
            raise BridgeError("Supply at least one key and one repetition")
        args = {"keys": keys}
        if ATTR_HOLD_SECS in kwargs:
            args["hold_ms"] = round(kwargs[ATTR_HOLD_SECS] * 1000)
        for index in range(repeats):
            if index and kwargs.get(ATTR_DELAY_SECS):
                await asyncio.sleep(kwargs[ATTR_DELAY_SECS])
            await self.coordinator.client.tool("tv_remote", **args)
        await self.coordinator.async_request_refresh()
