#!/usr/bin/env python3
"""Compatibility entrypoint for the route-aware bidirectional draw audit."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE.parent / "audit"
for path in (HERE, AUDIT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from draw_signal_closure_engine_v502_r4 import *  # noqa: F401,F403
from draw_signal_closure_engine_v502_r4 import main as _main

if __name__ == "__main__":
    raise SystemExit(_main())
