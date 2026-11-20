"""Bridge health summary."""

from homeassistant.components.sensor import SensorEntity
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
        async_add_entities([HealthSensor(coordinator, "health")], config_subentry_id=coordinator.subentry_id)


class HealthSensor(BridgeEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.health.get("state")

    @property
    def extra_state_attributes(self) -> dict:
        health = self.coordinator.data.health
        return {
            key: health[key]
            for key in ("ok", "evidence", "classification", "error", "problems")
            if key in health
        }
