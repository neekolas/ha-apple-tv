# /// script
# dependencies = ["mcp>=2.3,<3", "httpx2>=2.16.2,<3", "pyyaml>=6.0.3,<7", "pillow>=13,<14"]
# ///
"""Run from the repo with uv run spikes/deeplinks/validate.py [service[:number] ...].

The number is the 1-based position in candidates.yaml. Use --repeat 3 on
winners after visual review. Set ATV_WAKE_PROFILE to allow selection of that
profile when it has focus on the tvOS wake picker. Each run preserves old evidence.
"""
import argparse
import asyncio
import base64
import io
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx2
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
GATE = Path("/tmp/atvlab/tv-free")
ENDPOINT = "http://homeassistant.local/api/atv_bridge/mcp"
HOME = "com.apple.HeadBoard"


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def bridge():
    token = Path("~/.config/atv-bridge/ha_token").expanduser().read_text().strip()
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120) as client:
        async with streamable_http_client(ENDPOINT, http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session


async def call(session, tool, args, image_path=None):
    result = await session.call_tool(tool, args)
    reply = {}
    for block in result.content:
        if block.type == "text":
            try:
                reply.update(json.loads(block.text))
            except (TypeError, ValueError):
                reply.update(ok=False, summary=block.text)
        elif block.type == "image" and image_path:
            with Image.open(io.BytesIO(base64.b64decode(block.data))) as frame:
                frame.save(image_path, format="PNG")
    return reply


def app_id(result):
    return ((result.get("screen") or {}).get("app") or {}).get("id")


def playing(result):
    return (result.get("screen") or {}).get("playing") or {}


async def home(session):
    wake_profile = os.getenv("ATV_WAKE_PROFILE")
    reply = await call(session, "tv_remote", {"keys": ["home"]})
    for _ in range(3):
        if app_id(reply) == HOME and playing(reply).get("state") != "playing":
            return reply
        await asyncio.sleep(1)
        reply = await call(session, "tv_look", {})
        screen = reply.get("screen") or {}
        if (app_id(reply) == "com.apple.TVSystemUIService"
                and wake_profile and (screen.get("focus") or {}).get("label") == wake_profile
                and any(item.get("label") == "Add Profile" for item in reply.get("data", {}).get("items", []))):
            print("Selecting the configured profile on the tvOS wake picker.", flush=True)
            with (OUT / "profile-events.jsonl").open("a") as log:
                log.write(json.dumps({"at": timestamp(), "picker": "tvOS wake", "selection": "configured profile"}) + "\n")
            reply = await call(session, "tv_remote", {"keys": ["select"]})
            continue
        if app_id(reply) == HOME and playing(reply).get("state") == "playing":
            reply = await call(session, "tv_remote", {"keys": ["pause"]})
        elif app_id(reply) and app_id(reply) != HOME:
            reply = await call(session, "tv_remote", {"keys": ["home"]})
    if app_id(reply) != HOME or playing(reply).get("state") == "playing":
        raise RuntimeError(f"Home or stopped playback unconfirmed: {reply.get('screen')}")
    return reply


def provisional(service, candidate, look):
    screen = look.get("screen") or {}
    items = [i["label"] for i in (look.get("data") or {}).get("items", []) if i.get("label")]
    labels = " ".join(items + [(screen.get("focus") or {}).get("label") or ""]).casefold()
    title = candidate.get("title", service["title"]).casefold()
    foreground = app_id(look)
    media = playing(look)
    if foreground != service["bundle_id"]:
        return "failed" if foreground else "unconfirmed"
    aliases = [title, *(name.casefold() for name in service.get("playback_aliases", []))]
    media_title = str(media.get("title") or "").casefold()
    if media.get("state") == "playing" and any(name in media_title for name in aliases):
        return "playing"
    if title in labels and any(word in labels for word in ["episode", "resume", "watch now", "play", "season"]):
        return "title_page"
    return "app_home"


def save_record(path, record):
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    partial.replace(path)


async def inspect_landing(session, record, record_path, image_path):
    """Inspect the current PNG before navigating a welcome screen or accepting a focused default profile."""
    record["steps"] = []
    while True:
        save_record(record_path, record)
        print(f"REVIEW {image_path.name}", flush=True)
        command = input().strip()
        if command.startswith("done "):
            _, outcome, sign_in, note = command.split(" ", 3)
            if outcome not in {"failed", "app_home", "title_page", "playing", "unconfirmed"}:
                raise ValueError("Unknown reviewed outcome")
            if sign_in not in {"signed_in", "signed_out", "sso_continue", "unknown"}:
                raise ValueError("Unknown sign-in state")
            record["review"] = dict(outcome=outcome, note=note, screenshot_reviewed=image_path.exists())
            record["sign_in_status"] = sign_in
            return
        if command.startswith("go "):
            tool, args = "tv_navigate", {"to": command[3:], "select": True}
        elif command in {"up", "down", "left", "right", "select", "menu"}:
            tool, args = "tv_remote", {"keys": [command]}
        else:
            print("Use go LABEL, a direction, select, menu, or done OUTCOME SIGN_IN_STATUS NOTE.", flush=True)
            continue
        action = await call(session, tool, args)
        await asyncio.sleep(3)
        look = await call(session, "tv_look", {})
        image_path = record_path.with_name(f"{record_path.stem}-step{len(record['steps']) + 1}.png")
        image_look = await call(session, "tv_look", {"image": True, "max_width": 1280}, image_path)
        record["steps"].append(dict(tool=tool, args=args, action=action, look=look,
                                    image_look=image_look, image=image_path.name if image_path.exists() else None))


async def trial(session, service, index, candidate, open_url, baseline, interactive):
    key = service["key"]
    number = 1 + max((int(p.stem.rsplit("-", 1)[1]) for p in OUT.glob(f"{key}-*.json")), default=0)
    record_path = OUT / f"{key}-{number}.json"
    image_path = record_path.with_suffix(".png")
    mode = "tv_open" if open_url and candidate["goal"] != "C" else "tv_play"
    args = {"url" if mode == "tv_open" else "link": candidate["url"]}
    record = dict(service=key, candidate=index, tested_at=timestamp(), tool=mode, args=args)
    print(f"{key}:{index} -> {image_path.name}", flush=True)
    record["baseline"] = baseline
    save_record(record_path, record)
    try:
        record["launch"] = await call(session, mode, args)
        await asyncio.sleep(6)
        record["look"] = await call(session, "tv_look", {})
        record["image_look"] = await call(session, "tv_look", {"image": True, "max_width": 1280}, image_path)
        look = record["look"] if record["look"].get("ok") else record["image_look"]
        record["provisional"] = provisional(service, candidate, look)
        record["image"] = image_path.name if image_path.exists() else None
        record["review"] = None
        observations = [record["look"], record["image_look"]]
        if interactive:
            await inspect_landing(session, record, record_path, image_path)
            if record["steps"]:
                last = record["steps"][-1]
                observations = [last["look"], last["image_look"]]
        if any(playing(r).get("state") == "playing" for r in observations):
            record["pause"] = await call(session, "tv_remote", {"keys": ["pause"]})
            if not app_id(look):
                await call(session, "tv_remote", {"keys": ["menu"]})
                await asyncio.sleep(2)
                back_image = record_path.with_name(record_path.stem + "-back.png")
                record["after_back"] = await call(
                    session, "tv_look", {"image": True, "max_width": 1280}, back_image
                )
    finally:
        save_record(record_path, record)
        record["cleanup"] = await home(session)
        save_record(record_path, record)
    print(f"{key}:{index} {record.get('provisional')} foreground={app_id(look)} image={record.get('image')}", flush=True)
    return record["cleanup"]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selectors", nargs="*", help="Service key or service:candidate-number")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--interactive", action="store_true", help="Review each PNG with the TV still at that screen")
    parser.add_argument("--remaining", action="store_true", help="Skip completed trials with a saved PNG")
    parser.add_argument("--list", action="store_true", help="List candidates offline")
    args = parser.parse_args()
    matrix = yaml.safe_load((ROOT / "candidates.yaml").read_text())
    trials = [(s, n, c) for s in matrix["services"] if not s.get("skipped")
              for n, c in enumerate(s["candidates"], 1)]
    names = {name for s, n, c in trials for name in (s["key"], f"{s['key']}:{n}")}
    if set(args.selectors) - names or args.repeat < 1:
        parser.error("Unknown selector or invalid repeat count")
    selected = [(s, n, c) for s, n, c in trials if not args.selectors or
                s["key"] in args.selectors or f"{s['key']}:{n}" in args.selectors]
    if args.remaining:
        finished = set()
        for path in OUT.glob("*-*.json"):
            record = json.loads(path.read_text())
            if record.get("cleanup") and record.get("image"):
                finished.add((record["service"], record["candidate"]))
        selected = [(s, n, c) for s, n, c in selected if (s["key"], n) not in finished]
    if args.list:
        for s, n, c in selected:
            print(f"{s['key']}:{n} {c['goal']} {c['url']}")
        return
    if not GATE.is_file():
        parser.exit(2, f"Waiting for the TV handoff at {GATE}\n")
    OUT.mkdir(exist_ok=True)
    async with bridge() as session:
        schemas = {t.name: t.input_schema for t in (await session.list_tools()).tools}
        if not (OUT / "inventory.json").exists():
            resources = await session.list_resources()
            if any(str(r.uri).rstrip("/") == "tv://apps" for r in resources.resources):
                inventory = await session.read_resource("tv://apps")
                save_record(OUT / "inventory.json", json.loads(inventory.contents[0].text))
        open_schema = schemas["tv_open"]
        open_url = "url" in open_schema["properties"] and "app" not in open_schema.get("required", [])
        baseline = await home(session)
        for s, n, c in selected:
            for _ in range(args.repeat):
                if not GATE.is_file():
                    raise RuntimeError("TV handoff withdrawn")
                baseline = await trial(session, s, n, c, open_url, baseline, args.interactive)


if __name__ == "__main__":
    asyncio.run(main())
