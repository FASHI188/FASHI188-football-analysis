#!/usr/bin/env python3
"""Verify closure outputs without forcing either legal audit decision.

This compatibility step is intentionally non-transforming. It checks that the decision,
candidate list, preregistration and independently discovered research-asset coverage agree.
It does not train, score, or rewrite the audit conclusion.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import draw_signal_closure_engine_v502 as core


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_decision_evidence(decision: str, candidates: Sequence[Mapping[str, Any]], preregistration: Mapping[str, Any] | None) -> dict[str, Any]:
    if decision not in core.ALLOWED_DECISIONS:
        raise ValueError(f"unsupported decision: {decision}")
    expected = core.POSITIVE_DECISION if candidates else core.NEGATIVE_DECISION
    if decision != expected:
        raise ValueError(f"decision/evidence mismatch: decision={decision} expected={expected}")
    if candidates:
        if not isinstance(preregistration, Mapping):
            raise ValueError("positive decision requires preregistration")
        registered = list(preregistration.get("features", []))
        candidate_names = [str(row["field"]) for row in candidates]
        if registered != candidate_names:
            raise ValueError("preregistration features do not match candidates")
        if preregistration.get("status") != "PRE_REGISTERED_NOT_RUN":
            raise ValueError("positive preregistration status mismatch")
        if preregistration.get("run_authorized") is not False:
            raise ValueError("audit may not authorize training")
    elif preregistration is not None:
        raise ValueError("negative decision requires null preregistration")
    return {"decision": decision, "expected_decision": expected, "candidate_count": len(candidates), "consistent": True}


def verify_asset_coverage(coverage: Mapping[str, Any]) -> dict[str, Any]:
    expected = list(coverage.get("expected", []))
    matched = list(coverage.get("matched", []))
    missing = sorted(set(expected) - set(matched))
    extra = sorted(set(matched) - set(expected))
    claimed = bool(coverage.get("all_covered"))
    actual = not missing and not extra
    if claimed != actual:
        raise ValueError("asset coverage flag does not match expected/matched sets")
    if not actual:
        raise ValueError(f"research asset coverage incomplete: missing={missing[:10]} extra={extra[:10]}")
    return {"expected_count": len(expected), "matched_count": len(matched), "missing": missing, "extra": extra, "all_covered": actual}


def verify_closure_outputs(closure_dir: Path) -> dict[str, Any]:
    audit = _load(closure_dir / "closure_audit.json")
    feature = _load(closure_dir / "feature_difference.json")
    decision_obj = _load(closure_dir / "decision.json")
    complete = _load(closure_dir / "complete_research_file_ledger.json")
    metadata = _load(closure_dir / "metadata.json")

    candidates = feature.get("EXISTING_PIT_SAFE_UNTESTED_FEATURES", [])
    decision_check = verify_decision_evidence(str(audit.get("decision")), candidates, audit.get("preregistration"))
    if decision_obj.get("decision") != audit.get("decision") or metadata.get("decision") != audit.get("decision"):
        raise ValueError("decision differs across audit/decision/metadata")
    coverage = audit.get("research_asset_coverage")
    if not isinstance(coverage, Mapping):
        raise ValueError("research_asset_coverage missing")
    coverage_check = verify_asset_coverage(coverage)
    if complete.get("coverage") != coverage:
        raise ValueError("complete ledger coverage differs from audit coverage")
    if int(complete.get("count", -1)) != len(complete.get("rows", [])):
        raise ValueError("complete ledger count mismatch")
    if any(row.get("formal_weight") != 0 for row in complete.get("rows", [])):
        raise ValueError("nonzero formal weight in research ledger")
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
    return {"status": "PASS", "decision_check": decision_check, "asset_coverage_check": coverage_check, "near_miss_count": len(feature.get("UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED", [])), "formal_weight": 0, "model_training": 0, "provider_network_used": False}


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
