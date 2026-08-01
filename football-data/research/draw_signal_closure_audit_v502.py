#!/usr/bin/env python3
"""Compatibility entrypoint for the bidirectional draw-signal closure audit."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from draw_signal_closure_engine_v502 import *  # noqa: F401,F403
from draw_signal_closure_engine_v502 import main as _engine_main

if __name__ == "__main__":
    raise SystemExit(_engine_main())
