"""Bridge connectivity and TV keyboard focus."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
        async_add_entities([
            Connected(coordinator, "connected"),
            BridgeBinarySensor(coordinator, "keyboard_focused"),
        ], config_subentry_id=coordinator.subentry_id)


class BridgeBinarySensor(BridgeEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        return self.status.get(self.translation_key)


class Connected(BridgeBinarySensor):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
