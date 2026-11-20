"""Resolve one Apple TV from its authenticated identity and Bonjour advertisements."""

import asyncio
import logging
import re

import pyatv
from pyatv.const import DeviceModel, Protocol

from ..state import Fault

_LOG = logging.getLogger(__name__)


def unreachable():
    return Fault(
        "tv_unreachable",
        "Could not reach the selected Apple TV.",
        "Wake the TV and check that it shares the bridge's network. Check apple_tv_host "
        "if set, or select the TV again in Home Assistant.",
        True,
    )


def describe(config):
    return {
        "name": config.name,
        "model": config.device_info.raw_model or config.device_info.model_str,
        "identifiers": sorted(set(config.all_identifiers)),
        "address": str(config.address),
    }


def matches(identifiers, config):
    return not set(map(str.casefold, identifiers)).isdisjoint(
        map(str.casefold, config.all_identifiers)
    )


class Resolver:
    def __init__(self, host, state, identifier=""):
        self.configured_host = host
        self.configured_id = identifier
        self.state = state
        self.source = "configured" if host else "last_known" if self.target else None
        self.address = host or self.target.get("last_known_host")
        self.change = None

    @property
    def target(self):
        return self.state.pairings.get("tv", {})

    async def scan(self, host=None):
        try:
            loop = asyncio.get_running_loop()
            if host:
                timeout = 5 if host == self.configured_host else 2
                configs = await pyatv.scan(loop=loop, hosts=[host], timeout=timeout)
            else:
                configs = await pyatv.scan(loop=loop)
        except OSError:
            configs = []
        return [
            config
            for config in configs
            if config.all_identifiers
            and config.get_service(Protocol.Companion)
            and (
                (
                    config.device_info.model != DeviceModel.Unknown
                    and config.device_info.model_str.startswith("Apple TV ")
                )
                or re.fullmatch(r"AppleTV[0-9]+,[0-9]+", config.device_info.raw_model or "")
            )
            and (not host or str(config.address) == host)
        ]

    async def resolve(self, selected=None, failed_host=None):
        target = self.target
        identifiers = target.get("identifiers", [])
        explicit = selected or self.configured_id
        if explicit and explicit.casefold() not in {i.casefold() for i in identifiers}:
            target, identifiers = {}, [explicit]
        directed = {}
        if self.configured_host:
            directed[self.configured_host] = "configured"
        if last := target.get("last_known_host"):
            directed.setdefault(last, "last_known")
        for host, source in directed.items():
            if host == failed_host:
                continue
            for config in await self.scan(host):
                if matches(identifiers, config) or (source == "configured" and not identifiers):
                    return config, source

        configs = await self.scan()
        found = [c for c in configs if matches(identifiers, c)]
        if not found and not identifiers and self.configured_host:
            found = [c for c in configs if str(c.address) == self.configured_host]
        if not found and target.get("name"):
            found = [c for c in configs if c.name == target["name"]]
            if len(found) == 1:
                _LOG.warning(
                    "Apple TV identifiers changed. Using exact name %r at %s.",
                    found[0].name,
                    found[0].address,
                )
        if len(found) == 1:
            return found[0], "discovered"
        # A sleeping receiver may only answer directed queries, including on retry.
        if failed_host in directed:
            for config in await self.scan(failed_host):
                if matches(identifiers, config) or (
                    not identifiers and failed_host == self.configured_host
                ):
                    return config, directed[failed_host]
        raise unreachable()

    async def host(self, failed_host=None):
        config, _ = await self.resolve(failed_host=failed_host)
        return str(config.address)

    async def airplay_endpoint(self, failed_host=None):
        config, _ = await self.resolve(failed_host=failed_host)
        service = config.get_service(Protocol.AirPlay)
        if not service or not 0 < service.port <= 65535:
            raise Fault("tv_unreachable", "Receiver did not advertise an AirPlay port.",
                        "Check that AirPlay is enabled on this receiver.", True)
        return str(config.address), service.port

    def remember(self, config, source, *, selected=None, **pairings):
        """Commit the receiver identity only after PIN exchange or authenticated connect."""
        tv = describe(config)
        host = tv.pop("address")
        previous = self.target
        explicit = selected or self.configured_id
        known_ids = previous.get("identifiers", [])
        same = (
            not explicit
            or explicit.casefold() in {i.casefold() for i in known_ids}
            or matches(known_ids, config)
        )
        if same:
            tv["identifiers"] = sorted(set(tv["identifiers"] + known_ids))
        elif self.state.receiver("remote_pairing"):
            pairings["remote_pairing"] = None
        tv["apple_tv_id"] = (same and previous.get("apple_tv_id")) or explicit or config.identifier
        tv["last_known_host"] = host
        if service := config.get_service(Protocol.AirPlay):
            tv["airplay_port"] = service.port
        old = previous.get("last_known_host") if same else None
        if pairings or previous != tv:
            self.state.save_pairings(tv=tv, **pairings)
        self.address, self.source = host, source
        if old and old != host:
            _LOG.info("Apple TV %r moved from %s to %s.", tv["name"], old, host)
            self.change = {
                "code": "address_changed",
                "level": "info",
                "previous": old,
                "address": host,
            }
        elif not same:
            self.change = None
