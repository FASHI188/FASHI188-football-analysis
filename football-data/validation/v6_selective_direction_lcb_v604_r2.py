#!/usr/bin/env python3
"""Runtime-reduced launcher for V6.0.4.

Keeps the same frozen model, thresholds, gates and deterministic bootstrap seed while
reducing bootstrap repetitions from 3000 to 1000 for the one-time CI challenge.
"""
from __future__ import annotations

import v6_selective_direction_lcb_v604 as core

core.BOOTSTRAP_REPS = 1000

if __name__ == "__main__":
    raise SystemExit(core.main())
