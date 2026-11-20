#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["aiohttp>=3.14.5,<4", "yarl>=1.25.1,<2"]
# ///
"""Deploy the Apple TV bridge to Home Assistant OS."""

import argparse
import asyncio
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
from contextlib import suppress
from ipaddress import IPv4Address
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from yarl import URL

ROOT = Path(__file__).resolve().parents[2]
APP = "local_atv_bridge"
SSH = "core_ssh"
SSH_APPS = (SSH, "a0d7b954_ssh")
BUILD_TIMEOUT = 900
TOKEN_FILE = Path.home() / ".config/atv-bridge/ha_token"


class DeployError(Exception):
    pass


class LostReply(DeployError):
    """The command was sent and its result is unknown."""


def read_token(path):
    try:
        with path.open() as f:
            attrs = os.fstat(f.fileno())
            if not stat.S_ISREG(attrs.st_mode) or stat.S_IMODE(attrs.st_mode) != 0o600:
                raise DeployError(
                    f"Expected a regular token file with mode 600 at {path}"
                )
            token = f.read().strip()
    except FileNotFoundError:
        raise DeployError(f"Missing admin token file {path}") from None
    if not token or any(c.isspace() for c in token):
        raise DeployError(f"Expected one token in {path}")
    return token


class HA:
    def __init__(self, session, url, token):
        self.session = session
        self.base = URL(url)
        if (
            self.base.scheme not in {"http", "https"}
            or not self.base.host
            or self.base.user is not None
            or self.base.query_string
            or self.base.fragment
            or self.base.path != "/"
        ):
            raise DeployError("--url must be an HTTP(S) origin")
        self.token = token
        self.version = None

    def say(self, message):
        print(str(message).replace(self.token, "[redacted]"), flush=True)

    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    async def ws(self, kind, *, wait=25, **fields):
        async with asyncio.timeout(wait + 5):
            async with self.session.ws_connect(
                self.base / "api/websocket", heartbeat=25
            ) as sock:
                hello = await sock.receive_json(timeout=10)
                if hello.get("type") != "auth_required":
                    raise DeployError("Expected Home Assistant websocket greeting")
                await sock.send_json({"type": "auth", "access_token": self.token})
                auth = await sock.receive_json(timeout=10)
                if auth.get("type") != "auth_ok":
                    raise DeployError("HA rejected the access token")
                self.version = auth.get("ha_version")
                await sock.send_json({"id": 1, "type": kind, **fields})
                try:
                    frame = await sock.receive(timeout=wait)
                    if frame.type != aiohttp.WSMsgType.TEXT:
                        raise LostReply(f"Websocket closed while waiting for {kind}")
                except (TimeoutError, aiohttp.ClientConnectionError) as err:
                    raise LostReply(
                        f"No reply for {fields.get('endpoint', kind)}. Check its state before retrying."
                    ) from err
                reply = frame.json()
                if reply.get("type") != "result" or reply.get("id") != 1:
                    raise DeployError(f"Unexpected websocket result for {kind}")
                if not reply.get("success"):
                    error = reply.get("error", {})
                    raise DeployError(
                        f"{kind} failed: {error.get('message', error.get('code'))}"
                    )
                return reply.get("result")

    async def sup(self, endpoint, method="get", *, data=None, timeout=30):
        self.say(f"{method.upper()} Supervisor {endpoint}")
        fields = {"endpoint": endpoint, "method": method, "timeout": timeout}
        if data is not None:
            fields["data"] = data
        return await self.ws("supervisor/api", wait=timeout + 5, **fields)

    async def apps(self):
        return (await self.sup("/addons"))["addons"]

    async def info(self, slug):
        return await self.sup(f"/addons/{slug}/info")

    async def get_json(self, url, *, auth=False):
        async with self.session.get(
            url,
            headers=self.headers() if auth else {},
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=8),
            raise_for_status=True,
        ) as response:
            return await response.json()

    async def health(self):
        url = self.base.with_scheme("http").with_port(8766) / "api/health"
        try:
            result = await self.get_json(url)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return False
        return (
            isinstance(result, dict)
            and result.get("ok") is True
            and result.get("state") == "running"
        )

    async def logs(self, follow):
        path = f"api/hassio/addons/{APP}/logs" + ("/follow" if follow else "")
        self.say(f"GET /{path}")
        async with self.session.get(
            self.base / path,
            headers=self.headers(),
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(
                total=None if follow else 30, sock_connect=10
            ),
            raise_for_status=True,
        ) as response:
            async for line in response.content:
                self.say(line.decode(errors="replace").rstrip("\r\n"))


