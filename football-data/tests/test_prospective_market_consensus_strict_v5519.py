#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_strict_v5519 import build, validate_consensus
from prospective_market_snapshot_v523 import canonical_sha256, validate as validate_snapshot

RECEIPT = ROOT / "manifests" / "prospective_market_consensus_strict_v5519_status.json"


def snapshot(provider_group: str, provider_name: str, observed: str, ah_line: float = 0.0, ou_line: float = 2.5) -> dict:
    row = {
        "competition_id": "STRICT_GATE_TEST",
        "season": "2026",
        "home_team": "Home FC",
        "away_team": "Away FC",
        "kickoff_utc": "2026-08-01T18:00:00+00:00",
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": observed,
        "accessed_at_utc": observed,
        "source_observed_at_utc": observed,
        "surface_observed_at_utc": {
            "one_x_two": observed,
            "asian_handicap": observed,
            "over_under": observed,
        },
        "source_url": f"https://example.invalid/{provider_group}",
        "provider_name": provider_name,
        "provider_group": provider_group,
        "one_x_two": {"home": 2.10, "draw": 3.20, "away": 3.40},
        "asian_handicap": {"line": ah_line, "home": 1.90, "away": 1.90},
        "over_under": {"line": ou_line, "over": 1.92, "under": 1.88},
        "observation_semantics": {"retrospective_backfill": False},
    }
    row["raw_snapshot_sha256"] = canonical_sha256(row)
    result = validate_snapshot(row)
    assert result["passed"] is True, result
    return row


def main() -> int:
    a = snapshot("provider_a", "Provider A", "2026-07-22T10:00:00+00:00")
    b = snapshot("provider_b", "Provider B", "2026-07-22T10:02:00+00:00")

    full = build([a, b])
    full_validation = validate_consensus(full)
    assert full_validation["passed"] is True
    assert full["promotion_evidence_eligible"] is True
    assert full["required_surface_consensus_eligibility"] == {
        "one_x_two": True,
        "asian_handicap": True,
        "over_under": True,
    }
    assert full["promotion_ineligibility_reasons"] == []

    ah_mismatch_b = snapshot("provider_b", "Provider B", "2026-07-22T10:02:00+00:00", ah_line=0.25)
    ah_mismatch = build([a, ah_mismatch_b])
    assert validate_consensus(ah_mismatch)["passed"] is True
    assert ah_mismatch["promotion_evidence_eligible"] is False
    assert ah_mismatch["required_surface_consensus_eligibility"]["asian_handicap"] is False
    assert "NO_COMPARABLE_ASIAN_HANDICAP_CONSENSUS" in ah_mismatch["promotion_ineligibility_reasons"]

    ou_mismatch_b = snapshot("provider_b", "Provider B", "2026-07-22T10:02:00+00:00", ou_line=2.75)
    ou_mismatch = build([a, ou_mismatch_b])
    assert validate_consensus(ou_mismatch)["passed"] is True
    assert ou_mismatch["promotion_evidence_eligible"] is False
    assert ou_mismatch["required_surface_consensus_eligibility"]["over_under"] is False
    assert "NO_COMPARABLE_OVER_UNDER_CONSENSUS" in ou_mismatch["promotion_ineligibility_reasons"]

    duplicate_provider_rejected = False
    try:
        dup = deepcopy(b)
        dup["provider_group"] = "provider_a"
        dup["raw_snapshot_sha256"] = canonical_sha256({k: v for k, v in dup.items() if k != "raw_snapshot_sha256"})
        build([a, dup])
    except ValueError:
        duplicate_provider_rejected = True
    assert duplicate_provider_rejected is True

    skew_rejected = False
    try:
        late = snapshot("provider_b", "Provider B", "2026-07-22T10:06:00+00:00")
        build([a, late])
    except ValueError:
        skew_rejected = True
    assert skew_rejected is True

    receipt = {
        "schema_version": "V5.5.19-strict-three-surface-consensus-acceptance-r1",
        "status": "PASS",
        "full_three_surface_case": {
            "promotion_evidence_eligible": full["promotion_evidence_eligible"],
            "required_surface_consensus_eligibility": full["required_surface_consensus_eligibility"],
            "validation": full_validation,
        },
        "ah_line_mismatch_case": {
            "promotion_evidence_eligible": ah_mismatch["promotion_evidence_eligible"],
            "reasons": ah_mismatch["promotion_ineligibility_reasons"],
        },
        "ou_line_mismatch_case": {
            "promotion_evidence_eligible": ou_mismatch["promotion_evidence_eligible"],
            "reasons": ou_mismatch["promotion_ineligibility_reasons"],
        },
        "duplicate_provider_group_rejected": duplicate_provider_rejected,
        "timestamp_skew_over_300s_rejected": skew_rejected,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Promotion requires two unique independent provider groups within 300 seconds AND comparable 1X2/AH/OU consensus. AH/OU main-line mismatch remains research-only and cannot be promoted as complete three-surface evidence.",
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
