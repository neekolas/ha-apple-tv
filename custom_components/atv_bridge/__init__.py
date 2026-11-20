"""Home Assistant integration for the Apple TV Agent Bridge."""

import asyncio
from dataclasses import dataclass
from types import MappingProxyType

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BridgeClient, BridgeError
from .const import DOMAIN, ISSUES
from .coordinator import BridgeCoordinator, BridgeEntry
from .http import BundleView, MCPView, ToolView

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.http.register_view(MCPView())
    hass.http.register_view(BundleView())
    hass.http.register_view(ToolView())
    return True


@dataclass
class BridgeFleet:
    client: BridgeClient
    receivers: list[BridgeCoordinator]

    async def watch_inventory(self, hass, entry):
        known = {(c.tv["id"], c.tv["name"], c.tv["paired"]) for c in self.receivers}
        while True:
            await asyncio.sleep(30)
            if hass.config_entries.subentries.async_progress_by_handler(
                (entry.entry_id, "apple_tv")
            ):
                continue
            try:
                current = await self.client.inventory()
            except BridgeError:
                continue
            if (
                current is not None
                and {(t["id"], t["name"], t["paired"]) for t in current} != known
            ):
                hass.config_entries.async_schedule_reload(entry.entry_id)
                return


def receivers(entry):
    data = entry.runtime_data
    return data.receivers if isinstance(data, BridgeFleet) else [data]


async def subentries_changed(hass, entry):
    current = {sub.unique_id for sub in entry.subentries.values()}
    known = set(entry.data.get("receiver_subentries", {}))
    if current != known:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: BridgeEntry) -> None:
    client = BridgeClient(async_get_clientsession(hass), entry.data)
    try:
        tvs = await client.inventory()
    except BridgeError as exc:
        raise ConfigEntryNotReady("Bridge inventory is unavailable") from exc
    if tvs is None:
        entry.runtime_data = BridgeCoordinator(hass, entry, client)
    else:
        # HA removes subentries synchronously and may do so while the app is offline.
        # Keep the last reconciled IDs until the app acknowledges a removal.
        known = entry.data.get("receiver_subentries", {})
        current = {sub.unique_id: sub for sub in entry.subentries.values()}
        inventory_ids = {tv["id"] for tv in tvs}
        removed = known.keys() - current.keys()
        try:
            for key in removed & inventory_ids:
                await client.request(f"/api/tvs/{key}", method="DELETE")
        except BridgeError as exc:
            raise ConfigEntryNotReady("Bridge could not remove the receiver") from exc
        for key in current.keys() - inventory_ids:
            hass.config_entries.async_remove_subentry(entry, current[key].subentry_id)
        for key in removed | (known.keys() - inventory_ids):
            for code in ISSUES:
                ir.async_delete_issue(hass, DOMAIN, f"{known[key]}_{code}")
        tvs = [tv for tv in tvs if tv["id"] not in removed]
        coordinators = []
        for tv in tvs:
            sub = next(
                (s for s in entry.subentries.values() if s.unique_id == tv["id"]), None
            )
            if tv["paired"] and not sub:
                sub = ConfigSubentry(
                    data=MappingProxyType({"tv": tv["id"]}),
                    subentry_type="apple_tv",
                    title=tv["name"],
                    unique_id=tv["id"],
                )
                hass.config_entries.async_add_subentry(entry, sub)
            if sub and sub.title != tv["name"]:
                hass.config_entries.async_update_subentry(entry, sub, title=tv["name"])
            coordinator = BridgeCoordinator(
                hass,
                entry,
                client.for_tv(tv["id"]),
                tv,
                sub.subentry_id if sub else None,
            )
            coordinators.append(coordinator)
            if sub:
                devices = dr.async_get(hass)
                device = devices.async_get_device(
                    identifiers={(DOMAIN, coordinator.entity_prefix)}
                )
                if (
                    device
                    and sub.subentry_id
                    not in device.config_entries_subentries.get(entry.entry_id, set())
                ):
                    devices.async_update_device(
                        device.id,
                        new_config_entry_id=entry.entry_id,
                        new_config_subentry_id=sub.subentry_id,
                    )
                    entities = er.async_get(hass)
                    for entity in er.async_entries_for_device(entities, device.id):
                        if entity.config_entry_id == entry.entry_id:
                            entities.async_update_entity(
                                entity.entity_id, config_subentry_id=sub.subentry_id
                            )
        reconciled = {c.tv["id"]: c.entity_prefix for c in coordinators if c.subentry_id}
        hass.config_entries.async_update_entry(entry, data={**entry.data, "receiver_subentries": reconciled})
        entry.runtime_data = BridgeFleet(client, coordinators)
    for coordinator in receivers(entry):
        await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if isinstance(entry.runtime_data, BridgeFleet):
        entry.async_on_unload(entry.add_update_listener(subentries_changed))
        entry.async_create_background_task(
            hass,
            entry.runtime_data.watch_inventory(hass, entry),
            "atv_bridge inventory",
        )
    for coordinator in receivers(entry):
        entry.async_create_background_task(
            hass, coordinator.listen(), "atv_bridge events"
        )


async def async_unload_entry(hass: HomeAssistant, entry: BridgeEntry) -> None:
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BridgeEntry) -> None:
    for prefix in [
        entry.entry_id,
        *(f"{entry.entry_id}_{s.unique_id}" for s in entry.subentries.values()),
    ]:
        for code in ISSUES:
            ir.async_delete_issue(hass, DOMAIN, f"{prefix}_{code}")
