"""Replay captured tvOS dialogs in the bridge image with its production OCR binary.

Mount spikes/deeplinks/out at /samples and feed this script to the image's
`python -` with Docker stdin enabled. No Apple TV or network is needed.
"""
from pathlib import Path

from atv_bridge_app.tools.deep_link import confirmed_open_image

for filename, requested, expected in (
    ("netflix-2.png", "Netflix", True),
    ("hulu-2.png", "Hulu", True),
    ("netflix-2.png", "Hulu", False),
    ("netflix-2-step2.png", "Netflix", False),
    ("hulu-2-refresh.png", "Hulu", False),
):
    result = confirmed_open_image((Path("/samples") / filename).read_bytes(), [requested])
    print(f"{filename} / {requested}: {result}")
    if result != expected:
        raise SystemExit(f"Expected {expected} for {filename}")
