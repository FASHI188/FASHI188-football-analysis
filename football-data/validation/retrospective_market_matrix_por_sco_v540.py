#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import retrospective_market_matrix_projection_v530 as base

base.DOMAINS = ["POR_PrimeiraLiga", "SCO_Premiership"]
base.OU_COORDINATION_DOMAINS = set()
base.OUT = ROOT / "manifests" / "retrospective_market_matrix_por_sco_v540_status.json"
base.SEED = 5402026

if __name__ == "__main__":
    raise SystemExit(base.main())