def pick(data, keys):
    return {k: data.get(k) for k in keys}


def tailscale_options(options):
    """Print known non-secret settings. Hide new and free-form options by default."""
    safe = {
        "accept_dns",
        "accept_routes",
        "advertise_connector",
        "advertise_exit_node",
        "advertise_routes",
        "advertise_tags",
        "always_use_derp",
        "exit_node",
        "log_suppression",
        "log_upload",
        "share_homeassistant",
        "share_on_port",
        "snat_subnet_routes",
        "stateful_filtering",
        "taildrop",
        "userspace_networking",
    }
    report = {k: v if k in safe else "[redacted]" for k, v in options.items()}
    if isinstance(options.get("taildrive"), dict):
        report["taildrive"] = {
            k: v if isinstance(v, bool) else "[redacted]"
            for k, v in options["taildrive"].items()
        }
    if isinstance(options.get("login_server"), str):
        # The full URL can carry credentials in any component except the host.
        report["login_server"] = (
            urlsplit(options["login_server"]).hostname or "[redacted]"
        )
    return report


async def check(ha, limited):
    ha.say("GET /api/")
    await ha.get_json(ha.base / "api/", auth=True)
    supervisor = await ha.sup("/supervisor/info")
    apps = await ha.apps()
    indexed = {app["slug"]: app for app in apps}
    report = {
        "HA version": ha.version,
        "Supervisor version": supervisor["version"],
        "architecture": supervisor.get("arch"),
        "installed apps": [pick(a, ("name", "slug", "version", "state")) for a in apps],
        "SSH": {},
        "Tailscale": [],
    }
    for slug in SSH_APPS:
        item = {"installed": slug in indexed}
        if slug in indexed:
            item["state"] = indexed[slug]["state"]
            if not limited:
                detail = await ha.info(slug)
                item["host TCP port"] = (detail.get("network") or {}).get("22/tcp")
                item["protection mode"] = detail.get("protected")
                item["deployment requires disabling protection"] = False
        report["SSH"][slug] = item
    for app in apps:
        if app["slug"].endswith("_tailscale"):
            item = pick(app, ("slug", "state"))
            if not limited:
                item["options"] = tailscale_options(
                    (await ha.info(app["slug"]))["options"]
                )
            report["Tailscale"].append(item)
    if not limited:
        report["free disk (GiB)"] = (await ha.sup("/host/info"))["disk_free"]
        network = await ha.sup("/network/info")
        report["network interfaces"] = [
            pick(i, ("interface", "type", "connected", "primary", "ipv4", "ipv6"))
            for i in network["interfaces"]
        ]
    ha.say(json.dumps(report, indent=2))


