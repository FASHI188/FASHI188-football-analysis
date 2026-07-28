#!/usr/bin/env python3
"""V6.48.4 live capture wrapper.

Uses the V6.48.2 synchronized Kambi acquisition engine unchanged, but points its identity
input to the phase-aware V6.48.4 live-capture registry. This keeps acquisition logic
single-sourced while allowing independently verified current qualifying participants.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import v6_full17_kambi_capture_v6482 as base

base.REGISTRY = ROOT / "config" / "v6_full17_capture_identity_v6484.json"

if __name__ == "__main__":
    raise SystemExit(base.main())
