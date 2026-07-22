#!/usr/bin/env python3
"""V6.5.2 evidence-based research architecture registry.

This does NOT modify CURRENT. It freezes the research conclusions already demonstrated by
explicit historical/forward receipts so subsequent experiments cannot silently reintroduce
rejected modules or demote the stronger market baseline without new evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAN = ROOT / "manifests"
OUT = MAN / "v6_market_first_architecture_registry_v652_status.json"

SOURCES = {
    "pooled_baseline": "v6_sampled_17domain_pooled_gate_v625_r4_status.json",
    "error_atlas": "v6_error_atlas_v640_status.json",
    "hierarchical_adaptive": "v6_hierarchical_adaptive_outcome_v641_status.json",
    "ordered_draw_band": "v6_ordered_draw_band_v642_status.json",
    "multimarket_direct": "v6_multimarket_draw_side_v643_status.json",
    "team_shock": "v6_team_shock_residual_v644_status.json",
    "multimarket_veto": "v6_multimarket_risk_veto_v646_status.json",
    "market_anchor": "v6_sampled_15domain_market_anchor_v647_r2_status.json",
    "market_selector": "v6_market_first_selector_v650_status.json",
    "market_forward_freeze": "v6_market_first_forward_freeze_v651.json",
    "market_forward_eval": "v6_market_first_forward_evaluation_v651_status.json",
}


def load(name: str) -> dict[str, Any]:
    path = MAN / name
    if not path.exists():
        raise RuntimeError(f"missing required receipt: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def sha(name: str) -> str:
    return hashlib.sha256((MAN / name).read_bytes()).hexdigest()


def main() -> int:
    x = {key: load(name) for key, name in SOURCES.items()}
    baseline = x["pooled_baseline"]["full_direction_metrics"]["combined_1700"]
    market = x["market_anchor"]["overall"]
    selector = x["market_selector"]
    hierarchy = x["hierarchical_adaptive"]
    draw = x["ordered_draw_band"]
    mm = x["multimarket_direct"]
    shock = x["team_shock"]
    veto = x["multimarket_veto"]

    checks = {
        "baseline_1700_is_exact_pooled": x["pooled_baseline"]["correction"]["v601_actual_architecture"] == "single pooled model across all 17 domains",
        "market_matches_1500": int(x["market_anchor"]["coverage"]["market_matched_count"]) == 1500,
        "market_beats_v6_same_rows": float(market["market_gain_pp_on_same_rows"]) > 0.0,
        "market_hybrid_beats_v6_1700": float(market["hybrid_gain_pp_all_1700"]) > 0.0,
        "hierarchy_has_positive_dev_gain": float(hierarchy["newer_accuracy_gain_pp"]) > 0.0,
        "hierarchy_probability_guard_passes": all(bool(v) for v in hierarchy["newer_proper_score_guard"].values()),
        "ordered_draw_rejected": not bool(draw.get("research_gate_passed")),
        "multimarket_direct_rejected": not bool(mm.get("research_gate_passed")),
        "team_shock_rejected": not bool(shock.get("research_gate_passed")),
        "multimarket_veto_no_increment": abs(float(veto.get("holdout_accuracy_delta_pp_vs_confidence_only") or 0.0)) < 1e-12,
        "market_selector_raw65_on_newer": bool(selector.get("primary_newer_raw65_met")),
        "market_selector_not_wilson65_yet": not bool(selector.get("primary_newer_wilson65_met")),
        "forward_market_epoch_frozen": x["market_forward_freeze"].get("status") == "FROZEN",
        "forward_market_integrity_pass": x["market_forward_eval"].get("status") == "PASS",
    }
    status = "PASS" if all(checks.values()) else "FAIL_EVIDENCE_CONSISTENCY"

    payload = {
        "schema_version": "V6.5.2-market-first-research-architecture-registry-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "evidence_checks": checks,
        "research_architecture": {
            "primary_1x2_probability": {
                "registration": "RESEARCH_CHAMPION",
                "method": "synchronized de-vigged 1X2 market when verifiable",
                "historical_evidence": {
                    "fixed_1700_v6_accuracy": baseline["accuracy"],
                    "market_matched_count": x["market_anchor"]["coverage"]["market_matched_count"],
                    "market_same_rows_accuracy": market["market_on_same_matched"]["accuracy"],
                    "v6_same_rows_accuracy": market["v601_on_market_matched"]["accuracy"],
                    "market_gain_pp_same_rows": market["market_gain_pp_on_same_rows"],
                    "hybrid_1700_accuracy": market["hybrid_market_else_v601_1700"]["accuracy"],
                },
            },
            "fallback_1x2": {
                "registration": "RESEARCH_FALLBACK",
                "method": "V6.4.1 hierarchical/domain/season adaptive layer",
                "reason": "positive development gain with Brier/RPS/LogLoss guard, but not superior to market primary",
            },
            "selective_execution": {
                "registration": "PRISTINE_FORWARD_CHALLENGE",
                "method": "market top1 confidence >=0.35, draws excluded",
                "historical_newer_accuracy": selector["primary_newer_test"]["accuracy"],
                "historical_newer_coverage": selector["primary_newer_test"]["coverage"],
                "historical_newer_wilson90_lower": selector["primary_newer_test"]["wilson90_lower"],
                "forward_epoch": "V6.5.1",
            },
            "context_residual_layer": {
                "registration": "PROSPECTIVE_RESEARCH_ONLY",
                "allowed_inputs": [
                    "pre-match player availability/injuries/suspensions",
                    "expected-lineup continuity from information available before freeze",
                    "manager change/tenure",
                    "competition/task/two-leg state",
                ],
                "constraint": "may adjust market only after independent PIT evidence; current actual lineup unavailable at betting freeze may not leak into historical features",
            },
            "diagnostic_only": [
                "AH/OU synchronized surfaces",
                "V6-market disagreement",
                "V6.4.0 error atlas",
            ],
            "rejected_for_probability_promotion_without_new_evidence": [
                "V6.4.2 ordered draw-band replacement",
                "V6.4.3 direct AH/OU/1X2 probability rewrite",
                "V6.4.4 result-surprise team shock residual",
                "V6.4.6 AH/OU risk veto beyond 1X2 confidence",
                "previous Understat xG residual challenger",
                "previous second-level correctness selector",
            ],
        },
        "source_receipts": {key: {"path": name, "sha256": sha(name)} for key, name in SOURCES.items()},
        "promotion_policy": {
            "historical_development_can_register_challengers": True,
            "historical_development_cannot_change_CURRENT": True,
            "fresh_forward_required_for_promotion": True,
            "automatic_promotion": False,
            "manual_review_required": True,
        },
        "governance": {
            "formal_version_remains_V5": True,
            "current_rule_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
