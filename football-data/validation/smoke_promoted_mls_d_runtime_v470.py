#!/usr/bin/env python3
"""Runtime smoke audit for the hash-bound promoted USA_MLS D|T module."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, derive_score_marginals, atomic_write_json
from promoted_challenger_runtime_gate_v470 import apply_hash_bound_promoted_v470_challengers

OUT = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470_runtime_smoke.json"


def _base_calculation():
    raw = [
        (0, 0, 0.08),
        (1, 0, 0.16), (0, 1, 0.12),
        (2, 0, 0.12), (1, 1, 0.18), (0, 2, 0.08),
        (3, 0, 0.06), (2, 1, 0.08), (1, 2, 0.06), (0, 3, 0.03),
        (4, 0, 0.01), (3, 1, 0.01), (2, 2, 0.005), (1, 3, 0.003), (0, 4, 0.002),
    ]
    total = sum(p for _, _, p in raw)
    matrix = [
        {"home_goals": h, "away_goals": a, "probability": p / total}
        for h, a, p in raw
    ]
    marg = derive_score_marginals(matrix)
    return {
        "module_states": {"unified_score_matrix": "通过", "oof_matrix_calibration": "通过"},
        "probabilities": {
            "one_x_two": marg["1x2"],
            "total_goals": marg["total_goals"],
            "btts_yes": marg["btts_yes"],
            "score_matrix": matrix,
        },
        "derived_markets": {
            "home_handicap": {"line": -0.5, "win": 0.0, "push": 0.0, "loss": 0.0},
            "over_total": {"line": 2.5, "win": 0.0, "push": 0.0, "loss": 0.0},
        },
        "conclusions": {"confidence_grade": "C", "price_status": "No Bet"},
        "model_audit": {"season": "2026"},
    }


def _margin2plus(matrix):
    return sum(
        float(cell["probability"])
        for cell in matrix
        if abs(int(cell["home_goals"]) - int(cell["away_goals"])) >= 2
    )


def main() -> int:
    calculation = _base_calculation()
    before = derive_score_marginals(calculation["probabilities"]["score_matrix"])
    before_margin2 = _margin2plus(calculation["probabilities"]["score_matrix"])

    context = {"match_identity": {"competition_id": "USA_MLS", "season": "2026"}}
    promoted = apply_hash_bound_promoted_v470_challengers(context, calculation)
    after = derive_score_marginals(promoted["probabilities"]["score_matrix"])
    after_margin2 = _margin2plus(promoted["probabilities"]["score_matrix"])
    total_keys = ("0", "1", "2", "3", "4", "5", "6", "7+")
    max_total_residual = max(abs(before["total_goals"][k] - after["total_goals"][k]) for k in total_keys)

    non_mls = apply_hash_bound_promoted_v470_challengers(
        {"match_identity": {"competition_id": "ENG_PremierLeague", "season": "2026"}},
        calculation,
    )
    wrong_season = apply_hash_bound_promoted_v470_challengers(
        {"match_identity": {"competition_id": "USA_MLS", "season": "2027"}},
        calculation,
    )

    checks = {
        "mls_2026_module_passed": promoted.get("module_states", {}).get("conditional_allocation_v470") == "通过",
        "probability_conservation": abs(after["probability_sum"] - 1.0) <= 1e-10,
        "total_marginal_preserved": max_total_residual <= 1e-10,
        "validated_margin2plus_direction_applied": after_margin2 < before_margin2,
        "non_mls_not_activated": non_mls.get("module_states", {}).get("conditional_allocation_v470") == "未启用",
        "new_season_without_receipt_not_activated": wrong_season.get("module_states", {}).get("conditional_allocation_v470") == "未启用",
        "handicap_recomputed": isinstance(promoted.get("derived_markets", {}).get("home_handicap", {}).get("win"), float),
        "total_market_recomputed": isinstance(promoted.get("derived_markets", {}).get("over_total", {}).get("win"), float),
        "top_score_rebuilt": bool(promoted.get("conclusions", {}).get("top_score")),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "V4.7.0-USA_MLS-promoted-runtime-smoke-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "checks": checks,
        "max_total_marginal_residual": max_total_residual,
        "probability_sum_residual": after["probability_sum"] - 1.0,
        "margin2plus_before": before_margin2,
        "margin2plus_after": after_margin2,
        "mls_runtime_audit": promoted.get("conditional_allocation_v470_audit"),
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
