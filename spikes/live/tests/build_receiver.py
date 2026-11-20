"""Build v0.6.0's diagnostic receiver on macOS. The production sender builds unmodified on Linux."""

import json
import subprocess

from atvlab import ROOT


def build_receiver():
    binary = ROOT / "bin/test-receiver"
    src = ROOT / "bin/doubletake-src"
    src.parent.mkdir(exist_ok=True)
    if not src.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                "v0.6.0",
                "https://github.com/omarroth/doubletake",
                src,
            ],
            check=True,
        )
    rev = subprocess.check_output(["git", "-C", src, "rev-parse", "HEAD"], text=True).strip()
    if rev != "be27776beb05beaa149a7096e4722f5cba60eb37":
        raise ValueError("Unexpected diagnostic receiver source revision")
    source = src / "internal/airplay/capture.go"
    replacement = ROOT / "bin/receiver-capture.go"
    # Go also compiles the unused Linux capture path when building the diagnostic receiver.
    replacement.write_text(source.read_text().replace("Pdeathsig: syscall.SIGKILL", ""))
    overlay = ROOT / "bin/receiver-overlay.json"
    overlay.write_text(json.dumps({"Replace": {str(source): str(replacement)}}))
    subprocess.run(
        ["go", "build", "-overlay", overlay, "-o", binary, "./cmd/doubletake-test-receiver"],
        cwd=src,
        check=True,
    )
    return binary
