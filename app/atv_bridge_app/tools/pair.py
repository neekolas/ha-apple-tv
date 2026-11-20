"""Exchange a room-scoped pairing code without exposing it in tool results."""

import asyncio
import re

from ..pairing import SCREEN_CODE
from ..state import Fault
from .navigation import label

SETTINGS_PATH = ["Remotes and Devices", "Remote App and Devices"]


def airplay_code(items):
    texts = [
        str(text).strip()
        for item in items
        for text in (label(item), item.get("value"))
        if text
    ]
    context = " ".join(texts).lower()
    if "airplay" not in context or not any(
        word in context for word in ("code", "pin", "passcode")
    ):
        return None
    codes = set()
    digits = []
    for text in texts:
        codes.update(re.findall(r"(?<!\d)\d{4}(?!\d)", text))
        if re.fullmatch(r"\d", text):
            digits.append(text)
        elif re.fullmatch(r"\d(?:\s+\d){3}", text):
            codes.add("".join(text.split()))
    if len(digits) == 4:
        codes.add("".join(digits))
    return codes.pop() if len(codes) == 1 else None


async def exchange(
    tools,
    kind,
    code=None,
    pairing_id=None,
    notify=False,
    read_code_from_screen=False,
    cancel=False,
):
    from .public import checked, outcome, UNKNOWN

    pairing = tools.pairing
    protocol, length = ("airplay", 4) if kind == "control" else ("remote_pairing", 6)
    if sum((code is not None, cancel, read_code_from_screen)) > 1 or (
        notify and (code is not None or cancel)
    ):
        raise Fault(
            "invalid_arguments",
            "Choose one pairing operation.",
            "Start, submit a code, or cancel.",
        )
    if read_code_from_screen and (
        kind != "control" or not tools.ops.state.receiver("remote_pairing")
    ):
        raise Fault(
            "invalid_arguments",
            "Reading a code requires a control prompt and a working screen pairing.",
            "Start tv_pair and ask the user to read the code.",
        )
    if cancel:
        await pairing.cancel(protocol, pairing_id or pairing.id)
        tools.engine.publish({"pairing": "cancelled"})
        return outcome("Pairing cancelled.", ["PIN session closed"], {"kind": kind}) | {
            "screen": UNKNOWN.copy()
        }
    if code is None:
        if pairing_id:
            current = await pairing.inspect(protocol, pairing_id, notify)
            if not read_code_from_screen:
                current.pop("notify")
                return outcome(current["ask_user"], data=current, verified=False) | {
                    "screen": UNKNOWN.copy()
                }
        if (
            kind == "screen"
            and not pairing.id
            and tools.ops.state.receiver("companion")
        ):
            try:
                checked(await tools.engine.ready())
                await tools.prepare_ui()
                await tools.tv_open("Settings", path=SETTINGS_PATH)
            except Fault:
                # A missing screen record prevents AX from verifying the menu path.
                # Directed discovery below waits for this TV to advertise the PIN service.
                pass
        ask = (
            "Please read me the 4-digit AirPlay code on the TV."
            if kind == "control"
            else SCREEN_CODE
        )
        prompt = dict(
            kind=kind,
            code_length=length,
            ask_user=ask,
            notify=notify,
            state="code_on_screen",
        )
        await pairing.start(protocol, prompt=prompt)
        pairing_id = pairing.id
        tools.engine.publish({"pairing": pairing.pending["state"]})
        if read_code_from_screen:
            for attempt in range(3):
                try:
                    observed = await tools.read_observation()
                    code = airplay_code(observed["items"])
                except Fault:
                    pass
                if code:
                    break
                if attempt < 2:
                    await asyncio.sleep(0.5)
        if code is None:
            data = await pairing.inspect(protocol, pairing_id)
            data.pop("notify")
            evidence = (
                ["Code could not be read unambiguously"]
                if read_code_from_screen
                else []
            )
            return outcome(data["ask_user"], evidence, data, verified=False) | {
                "screen": UNKNOWN.copy()
            }
    if not re.fullmatch(rf"[0-9]{{{length}}}", code):
        raise Fault(
            "invalid_arguments",
            f"Expected a {length}-digit code.",
            "Submit the digits as text, including leading zeros.",
        )
    await pairing.finish(pairing_id or pairing.id, code, protocol=protocol)
    if kind == "control":
        async with tools.engine.session(fresh=True) as client:
            await client.apps()
        evidence = ["Authenticated Companion app list"]
    else:
        observation = await tools.read_observation()
        if not observation["items"] and not observation["focused"]:
            raise Fault(
                "screen_unavailable",
                "The new screen pairing returned no accessible elements.",
                "Read tv_look to check the screen.",
            )
        evidence = ["Authenticated accessibility read"]
    tools.engine.publish({"pairing": "verified"})
    return outcome(f"Verified {kind} pairing.", evidence, {"kind": kind}) | {
        "screen": UNKNOWN.copy()
    }
