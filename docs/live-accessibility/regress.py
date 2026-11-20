#!/usr/bin/env python3
"""Run the paired-TV regression through HA's MCP endpoint.

Usage: python3 docs/live-accessibility/regress.py --runs 3 --leave-on-devices
Requires uv and the live MCP client at /tmp/atvlab/mcpcall.py (override with --client).
Start with Settings last left at its top-level menu. Only labeled menu entries
are selected. The list of paired devices is observed, never activated.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

HOME = "com.apple.HeadBoard"
SETTINGS = "com.apple.TVSettings"
MENU = "Remotes and Devices"
DEVICES = "Remote App and Devices"


def require(value, message):
    if not value:
        raise RuntimeError(message)


def call(client, name, **arguments):
    started = time.monotonic()
    command = ["uv", "run", "--quiet", str(client), name, json.dumps(arguments)]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=150)
    require(proc.returncode == 0, f"{name} client exited {proc.returncode}")
    result = json.loads(proc.stdout)
    require(
        result.get("ok") and result.get("verified"),
        f"{name} was unverified ({(result.get('error') or {}).get('code')}): {result.get('summary')}; "
        f"keys={result.get('data', {}).get('path', [])}, evidence={result.get('evidence', [])}",
    )
    screen = result["screen"]
    focus = (screen.get("focus") or {}).get("label")
    require(focus, f"{name} returned no focused label")
    print(f"  {name} {(time.monotonic() - started):.2f}s", flush=True)
    return result


def check_look(client, bundle, heading=None):
    result = call(client, "tv_look")
    screen = result["screen"]
    app = screen["app"]
    require(
        app and app["id"] == bundle and app["source"] == "accessibility",
        "Wrong foreground application",
    )
    require(app["name"] == ("Home" if bundle == HOME else "Settings"), "Wrong system app name")
    focus = screen["focus"]["label"]
    items = result["data"]["items"]
    require(
        [item["label"] for item in items if item["focused"]] == [focus],
        "Marked item does not match the observed focus",
    )
    if heading:
        require(any(item["label"] == heading for item in items), "Missing menu heading")
    return focus


def open_path(client):
    result = call(client, "tv_open", app="Settings", path=[MENU, DEVICES])
    steps = result["data"]["steps"]
    require([step["target"] for step in steps] == [MENU, DEVICES], "Incomplete menu path")
    require(
        all(step["verified"] and step["focus_after"] and step["target_visible"] for step in steps),
        "A menu selection was not verified",
    )
    require(result["screen"]["app"]["id"] == SETTINGS, "Settings lost foreground")
    require(
        check_look(client, SETTINGS, DEVICES) == steps[-1]["focus_after"],
        "Independent read disagrees with final path step",
    )
    print(f"  verified path: {result['data']['path']}", flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--client", type=Path, default=Path("/tmp/atvlab/mcpcall.py"))
    cli.add_argument("--runs", type=int, default=3)
    cli.add_argument("--leave-on-devices", action="store_true")
    args = cli.parse_args()
    require(args.runs > 0 and args.client.is_file(), "Specify a client and at least one run")
    for index in range(args.runs):
        start = time.monotonic()
        # Pressing Home while already there can have an unrelated effect. Read
        # first, then send Home only when there is an app to leave.
        first = call(args.client, "tv_look")
        if first["screen"]["app"]["id"] != HOME:
            call(args.client, "tv_remote", keys=["home"])
        check_look(args.client, HOME)
        open_path(args.client)
        call(args.client, "tv_remote", keys=["menu"])
        check_look(args.client, SETTINGS, MENU)
        call(args.client, "tv_remote", keys=["menu"])
        require(
            check_look(args.client, SETTINGS, "Settings") == MENU,
            "Back did not return to Settings root",
        )
        print(f"run {index + 1} passed in {time.monotonic() - start:.2f}s", flush=True)
    if args.leave_on_devices:
        open_path(args.client)


if __name__ == "__main__":
    main()
