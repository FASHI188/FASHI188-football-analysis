#!/usr/bin/env python3
"""V6.35.0 fixed reserve-500 staged 1X2 evaluation.

Contract
--------
- Reuse the already-built V6.32 pre-match feature panel; no new data collection.
- Freeze one random reserve pool of 500 2025/26 eligible matches with seed 635500.
- First 100 = Fast100 screen.
- Next 300 = confirmation only if Fast100 passes.
- Final 100 = sealed holdout; never scored in this script.
- No confidence filtering, league dropping, seed replacement, or test-driven retuning.
- Candidate is the frozen V6.32 CatBoost configuration selected only on historical validation.
- Fast100 pass requires >= market +3pp Top1 and LogLoss/RPS no worse than market by >0.01.
- Research only; formal_weight=0; CURRENT unchanged.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402

OUT = ROOT / "manifests" / "v6_reserve500_staged_1x2_v6350_status.json"
SEED = 635500
RESERVE = 500
FAST_N = 100
CONFIRM_N = 300
SEALED_N = 100
TEST_SEASON = "2025/26"

# Frozen from V6.32 historical validation. Never selected on this reserve pool.
DEPTH = 4
L2 = 30.0
DRAW_WEIGHT = 1.3
MARKET_BLEND = 0.50


def gate(candidate: dict, market: dict) -> dict:
    top1_uplift_pp = (float(candidate["top1"]) - float(market["top1"])) * 100.0
    return {
        "required_market_top1_uplift_pp": 3.0,
        "top1_uplift_pp": top1_uplift_pp,
        "top1_pass": top1_uplift_pp >= 3.0 - 1e-12,
        "logloss_guard": float(candidate["logloss"]) <= float(market["logloss"]) + 0.01,
        "rps_guard": float(candidate["rps"]) <= float(market["rps"]) + 0.01,
        "passed": bool(
            top1_uplift_pp >= 3.0 - 1e-12
            and float(candidate["logloss"]) <= float(market["logloss"]) + 0.01
            and float(candidate["rps"]) <= float(market["rps"]) + 0.01
        ),
    }


def slim_match(r: dict) -> dict:
    return {
        "competition_id": r["competition_id"],
        "date": r["date"],
        "home_team": r["home_team"],
        "away_team": r["away_team"],
    }


def main() -> int:
    rows, data_audit, feature_names = v632._build_rows()
    train = [r for r in rows if r["season"] in v632.TRAIN_SEASONS]
    test = [dict(r) for r in rows if r["season"] == TEST_SEASON]
    if len(test) < RESERVE:
        raise RuntimeError(f"insufficient test rows for reserve500: {len(test)}")

    model = v632._fit(train, DEPTH, L2, DRAW_WEIGHT)
    v632._apply(model, test, MARKET_BLEND, "candidate")

    ordered = sorted(test, key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))
    random.Random(SEED).shuffle(ordered)
    reserve = ordered[:RESERVE]
    fast100 = reserve[:FAST_N]
    confirm300 = reserve[FAST_N:FAST_N + CONFIRM_N]
    sealed100 = reserve[FAST_N + CONFIRM_N:]
    if len(fast100) != 100 or len(confirm300) != 300 or len(sealed100) != 100:
        raise RuntimeError("reserve partition mismatch")

    fast_metrics = {
        "market": v632._metrics(fast100, "market"),
        "formal": v632._metrics(fast100, "formal"),
        "candidate": v632._metrics(fast100, "candidate"),
    }
    fast_gate = gate(fast_metrics["candidate"], fast_metrics["market"])

    confirm = None
    if fast_gate["passed"]:
        confirm_metrics = {
            "market": v632._metrics(confirm300, "market"),
            "formal": v632._metrics(confirm300, "formal"),
            "candidate": v632._metrics(confirm300, "candidate"),
        }
        confirm = {
            "metrics": confirm_metrics,
            "candidate_vs_market_top1_pp": (confirm_metrics["candidate"]["top1"] - confirm_metrics["market"]["top1"]) * 100.0,
            "candidate_top1_at_least_60": bool(confirm_metrics["candidate"]["top1"] >= 0.60 - 1e-12),
            "candidate_top1_at_least_65": bool(confirm_metrics["candidate"]["top1"] >= 0.65 - 1e-12),
        }

    payload = {
        "schema_version": "V6.35.0-reserve500-staged-1x2-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_FIXED_RESERVE500_STAGED_NO_PROMOTION",
        "reserve_contract": {
            "test_season": TEST_SEASON,
            "seed": SEED,
            "eligible_population": len(test),
            "reserve_count": RESERVE,
            "fast100_count": FAST_N,
            "confirm300_count": CONFIRM_N,
            "sealed100_count": SEALED_N,
            "all_fast100_scored": True,
            "confirm300_scored_only_if_fast_gate_passes": True,
            "sealed100_scored": False,
            "confidence_filtering": False,
            "league_dropping": False,
            "seed_replacement": False,
            "test_parameter_retuning": False,
        },
        "candidate": {
            "source": "V6.32 frozen CatBoost shared-feature challenger",
            "feature_count": len(feature_names),
            "depth": DEPTH,
            "l2_leaf_reg": L2,
            "draw_weight": DRAW_WEIGHT,
            "market_blend": MARKET_BLEND,
        },
        "data_audit": data_audit,
        "fast100": {
            "metrics": fast_metrics,
            "gate": fast_gate,
            "decision": "ADVANCE_TO_CONFIRM300" if fast_gate["passed"] else "STOP_BEFORE_CONFIRM300",
        },
        "confirm300": confirm,
        "sealed100": {
            "status": "SEALED_NOT_SCORED",
            "match_keys_sha_material": [slim_match(r) for r in sealed100],
        },
        "decision": (
            "FAST100_PASSED_CONFIRM300_EXECUTED"
            if fast_gate["passed"]
            else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
        ),
        "governance": {
            "research_only": True,
            "current_unchanged": True,
            "no_automatic_promotion": True,
            "do_not_tune_on_fast100": True,
            "do_not_open_sealed100": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "fast100_market_top1": fast_metrics["market"]["top1"],
        "fast100_candidate_top1": fast_metrics["candidate"]["top1"],
        "fast_gate_passed": fast_gate["passed"],
        "confirm300_executed": confirm is not None,
        "confirm300_candidate_top1": None if confirm is None else confirm["metrics"]["candidate"]["top1"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
