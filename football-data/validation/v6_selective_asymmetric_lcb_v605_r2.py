#!/usr/bin/env python3
"""Runtime-reduced launcher for V6.0.5 with unchanged policy and gates."""
from __future__ import annotations

import v6_selective_asymmetric_lcb_v605 as core

core.BOOTSTRAP_REPS = 1000

if __name__ == "__main__":
    raise SystemExit(core.main())
