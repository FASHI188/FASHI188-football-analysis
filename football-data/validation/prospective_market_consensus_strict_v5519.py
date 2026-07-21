#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_v554 import build as build_base
from prospective_market_snapshot_v523 import canonical_sha256

OUT_ROOT = ROOT / "evidence" / "market_consensus_prospective"
SCHEMA = "V5.5.19-strict-three-surface-market-consensus-r1"


def build(rows: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base(rows)
    eligibility = payload.get("surface_consensus_eligibility") or {}
    required = {
        "one_x_two": bool(eligibility.get("one_x_two")),
        "asian_handicap": bool(eligibility.get("asian_handicap")),
        "over_under": bool(eligibility.get("over_under")),
    }
    missing = [name for name, ok in required.items() if not ok]
    payload["schema_version"] = SCHEMA
    payload["consensus_type"] = "INDEPENDENT_PROVIDER_CONSENSUS"
    payload["strict_three_surface_required_for_promotion"] = True
    payload["required_surface_consensus_eligibility"] = required
    payload["promotion_evidence_eligible"] = not missing
    payload["promotion_ineligibility_reasons"] = [f"NO_COMPARABLE_{name.upper()}_CONSENSUS" for name in missing]
    payload["governance_note"] = (
        "Two independent provider groups and <=300s synchronization are necessary but not sufficient for promotion. "
        "Promotion additionally requires comparable consensus on 1X2, Asian Handicap and Over/Under. "
        "Main-line mismatch may remain a valid research observation but cannot be promoted as full three-surface market evidence."
    )
    payload.pop("consensus_sha256", None)
    payload["consensus_sha256"] = canonical_sha256(payload)
    return payload


def validate_consensus(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA:
        errors.append("SCHEMA_MISMATCH")
    if payload.get("consensus_type") != "INDEPENDENT_PROVIDER_CONSENSUS":
        errors.append("CONSENSUS_TYPE_MISMATCH")
    try:
        provider_count = int(payload.get("provider_count") or 0)
    except Exception:
        provider_count = 0
    groups = list(payload.get("provider_groups") or [])
    if provider_count < 2:
        errors.append("INSUFFICIENT_INDEPENDENT_PROVIDERS")
    if len(groups) != provider_count or len(groups) != len(set(groups)):
        errors.append("PROVIDER_GROUP_INDEPENDENCE_FAIL")
    try:
        if float(payload.get("cross_provider_timestamp_spread_seconds")) > 300.0:
            errors.append("CONSENSUS_TIMESTAMP_SKEW_FAIL")
    except Exception:
        errors.append("CONSENSUS_TIMESTAMP_MISSING")

    required = payload.get("required_surface_consensus_eligibility") or {}
    missing = [name for name in ("one_x_two", "asian_handicap", "over_under") if not bool(required.get(name))]
    promotion = bool(payload.get("promotion_evidence_eligible"))
    if promotion != (not missing):
        errors.append("PROMOTION_ELIGIBILITY_INCONSISTENT")
    reasons = list(payload.get("promotion_ineligibility_reasons") or [])
    expected_reasons = [f"NO_COMPARABLE_{name.upper()}_CONSENSUS" for name in missing]
    if sorted(reasons) != sorted(expected_reasons):
        errors.append("PROMOTION_INELIGIBILITY_REASON_MISMATCH")

    expected_hash = str(payload.get("consensus_sha256") or "")
    unhashed = dict(payload)
    unhashed.pop("consensus_sha256", None)
    if expected_hash != canonical_sha256(unhashed):
        errors.append("CONSENSUS_HASH_MISMATCH")
    return {"passed": not errors, "errors": errors, "promotion_evidence_eligible": promotion}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", nargs="+")
    parser.add_argument("--out")
    parser.add_argument("--write-research-ineligible", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.snapshots]
    payload = build(rows)
    result = validate_consensus(payload)
    if not result["passed"]:
        raise ValueError(f"strict consensus validation failed: {result['errors']}")
    if not payload["promotion_evidence_eligible"] and not args.write_research_ineligible:
        print(json.dumps({
            "status": "STRICT_CONSENSUS_RESEARCH_ONLY_NOT_WRITTEN",
            "promotion_evidence_eligible": False,
            "reasons": payload["promotion_ineligibility_reasons"],
            "consensus_sha256": payload["consensus_sha256"],
        }, ensure_ascii=False, indent=2))
        return 0
    out = Path(args.out) if args.out else OUT_ROOT / (
        f"{payload['competition_id']}__{payload['home_team']}__{payload['away_team']}__"
        f"{payload['consensus_observed_at_utc'].replace(':', '').replace('+00:00', 'Z')}__n{payload['provider_count']}__strict.json"
    )
    if out.exists():
        raise FileExistsError(f"immutable strict consensus already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "VALID_STRICT_MARKET_CONSENSUS_WRITTEN",
        "path": str(out),
        "promotion_evidence_eligible": payload["promotion_evidence_eligible"],
        "consensus_sha256": payload["consensus_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
