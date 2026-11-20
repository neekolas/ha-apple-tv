"""Recognize visible launch prompts and choose an exact accessible action."""

import re
from pathlib import Path

import yaml

from ..state import Fault
from .navigation import label, normal

SERVICES = yaml.safe_load((Path(__file__).parents[1] / "data/deeplinks.validated.yaml").read_text())["services"]
KNOWN_PICKERS = {
    service["bundle_id"]
    for service in SERVICES
    if service["profile_picker"]["observed"] is True
}
SYSTEM = {"com.apple.TVSystemUIService", "com.apple.PineBoard", "com.apple.HeadBoard"}
HEADINGS = {
    "who's watching",
    "who’s watching",
    "choose a profile",
    "select a profile",
    "choose profile",
}
CONTROLS = {
    "add profile",
    "new profile",
    "manage profiles",
    "edit profiles",
    "edit",
    "add a profile",
    "switch profiles",
}


def prompt_choice(view, *, system=False, preferred=None):
    app = view.get("app") or {}
    bundle = app.get("bundle_id")
    names = [label(item) for item in view["items"] if label(item)]
    options = {normal(name).rstrip("?"): name for name in names}
    owner = normal(app.get("name"))
    focus = label(view["focused"])
    system_picker = (bundle in SYSTEM or owner == "system ui") and (
        "add profile" in options or bool(options.keys() & HEADINGS)
    )
    if system:
        if not system_picker:
            return None
    else:
        denies = (
            "ask app not to track",
            "decline",
            "don't allow",
            "don’t allow",
            "not now",
        )
        consent = (
            any(
                "permission" in t
                or "allow " in t
                or "consent" in t
                or "privacy" in t
                or "notifications" in t
                or "terms of use" in t
                for t in options
            )
            or "allow" in options
            or "accept" in options
        )
        deny = next(
            (
                options[d]
                for d in denies
                if d in options and (consent or d == denies[0])
            ),
            None,
        )
        if deny:
            return "Privacy choice", deny
        if (
            consent
            and ("allow" in options or "accept" in options)
            and any(
                "track" in key or "permission" in key or "consent" in key
                for key in options
            )
        ):
            raise Fault(
                "uncertain",
                "No privacy-preserving choice is accessible.",
                "Read tv_look and dismiss the prompt on the TV.",
            )
        if not (
            options.keys() & HEADINGS
            or (bundle in KNOWN_PICKERS and options.keys() & CONTROLS)
        ):
            return None
    candidates = [
        name
        for key, name in options.items()
        if key not in HEADINGS | CONTROLS
        and key != owner
        and not key.startswith(("add ", "edit ", "manage ", "choose ", "select "))
        and key
        not in (
            "profiles",
            "settings",
            "apple tv",
            "system ui",
            "cancel",
            "back",
            "allow",
        )
    ]
    if not system:
        non_kids = [
            name
            for name in candidates
            if not re.search(r"\bkids?\b|children", name, re.I)
        ]
        candidates = non_kids or (candidates if len(candidates) == 1 else [])
    by_name = {normal(v): v for v in candidates}
    target = by_name.get(normal(preferred)) or by_name.get(normal(focus))
    if not target and not system and candidates and focus and normal(focus) in options:
        target = candidates[0]
    if not target:
        raise Fault(
            "screen_unavailable",
            "The profile picker has no identifiable selectable profile.",
            "Choose the profile on the TV and retry.",
        )
    return ("tvOS profile" if system else "App profile"), target
