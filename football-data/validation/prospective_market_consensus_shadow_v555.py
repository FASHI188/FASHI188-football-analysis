#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_v554 import validate_consensus as validate_consensus_v554
from prospective_market_consensus_strict_v5519 import validate_consensus as validate_consensus_v5519
from prospective_market_matrix_por_v543 import project as project_por_1x2
from prospective_market_matrix_shadow_v531 import _devig as devig, project as project_dual

SELECTIVE_CFG = ROOT / "config" / "prospective_market_selective_challenger_v526.json"
MATRIX_REGISTRY = ROOT / "config" / "market_matrix_projection_final_registry_v531.json"


def validate_consensus(consensus: dict[str, Any]) -> dict[str, Any]:
    schema = str(consensus.get("schema_version") or "")
    if schema.startswith("V5.5.19-strict-three-surface-market-consensus"):
        result = validate_consensus_v5519(consensus)
        return {
            "passed": bool(result.get("passed")),
            "errors": list(result.get("errors") or []),
            "promotion_evidence_eligible": bool(result.get("promotion_evidence_eligible")),
            "validator": "V5.5.19_STRICT_THREE_SURFACE",
        }
    result = validate_consensus_v554(consensus)
    result["validator"] = "V5.5.4_BASE_CONSENSUS"
    return result


def evaluate_selective(consensus: dict[str, Any]) -> dict[str, Any]:
    validation = validate_consensus(consensus)
    cfg = json.loads(SELECTIVE_CFG.read_text(encoding="utf-8"))
    cid = str(consensus.get("competition_id") or "")
    candidate = (cfg.get("candidate_domains") or {}).get(cid)
    result = {
        "schema_version": "V5.5.5-consensus-selective-shadow-r2",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": cid,
        "consensus_validation_passed": bool(validation.get("passed")),
        "consensus_errors": validation.get("errors") or [],
        "consensus_validator": validation.get("validator"),
        "market_input_kind": "INDEPENDENT_PROVIDER_CONSENSUS",
        "promotion_evidence_eligible": bool(consensus.get("promotion_evidence_eligible")),
        "provider_count": consensus.get("provider_count"),
        "consensus_sha256": consensus.get("consensus_sha256"),
        "registered_candidate_domain": bool(candidate),
        "shadow_status": "NO_SHADOW_DIRECTION",
        "formal_direction_override": False,
        "probability_mutation": False,
        "formal_weight": 0,
    }
    if not validation.get("passed"):
        result["shadow_status"] = "CONSENSUS_INVALID_FAIL_CLOSED"
        return result
    if not consensus.get("promotion_evidence_eligible"):
        result["shadow_status"] = "CONSENSUS_RESEARCH_ONLY_NOT_PROMOTION_ELIGIBLE"
        return result
    if not candidate:
        result["shadow_status"] = "DOMAIN_NOT_REGISTERED_FOR_MARKET_SELECTIVE_SHADOW"
        return result
    one = devig({k: float(consensus["one_x_two"][k]) for k in ("home", "draw", "away")})
    ordered = sorted(one.items(), key=lambda item: (item[1], item[0]), reverse=True)
    top1, top2 = ordered[0], ordered[1]
    gap = float(top1[1] - top2[1])
    threshold = float(candidate["gap_threshold"])
    result.update({
        "de_vigged_1x2": one,
        "market_top1": top1[0],
        "market_top1_probability": top1[1],
        "market_top2": top2[0],
        "market_top2_probability": top2[1],
        "market_top1_top2_gap": gap,
        "registered_gap_threshold": threshold,
        "timing_robust_point_gate": bool(candidate.get("timing_robust_point_gate", False)),
        "threshold_retuned": bool(candidate.get("threshold_retuned", False)),
    })
    if not result["timing_robust_point_gate"]:
        result["shadow_status"] = "TIMING_ROBUSTNESS_NOT_REGISTERED_FAIL_CLOSED"
    elif gap >= threshold:
        result["shadow_status"] = "SHADOW_MARKET_HIGH_CONFIDENCE_DIRECTION"
        result["shadow_direction"] = top1[0]
    else:
        result["shadow_status"] = "SHADOW_GATE_NOT_MET"
    return result


