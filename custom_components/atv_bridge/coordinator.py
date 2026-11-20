"""Refresh bridge state on events and periodically reconcile it."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from aiohttp import ClientError, ClientWSTimeout, WSMsgType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .notifications import PairingNotifications
from .api import BridgeClient, BridgeError
from .const import DOMAIN, ISSUES

_LOGGER = logging.getLogger(__name__)


@dataclass
class Snapshot:
    status: dict
    health: dict
    apps: list[str]


type BridgeEntry = ConfigEntry[BridgeCoordinator]


class BridgeCoordinator(DataUpdateCoordinator[Snapshot]):
    def __init__(
        self, hass: HomeAssistant, entry: BridgeEntry, client: BridgeClient, tv=None, subentry_id=None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
            always_update=False,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=0.5, immediate=True
            ),
        )
        self.tv = tv
        self.subentry_id = subentry_id
        self.entity_prefix = entry.entry_id if not tv or tv["legacy"] else f"{entry.entry_id}_{tv['id']}"
        self.entry = entry
        self.client = client
        self.notifications = PairingNotifications(self)
        entry.async_on_unload(hass.bus.async_listen("mobile_app_notification_action", self.notifications.reply))
        self._apps: list[str] = []
        self._next_app_list = 0.0

    def _issue(self, code: str, active: bool) -> None:
        issue_id = f"{self.entity_prefix}_{code}"
        if active:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                config_entry_id=self.entry.entry_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=code,
                translation_placeholders={"tv": self.tv["name"] if self.tv else "Apple TV"},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _async_update_data(self) -> Snapshot:
        try:
            health = await self.client.request("/api/health", check=False)
        except BridgeError as err:
            self._issue("unreachable", True)
            raise UpdateFailed(str(err)) from err
        codes = {
            health.get("state"),
            (health.get("error") or {}).get("code"),
            health.get("classification"),
        }
        codes.update(problem.get("code") for problem in health.get("problems", []))
        if "tv_unreachable" in codes:
            codes.add("unreachable")
        for issue in ISSUES:
            self._issue(issue, issue in codes)
        try:
            await self.notifications.update(health, codes)
        except Exception:  # Notification delivery must not make the TV entities unavailable.
            _LOGGER.warning("Could not deliver the pairing notification for %s", self.entity_prefix)
        if "pairing_removed" in codes and not self.tv:
            self.entry.async_start_reauth(self.hass)

        try:
            status = await self.client.request("/api/status", check=False)
        except BridgeError as err:
            self._issue("unreachable", True)
            raise UpdateFailed(str(err)) from err

        if status.get("connected") and monotonic() >= self._next_app_list:
            try:
                result = await self.client.request("/api/apps")
            except BridgeError:
                _LOGGER.debug("Bridge could not list installed apps")
                self._next_app_list = monotonic() + 30
            else:
                self._apps = [
                    app["name"] for app in result.get("apps", []) if app.get("name")
                ]
                self._next_app_list = monotonic() + 300
        return Snapshot(status, health, self._apps)

    async def listen(self) -> None:
        """Treat each notification as an invalidation. REST supplies a full snapshot."""
        delay = 1
        while True:
            try:
                async with self.client.session.ws_connect(
                    self.client.base_url.with_path(self.client.path("/api/events")),
                    headers=self.client.headers,
                    timeout=ClientWSTimeout(ws_receive=300, ws_close=3),
                ) as socket:
                    # Reconcile changes missed while disconnected.
                    await self.async_request_refresh()
                    async for message in socket:
                        if message.type == WSMsgType.TEXT:
                            delay = 1
                            await self.async_request_refresh()
            except (ClientError, TimeoutError, OSError):
                _LOGGER.debug("Bridge event stream disconnected")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
