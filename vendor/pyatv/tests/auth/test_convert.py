"""Persistent HAP credential interchange."""

import base64
import json

import pytest

from pyatv.auth import convert
from pyatv.exceptions import InvalidCredentialsError

# RFC 8032 test vector 1, with distinct controller and receiver identifiers.
SEED = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
RECEIVER_KEY = "ab" * 32
CONTROLLER = "5b3c1a0e-0000-4000-8000-000000000001"
RECEIVER = "Receiver-2"
DEVICE = "AA:BB:CC:00:00:0A"
CREDS = ":".join(
    [RECEIVER_KEY, SEED, RECEIVER.encode().hex(), CONTROLLER.encode().hex()]
)


def test_tvpipe_wire_format_and_multi_receiver():
    text = convert.to_tvpipe(
        CREDS, name="Livingroom (2)", device_id=DEVICE, addr="192.0.2.1:7000"
    )
    assert text == (
        "# tvpipe AirPlay credentials: private, do not share\n"
        f"controller = {CONTROLLER}\nseed = {SEED}\n\n"
        f"receiver = {RECEIVER.encode().hex()}\npk = {RECEIVER_KEY}\n"
        f"name = Livingroom (2)\ndeviceid = {DEVICE}\naddr = 192.0.2.1:7000\n"
    )
    many = text + f"\nreceiver = 02\npk = {RECEIVER_KEY}\npairing = raw\n"
    assert convert.from_tvpipe(text.replace("\n", "\r\n")) == CREDS
    assert convert.from_tvpipe(many, receiver_id=RECEIVER.encode()) == CREDS
    with pytest.raises(InvalidCredentialsError):
        convert.from_tvpipe(many)
    with pytest.raises(InvalidCredentialsError):
        convert.from_tvpipe(many, receiver_id=b"\x02")


def test_doubletake_wire_format_and_cross_format_round_trip():
    text = convert.to_doubletake(CREDS, device_id=DEVICE)
    entry = json.loads(text)[DEVICE]
    assert entry["pairing_id"] == CONTROLLER
    assert entry["receiver_id"] == RECEIVER
    assert entry["pairing_protocol"] == "hap"
    for key, value in [
        ("ed25519_public", PUBLIC),
        ("ed25519_seed", SEED),
        ("receiver_ed25519_public", RECEIVER_KEY),
    ]:
        assert base64.b64decode(entry[key], validate=True).hex() == value
    assert (
        convert.from_tvpipe(
            convert.to_tvpipe(convert.from_doubletake(text, device_id=DEVICE))
        )
        == CREDS
    )


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("pairing_protocol", "raw"),
        ("ed25519_public", base64.b64encode(bytes(32)).decode()),
        ("receiver_id", ""),
        ("receiver_ed25519_public", "not base64"),
    ],
)
def test_reject_unusable_doubletake_pairing(field, replacement):
    store = json.loads(convert.to_doubletake(CREDS, device_id=DEVICE))
    store[DEVICE][field] = replacement
    with pytest.raises(InvalidCredentialsError):
        convert.from_doubletake(json.dumps(store), device_id=DEVICE)


@pytest.mark.parametrize("value", ["", ":", "01:02", "01:02:03:04"])
def test_reject_incomplete_pairings(value):
    with pytest.raises(InvalidCredentialsError):
        convert.companion_credentials(value)
