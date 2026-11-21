import json
import os
import secrets
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

CONTROLLER_NAME = "Agent Bridge"


def now():
    return datetime.now(UTC).isoformat()


def write_private(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w") as f:
        try:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


class State:
    def __init__(self, root, *, controller=None):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if controller is None:
            self.identity = self.load_or_create(
                "identity.json",
                lambda: {
                    "controller_id": str(uuid.uuid4()).upper(),
                    "ed25519_seed": secrets.token_hex(32),
                    "name": CONTROLLER_NAME,
                },
            )
            if self.identity.get("name") != CONTROLLER_NAME:
                self.identity["name"] = CONTROLLER_NAME
                write_private(self.root / "identity.json", json.dumps(self.identity))
            token = self.root / "internal_token"
            if not token.exists():
                write_private(token, secrets.token_urlsafe(32))
            self.token = token.read_text().strip()
            if not self.token:
                raise ValueError("Empty internal_token")
        else:
            self.identity, self.token = controller.identity, controller.token
        self.pairings = self.load_or_create(
            "pairings.json", lambda: dict.fromkeys(("airplay", "companion", "raop", "remote_pairing"))
        )

    def load_or_create(self, name, create):
        path = self.root / name
        if not path.exists():
            write_private(path, json.dumps(create()))
        return json.loads(path.read_text())

    def receiver(self, protocol):
        airplay = self.pairings.get("airplay")
        if protocol == "raop":
            return airplay or self.pairings.get("raop")
        if (
            protocol == "companion"
            and self.pairings.get("credential_reuse", {}).get("companion") is not False
        ):
            return self.pairings.get("companion") or airplay
        return self.pairings.get(protocol)

    def pairing_needed(self):
        needed = []
        if self.pairings.get("airplay") and not self.receiver("companion"):
            needed.append("companion")
        if not self.pairings.get("remote_pairing"):
            needed.append("remote_pairing")
        return needed

    def save_pairings(self, **updates):
        pairings = self.pairings | updates
        write_private(self.root / "pairings.json", json.dumps(pairings))
        self.pairings = pairings

    def write_tvpipe(self, host, port=None):
        from .atv import tvpipe_credentials

        if port is None:
            port = (self.pairings.get("tv") or {}).get("airplay_port", 7000)
        text = tvpipe_credentials(self.identity, self.pairings.get("airplay"), host, port)
        write_private(self.root / "tvpipe" / "credentials.txt", text)

    def audit(self, name, args, result):
        path = self.root / "audit.jsonl"
        if path.exists() and path.stat().st_size >= 10 * 1024**2:
            path.replace(path.with_suffix(".jsonl.1"))
        # Only record argument names. Text, page content, URLs and message data can be private.
        entry = {
            "at": now(),
            "tool": name,
            "arguments": sorted(args) if isinstance(args, dict) else [],
            "ok": result["ok"],
            "state": result.get("state") or ("verified" if result.get("verified") else "unverified"),
        }
        if "error" in result:
            entry["error_code"] = result["error"]["code"]
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def success(state, evidence=(), **fields):
    return {"ok": True, "state": state, "evidence": list(evidence), **fields}


# Standalone mode replaces remediation text that names Home Assistant steps.
REWORDING = {}


def reworded(text):
    return REWORDING.get(text, text)


class Fault(Exception):
    def __init__(self, code, message, remediation, retryable=False, **fields):
        super().__init__(message)
        self.code = code
        self.remediation = reworded(remediation)
        self.retryable = retryable
        self.fields = fields

    def result(self, state="failed", evidence=()):
        return {
            "ok": False,
            "state": state,
            "evidence": list(evidence),
            "error": {
                "code": self.code,
                "message": str(self),
                "retryable": self.retryable,
                "remediation": self.remediation,
            },
            **self.fields,
        }


def classify(exc):
    if isinstance(exc, Fault):
        return exc
    causes = []
    errno = None
    while exc is not None:
        if errno is None:
            errno = getattr(exc, "errno", None)
        causes.append(f"{type(exc).__name__} {exc}".lower())
        exc = exc.__cause__
    reason = " ".join(causes)
    if any(marker in reason for marker in ("403", "forbidden", "access denied", "not permitted")):
        return Fault(
            "allow_access_changed", "The TV denied access.", "Check AirPlay and HomeKit → Allow Access."
        )
    if "pairingerror" in reason:
        return Fault(
            "pairing_failed",
            "The PIN exchange failed.",
            "Start pairing again and enter the current PIN.",
        )
    if any(
        marker in reason
        for marker in ("authenticationerror", "invalidcredentials", "401", "signature error")
    ):
        return Fault(
            "pairing_removed", "The TV rejected the saved credentials.", "Pair this controller again."
        )
    if any(
        marker in reason
        for marker in ("command failed:", "command failed with", "companion rejected command")
    ):
        return Fault(
            "command_rejected",
            "Companion rejected the request.",
            "Check that this command is supported.",
        )
    return Fault(
        "unreachable",
        "The Apple TV session is unavailable.",
        "Check the configured IP and Wi-Fi, then wake the TV.",
        True,
        **({"errno": errno} if errno is not None else {}),
    )
