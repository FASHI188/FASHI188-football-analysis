#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import retrospective_ah_ou_ceiling_v528 as base

base.DOMAINS = ["POR_PrimeiraLiga", "SCO_Premiership"]
base.OUT = ROOT / "manifests" / "retrospective_ah_ou_por_sco_v537_status.json"
base.SEED = 5372026

if __name__ == "__main__":
    raise SystemExit(base.main())
