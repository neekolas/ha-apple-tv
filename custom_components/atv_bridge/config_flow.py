"""Discover the app and pair its controller with the Apple TV."""

from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigSubentryFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import BridgeClient, BridgeError
from .const import DEFAULT_PORT, DOMAIN, NAME

CONNECTION_SCHEMA = probatio.Schema(
    {
        probatio.Required("host", default="127.0.0.1"): str,
        probatio.Required("port", default=DEFAULT_PORT): cv.port,
        probatio.Required("token"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)
PIN_SCHEMA = probatio.Schema({probatio.Required("pin"): str})
OPTIONAL_PROTOCOLS = ("companion", "remote_pairing")
PROTOCOL_NAMES = {
    "airplay": "AirPlay",
    "companion": "Companion",
    "remote_pairing": "screenshot",
}


class PairingSteps:
    """Share one PIN exchange across setup, reauth, and options."""

    client: BridgeClient
    protocol: str
    pairing_id: str
    remaining: list[str]
    replacement_protocol = None

    async def _begin(self, config: Mapping[str, Any]) -> ConfigFlowResult:
        self.client = BridgeClient(async_get_clientsession(self.hass), config)
        self.protocol = "airplay"
        self.selected = None
        tvs = await self.client.inventory()
        if tvs is not None:
            paired = [tv for tv in tvs if tv["paired"]]
            if (
                paired
                and isinstance(self, BridgeConfigFlow)
                and self.source != SOURCE_REAUTH
            ):
                return self._finish()
            if len(tvs) == 1:
                self.client = self.client.for_tv(tvs[0]["id"])
            else:
                self.multi_select = True
                return await self.async_step_select_tv()
        health = await self.client.request("/api/health", check=False)
        # Older app versions and installations with an explicit address go straight to PIN.
        if tvs is None and health.get("discover_for_pairing"):
            return await self.async_step_select_tv()
        return await self._start_pin()

    async def async_step_select_tv(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        try:
            if user_input and user_input.get("apple_tv_id"):
                self.selected = user_input["apple_tv_id"]
                if getattr(self, "multi_select", False):
                    return await self._choose_tv(self.selected)
                return await self._start_pin()
            result = await self.client.request("/api/discover")
            options = []
            if getattr(self, "multi_select", False):
                tvs = await self.client.inventory()
                known = {i.casefold() for tv in tvs for i in tv["identifiers"]}
                addresses = {tv["host"] for tv in tvs}
                options = [
                    SelectOptionDict(value=tv["id"], label=tv["name"])
                    for tv in tvs
                    if not (tv["paired"] and tv.get("screen_paired"))
                    or not isinstance(self, AddTVFlow)
                ]
                for tv in result["devices"]:
                    if tv["address"] not in addresses and known.isdisjoint(
                        i.casefold() for i in tv["identifiers"]
                    ):
                        options.append(
                            SelectOptionDict(
                                value=tv["identifiers"][0],
                                label=f"{tv['name']} ({tv['address']})",
                            )
                        )
                return self.async_show_form(
                    step_id="select_tv",
                    errors={} if options else {"base": "no_tvs"},
                    data_schema=probatio.Schema(
                        {
                            probatio.Required("apple_tv_id"): SelectSelector(
                                SelectSelectorConfig(options=options)
                            )
                        }
                        if options
                        else {}
                    ),
                )
            paired = result.get("paired_tv") or {}
            if paired and len(result["devices"]) <= 1:
                return await self._start_pin()
            known_ids = {i.casefold() for i in paired.get("identifiers", [])}
            default = None
            for device in result["devices"]:
                identifier = device["identifiers"][0]
                if not known_ids.isdisjoint(
                    i.casefold() for i in device["identifiers"]
                ):
                    default = identifier
                label = f"{device['name']} ({device['model']}, {device['address']})"
                options.append(SelectOptionDict(value=identifier, label=label))
            if paired and default is None:
                default = paired["apple_tv_id"]
                options.append(
                    SelectOptionDict(value=default, label=f"{paired['name']} (paired)")
                )
            if not options:
                errors["base"] = "no_tvs"
            if len(options) == 1:
                default = options[0]["value"]
            field = (
                probatio.Required("apple_tv_id", default=default)
                if default
                else probatio.Required("apple_tv_id")
            )
            schema = (
                {field: SelectSelector(SelectSelectorConfig(options=options))}
                if options
                else {}
            )
        except BridgeError:
            errors["base"] = "cannot_connect"
            schema = {}
        return self.async_show_form(
            step_id="select_tv", data_schema=probatio.Schema(schema), errors=errors
        )

    async def _choose_tv(self, value):
        tvs = await self.client.inventory()
        tv = next((tv for tv in tvs if tv["id"] == value), None)
        if tv is None:
            tv = (await self.client.request("/api/tvs", {"id": value}))["tv"]
        self.tv = tv
        self.selected = None
        self.client = self.client.for_tv(tv["id"])
        if isinstance(self, AddTVFlow) and tv["paired"]:
            if tv.get("screen_paired"):
                return self.async_abort(reason="already_configured")
            return await self.async_step_check_pairing()
        return await self._start_pin()

    async def _start_pin(self) -> ConfigFlowResult:
        body = {"protocol": self.protocol}
        if self.replacement_protocol == self.protocol:
            body["replace"] = True
        if self.protocol == "airplay" and self.selected:
            body["apple_tv_id"] = self.selected
        result = await self.client.request("/api/pair/start", body)
        if not (pairing_id := result.get("pairing_id")):
            raise BridgeError("Bridge did not return a pairing ID")
        self.pairing_id = pairing_id
        return self._pin_form()

    def _pin_form(self) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="pin",
            data_schema=PIN_SCHEMA,
            description_placeholders={"protocol": PROTOCOL_NAMES[self.protocol]},
        )

    async def async_step_pin(self, user_input=None) -> ConfigFlowResult:
        if user_input is None:
            return self._pin_form()
        try:
            await self.client.request(
                "/api/pair/finish",
                {"pairing_id": self.pairing_id, "pin": user_input["pin"]},
            )
        except BridgeError:
            return self.async_show_form(
                step_id="retry_pin", errors={"base": "pairing_failed"}
            )
        if self.protocol == "airplay":
            return await self.async_step_check_pairing()
        return self._next_protocol()

    async def async_step_retry_pin(self, user_input=None) -> ConfigFlowResult:
        try:
            return await self._start_pin()
        except BridgeError:
            return self.async_show_form(
                step_id="retry_pin", errors={"base": "cannot_connect"}
            )

    async def async_step_check_pairing(self, user_input=None) -> ConfigFlowResult:
        try:
            health = await self.client.request("/api/health", check=False)
        except BridgeError:
            return self.async_show_form(
                step_id="check_pairing", errors={"base": "cannot_connect"}
            )
        needed = health.get("pairing_needed", [])
        pairings = health.get("pairings", {})
        self.remaining = [
            p
            for p in OPTIONAL_PROTOCOLS
            if p in needed or (p in pairings and pairings[p] in (None, False))
        ]
        return self._next_protocol()

    def _next_protocol(self) -> ConfigFlowResult:
        if not self.remaining:
            return self._finish()
        self.protocol = self.remaining.pop(0)
        return self._optional_form()

    def _optional_form(self, errors=None) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="optional",
            data_schema=probatio.Schema(
                {probatio.Required("pair", default=True): bool}
            ),
            description_placeholders={"protocol": PROTOCOL_NAMES[self.protocol]},
            errors=errors or {},
        )

    async def async_step_optional(self, user_input=None) -> ConfigFlowResult:
        if user_input is None:
            return self._optional_form()
        if not user_input["pair"]:
            return self._next_protocol()
        try:
            return await self._start_pin()
        except BridgeError:
            return self._optional_form({"base": "cannot_connect"})


class BridgeConfigFlow(PairingSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1
    config: dict

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {"apple_tv": AddTVFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BridgeOptionsFlow()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            self.config = user_input
            try:
                return await self._begin(self.config)
            except (BridgeError, ValueError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user", data_schema=CONNECTION_SCHEMA, errors=errors
        )

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        try:
            self.config = CONNECTION_SCHEMA(
                {k: discovery_info.config[k] for k in ("host", "port", "token")}
            )
        except (KeyError, probatio.Invalid):
            return self.async_abort(reason="invalid_discovery")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(updates=self.config)
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input=None) -> ConfigFlowResult:
        return await self._confirm("hassio_confirm", user_input)

    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        self.config = dict(self._get_reauth_entry().data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        return await self._confirm("reauth_confirm", user_input)

    async def _confirm(self, step_id, user_input) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                return await self._begin(self.config)
            except (BridgeError, ValueError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id=step_id, errors=errors)

    def _finish(self) -> ConfigFlowResult:
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry())
        return self.async_create_entry(title=NAME, data=self.config)


class BridgeOptionsFlow(PairingSteps, OptionsFlow):
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        self.client = BridgeClient(
            async_get_clientsession(self.hass), self.config_entry.data
        )
        try:
            tvs = await self.client.inventory()
        except BridgeError:
            return self.async_show_form(
                step_id="init", errors={"base": "cannot_connect"}
            )
        if user_input is not None:
            try:
                self.replacement_protocol = user_input.get("protocol", "airplay")
                if self.replacement_protocol == "notifications":
                    return await self.async_step_notifications()
                if tvs:
                    selected = user_input.get("tv") or (
                        tvs[0]["id"] if len(tvs) == 1 else None
                    )
                    if selected not in {tv["id"] for tv in tvs}:
                        raise BridgeError("Choose a TV")
                    self.selected = None
                    self.protocol, self.remaining = self.replacement_protocol, []
                    self.client = self.client.for_tv(selected)
                    return await self._start_pin()
                if self.replacement_protocol == "remote_pairing":
                    self.client = BridgeClient(
                        async_get_clientsession(self.hass), self.config_entry.data
                    )
                    self.protocol, self.remaining = "remote_pairing", []
                    return await self._start_pin()
                return await self._begin(self.config_entry.data)
            except BridgeError:
                errors["base"] = "cannot_connect"
        choices = SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value="notifications", label="Pairing notifications"),
                    SelectOptionDict(value="airplay", label="Re-pair control"),
                    SelectOptionDict(value="remote_pairing", label="Re-pair screen"),
                ]
            )
        )
        fields = {probatio.Required("protocol", default="airplay"): choices}
        if tvs:
            selector = (
                probatio.Required("tv", default=tvs[0]["id"])
                if len(tvs) == 1
                else probatio.Optional("tv")
            )
            fields[selector] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=t["id"], label=t["name"]) for t in tvs
                    ]
                )
            )
        return self.async_show_form(
            step_id="init", errors=errors, data_schema=probatio.Schema(fields)
        )

    async def async_step_notifications(self, user_input=None):
        targets = ["notify." + name for name in self.hass.services.async_services().get("notify", {})
                   if name.startswith("mobile_app_")]
        if user_input is not None and user_input.get("notify_target") in ["", *targets]:
            return self.async_create_entry(title="", data=dict(self.config_entry.options, **user_input))
        options = [SelectOptionDict(value="", label="Off"),
                   *(SelectOptionDict(value=t, label=t) for t in sorted(targets))]
        current = self.config_entry.options.get("notify_target", "")
        return self.async_show_form(step_id="notifications", data_schema=probatio.Schema({
            probatio.Required("notify_target", default=current if current in targets else ""):
                SelectSelector(SelectSelectorConfig(options=options))
        }))

    def _finish(self) -> ConfigFlowResult:
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_create_entry(title="", data=dict(self.config_entry.options))


