"""Redacted bridge diagnostics."""

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import BridgeFleet
from .coordinator import BridgeEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BridgeEntry
) -> dict:
    data = {"connection": dict(entry.data)}
    if coordinator := getattr(entry, "runtime_data", None):
        if isinstance(coordinator, BridgeFleet):
            data["tvs"] = {
                c.tv["id"]: {"status": c.data.status, "health": c.data.health}
                for c in coordinator.receivers
            }
        else:
            data.update(status=coordinator.data.status, health=coordinator.data.health)
    return async_redact_data(data, {"token", "authorization", "pin", "pairing_id"})
