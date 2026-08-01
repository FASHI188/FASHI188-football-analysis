#!/usr/bin/env python3
"""V5.0.3 wrapper around the historically validated V5.0.2 audit engine."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
LEGACY_EXTERNAL_REQUEST_ATTEMPTS_SCOPE = (
    "V5.0.2_LEDGER_COMPATIBILITY: provider requests plus audit-business-code "
    "external requests; excludes GitHub Actions workflow infrastructure network"
)


def _install_utf8_git_boundary() -> None:
    """Keep V5.0.2 files unchanged while making the V5.0.3 entry UTF-8 safe."""
    historical.base.git = claims.git_utf8


def _network_zero_fields() -> dict[str, Any]:
    """Return scoped zero-request facts, including the V5.0.2 ledger alias."""
    provider = 0
    audit_code = 0
    return {
        "provider_network_used": False,
        "provider_request_attempts": provider,
        "audit_code_external_request_attempts": audit_code,
        "external_request_attempts": provider + audit_code,
        "external_request_attempts_scope": LEGACY_EXTERNAL_REQUEST_ATTEMPTS_SCOPE,
        "workflow_infrastructure_network_used": "OUT_OF_SCOPE_NOT_MEASURED_BY_AUDIT_CODE",
    }


def build_audit(root: Path) -> dict[str, Any]:
    _install_utf8_git_boundary()
    audit = dict(historical.build_audit(root))
    rule_info = claims.validate_rule_sources(root)
    audit["schema_version"] = "DRAW-SIGNAL-CLOSURE-AUDIT-V503-1.2"
    audit["rule_version"] = RULE_VERSION
    audit["authoritative_rule"] = rule_info
    audit["historical_relationship"] = {
        "previous_version": PREVIOUS_VERSION,
        "previous_exact_head": PREVIOUS_EXACT_HEAD,
        "previous_status": "HISTORICAL_VALIDATED_VERSION",
        "silent_rewrite": False,
    }
    audit.update({
        "formal_weight": 0,
        "model_training": 0,
        "new_target_period_scoring": 0,
        **_network_zero_fields(),
        "api_football_key_accessed": False,
    })
    audit["execution_boundary"] = {
        "measurement_scope": "AUDIT_BUSINESS_CODE_ONLY",
        "measurement_phase": "AUDIT_OBJECT_BUILT_BEFORE_OUTPUT_WRITE",
        "actual_steps": [
            "historical-v502-audit-computation",
            "v503-authoritative-rule-validation",
            "v503-audit-wrapper-computation",
        ],
        "not_yet_executed_at_measurement": [
            "structured-claim-output-write-and-verification",
            "workflow-artifact-upload",
        ],
        "provider_request_attempts": 0,
        "audit_code_external_request_attempts": 0,
        "external_request_attempts": 0,
        "external_request_attempts_scope": LEGACY_EXTERNAL_REQUEST_ATTEMPTS_SCOPE,
        "workflow_infrastructure_network_used": "OUT_OF_SCOPE_NOT_MEASURED_BY_AUDIT_CODE",
        "mock_used": False,
        "model_training": 0,
        "new_target_period_scoring": 0,
        "formal_asset_changes": 0,
    }
    audit.pop("audit_sha256", None)
    audit["audit_sha256"] = historical.base.canonical_sha(audit)
    return audit


def _write_execution_receipt(out: Path, audit: Mapping[str, Any]) -> dict[str, Any]:
    receipt = {
        "schema_version": "DRAW-SIGNAL-V503-AUDIT-EXECUTION-RECEIPT-1.1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "head": audit["head"],
        "measurement_scope": "AUDIT_BUSINESS_CODE_ONLY",
        "actual_completed_steps": [
            "historical-v502-audit-computation",
            "v503-authoritative-rule-validation",
            "v503-audit-wrapper-computation",
            "structured-claim-record-generation",
            "claim-report-json-deterministic-render",
            "claim-report-markdown-deterministic-render",
            "claim-output-deterministic-verification",
        ],
        "not_executed_by_audit_code": [
            "workflow-checkout",
            "workflow-runtime-setup",
            "workflow-artifact-upload",
        ],
        "provider_request_attempts": 0,
        "audit_code_external_request_attempts": 0,
        "external_request_attempts": 0,
        "external_request_attempts_scope": LEGACY_EXTERNAL_REQUEST_ATTEMPTS_SCOPE,
        "workflow_infrastructure_network_used": "REPORTED_BY_WORKFLOW_METADATA_NOT_THIS_RECEIPT",
        "artifact_upload_status": "NOT_EXECUTED_BY_AUDIT_CODE",
        "model_training": 0,
        "new_target_period_scoring": 0,
        "formal_asset_changes": 0,
    }
    (out / "audit_execution_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt


def write_audit(out: Path, audit: Mapping[str, Any], root: Path) -> None:
    historical.write_audit(out, audit)
    claim_output = claims.write_claim_outputs(out, audit, root)
    claims.verify_existing(out, root)
    receipt = _write_execution_receipt(out, audit)

    metadata_path = out / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    binding = claim_output["verification"]["binding"]
    metadata.update({
        "schema_version": "DRAW-SIGNAL-CLOSURE-METADATA-V503-1.2",
        "rule_version": RULE_VERSION,
        "rule_path": claims.RULE_PATH,
        "rule_sha256": claim_output["rule_info"]["rule_sha256"],
        "claim_records_sha256": binding["claim_records_sha256"],
        "claim_records_object_sha256": binding["claim_records_object_sha256"],
        "claim_report_json_sha256": binding["claim_report_json_sha256"],
        "claim_report_markdown_sha256": binding["claim_report_markdown_sha256"],
        "claim_contract_status": claim_output["verification"]["status"],
        "audit_execution_receipt_observed_at_utc": receipt["observed_at_utc"],
        **_network_zero_fields(),
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
    root = Path(claims.git_utf8(Path.cwd(), "rev-parse", "--show-toplevel"))
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
        "provider_request_attempts": 0,
        "audit_code_external_request_attempts": 0,
        "external_request_attempts": 0,
        "external_request_attempts_scope": LEGACY_EXTERNAL_REQUEST_ATTEMPTS_SCOPE,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