async def run(*args, check=True):
    proc = await asyncio.create_subprocess_exec(
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, err = await proc.communicate()
    except BaseException:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        raise
    if check and proc.returncode:
        raise DeployError(
            f"{Path(args[0]).name} exited {proc.returncode}: {err.decode(errors='replace').strip()}"
        )
    return (
        proc.returncode,
        out.decode(errors="replace").strip(),
        err.decode(errors="replace").strip(),
    )


def key_path(directory):
    dedicated = directory / "atv_bridge_ha"
    standard = directory / "id_ed25519"
    if (
        dedicated.exists()
        or not standard.exists()
        or not standard.with_suffix(".pub").exists()
    ):
        return dedicated
    return standard


async def public_key(ha, key):
    pub = key.with_suffix(".pub")
    if not key.exists():
        if pub.exists():
            raise DeployError(f"Private key missing for {pub}")
        key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        ha.say(f"Generating dedicated Ed25519 key at {key}")
        await run(
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-f",
            key,
            "-N",
            "",
            "-C",
            "atv-bridge-deploy",
        )
    if not pub.exists():
        _, contents, _ = await run("ssh-keygen", "-y", "-f", key)
        pub.write_text(contents + "\n")
    contents = pub.read_text().strip()
    if "\n" in contents or not contents.startswith("ssh-ed25519 "):
        raise DeployError(f"Expected one Ed25519 public key in {pub}")
    return contents


class Remote:
    def __init__(self, ha, port):
        self.ha = ha
        key = key_path(Path.home() / ".ssh")
        if not key.is_file():
            raise DeployError("Run ssh-setup to configure the SSH identity")
        self.options = [
            "-i",
            key,
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
        ]
        self.port = str(port)
        self.target = "root@" + ha.base.host_subcomponent

    async def shell(self, command, *, check=True):
        return await run(
            "ssh", *self.options, "-p", self.port, self.target, command, check=check
        )

    async def copy_tree(self, source, roots, subdir=""):
        # All remote paths are from fixed mount names or mktemp, never app options.
        choices = " ".join(roots)
        _, mount, _ = await self.shell(
            f'for d in {choices}; do if [ -d "$d" ]; then echo "$d"; exit 0; fi; done; exit 1'
        )
        if mount not in roots:
            raise DeployError("Unexpected SSH mount path")
        parent = mount + subdir
        dest = parent + "/atv_bridge"
        self.ha.say(f"Copy {source.relative_to(ROOT)} to {self.target}:{dest}")
        _, stage, _ = await self.shell(
            f"mkdir -p {parent} && mktemp -d {parent}/.atv-deploy.XXXXXXXX"
        )
        if not re.fullmatch(re.escape(parent) + r"/\.atv-deploy\.[A-Za-z0-9]+", stage):
            raise DeployError("Unexpected SSH staging path")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "context.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    tar.add(source, arcname=".", filter=archive_filter)
                await run(
                    "scp",
                    *self.options,
                    "-P",
                    self.port,
                    archive,
                    f"{self.target}:{stage}/context.tar.gz",
                )
            # Replace the whole tree so deleted files cannot survive a deployment.
            await self.shell(f"""
set -eu
cd {stage}
mkdir new
tar xzf context.tar.gz -C new
mkdir {parent}/.atv-deploy-lock || {{ echo 'Deployment lock exists' >&2; exit 1; }}
restore_if_interrupted() {{
    if [ ! -e {dest} ] && [ -e previous ]; then mv previous {dest}; fi
    rmdir {parent}/.atv-deploy-lock
}}
trap restore_if_interrupted EXIT
trap 'exit 1' HUP INT TERM
if [ -e {dest} ]; then mv {dest} previous; fi
mv new {dest}
""")
        except BaseException:
            # If the publishing SSH session was lost, it may still be using the
            # staging directory. Leave it intact so its trap can restore the tree.
            self.ha.say(f"Upload interrupted. Inspect staging directory {stage}.")
            raise
        await self.shell(f"rm -rf {stage}")


def archive_filter(member):
    name = Path(member.name).name
    if name in {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".cache",
        ".DS_Store",
        "node_modules",
        ".tox",
    } or name.endswith((".egg-info", ".pyc", ".pyo")):
        return None
    return member


async def choose_port(ha, installed, info):
    existing = (info.get("network") or {}).get("22/tcp")
    if existing and info["state"] == "started":
        return int(existing)
    reserved = set()
    for app in installed:
        if app["slug"] != SSH:
            mappings = (await ha.info(app["slug"])).get("network") or {}
            reserved.update(
                int(v) for k, v in mappings.items() if k.endswith("/tcp") and v
            )
    candidates = ([int(existing)] if existing else []) + list(range(2222, 2254))
    for port in dict.fromkeys(candidates):
        if port in reserved:
            continue
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ha.base.host, port, all_errors=True), 3
            )
        except ConnectionRefusedError:
            return port
        except ExceptionGroup as errors:
            if errors.split(ConnectionRefusedError)[1] is None:
                return port
            continue
        except OSError:
            continue
        writer.close()
        await writer.wait_closed()
    raise DeployError("No available SSH port in 2222–2253")


