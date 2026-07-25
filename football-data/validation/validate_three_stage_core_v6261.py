#!/usr/bin/env python3
"""V6.26.1 validation for the three-stage 1X2 -> total -> score core.

This deliberately reuses the already-audited strict-daily-PIT retrospective market benchmark
from V6.19.1, but swaps the reconciliation implementation to the new executable V6.26 core.
Historical market rows in this benchmark do not have original quote timestamps, therefore this
script can validate architecture/invariants and reject a bad design, but can NEVER promote it.

Asian handicap is absent from the optimization target by construction. It is neither a hard
constraint nor a promotion metric in this validation.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import three_stage_core_v6260 as core  # noqa: E402
import validate_architecture_order_v6190 as arch  # noqa: E402
import validate_decoupled_1x2_total_fusion_v6191 as benchmark  # noqa: E402
from football_v460_engine import load_config  # noqa: E402

OUT = ROOT / "manifests" / "v6_three_stage_core_v6261_status.json"


def _synthetic_invariant_test() -> dict[str, Any]:
    prior = []
    weights = {
        (0, 0): 0.08, (1, 0): 0.13, (0, 1): 0.09, (1, 1): 0.15,
        (2, 0): 0.11, (0, 2): 0.07, (2, 1): 0.12, (1, 2): 0.09,
        (2, 2): 0.07, (3, 0): 0.04, (0, 3): 0.025, (3, 1): 0.025,
    }
    z = sum(weights.values())
    for (h, a), p in weights.items():
        prior.append({"home_goals": h, "away_goals": a, "probability": p / z})
    # The synthetic prior has support only for 0..4 total; zero-target unsupported buckets are legal.
    target_one = [0.52, 0.27, 0.21]
    target_total = [0.08, 0.22, 0.29, 0.24, 0.17, 0.0, 0.0, 0.0]
    matrix, audit = core.reconcile(prior, target_one, target_total)
    one = core.one_x_two_vector(matrix)
    total = core.total_goals_vector(matrix)
    max_one = max(abs(a - b) for a, b in zip(one, target_one))
    max_total = max(abs(a - b) for a, b in zip(total, target_total))
    mass_resid = abs(sum(float(c["probability"]) for c in matrix) - 1.0)
    return {
        "passed": bool(audit.get("converged")) and max(max_one, max_total, mass_resid) <= 2e-10,
        "audit": audit,
        "max_1x2_residual": max_one,
        "max_total_residual": max_total,
        "probability_sum_residual": mass_resid,
        "asian_handicap_hard_constraint": False,
    }


def _run_historical_structure_benchmark() -> dict[str, Any]:
    cfg = load_config()
    original = arch.reconcile
    arch.reconcile = core.reconcile
    try:
        season_results: dict[str, Any] = {}
        meta: dict[str, Any] = {}
        all_rows: list[dict[str, Any]] = []
        attempted = converged = 0
        max_one = max_total = 0.0
        for season in benchmark.SEASONS:
            season_rows: list[dict[str, Any]] = []
            meta[season] = {}
            for cid in benchmark.COMPS:
                rows, info = benchmark.eval_comp_season(cid, season, cfg)
                season_rows.extend(rows)
                meta[season][cid] = info
                attempted += int(info.get("attempted") or 0)
                converged += int(info.get("new_converged") or 0)
                max_one = max(max_one, float(info.get("max_1x2_constraint_residual") or 0.0))
                max_total = max(max_total, float(info.get("max_total_constraint_residual") or 0.0))
            season_results[season] = benchmark.summarize(season_rows)
            all_rows.extend(season_rows)
        aggregate = benchmark.summarize(all_rows)
    finally:
        arch.reconcile = original

    if not all_rows:
        architecture_gate = False
        reasons = ["NO_BENCHMARK_ROWS"]
    else:
        reasons = []
        checks = {
            "1x2_brier_nonworse": aggregate["new_1x2_brier"] <= aggregate["formal_1x2_brier"] + 1e-12,
            "1x2_logloss_nonworse": aggregate["new_1x2_logloss"] <= aggregate["formal_1x2_logloss"] + 1e-12,
            "total_rps_nonworse": aggregate["new_total_rps"] <= aggregate["formal_total_rps"] + 1e-12,
            "score_top3_nonworse": aggregate["new_score_top3"] + 1e-12 >= aggregate["formal_score_top3"],
            "all_reconciliations_converged": attempted > 0 and converged == attempted,
            "1x2_constraint_residual": max_one <= 2e-10,
            "total_constraint_residual": max_total <= 2e-10,
        }
        reasons = [name for name, passed in checks.items() if not passed]
        architecture_gate = not reasons

    return {
        "classification": "RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "attempted": attempted,
        "converged": converged,
        "convergence_rate": converged / attempted if attempted else None,
        "max_1x2_constraint_residual": max_one,
        "max_total_constraint_residual": max_total,
        "aggregate": aggregate,
        "season_results": season_results,
        "meta": meta,
        "architecture_gate_passed": architecture_gate,
        "architecture_gate_failures": reasons,
    }


def main() -> int:
    synthetic = _synthetic_invariant_test()
    historical = _run_historical_structure_benchmark()
    passed = bool(synthetic["passed"]) and bool(historical["architecture_gate_passed"])
    payload = {
        "schema_version": "V6.26.1-three-stage-core-validation-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_RESEARCH_ARCHITECTURE" if passed else "FAIL_RESEARCH_ARCHITECTURE",
        "formal_current_version": "V5.0.1",
        "formal_current_unchanged": True,
        "formal_weight": 0,
        "design": {
            "priority": ["1X2", "TOTAL_GOALS_0_7PLUS", "EXACT_SCORE"],
            "score_is_reconciliation_last": True,
            "asian_handicap_primary_target": False,
            "asian_handicap_probability_mutation": False,
            "asian_handicap_role": "auxiliary/derived only",
            "module_gate_separated_from_system_gate": True,
            "random100_is_diagnostic_only": True,
        },
        "synthetic_invariant_test": synthetic,
        "historical_structure_benchmark": historical,
        "promotion": {
            "eligible": False,
            "reason": "historical market benchmark lacks original quote timestamps; fresh prospective PIT evidence is mandatory",
            "required_next": [
                "fresh synchronized PIT 1X2 and O/U evidence",
                "strict chronological module-gate validation for 1X2 head",
                "strict chronological module-gate validation for total head",
                "final-matrix system gate",
                "independent prospective frozen sample before CURRENT upgrade"
            ]
        },
        "governance": {
            "no_current_mutation": True,
            "no_formal_weight_change": True,
            "no_manual_probability_adjustment": True,
            "no_AH_hard_constraint": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "synthetic": synthetic,
        "historical_aggregate": historical.get("aggregate"),
        "historical_gate": historical.get("architecture_gate_passed"),
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
