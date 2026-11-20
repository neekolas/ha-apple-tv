#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "httpx>=0.27,<1"]
# ///
"""Schedule one wake and sleep through HA's authenticated MCP endpoint."""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call(args, on):
    started = datetime.now(timezone.utc)
    clock = monotonic()
    record = {"at": started.isoformat(), "tv": args.tv, "on": on, "errno": None}
    try:
        token = (
            os.getenv("HA_TOKEN") or args.token_file.expanduser().read_text().strip()
        )
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=120
        ) as http:
            async with streamable_http_client(
                args.url.rstrip("/") + "/api/atv_bridge/mcp", http_client=http
            ) as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    reply = await session.call_tool(
                        "tv_power", {"tv": args.tv, "on": on}
                    )
                    result = json.loads(
                        next(c.text for c in reply.content if c.type == "text")
                    )
                    record.update(
                        result=result, errno=result.get("data", {}).get("errno")
                    )
    except Exception as exc:
        record["error"] = type(exc).__name__
        causes = [exc]
        while causes:
            cause = causes.pop()
            if (number := getattr(cause, "errno", None)) is not None:
                record["errno"] = number
                break
            causes.extend(getattr(cause, "exceptions", ()))
            if cause.__cause__:
                causes.append(cause.__cause__)
    record["duration_s"] = round(monotonic() - clock, 3)
    with args.output.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record.get("result", {}).get("verified", False)


async def main(args):
    if args.at.tzinfo is None:
        raise SystemExit("--at needs an explicit UTC offset")
    await asyncio.sleep(max(0, args.at.timestamp() - datetime.now().timestamp()))
    woke = await call(args, True)
    await asyncio.sleep(args.hold_seconds)
    slept = await call(args, False)
    return 0 if woke and slept else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--tv", required=True)
    parser.add_argument("--url", required=True, help="Home Assistant origin")
    parser.add_argument(
        "--token-file", type=Path, default=Path("~/.config/atv-bridge/ha_token")
    )
    parser.add_argument("--hold-seconds", type=float, default=3)
    parser.add_argument("--output", type=Path, default=Path("overnight-wake.jsonl"))
    raise SystemExit(asyncio.run(main(parser.parse_args())))