async def ssh_setup(ha):
    key = key_path(Path.home() / ".ssh")
    pub = await public_key(ha, key)
    installed = await ha.apps()
    if SSH not in {a["slug"] for a in installed}:
        await ha.sup(f"/store/addons/{SSH}/install", "post", timeout=BUILD_TIMEOUT)
    info = await ha.info(SSH)
    port = await choose_port(ha, installed, info)
    options = info["options"].copy()
    keys = options.get("authorized_keys", [])
    if not any(k.split()[:2] == pub.split()[:2] for k in keys):
        options["authorized_keys"] = [*keys, pub]
    options["server"] = {**options.get("server", {}), "tcp_forwarding": False}
    network = {**(info.get("network") or {}), "22/tcp": port}
    changed = options != info["options"] or network != info.get("network")
    if changed:
        await ha.sup(
            f"/addons/{SSH}/options",
            "post",
            data={"options": options, "network": network},
        )
    remote = Remote(ha, port)
    if info["state"] != "started":
        await ha.sup(f"/addons/{SSH}/start", "post", timeout=120)
    else:
        if not changed:
            # Options may already be saved after an interrupted setup. Check the
            # running sshd before deciding whether it needs to load them.
            code, _, err = await remote.shell("true", check=False)
            if code == 0:
                ha.say(f"SSH ready on port {port}")
                return
            if "host key" in err.lower() or "identification has changed" in err.lower():
                raise DeployError(err)
        await ha.sup(f"/addons/{SSH}/restart", "post", timeout=120)
    ha.say(f"Checking SSH public-key login on TCP {port}")
    async with asyncio.timeout(90):
        while True:
            code, _, err = await remote.shell("true", check=False)
            if code == 0:
                ha.say(f"SSH ready on port {port}")
                return
            if any(
                s in err.lower()
                for s in (
                    "permission denied",
                    "host key verification failed",
                    "identification has changed",
                )
            ):
                raise DeployError(err)
            await asyncio.sleep(2)


async def remote_for_push(ha):
    info = await ha.info(SSH)
    port = (info.get("network") or {}).get("22/tcp")
    if info.get("state") != "started" or not port:
        raise DeployError("Run ssh-setup before copying files")
    return Remote(ha, port)


async def wait_app(ha):
    ha.say(f"Waiting for http://{ha.base.host}:8766/api/health to report running")

    async def follow_logs():
        while True:
            try:
                await ha.logs(True)
            except aiohttp.ClientResponseError as err:
                if err.status < 500:
                    raise
            except (aiohttp.ClientConnectionError, TimeoutError):
                pass
            ha.say("App log stream closed. Reconnecting in 3 seconds.")
            await asyncio.sleep(3)

    logs = asyncio.create_task(follow_logs())
    try:
        while not await ha.health():
            if logs.done():
                logs.result()
            await asyncio.sleep(3)
        ha.say("App watchdog reports running")
    finally:
        logs.cancel()
        with suppress(asyncio.CancelledError):
            await logs


