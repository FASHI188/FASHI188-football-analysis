#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bayesian_dynamic_state_oof_v501_same_day_safe import validate_domain_same_day_safe
from platform_core import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v501"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    args = parser.parse_args()
    try:
        report = validate_domain_same_day_safe(args.competition)
    except Exception as exc:
        report = {
            "schema_version": "V5.0.1-bayesian-dynamic-state-oof-domain-r2",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": args.competition,
            "status": "FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
            "same_day_outcomes_withheld": True,
            "replaces_invalidated_v500_evidence": True,
            "invalidation_receipt": "manifests/bayesian_dynamic_state_v500_invalidation_status.json",
            "formal_weight": 0,
            "automatic_promotion": False,
            "probability_change": False,
            "outer_prediction_count": None,
            "evaluated_outer_season_count": None,
            "pooled_metrics": None,
            "paired_block_bootstrap": None,
            "checks": None,
            "handicap_target_status": None,
            "policy": "Fail-closed evidence receipt. Validation gates were not weakened and formal probabilities remain unchanged.",
        }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUT_DIR / f"{args.competition}.json", report)
    print(json.dumps({"competition_id": args.competition, "status": report["status"], "reason": report.get("reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
