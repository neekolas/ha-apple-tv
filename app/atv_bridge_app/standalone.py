"""Standalone mode: run the bridge as a plain MCP server, without Home Assistant.

Home Assistant mode reads Supervisor's /data/options.json. Standalone mode builds the
same options from TV_USE_* environment variables and command flags. A flag wins over
its environment variable. Standalone mode serves MCP and the REST routes on one port
behind a bearer token that the operator supplies.
"""

import argparse
import hmac
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .page_network import PageNetworkPolicy
from .state import Fault

PREFIX = "TV_USE_"
MIN_TOKEN_LENGTH = 16
STAGE_PORT = 8766

# The same keys and defaults as `options` in app/config.yaml.
DEFAULTS = {
    "apple_tvs": [],
    "max_active_mirrors": 1,
    "apple_tv_host": "",
    "apple_tv_id": "",
    "presentation_mode": "mirror",
    "mirror_size": "1920x1080",
    "page_scripts": True,
    "page_network_allow": [],
    "mirror_fps": 10,
    "default_profile": "",
    "app_profile": "",
    "watch_history": True,
    "history_interval": 300,
    "history_fast_interval": 15,
    "history_max_entries": 2000,
    "log_level": "info",
}


def choice(*values):
    def parse(text):
        value = text.strip().lower()
        if value not in values:
            raise argparse.ArgumentTypeError(f"choose one of {', '.join(values)}, not {text!r}")
        return value

    return parse


def integer(low, high=None):
    def parse(text):
        try:
            value = int(text.strip())
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, not {text!r}") from None
        if value < low or (high is not None and value > high):
            limit = f"{low} through {high}" if high is not None else f"at least {low}"
            raise argparse.ArgumentTypeError(f"expected {limit}, not {value}")
        return value

    return parse


def boolean(text):
    value = text.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, not {text!r}")


def address(text):
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an IP address, not {text!r}") from None


def ipv4(text):
    try:
        return str(ipaddress.IPv4Address(text.strip()))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an IPv4 address, not {text!r}") from None


def text(value):
    return value.strip()


def tv(value):
    """Parse `host=IP,id=ID,name=NAME` with any subset of the keys, or a bare IPv4 address."""
    value = value.strip()
    if "=" not in value:
        return {"host": ipv4(value)}
    entry = {}
    for part in value.split(","):
        key, sep, field = (s.strip() for s in part.partition("="))
        key = key.lower()
        if not sep or key not in ("host", "id", "name") or not field or key in entry:
            raise argparse.ArgumentTypeError(
                f"expected host=IP, id=ID and name=NAME separated by commas, not {value!r}"
            )
        entry[key] = ipv4(field) if key == "host" else field
    return entry


@dataclass(frozen=True)
class Option:
    key: str
    flag: str
    env: str
    parse: object
    help: str
    kind: str = "value"  # value, list, bool or flag
    separator: str = ","


