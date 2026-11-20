"""Public v1 MCP and REST contract."""

from ..state import Fault
from . import BOOL, KEYS, MEDIA_COMMANDS, TEXT, number, schema

UNKNOWN = {
    "power": "unknown",
    "app": None,
    "playing": None,
    "presenting": None,
    "focus": None,
    "keyboard": False,
}


def outcome(summary, evidence=(), data=None, verified=True):
    return {
        "ok": True,
        "summary": summary,
        "verified": verified,
        "evidence": list(evidence),
        "data": data or {},
    }


def checked(result):
    if not result["ok"]:
        e = result["error"]
        raise Fault(
            e["code"],
            e["message"],
            e["remediation"],
            e["retryable"],
            evidence=result.get("evidence", []),
            **({"errno": result["errno"]} if result.get("errno") is not None else {}),
        )
    return result


MAX_STEPS = 50


def described(spec, description, **annotations):
    return spec | {"description": description, **annotations}


def sequence_schema():
    target = described(TEXT, "Full accessible label. Use $item for the current for_each label.")
    check = schema({
        "focus": described(TEXT, "Exact focused label, or $item."),
        "visible": described(TEXT, "Exact label in the current accessibility items or focus, or $item."),
        "value": described({"type": "string"}, "Exact value of the focused item, or $item. An empty string is a value."),
        "app": described(TEXT, "Exact foreground app name or bundle ID from accessibility, or $item."),
    }, minProperties=1)
    steps = [
        schema({"key": described({"enum": list(KEYS)}, "Send this key once and wait for acknowledgment.")}, ["key"]),
        schema({"pause_ms": described(number(1, 5000), "Wait this many milliseconds before continuing.")}, ["pause_ms"]),
        schema({"to": target, "select": described({"type": "boolean"}, "Select after the exact label is focused.", default=False)}, ["to"]),
        schema({"text": described(TEXT, "Replace the focused field and verify keyboard readback.")}, ["text"]),
        schema({
            "app": described(TEXT, "Open this installed app by name or bundle ID."),
            "path": described({"type": "array", "items": TEXT, "minItems": 1, "maxItems": 10}, "Follow and select up to ten menu labels, as in tv_open."),
        }, ["app"]),
        schema({"expect": described(check, "Require all supplied fields to match on the same fresh screen read.")}, ["expect"]),
    ]
    return schema({
        "steps": described({"type": "array", "items": {"oneOf": steps}, "minItems": 1, "maxItems": MAX_STEPS},
                           "Ordered remote operations. At most 50 steps across all iterations."),
        "for_each": described({"type": "array", "minItems": 1, "maxItems": MAX_STEPS, "items": TEXT},
                              "Run all steps once for each listed label, in order. Substitute the whole string $item in to or expect."),
    }, ["steps"])