def evaluate_matrix(consensus: dict[str, Any], formal_matrix: list[dict[str, Any]]) -> dict[str, Any]:
    validation = validate_consensus(consensus)
    registry = json.loads(MATRIX_REGISTRY.read_text(encoding="utf-8"))
    cid = str(consensus.get("competition_id") or "")
    primary = (registry.get("primary_prospective_architecture_candidates") or {}).get(cid)
    separate = (registry.get("separate_question_time_candidates") or {}).get(cid)
    cfg = primary or separate
    result = {
        "schema_version": "V5.5.5-consensus-market-matrix-shadow-r3",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": cid,
        "consensus_validation_passed": bool(validation.get("passed")),
        "consensus_errors": validation.get("errors") or [],
        "consensus_validator": validation.get("validator"),
        "market_input_kind": "INDEPENDENT_PROVIDER_CONSENSUS",
        "promotion_evidence_eligible": bool(consensus.get("promotion_evidence_eligible")),
        "provider_count": consensus.get("provider_count"),
        "consensus_sha256": consensus.get("consensus_sha256"),
        "registered_matrix_candidate": bool(cfg),
        "shadow_status": "NO_SHADOW_MATRIX",
        "formal_matrix_override": False,
        "formal_probability_mutation": False,
        "formal_weight": 0,
        "candidate_matrix": None,
    }
    if not validation.get("passed"):
        result["shadow_status"] = "CONSENSUS_INVALID_FAIL_CLOSED"
        return result
    if not consensus.get("promotion_evidence_eligible"):
        result["shadow_status"] = "CONSENSUS_RESEARCH_ONLY_NOT_PROMOTION_ELIGIBLE"
        return result
    if not cfg:
        result["shadow_status"] = "DOMAIN_NOT_REGISTERED_MATRIX_CANDIDATE"
        return result

    one = devig({k: float(consensus["one_x_two"][k]) for k in ("home", "draw", "away")})
    profile = str(cfg.get("profile") or "")
    if cid == "POR_PrimeiraLiga":
        candidate, audit = project_por_1x2(formal_matrix, one)
        result.update({
            "shadow_status": "SHADOW_MARKET_MATRIX_READY",
            "frozen_profile": profile,
            "de_vigged_1x2_target": one,
            "audit": audit,
            "candidate_matrix": candidate,
        })
        return result

    eligibility = consensus.get("surface_consensus_eligibility") or {}
    ou25 = consensus.get("over_under_2_5")
    if not bool(eligibility.get("over_under_2_5")) or not isinstance(ou25, dict):
        result["shadow_status"] = "OU25_CONSENSUS_REQUIRED_FOR_FROZEN_PROFILE"
        result["observed_main_ou_consensus"] = consensus.get("over_under")
        result["observed_ou25_consensus"] = ou25
        return result
    if abs(float(ou25.get("line")) - 2.5) > 1e-9:
        result["shadow_status"] = "OU25_CONSENSUS_NOT_ELIGIBLE_FAIL_CLOSED"
        result["observed_ou25_consensus"] = ou25
        return result

    ou_prob = devig({k: float(ou25[k]) for k in ("over", "under")})
    candidate, audit = project_dual(formal_matrix, one, ou_prob)
    result.update({
        "shadow_status": "SHADOW_MARKET_MATRIX_READY",
        "frozen_profile": profile,
        "de_vigged_1x2_target": one,
        "de_vigged_ou25_target": ou_prob,
        "main_ou_consensus": consensus.get("over_under"),
        "fixed_ou25_consensus": ou25,
        "audit": audit,
        "candidate_matrix": candidate,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("selective", "matrix"))
    parser.add_argument("consensus")
    parser.add_argument("formal_matrix", nargs="?")
    parser.add_argument("--out")
    args = parser.parse_args()
    consensus = json.loads(Path(args.consensus).read_text(encoding="utf-8"))
    if args.mode == "selective":
        payload = evaluate_selective(consensus)
    else:
        if not args.formal_matrix:
            raise SystemExit("matrix mode requires formal_matrix")
        payload = evaluate_matrix(consensus, json.loads(Path(args.formal_matrix).read_text(encoding="utf-8")))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