OPTIONS = [
    Option("apple_tvs", "--tv", "TVS", tv,
           "A TV as host=IP,id=ID,name=NAME (any subset) or a bare IPv4 address. Repeat the flag "
           "for more TVs. In the variable, separate TVs with semicolons.", "list", ";"),
    Option("presentation_mode", "--presentation-mode", "PRESENTATION_MODE",
           choice("mirror", "play_url", "auto"), "mirror, play_url or auto. Default mirror."),
    Option("mirror_size", "--mirror-size", "MIRROR_SIZE", choice("1280x720", "1920x1080"),
           "1920x1080 or 1280x720. Default 1920x1080."),
    Option("mirror_fps", "--mirror-fps", "MIRROR_FPS", integer(1, 60), "1 through 60. Default 10."),
    Option("max_active_mirrors", "--max-active-mirrors", "MAX_ACTIVE_MIRRORS", integer(1),
           "Concurrent mirror sessions across the TVs. Default 1."),
    Option("page_scripts", "--page-scripts", "PAGE_SCRIPTS", boolean,
           "Allow tv_page_run. Default true.", "bool"),
    Option("page_network_allow", "--page-network-allow", "PAGE_NETWORK_ALLOW", text,
           "Extra page destination: hostname, IP, CIDR or host:port. Repeat the flag for more. "
           "In the variable, separate entries with commas.", "list", ","),
    Option("default_profile", "--default-profile", "DEFAULT_PROFILE", text,
           "Preferred tvOS profile label."),
    Option("app_profile", "--app-profile", "APP_PROFILE", text, "Preferred in-app profile label."),
    Option("watch_history", "--watch-history", "WATCH_HISTORY", boolean,
           "Record what each TV plays. Default true.", "bool"),
    Option("history_interval", "--history-interval", "HISTORY_INTERVAL", integer(30, 3600),
           "Seconds between samples while nothing changes. Default 300."),
    Option("history_fast_interval", "--history-fast-interval", "HISTORY_FAST_INTERVAL",
           integer(5, 300), "Seconds to the next sample after a change. Default 15."),
    Option("history_max_entries", "--history-max-entries", "HISTORY_MAX_ENTRIES",
           integer(100, 20000), "Entries kept per TV. Default 2000."),
    Option("log_level", "--log-level", "LOG_LEVEL", choice("debug", "info", "warning", "error"),
           "debug, info, warning or error. Default info."),
    Option("listen", "--listen", "LISTEN", address,
           "Address for MCP and REST. Default 0.0.0.0, or 127.0.0.1 with --insecure-local."),
    Option("port", "--port", "PORT", integer(1, 65535), "Port for MCP and REST. Default 8765."),
    Option("stage_listen", "--stage-listen", "STAGE_LISTEN", address,
           "Address for the stage on port 8766. Default 127.0.0.1. Use 0.0.0.0 for play_url or "
           "auto on a Linux host, where the TV fetches MP4 files from the stage."),
    Option("token", "--token", "TOKEN", text, "Bearer token that clients must send."),
    Option("token_file", "--token-file", "TOKEN_FILE", text, "File that holds the bearer token."),
    Option("insecure_local", "--insecure-local", "INSECURE_LOCAL", boolean,
           "Accept requests without a token. Listens on loopback only.", "flag"),
]


def parser():
    result = argparse.ArgumentParser(
        prog="python -m atv_bridge_app",
        description="Run tv-use as a standalone MCP server, without Home Assistant.",
        epilog="Each flag has an environment variable, shown in brackets. A flag wins over its "
        "variable. State is kept in $ATV_BRIDGE_DATA (default /data).",
    )
    result.add_argument("--standalone", action="store_true",
                        help="Use standalone mode even if /data/options.json exists [TV_USE_MODE=standalone].")
    for option in OPTIONS:
        help = f"{option.help} [{PREFIX}{option.env}]"
        if option.kind == "bool":
            result.add_argument(option.flag, dest=option.key, action=argparse.BooleanOptionalAction,
                                default=None, help=help)
        elif option.kind == "flag":
            result.add_argument(option.flag, dest=option.key, action="store_const", const=True,
                                default=None, help=help)
        else:
            result.add_argument(option.flag, dest=option.key, type=option.parse, default=None,
                                action="append" if option.kind == "list" else "store",
                                metavar=option.flag.removeprefix("--").upper().replace("-", "_"), help=help)
    return result


def mode(argv, environ, root, supervisor):
    """Return "standalone" or "ha". Home Assistant mode is the default whenever Supervisor is present."""
    explicit = environ.get(PREFIX + "MODE", "").strip().lower()
    if explicit not in ("", "ha", "standalone"):
        raise SystemExit(f"{PREFIX}MODE must be standalone or ha, not {explicit!r}.")
    if "--standalone" in argv or explicit == "standalone":
        return "standalone"
    if explicit == "ha" or supervisor or (Path(root) / "options.json").exists():
        return "ha"
    return "standalone"


def bearer(token):
    expected = f"Bearer {token}".encode()

    def authorize(request):
        supplied = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(supplied, expected):
            return Fault(
                "unauthorized",
                "Bearer token required.",
                f"Send Authorization: Bearer <token> with the token from {PREFIX}TOKEN or {PREFIX}TOKEN_FILE.",
            )
        return None

    return authorize


