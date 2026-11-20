"""Convert persistent HAP pairings between pyatv, tvpipe and doubletake.

Functions operate on in-memory text. Exports contain exactly one receiver.
A controller ID is UTF-8 text on the wire, including its original case.
"""

import base64
import binascii
import json
from typing import Dict, Iterator, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pyatv.auth.hap_pairing import AuthenticationType, HapCredentials, parse_credentials
from pyatv.exceptions import InvalidCredentialsError


def _validate(creds: HapCredentials) -> HapCredentials:
    if (
        creds.type != AuthenticationType.HAP
        or len(creds.ltpk) != 32
        or len(creds.ltsk) != 32
    ):
        raise InvalidCredentialsError(
            "Expected a persistent HAP pairing with 32-byte keys"
        )
    return creds


def _parse(value: str) -> HapCredentials:
    try:
        return _validate(parse_credentials(value))
    except (AttributeError, ValueError, InvalidCredentialsError):
        # parse_credentials can include its input in an exception.
        raise InvalidCredentialsError("Invalid pyatv HAP credentials") from None


def companion_credentials(airplay: str) -> str:
    """Return the same persistent HAP identity for a Companion pair-verify."""
    return str(_parse(airplay))


def _single_line(value: str) -> str:
    if value.strip() != value or (value and not value.isprintable()):
        raise InvalidCredentialsError(
            "tvpipe fields must be printable single-line text"
        )
    return value


def to_tvpipe(
    value: str, *, name: str = "", device_id: str = "", addr: str = ""
) -> str:
    """Write a tvpipe controller block followed by one HAP receiver block."""
    creds = _parse(value)
    controller = _single_line(creds.client_id.decode("utf-8"))
    lines = [
        "# tvpipe AirPlay credentials: private, do not share",
        f"controller = {controller}",
        f"seed = {creds.ltsk.hex()}",
        "",
        f"receiver = {creds.atv_id.hex()}",
        f"pk = {creds.ltpk.hex()}",
        f"name = {_single_line(name)}",
    ]
    if device_id:
        lines.append(f"deviceid = {_single_line(device_id)}")
    if addr:
        lines.append(f"addr = {_single_line(addr)}")
    return "\n".join(lines) + "\n"


def _blocks(text: str) -> Iterator[Dict[str, str]]:
    block: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line and block:
            yield block
            block = {}
        elif line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            block.setdefault(key.strip(), value.strip())
    if block:
        yield block


def from_tvpipe(text: str, *, receiver_id: Optional[bytes] = None) -> str:
    """Read a tvpipe HAP receiver. Multiple receivers require an explicit ID."""
    controllers, receivers = [], []
    try:
        for block in _blocks(text):
            kind = next(iter(block))
            if kind == "controller":
                controllers.append(block)
            elif kind == "receiver":
                identifier = binascii.unhexlify(block["receiver"])
                if receiver_id is None or identifier == receiver_id:
                    receivers.append((identifier, block))
        if len(controllers) != 1 or len(receivers) != 1:
            raise InvalidCredentialsError(
                "Expected one controller and one matching receiver"
            )
        identifier, receiver = receivers[0]
        if receiver.get("pairing", "hap") != "hap":
            raise InvalidCredentialsError("Expected a tvpipe HAP receiver")
        controller = controllers[0]
        creds = HapCredentials(
            binascii.unhexlify(receiver["pk"]),
            binascii.unhexlify(controller["seed"]),
            identifier,
            controller["controller"].encode("utf-8"),
        )
        return str(_validate(creds))
    except (ValueError, KeyError, TypeError):
        raise InvalidCredentialsError("Invalid tvpipe credential fields") from None


def _public_key(seed: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()


def to_doubletake(value: str, *, device_id: str) -> str:
    """Write a single device entry in doubletake's credentials.json format."""
    if not device_id:
        raise ValueError("A doubletake device ID is required")
    creds = _parse(value)
    entry = {
        "pairing_id": creds.client_id.decode("utf-8"),
        "ed25519_public": base64.b64encode(_public_key(creds.ltsk)).decode("ascii"),
        "ed25519_seed": base64.b64encode(creds.ltsk).decode("ascii"),
        "pairing_protocol": "hap",
        "receiver_id": creds.atv_id.decode("utf-8"),
        "receiver_ed25519_public": base64.b64encode(creds.ltpk).decode("ascii"),
    }
    return json.dumps({device_id: entry}, indent=2)


def from_doubletake(text: str, *, device_id: str) -> str:
    """Read a named HAP receiver with its proven identity from doubletake JSON."""
    try:
        entry = json.loads(text)[device_id]
        if entry.get("pairing_protocol", "") not in ("", "hap"):
            raise InvalidCredentialsError("Expected a doubletake HAP pairing")
        creds = _validate(
            HapCredentials(
                base64.b64decode(entry["receiver_ed25519_public"], validate=True),
                base64.b64decode(entry["ed25519_seed"], validate=True),
                entry["receiver_id"].encode("utf-8"),
                entry["pairing_id"].encode("utf-8"),
            )
        )
        if _public_key(creds.ltsk) != base64.b64decode(
            entry["ed25519_public"], validate=True
        ):
            raise InvalidCredentialsError(
                "Controller public key does not match its seed"
            )
        return str(creds)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidCredentialsError("Invalid doubletake credential fields") from None