async def push_app(ha, args):
    remote = await remote_for_push(ha)
    ha.say("Stage vendor/pyatv with app/build/stage-context.sh")
    await run(ROOT / "app/build/stage-context.sh")
    if not (ROOT / "app/vendor/pyatv/pyproject.toml").is_file():
        raise DeployError("Missing staged pyatv source")
    await remote.copy_tree(ROOT / "app", ("/local_apps", "/addons"))
    try:
        async with asyncio.timeout(BUILD_TIMEOUT):
            await ha.sup("/store/reload", "post", timeout=120)
            installed = {a["slug"]: a for a in await ha.apps()}
            if APP not in installed:
                await ha.sup(
                    f"/store/addons/{APP}/install", "post", timeout=BUILD_TIMEOUT
                )
            elif installed[APP]["version"] != installed[APP]["version_latest"]:
                # Supervisor refuses rebuild if the store version changed.
                await ha.sup(
                    f"/store/addons/{APP}/update", "post", timeout=BUILD_TIMEOUT
                )
            else:
                await ha.sup(f"/addons/{APP}/rebuild", "post", timeout=BUILD_TIMEOUT)
            info = await ha.info(APP)
            options = info["options"].copy()
            for name in ("apple_tv_host", "log_level", "presentation_mode"):
                value = getattr(args, name)
                if value is not None:
                    options[name] = str(value)
            changed = options != info["options"]
            if changed:
                await ha.sup(
                    f"/addons/{APP}/options", "post", data={"options": options}
                )
            if info["state"] != "started":
                await ha.sup(f"/addons/{APP}/start", "post", timeout=180)
            elif changed:
                await ha.sup(f"/addons/{APP}/restart", "post", timeout=180)
            await wait_app(ha)
    except TimeoutError:
        raise DeployError(
            "15-minute deployment deadline reached. Check status and Supervisor jobs before retrying."
        ) from None


async def push_integration(ha):
    remote = await remote_for_push(ha)
    await remote.copy_tree(
        ROOT / "custom_components/atv_bridge",
        ("/homeassistant", "/config"),
        "/custom_components",
    )
    await ha.sup("/homeassistant/check", "post", timeout=240)
    try:
        await ha.sup("/core/restart", "post", timeout=240)
        restart_confirmed = True
    except LostReply:
        ha.say(
            "Core restart interrupted its websocket reply. Waiting for a restart transition."
        )
        restart_confirmed = False
    ha.say("Waiting for HA Core to answer and reach RUNNING")
    async with asyncio.timeout(300):
        while True:
            try:
                data = await ha.get_json(ha.base / "api/core/state", auth=True)
                if data["state"] != "RUNNING":
                    restart_confirmed = True
                elif restart_confirmed:
                    ha.say("HA Core is running")
                    return
            except aiohttp.ClientResponseError as err:
                if err.status < 500:
                    raise
                restart_confirmed = True
            except (aiohttp.ClientConnectionError, TimeoutError):
                restart_confirmed = True
            await asyncio.sleep(3)