def loopback(request):
    # A loopback Host header keeps a web page from reaching the open server through DNS rebinding.
    if urlsplit("//" + request.host).hostname not in ("127.0.0.1", "localhost", "::1"):
        return Fault(
            "unauthorized",
            "This server accepts only loopback host names.",
            "Connect to http://127.0.0.1 or http://localhost, or run with a token.",
        )
    return None


@dataclass(frozen=True)
class Settings:
    options: dict
    listen: str
    port: int
    stage_listen: str
    token: str | None

    @property
    def authorize(self):
        return bearer(self.token) if self.token else loopback

    @property
    def fleet(self):
        """Keyword arguments for Fleet."""
        return {"stage_host": self.stage_listen}

    def listeners(self, service):
        return [(service.app(authorize=self.authorize), self.listen, self.port)]

    def describe(self):
        host = f"[{self.listen}]" if ":" in self.listen else self.listen
        auth = "a bearer token" if self.token else "no token (--insecure-local)"
        tvs = len(self.options["apple_tvs"])
        return (f"tv-use standalone: MCP at http://{host}:{self.port}/mcp with {auth}, "
                f"stage on {self.stage_listen}:{STAGE_PORT}, {tvs} configured TV(s)")


def settings(argv, environ):
    """Build standalone Settings from flags and TV_USE_* variables. Exits with a message on bad input."""
    cli = parser()
    flags = vars(cli.parse_args(argv))
    values = {}
    for option in OPTIONS:
        value = flags[option.key]
        raw = environ.get(PREFIX + option.env, "")
        if value is None and raw.strip():
            try:
                if option.kind == "list":
                    value = [option.parse(item) for item in raw.split(option.separator) if item.strip()]
                elif option.kind == "flag":
                    value = boolean(raw)
                else:
                    value = option.parse(raw)
            except argparse.ArgumentTypeError as exc:
                cli.error(f"{PREFIX}{option.env}: {exc}")
        values[option.key] = value

    options = {key: list(value) if isinstance(value, list) else value for key, value in DEFAULTS.items()}
    options.update({key: value for key, value in values.items() if key in DEFAULTS and value is not None})
    try:
        PageNetworkPolicy(options["page_network_allow"])
    except ValueError as exc:
        cli.error(str(exc))

    if values["token"] and values["token_file"]:
        cli.error(f"Use {PREFIX}TOKEN or {PREFIX}TOKEN_FILE, not both.")
    token = values["token"]
    if values["token_file"]:
        try:
            token = Path(values["token_file"]).read_text().strip()
        except OSError as exc:
            cli.error(f"Cannot read the token file: {exc.strerror or exc}")
    insecure = bool(values["insecure_local"])
    if insecure and token:
        cli.error("Use a token or --insecure-local, not both.")
    if not insecure and not token:
        cli.error(f"A bearer token is required. Set {PREFIX}TOKEN or {PREFIX}TOKEN_FILE. "
                  "To run without a token on loopback only, pass --insecure-local.")
    if token and len(token) < MIN_TOKEN_LENGTH:
        cli.error(f"The token must have at least {MIN_TOKEN_LENGTH} characters.")
    listen = values["listen"] or ("127.0.0.1" if insecure else "0.0.0.0")
    if insecure and not ipaddress.ip_address(listen).is_loopback:
        cli.error("--insecure-local listens only on a loopback address.")
    port = values["port"] or 8765
    if port == STAGE_PORT:
        cli.error(f"Port {STAGE_PORT} is the stage port. Choose another port.")
    return Settings(options, listen, port, values["stage_listen"] or "127.0.0.1", token or None)


def configure(argv, environ, root, supervisor):
    """Return Settings in standalone mode, or None in Home Assistant mode."""
    if mode(argv, environ, root, supervisor) == "ha":
        if argv:
            raise SystemExit("Command flags apply only in standalone mode. "
                             f"Pass --standalone or set {PREFIX}MODE=standalone.")
        return None
    return settings(argv, environ)
