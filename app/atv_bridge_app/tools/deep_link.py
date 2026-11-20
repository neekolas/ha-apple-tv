"""Identify a deep link's destination and the tvOS Open confirmation."""

import asyncio
import base64
import io
import re
import subprocess
from urllib.parse import urlsplit

from PIL import Image, ImageStat

from ..processes import child_env
from ..state import Fault
from .navigation import label, normal
from .prompts import SERVICES


def link_targets(url, rule):
    """Allow a catalog handoff only for a validated Apple title ID."""
    targets = {rule["bundle_id"]: rule["names"][0]} if rule else {}
    parts = urlsplit(url)
    for service in SERVICES:
        candidate = service.get("candidate")
        entries = service["templates"] + ([candidate] if candidate else [])
        for entry in entries:
            example = urlsplit(entry.get("example") or "")
            if not example.scheme:
                continue
            if entry.get("route") == "provider_handoff":
                matches = (
                    entry in service["templates"]
                    and parts.hostname == example.hostname == "tv.apple.com"
                    and parts.path.rstrip("/").split("/")[-1]
                    == example.path.rstrip("/").split("/")[-1]
                )
            else:
                matches = parts.scheme == example.scheme and (
                    parts.scheme not in ("https", "http")
                    or parts.hostname == example.hostname
                )
                # Generic Apple catalog links cannot establish a provider.
                if parts.hostname == "tv.apple.com":
                    matches = False
            if matches:
                targets.setdefault(service["bundle_id"], service["service"])
    return targets


def title_action(view):
    names = [label(view.get("focused")), *(label(i) for i in view.get("items", []))]
    # Home feeds can advertise a featured Play button. A feed or selected Home
    # tab takes precedence over that generic action.
    if {"home", "more info"} <= {normal(n) for n in names}:
        return None
    if normal(names[0]) in {"home", "for you", "browse"} or any(
        normal(n).startswith(("continue watching", "top picks for you", "trending now"))
        for n in names
    ):
        return None
    return next(
        (
            name
            for name in names
            if re.match(
                r"^(?:start watching|watch now|resume|play|episodes|rate series|open in|buy from|rent from|shuffle|watch trailer)(?:\b|$)",
                normal(name),
            )
        ),
        None,
    )


def caption(text):
    return " ".join(re.findall(r"[\w+]+", normal(text)))


def confirmed_open_image(png, app_names):
    """Recognize the centered English tvOS dialog and which button is highlighted."""
    with Image.open(io.BytesIO(png)) as img:
        frame = img.convert("L").resize((1280, 720))

    left = frame.crop((475, 372, 622, 408))
    right = frame.crop((660, 372, 797, 408))
    if ImageStat.Stat(left).median[0] < 220 or ImageStat.Stat(right).median[0] > 155:
        return False
    # Combine the caption and both button labels on a white background. Never
    # pass other screen content to OCR or include recognized text in logs.
    page = Image.new("L", (400, 120), 255)
    page.paste(
        frame.crop((445, 314, 835, 350)).point(lambda p: 0 if p > 170 else 255), (5, 10)
    )
    page.paste(left.point(lambda p: 0 if p < 140 else 255), (25, 65))
    page.paste(right.point(lambda p: 0 if p > 170 else 255), (210, 65))
    data = io.BytesIO()
    page.resize((800, 240)).save(data, format="PNG")
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6"],
            input=data.getvalue(),
            env=child_env(),
            capture_output=True,
            timeout=3,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    text = caption(result.stdout.decode(errors="replace"))
    return any(text == f"open in {caption(name)} open cancel" for name in app_names)


async def confirm_custom_link(tools, targets):
    if not targets or not tools.ops.state.receiver("remote_pairing"):
        return
    view = await observed(tools)
    owner = (view.get("app") or {}).get("bundle_id")
    names = set(targets.values())
    labels = [caption(label(i)) for i in view.get("items", [])]
    system_prompt = owner in {
        "com.apple.TVSystemUIService",
        "com.apple.PineBoard",
    } and any(f"open in {caption(n)}" in labels for n in names)
    if system_prompt:
        confirm = normal(label(view.get("focused"))) == "open" and "cancel" in labels
    else:
        try:
            capture = await tools.ops.observer.call("screenshot", max_width=1280)
            tools.action_evidence.append(
                "Checked the custom-link dialog with one screenshot (tvOS toast)"
            )
            confirm = await asyncio.to_thread(
                confirmed_open_image, base64.b64decode(capture["png"]), names
            )
        except (Fault, ValueError, OSError):
            return
    if confirm:
        await tools.press("select", switching_app=True)
        tools.action_evidence.append(
            "Confirmed the target app’s focused Open button with Select"
        )


async def observed(tools):
    if tools.ops.state.receiver("remote_pairing"):
        try:
            return await tools.read_observation()
        except Fault:
            pass
    return {"app": None, "focused": None, "items": []}
