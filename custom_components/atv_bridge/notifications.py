"""Correlate a Companion app text reply with one TV's pending PIN."""

import re
from time import monotonic

from .api import BridgeError


class PairingNotifications:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.pending = None
        self.handled = set()
        self.last_sent = None

    @property
    def target(self):
        return self.coordinator.entry.options.get("notify_target", "").removeprefix(
            "notify."
        )

    async def send(self, message, actions=None):
        await self.coordinator.hass.services.async_call(
            "notify",
            self.target,
            {
                "message": message,
                "title": self.coordinator.tv["name"]
                if self.coordinator.tv
                else "Apple TV",
                **({"data": {"actions": actions}} if actions else {}),
            },
            blocking=True,
        )

    async def update(self, health, codes):
        if not self.target.startswith("mobile_app_"):
            return
        prompt = health.get("pending_pairing")
        if self.pending and (
            not prompt or self.pending["pairing_id"] != prompt["pairing_id"]
        ):
            self.pending = None
        invalid = set()
        if "pairing_removed" in codes and health.get("pairings", {}).get("airplay"):
            invalid.add("control")
        if "screen_pairing_invalid" in codes and health.get("pairings", {}).get(
            "remote_pairing"
        ):
            invalid.add("screen")
        # Pairing temporarily suspends health probes. Re-arm only on a healthy check.
        if not prompt and health.get("connected"):
            self.handled.intersection_update(invalid)
        if not prompt and (remaining := invalid - self.handled):
            kind = sorted(remaining)[0]
            result = await self.coordinator.client.request(
                "/api/tool/tv_pair", {"kind": kind, "notify": True}, check=False
            )
            if result["ok"]:
                self.handled.add(kind)
                prompt = result["data"] | {"notify": True}
        # Send the menu directions first. Offer a text reply only after consent
        # was requested on the TV, including when the same session changes state.
        if (
            prompt
            and prompt.get("notify")
            and prompt.get("state") == "waiting_for_pairing_screen"
        ):
            waiting = (prompt["pairing_id"], "waiting")
            if self.last_sent != waiting:
                await self.send(prompt["ask_user"])
                self.last_sent = waiting
            return
        if (
            not prompt
            or not prompt.get("notify")
            or self.pending
            or prompt["pairing_id"] == self.last_sent
        ):
            return
        action = "ATV_BRIDGE_PAIR_" + prompt["pairing_id"]
        self.pending = dict(
            prompt, action=action, deadline=monotonic() + prompt["expires_in_s"]
        )
        try:
            await self.send(
                "Apple TV needs pairing. " + prompt["ask_user"],
                [
                    {
                        "action": action,
                        "title": "Reply with code",
                        "behavior": "textInput",
                        "authenticationRequired": True,
                        "textInputButtonTitle": "Pair",
                    }
                ],
            )
            self.last_sent = prompt["pairing_id"]
        except Exception:
            self.pending = None
            raise

    async def reply(self, event):
        pending = self.pending
        if (
            not pending
            or event.data.get("action") != pending["action"]
            or not self.target
        ):
            return
        if monotonic() >= pending["deadline"]:
            self.pending = None
            await self.send("The pairing code expired. Start tv_pair again.")
            return
        code = str(event.data.get("reply_text", "")).strip()
        if not re.fullmatch(rf"[0-9]{{{pending['code_length']}}}", code):
            await self.send(
                f"Enter the {pending['code_length']} digits shown on the TV.",
                [
                    {
                        "action": pending["action"],
                        "title": "Reply with code",
                        "behavior": "textInput",
                        "authenticationRequired": True,
                    }
                ],
            )
            return
        self.pending = None
        try:
            result = await self.coordinator.client.request(
                "/api/tool/tv_pair",
                {
                    "pairing_id": pending["pairing_id"],
                    "kind": pending["kind"],
                    "code": code,
                },
                check=False,
            )
            message = (
                result["summary"]
                if result["ok"]
                else result["summary"] + " " + result["error"]["fix"]
            )
        except BridgeError:
            message = "Could not confirm pairing. Check tv_status."
        await self.send(message)
