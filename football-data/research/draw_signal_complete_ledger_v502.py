#!/usr/bin/env python3
"""Verify route-aware closure outputs without forcing a preselected decision."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
AUDIT = HERE.parent / "audit"
for path in (HERE, AUDIT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import draw_signal_closure_engine_v502_r4 as core


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_decision_evidence(
    decision: str,
    global_candidates: Sequence[Mapping[str, Any]],
    domain_candidates: Sequence[Mapping[str, Any]],
    route_closure: Mapping[str, Any],
    preregistration: Mapping[str, Any] | None,
    reconstructed_candidates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if decision not in core.ALLOWED_DECISIONS:
        raise ValueError(f"unsupported decision: {decision}")
    combined_domain = list(domain_candidates) + list(reconstructed_candidates)
    expected, expected_prereg = core.decide_and_preregister(global_candidates, combined_domain, route_closure)
    if decision != expected:
        raise ValueError(f"decision/evidence mismatch: decision={decision} expected={expected}")
    if decision == core.POSITIVE_DECISION:
        if not isinstance(preregistration, Mapping):
            raise ValueError("positive decision requires preregistration")
        if preregistration.get("status") != "PRE_REGISTERED_NOT_RUN":
            raise ValueError("positive preregistration status mismatch")
        if preregistration.get("run_authorized") is not False:
            raise ValueError("audit may not authorize training")
        if preregistration.get("formal_promotion_authorized") is not False:
            raise ValueError("audit may not authorize formal promotion")
        expected_features = expected_prereg.get("features") if expected_prereg else None
        if preregistration.get("features") != expected_features:
            raise ValueError("preregistration features/scopes do not match candidates")
    elif preregistration is not None:
        raise ValueError("non-positive decision requires null preregistration")
    if decision == core.NEGATIVE_DECISION and route_closure.get("blocks_exhausted"):
        raise ValueError("EXHAUSTED forbidden while experiment routes remain unresolved")
    if decision == core.UNRESOLVED_DECISION and not route_closure.get("blocks_exhausted"):
        raise ValueError("UNRESOLVED decision requires route blockers")
    return {
        "decision": decision,
        "expected_decision": expected,
        "global_candidate_count": len(global_candidates),
        "strict_domain_candidate_count": len(domain_candidates),
        "reconstructed_domain_candidate_count": len(reconstructed_candidates),
        "unresolved_route_count": len(route_closure.get("unresolved", [])),
        "consistent": True,
    }


def verify_asset_coverage(coverage: Mapping[str, Any], actual_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    required = ("missing", "extra", "inclusion_mismatch", "parse_failures", "all_covered")
    if any(key not in coverage for key in required):
        raise ValueError("asset coverage fields missing")
    actual = not any(coverage.get(key) for key in ("missing", "extra", "inclusion_mismatch", "parse_failures"))
    if bool(coverage.get("all_covered")) != actual:
        raise ValueError("asset coverage flag inconsistent")
    if not actual:
        raise ValueError(
            "research asset coverage incomplete: "
            f"missing={coverage.get('missing', [])[:5]} extra={coverage.get('extra', [])[:5]} "
            f"mismatch={coverage.get('inclusion_mismatch', [])[:5]} parse={coverage.get('parse_failures', [])[:5]}"
        )
    for row in actual_rows:
        included = bool(row.get("included"))
        if included and not row.get("inclusion_reason"):
            raise ValueError(f"included asset lacks reason: {row.get('path')}")
        if not included and not row.get("exclusion_reason"):
            raise ValueError(f"excluded asset lacks reason: {row.get('path')}")
    return {
        "expected_path_count": coverage.get("expected_path_count"),
        "actual_path_count": coverage.get("actual_path_count"),
        "expected_included_count": coverage.get("expected_included_count"),
        "actual_included_count": coverage.get("actual_included_count"),
        "all_covered": True,
    }


def verify_route_closure(route_closure: Mapping[str, Any], experiment_count: int) -> dict[str, Any]:
    rows = list(route_closure.get("rows", []))
    if len(rows) != experiment_count:
        raise ValueError("route closure row count mismatch")
    illegal = [row for row in rows if row.get("closure_state") not in core.ROUTE_STATES]
    if illegal:
        raise ValueError("illegal route closure state")
    unresolved = [row for row in rows if row.get("closure_state") == "UNRESOLVED"]
    if route_closure.get("unresolved") != unresolved:
        raise ValueError("unresolved route summary mismatch")
    blocks = bool(unresolved or route_closure.get("candidate_improvements") or route_closure.get("missing_result_evidence"))
    if bool(route_closure.get("blocks_exhausted")) != blocks:
        raise ValueError("route closure blocker flag mismatch")
    return {
        "route_count": len(rows),
        "unresolved_count": len(unresolved),
        "candidate_improvement_count": len(route_closure.get("candidate_improvements", [])),
        "missing_result_count": len(route_closure.get("missing_result_evidence", [])),
        "blocks_exhausted": blocks,
    }


def verify_closure_outputs(closure_dir: Path) -> dict[str, Any]:
    audit = _load(closure_dir / "closure_audit.json")
    feature = _load(closure_dir / "feature_difference.json")
    decision_obj = _load(closure_dir / "decision.json")
    complete = _load(closure_dir / "complete_research_file_ledger.json")
    metadata = _load(closure_dir / "metadata.json")

    route_check = verify_route_closure(audit.get("experiment_route_closure", {}), int(audit.get("experiment_count", -1)))
    global_candidates = feature.get("EXISTING_PIT_SAFE_UNTESTED_FEATURES", [])
    strict_domain_candidates = feature.get("DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES", [])
    reconstructed_candidates = feature.get("DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES", [])
    decision_check = verify_decision_evidence(
        str(audit.get("decision")),
        global_candidates,
        strict_domain_candidates,
        audit.get("experiment_route_closure", {}),
        audit.get("preregistration"),
        reconstructed_candidates,
    )
    if decision_obj.get("decision") != audit.get("decision") or metadata.get("decision") != audit.get("decision"):
        raise ValueError("decision differs across audit/decision/metadata")

    coverage = audit.get("research_asset_coverage")
    if not isinstance(coverage, Mapping):
        raise ValueError("research_asset_coverage missing")
    actual_rows = list(complete.get("rows", []))
    coverage_check = verify_asset_coverage(coverage, actual_rows)
    if complete.get("coverage") != coverage:
        raise ValueError("complete ledger coverage differs from audit coverage")
    if int(complete.get("count", -1)) != len(actual_rows):
        raise ValueError("complete ledger count mismatch")
    if any(row.get("formal_weight") != 0 for row in actual_rows):
        raise ValueError("nonzero formal weight in research ledger")

    provenance = audit.get("research_asset_registry_provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("registry provenance missing")
    if provenance.get("source_index_tree_sha256") != core.SOURCE_INDEX_TREE_SHA256:
        raise ValueError("registry source index SHA mismatch")
    if provenance.get("source_index_tree_sha256_verified") is not True:
        raise ValueError("registry source index SHA not verified")
    if provenance.get("source_head_present") is not False:
        raise ValueError("legacy source_head provenance remains active")

    market_aliases = {"ahh", "avgaha", "avgahh", "b365aha", "b365ahh", "b36ca", "bfeaha", "bfeahh", "maxaha", "maxahh", "paha", "pahh"}
    rows_by_name = {str(row.get("field", "")).lower(): row for row in feature.get("fields", [])}
    missing_aliases = sorted(market_aliases - set(rows_by_name))
    if missing_aliases:
        raise ValueError(f"expected market aliases missing from field audit: {missing_aliases}")
    bad_aliases = sorted(
        field for field in market_aliases
        if rows_by_name[field].get("classification") != "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN"
    )
    if bad_aliases:
        raise ValueError(f"Asian/market aliases misclassified: {bad_aliases}")

    round_rows = [row for row in reconstructed_candidates if str(row.get("field", "")).lower() == "round"]
    if len(round_rows) != 1:
        raise ValueError("round must appear exactly once as reconstructed research candidate")
    round_row = round_rows[0]
    if round_row.get("classification") != core.RECONSTRUCTED_CLASSIFICATION:
        raise ValueError("round classification is not reconstructed research-only")
    if round_row.get("qualifies_domain_specific_pit_safe_untested") is not False:
        raise ValueError("reconstructed round may not be labeled strict PIT candidate")
    if round_row.get("qualifies_domain_specific_reconstructed_research_candidate") is not True:
        raise ValueError("reconstructed round candidate gate missing")
    if round_row.get("forward_pit_proof_required") is not True:
        raise ValueError("round must require forward PIT proof")

    prereg = audit.get("preregistration")
    if not isinstance(prereg, Mapping):
        raise ValueError("reconstructed candidate requires non-authorized preregistration")
    round_features = [item for item in prereg.get("features", []) if str(item.get("field", "")).lower() == "round"]
    if len(round_features) != 1 or round_features[0].get("scope") != core.RECONSTRUCTED_SCOPE:
        raise ValueError("round preregistration scope mismatch")
    if prereg.get("run_authorized") is not False or prereg.get("formal_promotion_authorized") is not False:
        raise ValueError("reconstructed preregistration must not authorize run or promotion")
    if prereg.get("forward_pit_proof_required") is not True:
        raise ValueError("reconstructed preregistration must require forward PIT proof")
    if prereg.get("holdout_status") != "NOT_YET_PROVEN_UNTOUCHED":
        raise ValueError("holdout status mismatch")

    for obj in (audit, metadata):
        if obj.get("formal_weight") != 0:
            raise ValueError("formal_weight must remain zero")
        if obj.get("provider_network_used") is not False:
            raise ValueError("provider network use is forbidden")
        if obj.get("external_request_attempts") != 0:
            raise ValueError("external requests must remain zero")
        if obj.get("api_football_key_accessed") is not False:
            raise ValueError("API key access is forbidden")
        if obj.get("model_training") != 0:
            raise ValueError("model training is forbidden")

    return {
        "status": "PASS",
        "decision_check": decision_check,
        "route_closure_check": route_check,
        "asset_coverage_check": coverage_check,
        "registry_provenance": dict(provenance),
        "reconstructed_candidate_count": len(reconstructed_candidates),
        "near_miss_count": len(feature.get("UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED", [])),
        "formal_weight": 0,
        "model_training": 0,
        "provider_network_used": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--closure-dir", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = verify_closure_outputs(args.closure_dir)
    (args.closure_dir / "closure_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
