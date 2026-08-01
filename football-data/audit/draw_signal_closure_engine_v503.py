#!/usr/bin/env python3
"""V5.0.3 wrapper around the historically validated V5.0.2 audit engine."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent / "research"
for path in (HERE, RESEARCH):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import draw_signal_closure_engine_v502_r4 as historical
import draw_signal_claim_contract_v503 as claims

RULE_VERSION = claims.RULE_VERSION
PREVIOUS_VERSION = claims.PREVIOUS_VERSION
PREVIOUS_EXACT_HEAD = claims.PREVIOUS_EXACT_HEAD
FORMAL_WEIGHT = 0


def build_audit(root: Path) -> dict[str, Any]:
    audit = dict(historical.build_audit(root))
    rule_info = claims.validate_rule_sources(root)
    audit["schema_version"] = "DRAW-SIGNAL-CLOSURE-AUDIT-V503-1.0"
    audit["rule_version"] = RULE_VERSION
    audit["authoritative_rule"] = rule_info
    audit["historical_relationship"] = {
        "previous_version": PREVIOUS_VERSION,
        "previous_exact_head": PREVIOUS_EXACT_HEAD,
        "previous_status": "HISTORICAL_VALIDATED_VERSION",
        "silent_rewrite": False,
    }
    audit["formal_weight"] = 0
    audit["model_training"] = 0
    audit["new_target_period_scoring"] = 0
    audit["provider_network_used"] = False
    audit["external_request_attempts"] = 0
    audit["api_football_key_accessed"] = False
    audit["execution_boundary"] = {
        "actual_jobs": [
            "exact-head-no-provider-and-protected-asset-gates",
            "V5.0.2-historical-tests",
            "V5.0.3-claim-contract-counterexample-tests",
            "route-aware-read-only-signal-closure-audit",
            "structured-claim-validation",
            "artifact-metadata-and-upload",
        ],
        "skipped_jobs": [],
        "mock_used": False,
        "real_network_requests": 0,
        "provider_accessed": False,
        "model_training": 0,
        "new_target_period_scoring": 0,
        "formal_asset_changes": 0,
    }
    audit.pop("audit_sha256", None)
    audit["audit_sha256"] = historical.base.canonical_sha(audit)
    return audit


def write_audit(out: Path, audit: Mapping[str, Any], root: Path) -> None:
    historical.write_audit(out, audit)
    claim_output = claims.write_claim_outputs(out, audit, root)
    metadata_path = out / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update({
        "schema_version": "DRAW-SIGNAL-CLOSURE-METADATA-V503-1.0",
        "rule_version": RULE_VERSION,
        "rule_path": claims.RULE_PATH,
        "rule_sha256": claim_output["rule_info"]["rule_sha256"],
        "claim_records_sha256": claim_output["verification"]["records_sha256"],
        "claim_contract_status": claim_output["verification"]["status"],
        "new_target_period_scoring": 0,
        "model_diff": 0,
        "formal_data_diff": 0,
        "config_diff": 0,
        "current_diff": 0,
    })
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(historical.base.git(Path.cwd(), "rev-parse", "--show-toplevel"))
    audit = build_audit(root)
    write_audit(args.output_dir, audit, root)
    print(json.dumps({
        "head": audit["head"],
        "rule_version": audit["rule_version"],
        "decision": audit["decision"],
        "formal_weight": 0,
        "model_training": 0,
        "new_target_period_scoring": 0,
        "provider_network_used": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
