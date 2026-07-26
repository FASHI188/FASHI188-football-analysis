#!/usr/bin/env python3
"""Run V6.27.3 with a wider fixed attempt pool while preserving the same frozen model.

The first run used 180 fixed-order 2025/26 candidates and produced only 63 feasible projections.
This wrapper changes only ATTEMPT_POOL to 360. Alpha, seed, training season, test season, projection
method, metrics and ordering are unchanged. The receipt still evaluates only the first 100 successful
rows in the same outcome-blind random order.
"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'validation') not in sys.path:sys.path.insert(0,str(ROOT/'validation'))
import validate_three_stage_ah_soft_residual_random100_v6273 as model
model.ATTEMPT_POOL=360
if __name__=='__main__':raise SystemExit(model.main())
