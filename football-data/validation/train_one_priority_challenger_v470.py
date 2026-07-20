#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from football_v460_engine import load_config
from platform_core import ROOT
from train_priority_challengers_v470 import train_competition

ALLOWED = {
    "KOR_KLeague1",
    "NOR_Eliteserien",
    "SWE_Allsvenskan",
    "USA_MLS",
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ALLOWED:
        raise SystemExit("usage: train_one_priority_challenger_v470.py <competition_id>")
    competition_id = sys.argv[1]
    try:
        artifact = train_competition(competition_id, load_config())
        receipt = {
            "schema_version": "V4.7.0-priority-challenger-single-receipt",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "competition_id": competition_id,
            "status": "PASS",
            "formal_weight_change": False,
            "automatic_promotion": False,
            "artifact": artifact,
        }
    except Exception as exc:
        receipt = {
            "schema_version": "V4.7.0-priority-challenger-single-receipt",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "competition_id": competition_id,
            "status": "FAIL",
            "formal_weight_change": False,
            "automatic_promotion": False,
            "error": str(exc),
        }
    path = ROOT / "manifests" / f"priority_challenger_training_v470_{competition_id}.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
