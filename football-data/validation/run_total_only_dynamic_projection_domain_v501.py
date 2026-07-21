#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import validate_two_axis_dynamic_projection_v501 as validation
from platform_core import atomic_write_json, load_json

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v501_status.json"
OUT_DIR = ROOT / "manifests" / "total_only_dynamic_projection_v501"
TOTAL_ONLY_CONFIGS = tuple((scale, 0.0) for scale in validation.TOTAL_SCALES)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    args = parser.parse_args()
    competition_id = args.competition
    original_configs = validation.CONFIGS
    try:
        validation.CONFIGS = TOTAL_ONLY_CONFIGS
        adjudication = load_json(ADJUDICATION)
        item = (adjudication.get("adjudications") or {}).get(competition_id) or {}
        profile_id = str(item.get("frozen_shadow_profile") or "")
        if not profile_id:
            raise RuntimeError(f"frozen profile missing for {competition_id}")
        report = validation._domain(
            competition_id,
            profile_id,
            validation.common.SOURCE_CODES[competition_id],
        )
        report["schema_version"] = "V5.0.1-total-only-dynamic-projection-domain-r1"
        report["research_scope"] = "dynamic signal may alter P(T); dynamic share influence fixed at zero"
        report["share_scale_grid"] = [0.0]
    except Exception as exc:
        report = {
            "schema_version": "V5.0.1-total-only-dynamic-projection-domain-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": competition_id,
            "status": "FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
            "formal_weight": 0,
            "probability_change": False,
            "automatic_promotion": False,
            "formal_promotion_authorized": False,
            "same_day_outcomes_withheld": True,
            "research_scope": "dynamic signal may alter P(T); dynamic share influence fixed at zero",
            "policy": "Fail-closed research receipt; formal V5 probabilities remain unchanged.",
        }
    finally:
        validation.CONFIGS = original_configs

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUT_DIR / f"{competition_id}.json", report)
    print(json.dumps({"competition_id": competition_id, "status": report.get("status"), "reason": report.get("reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
