#!/usr/bin/env python3
"""Execution-only syntax repair for V6.25.13.

The statistical implementation in v6_total_xg_ordinal_residual_v62513 completed
model fitting/evaluation but used JSON-style ``true``/``false`` names while
constructing the final Python receipt. This shim defines those two names in the
module namespace and calls the unchanged main(). No feature, parameter, split,
selection rule, probability calculation, or metric is modified.
"""
from __future__ import annotations

import v6_total_xg_ordinal_residual_v62513 as core

core.true = True
core.false = False

if __name__ == "__main__":
    raise SystemExit(core.main())
