"""Run the app's health checks."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import receivers
from .coordinator import BridgeEntry
from .entity import BridgeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BridgeEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for coordinator in receivers(entry):
        async_add_entities([SelfTestButton(coordinator, "self_test")], config_subentry_id=coordinator.subentry_id)


class SelfTestButton(BridgeEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        await self.command("tv_status")