async def disable_builtin(ha, title, yes):
    ha.say("List built-in apple_tv config entries")
    entries = await ha.ws("config_entries/get", domain="apple_tv")
    matches = [e for e in entries if e["domain"] == "apple_tv" and e["title"] == title]
    if len(matches) != 1:
        raise DeployError(
            f"Expected one apple_tv entry titled {title!r}, found {len(matches)}"
        )
    entry = matches[0]
    label = f"apple_tv {title!r} (entry_id {entry['entry_id']})"
    if entry["disabled_by"] is not None:
        ha.say(f"{label} is already disabled by {entry['disabled_by']}")
        return
    if not yes:
        ha.say(f"Dry run: would disable {label}. Use --yes to apply.")
        return
    ha.say(f"Disable {label}")
    result = await ha.ws(
        "config_entries/disable", entry_id=entry["entry_id"], disabled_by="user"
    )
    if result.get("require_restart"):
        ha.say(
            "HA reports that a Core restart is required to finish unloading this entry."
        )
    confirmed = await ha.ws("config_entries/get", domain="apple_tv")
    if not any(
        e["entry_id"] == entry["entry_id"] and e["disabled_by"] == "user"
        for e in confirmed
    ):
        raise DeployError("HA did not confirm disabled_by=user")
    ha.say(f"Confirmed disabled_by=user for {label}")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--url",
        default="http://homeassistant.local",
        help="HA origin (default port 80)",
    )
    subs = p.add_subparsers(dest="command", required=True)
    inventory = subs.add_parser("check", help="Read HA and Supervisor inventory")
    inventory.add_argument(
        "--limited",
        action="store_true",
        help="Read only /api/, /supervisor/info and /addons",
    )
    subs.add_parser("ssh-setup", help="Install and configure Terminal & SSH")
    push = subs.add_parser(
        "push-app", help="Stage, upload, build and start the bridge app"
    )
    push.add_argument("--apple-tv-host", type=IPv4Address, help="Apple TV IPv4 address")
    push.add_argument("--log-level", choices=["debug", "info", "warning", "error"])
    push.add_argument("--presentation-mode", choices=["mirror", "play_url", "auto"])
    subs.add_parser(
        "push-integration", help="Upload, check configuration and restart Core"
    )
    disable = subs.add_parser(
        "disable-builtin", help="Disable the named built-in Apple TV entry"
    )
    disable.add_argument("--entry-title", required=True)
    confirmation = disable.add_mutually_exclusive_group()
    confirmation.add_argument(
        "--dry-run", action="store_true", help="Preview only (default)"
    )
    confirmation.add_argument(
        "--yes", action="store_true", help="Apply the disable operation"
    )
    logs = subs.add_parser("logs", help="Read bridge logs")
    logs.add_argument("--follow", "-f", action="store_true")
    subs.add_parser("status", help="Show bridge app state and watchdog")
    subs.add_parser("restart-app", help="Restart bridge app and wait for watchdog")
    return p


async def dispatch(ha, args):
    match args.command:
        case "check":
            await check(ha, args.limited)
        case "ssh-setup":
            await ssh_setup(ha)
        case "push-app":
            await push_app(ha, args)
        case "push-integration":
            await push_integration(ha)
        case "disable-builtin":
            await disable_builtin(ha, args.entry_title, args.yes)
        case "logs":
            await ha.logs(args.follow)
        case "status":
            if APP not in {a["slug"] for a in await ha.apps()}:
                ha.say(f"{APP} is not installed")
                return
            info = await ha.info(APP)
            ha.say(
                json.dumps(
                    {
                        **pick(info, ("slug", "version", "state")),
                        "watchdog_running": await ha.health(),
                    },
                    indent=2,
                )
            )
        case "restart-app":
            await ha.sup(f"/addons/{APP}/restart", "post", timeout=180)
            async with asyncio.timeout(180):
                await wait_app(ha)


async def main(args):
    token = read_token(TOKEN_FILE)
    trace = aiohttp.TraceConfig()

    async def reject_redirect(session, context, params):
        raise DeployError("Unexpected redirect. Set --url to the HA origin directly.")

    trace.on_request_redirect.append(reject_redirect)
    async with aiohttp.ClientSession(
        trace_configs=[trace], cookie_jar=aiohttp.DummyCookieJar()
    ) as session:
        ha = HA(session, args.url, token)
        try:
            await dispatch(ha, args)
        except (DeployError, OSError, aiohttp.ClientError, ValueError, KeyError) as err:
            raise DeployError(
                str(err).replace(token, "[redacted]") or "Operation timed out"
            ) from None


if __name__ == "__main__":
    try:
        asyncio.run(main(parser().parse_args()))
    except (DeployError, OSError) as err:
        sys.exit(str(err))
    except KeyboardInterrupt:
        sys.exit(130)