DIRECTIONS = ["up", "down", "left", "right"]
SHOW = {
    "url": TEXT,
    "html": TEXT,
    "files": {"type": "object", "required": ["index.html"], "additionalProperties": {"type": "string"}},
    "bundle": TEXT,
    "title": TEXT,
    "audio": {"enum": ["play", "mute"]},
    "keep_awake": BOOL,
}
SCHEMAS = {
    "tv_show": schema(
        SHOW,
        oneOf=[
            {"maxProperties": 0},
            *[{"required": [key]} for key in ("url", "html", "files", "bundle")],
        ],
    ),
    "tv_update": schema({"data": {}}, ["data"]),
    "tv_play": schema(
        {"youtube": TEXT, "media": TEXT, "link": TEXT, "title": TEXT, "app": TEXT},
        oneOf=[{"required": [key]} for key in ("youtube", "media", "link")]
        + [{"required": ["app", "title"]}],
        dependentRequired={"title": ["app"], "app": ["title"]},
    ),
    "tv_open": schema(
        {"app": TEXT, "search": TEXT, "path": {"type": "array", "items": TEXT, "minItems": 1}},
        ["app"],
        **{"not": {"required": ["search", "path"]}},
    ),
    "tv_look": schema({"image": BOOL, "max_width": number(1, 4096)}),
    "tv_navigate": schema({"to": TEXT, "within": TEXT, "select": BOOL}, ["to"]),
    "tv_remote": schema(
        {
            "keys": {"type": "array", "minItems": 1, "maxItems": 50, "items": {"enum": list(KEYS)}},
            "hold_ms": number(0, 10000),
            "text": TEXT,
            "submit": BOOL,
            "swipe": {"enum": DIRECTIONS},
        },
        oneOf=[{"required": [key]} for key in ("keys", "text", "swipe")],
        dependentRequired={"hold_ms": ["keys"], "submit": ["text"]},
        **{
            "if": {"required": ["hold_ms"], "properties": {"hold_ms": {"exclusiveMinimum": 0}}},
            "then": {"properties": {"keys": {"items": {"not": {"enum": list(MEDIA_COMMANDS)}}}}},
        },
    ),
    "tv_sequence": sequence_schema(),
    "tv_power": schema({"on": BOOL}, ["on"]),
    "tv_pair": schema({
        "kind": {"enum": ["control", "screen"]}, "code": {"type": "string", "pattern": "^[0-9]{4}([0-9]{2})?$"},
        "pairing_id": TEXT, "read_code_from_screen": BOOL, "notify": BOOL, "cancel": BOOL,
    }, ["kind"]),
    "tv_status": schema(),
}
# The dispatcher consumes routing fields before invoking the per-TV operation.
ROUTED_SCHEMAS = {
    name: spec | {"properties": spec["properties"] | {"tv": TEXT}} for name, spec in SCHEMAS.items()
}
ROUTED_SCHEMAS["tv_show"] = ROUTED_SCHEMAS["tv_show"] | {
    "properties": ROUTED_SCHEMAS["tv_show"]["properties"] | {"replace": BOOL},
    "oneOf": [
        {"properties": {"tv": TEXT}, "additionalProperties": False},
        *SCHEMAS["tv_show"]["oneOf"][1:],
    ],
}
INSTRUCTIONS = (
    "Select an Apple TV by name or ID with tv. Read tv://tvs to see the rooms. With several paired TVs, name the target before acting. To put your own content on it, host a web page "
    "(or a local web server shared with `tailscale serve`) and call `tv_show`. Use `tv_update` to change what the page "
    "shows. To watch something, use `tv_play`. To drive an app, use `tv_open`, then `tv_look` and "
    "`tv_navigate`. Use `tv_remote` only when nothing else fits. Use tv_pair for a missing or invalid control or screen pairing. Name the TV and relay ask_user. Poll with the same kind and pairing_id while waiting_for_pairing_screen. Submit the code as text at code_on_screen. Read the returned `screen` "
    "before your next step. Ask the user before anything that costs money."
)
PAGE_GUIDE = """Design for a 1920×1080 canvas with body text of at least 28 px. Put index.html at the root of
inline files or an uploaded ZIP. Use the base64: prefix for binary inline files. HTML and bundle pages
receive the tv helper automatically. Use tv.on('update', data => render(data)), tv.ready(), and assign
tv.state to a JSON value for tv_look. A message event also carries the original update data.
For live URLs, keep the server running. The Green loads the real site, including scripts, fetches,
WebSockets and reloads. For a local server, use tailscale serve and pass its HTTPS MagicDNS URL.
To use the helper on a live page, include <script src="http://127.0.0.1:8766/tv.js"></script>
and allow that script in the site's CSP. Pages that block framing open at the top level.
Page audio plays by default. Use audio: "mute" for a silent presentation."""
RETRYABLE = {"tv_unreachable", "page_unreachable", "screen_unavailable", "busy"}
CODES = {
    "wake_failed": "tv_unreachable",
    "unreachable": "tv_unreachable",
    "pairing_required": "not_paired",
    "allow_access_changed": "access_blocked",
    "invalid_pin": "not_paired",
    "invalid_pairing_id": "invalid_arguments",
    "command_rejected": "launch_refused",
    "app_not_found": "app_not_installed",
    "keyboard_unfocused": "no_text_field",
    "fork_required": "launch_refused",
    "stage_unavailable": "page_unreachable",
    "bundle_missing": "page_unreachable",
    "stage_timeout": "page_unreachable",
    "sender_failed": "page_unreachable",
    "invalid_bundle": "invalid_arguments",
    "content_full": "page_unreachable",
    "observer_failed": "screen_unavailable",
    "screen_pairing_invalid": "screen_unavailable",
    "observer_unavailable": "screen_unavailable",
    "delivery_uncertain": "uncertain",
    "open_unverified": "uncertain",
    "playback_unverified": "uncertain",
    "stage_delivery_uncertain": "uncertain",
}
STABLE = RETRYABLE | {
    "launch_refused", "tv_required", "mirror_busy", "tv_not_found",
    "not_paired",
    "pairing_removed",
    "access_blocked",
    "app_not_installed",
    "not_found",
    "no_text_field",
    "uncertain",
    "invalid_arguments",
}


def public_error(fault):
    code = CODES.get(fault.code, fault.code)
    if code not in STABLE:
        code = "uncertain"
    return {
        "code": code,
        "message": str(fault),
        "retryable": code in RETRYABLE,
        "fix": fault.remediation,
    }
