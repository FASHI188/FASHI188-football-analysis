#!/usr/bin/env python3
"""Train the remaining 13 V4.7 competition-specific challenger artifacts.

This complements the four priority domains already trained by
train_priority_challengers_v470.py.  Every competition is fitted independently;
no training rows, parameters, calibrators or challenger weights are shared.

The formal objective of this run is to screen the CURRENT-registered D|T
conditional structural challenger across the remaining domains.  The total-tail
result is retained as research evidence only and MUST remain formal_weight=0
because total_tail_v470 is not a registered formal V4.7 challenger in CURRENT.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
VALIDATION_DIR = ROOT_DIR / "validation"
for item in (str(ENGINE_DIR), str(VALIDATION_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

from football_v460_engine import load_config
from platform_core import ROOT
from train_priority_challengers_v470 import train_competition

REMAINING_COMPETITIONS = (
    "ENG_PremierLeague",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
    "ESP_LaLiga",
    "POR_PrimeiraLiga",
    "NED_Eredivisie",
    "SUI_SuperLeague",
    "SCO_Premiership",
    "JPN_J1",
    "BRA_SerieA",
    "ARG_Primera",
    "UEFA_ChampionsLeague",
)
OUT = ROOT / "manifests" / "remaining_competition_challenger_training_v470_status.json"


def main() -> int:
    config = load_config()
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for competition_id in REMAINING_COMPETITIONS:
        try:
            artifact = train_competition(competition_id, config)
            # Preserve the research-only status of total-tail explicitly in this aggregate.
            reports[competition_id] = {
                "competition_id": competition_id,
                "target_live_season": artifact.get("target_live_season"),
                "outer_predictions": artifact.get("outer_predictions"),
                "outer_folds": artifact.get("outer_folds"),
                "conditional_allocation": artifact.get("conditional_allocation"),
                "total_tail_research_only": artifact.get("total_tail"),
                "artifact_path": f"football-data/models/challengers_v470/{competition_id}/priority_v470.json",
                "formal_weight": 0,
            }
        except Exception as exc:
            failures[competition_id] = str(exc)
            reports[competition_id] = {
                "competition_id": competition_id,
                "status": "失败",
                "reason": str(exc),
                "formal_weight": 0,
            }

    payload = {
        "schema_version": "V4.7.0-remaining-competition-challenger-training-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "competition_count_requested": len(REMAINING_COMPETITIONS),
        "competition_count_built": len(REMAINING_COMPETITIONS) - len(failures),
        "competition_count_failed": len(failures),
        "formal_weight_change": False,
        "automatic_promotion": False,
        "training_policy": "strict competition-specific nested chronological OOF; no cross-competition rows, parameters, calibrators or weights",
        "formal_screening_target": "conditional_allocation_v470 only",
        "total_tail_policy": "research_only_formal_weight_0_until_future_complete_CURRENT_upgrade",
        "reports": reports,
        "failures": failures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "failures": failures,
        "conditional_summary": {
            cid: ((rep.get("conditional_allocation") or {}).get("status"))
            for cid, rep in reports.items()
        },
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
