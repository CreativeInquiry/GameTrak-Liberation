#!/usr/bin/env python3
"""Run the GameTrak OSC transmitter from a source checkout."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gametrak.osc import main


if __name__ == "__main__":
    sys.exit(main())
