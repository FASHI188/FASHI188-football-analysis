#!/usr/bin/env python3
"""Compatibility entry point for the CURRENT-documented OOF matrix calibrator path.

The actual maintained builder is validation/oof_matrix_calibration_v461.py. This
shim preserves the documented asset path without duplicating calibration logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engine"
VALIDATION_DIR = ROOT / "validation"
for path in (ENGINE_DIR, VALIDATION_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from oof_matrix_calibration_v461 import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
