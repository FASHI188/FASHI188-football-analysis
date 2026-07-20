#!/usr/bin/env python3
"""Train one remaining V4.7 competition-specific challenger in isolation."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
VALIDATION_DIR = ROOT_DIR / "validation"
for item in (str(ENGINE_DIR), str(VALIDATION_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

from football_v460_engine import load_config
from platform_core import ROOT
from train_priority_challengers_v470 import train_competition
from train_remaining_competition_challengers_v470 import REMAINING_COMPETITIONS

STATUS_ROOT = ROOT / "manifests" / "remaining_competition_challengers_v470"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True, choices=REMAINING_COMPETITIONS)
    args = parser.parse_args()
    cid = args.competition
    STATUS_ROOT.mkdir(parents=True, exist_ok=True)
    target = STATUS_ROOT / f"{cid}.json"
    try:
        artifact = train_competition(cid, load_config())
        payload = {
            "schema_version": "V4.7.0-remaining-single-domain-training-r1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "PASS",
            "competition_id": cid,
            "target_live_season": artifact.get("target_live_season"),
            "outer_predictions": artifact.get("outer_predictions"),
            "outer_folds": artifact.get("outer_folds"),
            "conditional_allocation": artifact.get("conditional_allocation"),
            "total_tail_research_only": artifact.get("total_tail"),
            "artifact_path": f"models/challengers_v470/{cid}/priority_v470.json",
            "formal_weight": 0,
            "automatic_promotion": False,
            "policy": "competition-specific nested chronological OOF; total-tail remains research-only; no automatic promotion",
        }
        rc = 0
    except Exception as exc:
        payload = {
            "schema_version": "V4.7.0-remaining-single-domain-training-r1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "FAILED",
            "competition_id": cid,
            "reason": str(exc),
            "formal_weight": 0,
            "automatic_promotion": False,
        }
        rc = 1
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