class AddTVFlow(PairingSteps, ConfigSubentryFlow):
    """Discover a receiver and bind all PIN exchanges to its own runtime."""

    async def async_step_user(self, user_input=None):
        self.client = BridgeClient(
            async_get_clientsession(self.hass), self._get_entry().data
        )
        self.multi_select = True
        self.protocol = "airplay"
        return await self.async_step_select_tv(user_input)

    def _optional_form(self, errors=None):
        return self.async_show_form(
            step_id="prepare_pin",
            data_schema=probatio.Schema(
                {probatio.Optional("skip_screen", default=False): bool}
                if self.protocol == "remote_pairing" else {}
            ),
            description_placeholders={
                "protocol": PROTOCOL_NAMES[self.protocol],
                "tv": self.tv["name"],
            },
            errors=errors or {},
        )

    async def async_step_prepare_pin(self, user_input=None):
        if user_input is None:
            return self._optional_form()
        if self.protocol == "remote_pairing" and user_input.get("skip_screen"):
            return self._next_protocol()
        try:
            return await self._start_pin()
        except BridgeError:
            return self._optional_form({"base": "cannot_connect"})

    def _finish(self):
        entry = self._get_entry()
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        if any(s.unique_id == self.tv["id"] for s in entry.subentries.values()):
            return self.async_abort(reason="pairing_completed")
        return self.async_create_entry(
            title=self.tv["name"], unique_id=self.tv["id"], data={"tv": self.tv["id"]}
        )
