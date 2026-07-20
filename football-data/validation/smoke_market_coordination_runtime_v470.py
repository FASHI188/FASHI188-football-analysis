#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from market_coordination_runtime_basis_v470 import apply_market_coordination_runtime
from platform_core import derive_score_marginals, sha256_json

OUT = ROOT / "manifests" / "market_coordination_runtime_v470_smoke.json"


def _matrix():
    raw = []
    total = 0.0
    for h in range(5):
        for a in range(5):
            weight = 1.0 / (1.0 + h + a) + (0.08 if h == a else 0.0)
            raw.append({"home_goals": h, "away_goals": a, "probability": weight})
            total += weight
    for cell in raw:
        cell["probability"] /= total
    return raw


def _context(formal_apply: bool):
    return {
        "original_market_snapshot": {
            "observed_at_utc": "2026-07-20T12:00:00Z",
            "one_x_two": {"home": 2.40, "draw": 3.40, "away": 2.90},
            "asian_handicap": {"line": 0.0, "home": 1.90, "away": 1.96},
            "total_goals": {"line": 2.5, "over": 1.88, "under": 1.98},
        },
        "market_assessment": {
            "snapshot_complete_gate": True,
            "lomo_validation_status": "通过" if formal_apply else "不可用",
            "formal_market_coordination_gate": formal_apply,
        },
        "gates": {
            "market_coordination_candidate_may_run": True,
            "formal_market_coordination_may_apply": formal_apply,
        },
    }


def _calculation():
    matrix = _matrix()
    marginals = derive_score_marginals(matrix)
    return {
        "probabilities": {
            "score_matrix": matrix,
            "one_x_two": marginals["1x2"],
            "total_goals": marginals["total_goals"],
            "btts_yes": marginals["btts_yes"],
        },
        "derived_markets": {},
        "module_states": {"unified_score_matrix": "通过", "market_coordination": "未启用"},
        "conclusions": {},
    }


def _write(payload: dict) -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "PASS" else 2


def main() -> int:
    try:
        base = _calculation()
        prior_sha = sha256_json(base["probabilities"]["score_matrix"])

        candidate = apply_market_coordination_runtime(_context(False), copy.deepcopy(base))
        formal = apply_market_coordination_runtime(_context(True), copy.deepcopy(base))
        blocked_context = _context(False)
        blocked_context["gates"]["market_coordination_candidate_may_run"] = False
        blocked = apply_market_coordination_runtime(blocked_context, copy.deepcopy(base))

        audit = candidate.get("optimization_audit") or {}
        fit = audit.get("market_fit_diagnostics") or {}
        checks = {
            "candidate_optimization_converged": audit.get("converged") is True,
            "candidate_residual_lte_1e_6": float(audit.get("max_constraint_residual", 1.0)) <= 1e-6,
            "candidate_probability_sum_conserved": abs(float(audit.get("probability_sum", 0.0)) - 1.0) <= 1e-8,
            "nonredundant_constraint_basis_used": audit.get("constraint_basis") == "1X2_DRAW + AH_FAIR_SETTLEMENT + OU_FAIR_SETTLEMENT",
            "full_1x2_fit_diagnostics_reported": isinstance(fit.get("one_x_two_residuals"), dict),
            "candidate_without_lomo_is_partial": candidate.get("module_states", {}).get("market_coordination") == "部分通过",
            "candidate_without_lomo_does_not_mutate_formal_matrix": sha256_json(candidate["probabilities"]["score_matrix"]) == prior_sha,
            "candidate_summary_exists": isinstance(candidate.get("market_coordination_candidate"), dict),
            "formal_gate_allows_matrix_mutation": sha256_json(formal["probabilities"]["score_matrix"]) != prior_sha,
            "formal_gate_marks_coordination_passed": formal.get("module_states", {}).get("market_coordination") == "通过",
            "blocked_snapshot_does_not_create_optimization_audit": blocked.get("optimization_audit") is None,
            "blocked_snapshot_does_not_mutate_matrix": sha256_json(blocked["probabilities"]["score_matrix"]) == prior_sha,
        }
        status = "PASS" if all(checks.values()) else "FAIL"
        return _write({
            "schema_version": "V4.7.0-market-coordination-runtime-smoke-r3",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": status,
            "checks": checks,
            "candidate_audit": audit,
            "candidate_summary": candidate.get("market_coordination_candidate"),
            "formal_applied": (formal.get("optimization_audit") or {}).get("formal_applied"),
            "formal_weight_change": False,
            "production_lomo_receipt_created": False,
            "policy": "Smoke validates execution semantics only; it creates no production LOMO receipt and does not activate formal EV.",
        })
    except Exception as exc:
        return _write({
            "schema_version": "V4.7.0-market-coordination-runtime-smoke-r3",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL",
            "checks": {},
            "error": str(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
            "formal_weight_change": False,
            "production_lomo_receipt_created": False,
        })


if __name__ == "__main__":
    raise SystemExit(main())
