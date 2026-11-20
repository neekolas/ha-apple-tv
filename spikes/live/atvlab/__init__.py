"""Run the repository app modules against the lab's editable pyatv fork."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.parents[1] / "app"
sys.path.insert(0, str(APP))
