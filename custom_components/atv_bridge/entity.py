"""Shared bridge entity identity and command dispatch."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import BridgeCoordinator


class BridgeEntity(CoordinatorEntity[BridgeCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BridgeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.entity_prefix
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name=coordinator.tv["name"] if coordinator.tv else NAME)

    @property
    def status(self) -> dict:
        return self.coordinator.data.status

    async def command(self, name: str, **arguments) -> None:
        await self.coordinator.client.tool(name, **arguments)
        await self.coordinator.async_request_refresh()


class PowerCommands:
    async def async_turn_on(self, **kwargs) -> None:
        await self.command("tv_power", on=True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.command("tv_power", on=False)
