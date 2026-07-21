#!/usr/bin/env python3
"""Re-adjudicate V5 player-XI research with explicit boolean polarity.

The V5.0.2 discovery and V5.0.3 replication metric receipts are retained as
immutable calculation evidence. Their domain status field was produced by a
buggy ``all(checks.values())`` expression, which treats the safe condition
``target_actual_xi_used_as_input=False`` as a failed gate. This governance
receipt recalculates only eligibility status; it does not recompute or alter any
metrics, probabilities or model outputs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for path in (VALIDATION, ENGINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from platform_core import atomic_write_json, load_json, sha256_file  # noqa: E402
from player_xi_gate_v503 import adjudicate_checks  # noqa: E402

DISCOVERY = ROOT / "manifests" / "player_xi_residual_signal_oof_v502_status.json"
REPLICATION = ROOT / "manifests" / "player_xi_residual_replication_v503_status.json"
CONTINUITY = ROOT / "manifests" / "lineup_latent_signal_oof_v502_status.json"
OUT = ROOT / "manifests" / "player_xi_gate_adjudication_v503_status.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def adjudicate_receipt(path: Path, pass_status: str) -> dict[str, Any]:
    receipt = load_json(path)
    domains: dict[str, Any] = {}
    passed: list[str] = []
    rejected: list[str] = []
    for competition_id, report in (receipt.get("reports") or {}).items():
        checks = report.get("checks") or {}
        gate_pass, detail = adjudicate_checks(checks)
        status = pass_status if gate_pass else "REJECT_KEEP_FORMAL_WEIGHT_0"
        domains[competition_id] = {
            "original_status": report.get("status"),
            "corrected_status": status,
            "gate_pass": gate_pass,
            "gate_detail": detail,
            "outer_prediction_count": report.get("outer_prediction_count"),
            "selected_profiles": report.get("selected_profiles"),
            "pooled_metrics": report.get("pooled_metrics"),
            "paired_block_bootstrap": report.get("paired_block_bootstrap"),
        }
        (passed if gate_pass else rejected).append(competition_id)
    return {
        "source_path": path.relative_to(ROOT.parent).as_posix(),
        "source_sha256": sha256_file(path),
        "source_schema_version": receipt.get("schema_version"),
        "source_generated_at_utc": receipt.get("generated_at_utc"),
        "passed_domains": sorted(passed),
        "rejected_domains": sorted(rejected),
        "domains": domains,
    }


def run(*, write: bool) -> dict[str, Any]:
    for path in (DISCOVERY, REPLICATION, CONTINUITY):
        if not path.is_file():
            raise RuntimeError(f"required receipt missing: {path}")
    continuity = adjudicate_receipt(
        CONTINUITY,
        "FEATURE_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW",
    )
    discovery = adjudicate_receipt(
        DISCOVERY,
        "PLAYER_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW",
    )
    replication = adjudicate_receipt(
        REPLICATION,
        "PLAYER_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW",
    )
    all_player_passed = sorted(
        set(discovery["passed_domains"]) | set(replication["passed_domains"])
    )
    replicated_pass_count = len(replication["passed_domains"])
    report = {
        "schema_version": "V5.0.3-player-xi-gate-adjudication-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS_GOVERNANCE_BUG_CORRECTED",
        "bug": {
            "code": "BOOLEAN_POLARITY_ALL_VALUES",
            "description": "A safe False value for target_actual_xi_used_as_input was included in all(checks.values()), making every domain ineligible regardless of all positive checks.",
            "metrics_affected": False,
            "bootstrap_affected": False,
            "sample_counts_affected": False,
            "probabilities_affected": False,
            "only_status_adjudication_affected": True,
        },
        "correct_gate_semantics": {
            "required_positive_checks": "must be true",
            "keys_ending_used_as_input": "must be false",
            "implementation": "football-data/validation/player_xi_gate_v503.py",
        },
        "continuity_signal": continuity,
        "player_signal_discovery": discovery,
        "player_signal_independent_replication": replication,
        "player_signal_matrix_projection_review_domains": all_player_passed,
        "independent_replication_pass_count": replicated_pass_count,
        "global_replication_status": (
            "MULTI_DOMAIN_REPLICATION_PASS"
            if replicated_pass_count >= 2
            else "SINGLE_OR_ZERO_DOMAIN_PASS_NO_GLOBAL_PROMOTION"
        ),
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "historical_receipt_policy": "Original metric receipts remain immutable evidence, but their domain status fields are superseded by this explicit-polarity governance adjudication.",
        "policy": "A corrected pass permits only competition-specific unified-matrix projection research. No formal probability influence, no cross-domain weight copy and no promotion are authorized."
    }
    if write:
        atomic_write_json(OUT, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    report = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
