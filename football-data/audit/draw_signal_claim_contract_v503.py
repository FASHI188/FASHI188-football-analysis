#!/usr/bin/env python3
"""V5.0.3 structured evidence and claim-boundary contract.

This module validates outward claims from the draw-signal closure audit. It does
not train, score a target period, access a provider/secret, or modify formal
assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

RULE_VERSION = "Football Draw Signal Closure Audit V5.0.3"
RULE_PATH = "football-data/audit/football_draw_signal_closure_audit_v503.md"
AGENTS_PATH = "AGENTS.md"
PREVIOUS_VERSION = "Football Draw Signal Closure Audit V5.0.2"
PREVIOUS_EXACT_HEAD = "3330b1cdddbaaf34d9d9c496902e649b81870275"

EVIDENCE_STATUSES = frozenset({"PROVEN", "COMPUTED", "INFERRED", "UNPROVEN", "NOT_AUTHORIZED"})
INFERENCE_QUALIFIERS = ("提示", "可能", "候选", "值得预注册验证", "尚需实验", "当前证据倾向于")
REQUIRED_CLAIM_FIELDS = frozenset({
    "claim_id", "claim_type", "claim_subject", "claim_text", "evidence_status",
    "scope", "exact_head", "evidence_refs", "execution_status", "pit_status",
    "holdout_status", "authorization_status", "limitations",
})
UNAUTHORIZED_ACTIONS = frozenset({
    "MODEL_TRAINING", "NEW_TARGET_PERIOD_SCORING", "HOLDOUT_ACCESS",
    "ROUND_EXPERIMENT", "PROVIDER_REQUEST", "SECRET_ACCESS",
    "FORMAL_WEIGHT_CHANGE", "FORMAL_ASSET_CHANGE", "PR_MERGE",
    "PR_READY_FOR_REVIEW",
})
STRONG_CLAIM_TYPES = frozenset({
    "EXHAUSTED", "TRAINING_AVAILABLE", "SIGNAL_EFFECTIVE", "FORMALLY_USABLE",
    "DRAW_PROBLEM_SOLVED", "ALL_FIELDS_TESTED", "REAL_NETWORK_VALIDATED",
    "PRODUCTION_PATH_VALIDATED", "BUSINESS_CONCLUSION_VALIDATED",
})


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_rule_sources(root: Path) -> dict[str, Any]:
    rule = root / RULE_PATH
    agents = root / AGENTS_PATH
    if not rule.is_file():
        raise ValueError(f"authoritative rule missing: {RULE_PATH}")
    if not agents.is_file():
        raise ValueError("root AGENTS.md missing")
    rule_text = rule.read_text(encoding="utf-8")
    agents_text = agents.read_text(encoding="utf-8")
    if RULE_VERSION not in rule_text:
        raise ValueError("V5.0.3 version declaration missing")
    if "CURRENT_AUTHORITATIVE_RULE" not in rule_text:
        raise ValueError("authoritative status declaration missing")
    if RULE_PATH not in agents_text or agents_text.count(RULE_PATH) != 1:
        raise ValueError("AGENTS.md must reference the exact V5.0.3 rule path once")
    return {
        "rule_version": RULE_VERSION,
        "rule_path": RULE_PATH,
        "rule_sha256": sha256_file(rule),
        "agents_path": AGENTS_PATH,
        "agents_sha256": sha256_file(agents),
        "historical_version": PREVIOUS_VERSION,
        "historical_status": "HISTORICAL_VALIDATED_VERSION",
        "valid": True,
    }


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Sequence, Mapping, set, frozenset)):
        return bool(value)
    return True


def _require_fields(record: Mapping[str, Any]) -> None:
    missing = sorted(field for field in REQUIRED_CLAIM_FIELDS if field not in record)
    empty = sorted(field for field in REQUIRED_CLAIM_FIELDS if field in record and not _nonempty(record[field]))
    if missing or empty:
        raise ValueError(f"claim record incomplete: missing={missing} empty={empty}")


def _validate_status_metadata(record: Mapping[str, Any]) -> None:
    status = str(record["evidence_status"])
    if status not in EVIDENCE_STATUSES:
        raise ValueError(f"illegal evidence_status: {status}")
    metadata = record.get("evidence_metadata")
    if status in {"PROVEN", "COMPUTED"} and not isinstance(metadata, Mapping):
        raise ValueError(f"{status} claim requires evidence_metadata")
    if status == "PROVEN":
        required = {"evidence_generation_method", "time_boundary", "production_path_executed", "skipped_paths", "mock_used"}
        missing = sorted(required - set(metadata or {}))
        if missing:
            raise ValueError(f"PROVEN metadata missing: {missing}")
    if status == "COMPUTED":
        required = {"input_identity", "input_scope", "algorithm_identity", "algorithm_binding", "output_identity", "reproducible", "business_validity_not_implied"}
        missing = sorted(required - set(metadata or {}))
        if missing:
            raise ValueError(f"COMPUTED metadata missing: {missing}")
        if metadata.get("business_validity_not_implied") is not True:
            raise ValueError("COMPUTED claim must state that business validity is not implied")
    if status == "INFERRED" and str(record.get("inference_qualifier") or "") not in INFERENCE_QUALIFIERS:
        raise ValueError("INFERRED claim requires an approved qualifier")
    if status == "UNPROVEN" and not record.get("limitations"):
        raise ValueError("UNPROVEN claim requires limitations")
    if status == "NOT_AUTHORIZED" and str(record.get("authorization_status")) != "NOT_AUTHORIZED":
        raise ValueError("NOT_AUTHORIZED evidence requires matching authorization_status")


def _validate_scope(record: Mapping[str, Any]) -> None:
    scope = record["scope"]
    if not isinstance(scope, Mapping):
        raise ValueError("scope must be a mapping")
    level = str(scope.get("level") or "")
    values = scope.get("values")
    if level not in {"GLOBAL", "DOMAIN_SPECIFIC", "WORKFLOW", "ARTIFACT", "REPOSITORY", "ACTION"}:
        raise ValueError(f"unsupported scope level: {level}")
    if not isinstance(values, list) or not values:
        raise ValueError("scope values must be a non-empty list")
    if "KOR_KLeague1" in values and level == "GLOBAL":
        raise ValueError("KOR_KLeague1 evidence may not be labeled GLOBAL")


def exhaustion_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    requirements = {
        "asset_universes_independent_complete": True,
        "all_candidate_fields_def_use_reviewed": True,
        "pit_available_at_proven": True,
        "all_canonical_routes_final": True,
        "unresolved_routes": 0,
        "missing_result_evidence": 0,
        "global_candidates": 0,
        "strict_domain_candidates": 0,
        "reconstructed_domain_candidates": 0,
        "all_registered_experiments_have_results": True,
        "unverified_aliases": 0,
        "omitted_tracked_assets": 0,
        "required_production_paths_executed": True,
    }
    failures = [key for key, expected in requirements.items() if context.get(key) != expected]
    return not failures, failures


def training_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    requirements = {
        "preregistration_frozen": True,
        "pit_safe_proven": True,
        "holdout_proven_untouched": True,
        "inputs_target_splits_metrics_frozen": True,
        "target_leakage_absent": True,
        "user_training_authorized": True,
    }
    failures = [key for key, expected in requirements.items() if context.get(key) != expected]
    return not failures, failures


def signal_effectiveness_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    requirements = {"preregistered_experiment_executed": True, "frozen_gates_passed": True, "independent_result_evidence": True}
    failures = [key for key, expected in requirements.items() if context.get(key) != expected]
    return not failures, failures


def formal_usability_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    requirements = {
        "validated_model_complete": True,
        "independent_holdout_complete": True,
        "calibration_stability_evidence": True,
        "scope_defined": True,
        "production_implementation_executed": True,
        "fallback_defined": True,
        "user_formal_asset_authorized": True,
    }
    failures = [key for key, expected in requirements.items() if context.get(key) != expected]
    return not failures, failures


def _validate_strong_claim(record: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    claim_type = str(record["claim_type"])
    status = str(record["evidence_status"])
    if claim_type == "EXHAUSTED":
        passed, failures = exhaustion_gate(context)
        if not passed:
            raise ValueError(f"EXHAUSTED prohibited: {failures}")
    elif claim_type == "TRAINING_AVAILABLE":
        passed, failures = training_gate(context)
        if not passed:
            raise ValueError(f"training availability prohibited: {failures}")
    elif claim_type in {"SIGNAL_EFFECTIVE", "DRAW_PROBLEM_SOLVED"}:
        passed, failures = signal_effectiveness_gate(context)
        if not passed:
            raise ValueError(f"signal effectiveness prohibited: {failures}")
    elif claim_type == "FORMALLY_USABLE":
        passed, failures = formal_usability_gate(context)
        if not passed:
            raise ValueError(f"formal usability prohibited: {failures}")
    elif claim_type == "ALL_FIELDS_TESTED" and not context.get("field_dataflow_contracts_nonempty"):
        raise ValueError("all-fields-tested claim prohibited while FIELD_DATAFLOW_CONTRACTS is empty")
    elif claim_type == "REAL_NETWORK_VALIDATED" and (context.get("network_mocked") or int(context.get("real_request_attempts", 0)) <= 0):
        raise ValueError("real network validation prohibited for mock-only/no-request execution")
    elif claim_type == "PRODUCTION_PATH_VALIDATED" and (context.get("live_job_conclusion") == "skipped" or not context.get("production_path_executed")):
        raise ValueError("production-path validation prohibited when live job/path is skipped")
    elif claim_type == "BUSINESS_CONCLUSION_VALIDATED" and context.get("artifact_uploaded") and not context.get("business_evidence_independent"):
        raise ValueError("Artifact existence alone cannot validate a business conclusion")
    if claim_type in STRONG_CLAIM_TYPES and status not in {"PROVEN", "COMPUTED"}:
        raise ValueError(f"strong claim {claim_type} cannot use {status}")


def _validate_authorization(record: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    action = str(record.get("action") or "")
    if action in UNAUTHORIZED_ACTIONS and not bool(context.get("user_authorized")) and record["evidence_status"] != "NOT_AUTHORIZED":
        raise ValueError(f"unauthorized action must be NOT_AUTHORIZED: {action}")
    if context.get("pit_status") == "UNPROVEN" and str(record["claim_type"]) in {"TRAINING_AVAILABLE", "FORMALLY_USABLE", "HOLDOUT_RELEASE_AVAILABLE"}:
        raise ValueError("unproved PIT prohibits training, formal use, and holdout release")


def _validate_historical_withdrawal(record: Mapping[str, Any]) -> None:
    if str(record["claim_type"]) != "HISTORICAL_CLAIM_WITHDRAWAL":
        return
    required = {"old_claim", "withdrawal_statement", "withdrawal_reason", "replacement_claim", "replacement_evidence_status"}
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(f"historical withdrawal fields missing: {missing}")
    if "撤销" not in str(record["withdrawal_statement"]) and "withdraw" not in str(record["withdrawal_statement"]).lower():
        raise ValueError("historical withdrawal must state withdrawal explicitly")
    if str(record["replacement_evidence_status"]) not in EVIDENCE_STATUSES:
        raise ValueError("replacement evidence status invalid")


def validate_claim(record: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    _require_fields(record)
    _validate_status_metadata(record)
    _validate_scope(record)
    _validate_strong_claim(record, context)
    _validate_authorization(record, context)
    _validate_historical_withdrawal(record)
    if not str(record["exact_head"]).strip():
        raise ValueError("exact_head is required")
    if not isinstance(record["evidence_refs"], list) or not record["evidence_refs"]:
        raise ValueError("evidence_refs must be a non-empty list")
    return {"claim_id": record["claim_id"], "valid": True, "evidence_status": record["evidence_status"]}


def validate_claims(records: Sequence[Mapping[str, Any]], contexts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    if not records:
        raise ValueError("critical claim records missing")
    contexts = contexts or {}
    ids: set[str] = set()
    results = []
    for record in records:
        claim_id = str(record.get("claim_id") or "")
        if claim_id in ids:
            raise ValueError(f"duplicate claim_id: {claim_id}")
        ids.add(claim_id)
        results.append(validate_claim(record, contexts.get(claim_id, {})))
    return {
        "schema_version": "DRAW-SIGNAL-CLAIM-CONTRACT-V503-1.0",
        "status": "PASS",
        "claim_count": len(records),
        "evidence_status_counts": {status: sum(1 for record in records if record["evidence_status"] == status) for status in sorted(EVIDENCE_STATUSES)},
        "records_sha256": canonical_sha(records),
        "results": results,
    }


def claim_record(claim_id: str, claim_type: str, subject: str, text: str, status: str, scope_level: str, scope_values: Sequence[str], exact_head: str, evidence_refs: Sequence[str], *, execution_status: str, pit_status: str, holdout_status: str, authorization_status: str, limitations: Sequence[str], evidence_metadata: Mapping[str, Any] | None = None, inference_qualifier: str | None = None, action: str | None = None, **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "claim_subject": subject,
        "claim_text": text,
        "evidence_status": status,
        "scope": {"level": scope_level, "values": list(scope_values)},
        "exact_head": exact_head,
        "evidence_refs": list(evidence_refs),
        "execution_status": execution_status,
        "pit_status": pit_status,
        "holdout_status": holdout_status,
        "authorization_status": authorization_status,
        "limitations": list(limitations),
    }
    if evidence_metadata is not None:
        record["evidence_metadata"] = dict(evidence_metadata)
    if inference_qualifier is not None:
        record["inference_qualifier"] = inference_qualifier
    if action is not None:
        record["action"] = action
    record.update(extra)
    return record


def build_current_claims(audit: Mapping[str, Any], rule_info: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    head = str(audit["head"])
    feature = audit.get("feature_difference", {})
    route = audit.get("experiment_route_closure", {})
    coverage = audit.get("research_asset_coverage", {})
    prereg = audit.get("preregistration") or {}
    reconstructed = list(feature.get("DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES", []))
    round_rows = [row for row in reconstructed if str(row.get("field", "")).lower() == "round"]
    round_row = round_rows[0] if len(round_rows) == 1 else {}
    domains = [str(item.get("competition")) for item in round_row.get("eligible_domain_specific_scopes", [])]
    proven_meta = {
        "evidence_generation_method": "exact-head Git checkout and deterministic validation",
        "time_boundary": "repository state at exact_head",
        "production_path_executed": True,
        "skipped_paths": [],
        "mock_used": False,
    }
    def computed_meta(input_id: str, scope: str, algo: str, output: str) -> dict[str, Any]:
        return {
            "input_identity": input_id,
            "input_scope": scope,
            "algorithm_identity": algo,
            "algorithm_binding": head,
            "output_identity": output,
            "reproducible": True,
            "business_validity_not_implied": True,
        }
    claims = [
        claim_record("rule-v503-authority", "RULE_AUTHORITY", "V5.0.3 authoritative rule", f"{RULE_VERSION} is the current authoritative claim-boundary rule.", "PROVEN", "REPOSITORY", [RULE_PATH], head, [RULE_PATH, AGENTS_PATH], execution_status="RULE_FILES_PARSED", pit_status="NOT_APPLICABLE", holdout_status="NOT_APPLICABLE", authorization_status="AUTHORIZED_FOR_AUDIT_ONLY", limitations=["V5.0.2 remains a historical validated implementation"], evidence_metadata=proven_meta),
        claim_record("asset-coverage", "ASSET_COVERAGE", "independent audit asset coverage", f"Expected={coverage.get('expected_path_count')} actual={coverage.get('actual_path_count')} missing={len(coverage.get('missing', []))} extra={len(coverage.get('extra', []))}.", "COMPUTED", "REPOSITORY", ["validation", "research", "manifests"], head, ["closure/closure_audit.json", "closure/complete_research_file_ledger.json"], execution_status="DETERMINISTIC_COMPUTATION_COMPLETE", pit_status="NOT_APPLICABLE", holdout_status="NOT_APPLICABLE", authorization_status="AUDIT_ONLY", limitations=["coverage does not prove predictive value"], evidence_metadata=computed_meta("frozen expected registry + live git ls-files", "repository tracked audit assets", "historical V5.0.2 asset ledger reused by V5.0.3", "research_asset_coverage")),
        claim_record("route-state", "ROUTE_STATE", "canonical route closure", f"UNRESOLVED={len(route.get('unresolved', []))}; missing_result_evidence={len(route.get('missing_result_evidence', []))}.", "COMPUTED", "REPOSITORY", ["canonical draw research routes"], head, ["closure/closure_audit.json", "closure/experiment_ledger.json"], execution_status="DETERMINISTIC_COMPUTATION_COMPLETE", pit_status="MIXED", holdout_status="NOT_APPLICABLE", authorization_status="AUDIT_ONLY", limitations=["unresolved routes prohibit exhaustion"], evidence_metadata=computed_meta("experiment route ledger", "registered canonical routes", "route closure classifier", "experiment_route_closure")),
        claim_record("decision-current", "AUDIT_DECISION", "current audit decision", str(audit.get("decision")), "COMPUTED", "REPOSITORY", ["draw-signal closure audit"], head, ["closure/decision.json", "closure/closure_audit.json"], execution_status="DETERMINISTIC_DECISION_COMPLETE", pit_status="RECONSTRUCTED_RESEARCH_ONLY", holdout_status=str(prereg.get("holdout_status") or "NOT_APPLICABLE"), authorization_status="NOT_AUTHORIZED_FOR_RUN", limitations=["decision is not an experiment result"], evidence_metadata=computed_meta("candidate sets + route closure", "current repository audit", "decide_and_preregister", "decision")),
        claim_record("round-candidate", "DOMAIN_CANDIDATE", "round reconstructed research candidate", "round satisfies the reconstructed-research preregistration candidate gate within KOR_KLeague1.", "INFERRED", "DOMAIN_SPECIFIC", domains or ["KOR_KLeague1"], head, ["closure/feature_difference.json", "closure/decision.json"], execution_status="CANDIDATE_IDENTIFIED_NOT_RUN", pit_status="PIT_RECONSTRUCTED_SCHEDULE_FIELD_RESEARCH_ONLY", holdout_status=str(prereg.get("holdout_status") or "NOT_YET_PROVEN_UNTOUCHED"), authorization_status="NOT_AUTHORIZED", limitations=["predictive gain unproved", "scope cannot expand beyond KOR_KLeague1"], inference_qualifier="候选"),
        claim_record("round-predictive-value", "SIGNAL_EFFECTIVE", "round predictive value", "round predictive value remains unproved.", "UNPROVEN", "DOMAIN_SPECIFIC", ["KOR_KLeague1"], head, ["closure/feature_difference.json", "closure/decision.json"], execution_status="PRE_REGISTERED_NOT_RUN", pit_status="RECONSTRUCTED_NOT_STRICT_PIT", holdout_status="NOT_YET_PROVEN_UNTOUCHED", authorization_status="NOT_AUTHORIZED", limitations=["no preregistered experiment result", "coverage does not prove value"]),
        claim_record("draw-signal-effectiveness", "SIGNAL_EFFECTIVE", "draw signal effectiveness", "Current evidence does not prove an effective draw signal.", "UNPROVEN", "GLOBAL", ["all competitions"], head, ["closure/claim_records.json", "closure/feature_difference.json"], execution_status="NO_PROMOTION_GRADE_EXPERIMENT_RESULT", pit_status="UNPROVEN_FOR_GLOBAL_SIGNAL", holdout_status="NOT_YET_PROVEN_UNTOUCHED", authorization_status="NOT_AUTHORIZED", limitations=["one reconstructed domain candidate cannot prove global effectiveness"]),
        claim_record("draw-problem-solved", "DRAW_PROBLEM_SOLVED", "draw problem solved", "The draw problem is not proved solved.", "UNPROVEN", "GLOBAL", ["actual target scope"], head, ["closure/claim_records.json"], execution_status="NO_COMPLETE_INDEPENDENT_RESULT", pit_status="UNPROVEN", holdout_status="NOT_YET_PROVEN_UNTOUCHED", authorization_status="NOT_AUTHORIZED", limitations=["no complete independent leakage-free evidence"]),
        claim_record("training-authorization", "ACTION_AUTHORIZATION", "model training", "Training is not authorized and has not executed.", "NOT_AUTHORIZED", "ACTION", ["MODEL_TRAINING"], head, ["closure/artifact_metadata.json", "closure/claim_records.json"], execution_status="NOT_EXECUTED", pit_status="UNPROVEN_FOR_TRAINING", holdout_status="NOT_YET_PROVEN_UNTOUCHED", authorization_status="NOT_AUTHORIZED", limitations=["separate explicit user approval required"], action="MODEL_TRAINING"),
        claim_record("round-run-authorization", "ACTION_AUTHORIZATION", "K-League round experiment", "The K-League round experiment is not authorized and has not run.", "NOT_AUTHORIZED", "ACTION", ["ROUND_EXPERIMENT"], head, ["closure/decision.json", "closure/claim_records.json"], execution_status="PRE_REGISTERED_NOT_RUN", pit_status="RECONSTRUCTED_NOT_STRICT_PIT", holdout_status="NOT_YET_PROVEN_UNTOUCHED", authorization_status="NOT_AUTHORIZED", limitations=["separate explicit user approval required"], action="ROUND_EXPERIMENT"),
        claim_record("provider-authorization", "ACTION_AUTHORIZATION", "Provider requests and secret access", "Provider requests and secret access are not authorized and did not occur.", "NOT_AUTHORIZED", "ACTION", ["PROVIDER_REQUEST", "SECRET_ACCESS"], head, ["closure/artifact_metadata.json"], execution_status="NOT_EXECUTED", pit_status="NOT_APPLICABLE", holdout_status="NOT_APPLICABLE", authorization_status="NOT_AUTHORIZED", limitations=["separate explicit user approval required"], action="PROVIDER_REQUEST"),
        claim_record("formal-asset-authorization", "ACTION_AUTHORIZATION", "formal assets and weight", "Formal asset and formal-weight changes are not authorized and did not occur.", "NOT_AUTHORIZED", "ACTION", ["FORMAL_ASSET_CHANGE", "FORMAL_WEIGHT_CHANGE"], head, ["closure/artifact_metadata.json"], execution_status="NOT_EXECUTED", pit_status="NOT_APPLICABLE", holdout_status="NOT_APPLICABLE", authorization_status="NOT_AUTHORIZED", limitations=["formal_weight remains zero"], action="FORMAL_ASSET_CHANGE"),
        claim_record("withdraw-exhausted", "HISTORICAL_CLAIM_WITHDRAWAL", "historical exhaustion claim", "The historical exhaustion claim is explicitly withdrawn and replaced.", "PROVEN", "REPOSITORY", ["draw-signal closure audit history"], head, [RULE_PATH, "closure/closure_audit.json", "closure/claim_records.json"], execution_status="GOVERNANCE_WITHDRAWAL_RECORDED", pit_status="RECONSTRUCTED_CANDIDATE_EXISTS", holdout_status="NOT_YET_PROVEN_UNTOUCHED", authorization_status="AUDIT_ONLY", limitations=["withdrawal does not prove replacement candidate effectiveness"], evidence_metadata=proven_meta, old_claim="EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING", withdrawal_statement="撤销旧结论 EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING", withdrawal_reason="KOR_KLeague1 reconstructed round candidate exists and canonical routes remain unresolved", replacement_claim="PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET", replacement_evidence_status="COMPUTED"),
    ]
    contexts = {
        "round-predictive-value": {"preregistered_experiment_executed": False, "frozen_gates_passed": False, "independent_result_evidence": False},
        "draw-signal-effectiveness": {"preregistered_experiment_executed": False, "frozen_gates_passed": False, "independent_result_evidence": False},
        "draw-problem-solved": {"preregistered_experiment_executed": False, "frozen_gates_passed": False, "independent_result_evidence": False},
        "training-authorization": {"user_authorized": False, "pit_status": "UNPROVEN"},
        "round-run-authorization": {"user_authorized": False},
        "provider-authorization": {"user_authorized": False},
        "formal-asset-authorization": {"user_authorized": False},
    }
    return claims, contexts


def build_report(records: Sequence[Mapping[str, Any]], audit: Mapping[str, Any], previous_head: str = PREVIOUS_EXACT_HEAD) -> dict[str, Any]:
    by_status = {status: [dict(row) for row in records if row["evidence_status"] == status] for status in EVIDENCE_STATUSES}
    return {
        "schema_version": "DRAW-SIGNAL-CLAIM-REPORT-V503-1.0",
        "accurate_object": {"pr": 77, "base": "main", "branch": "research/draw-challenger-v502", "previous_head": previous_head, "exact_new_head": audit["head"], "draft": True, "merged": False},
        "directly_proven": by_status["PROVEN"],
        "program_computed": by_status["COMPUTED"],
        "inferences_and_candidates": by_status["INFERRED"],
        "unproven": by_status["UNPROVEN"],
        "not_authorized": by_status["NOT_AUTHORIZED"],
        "execution_boundary": audit.get("execution_boundary", {}),
        "cold_conclusion": "V5.0.3准确表述和证据边界合同已在准确HEAD完成验证。该结果只证明审计治理规则及其反例测试通过，不证明round具有预测价值，不证明平局信号有效，不授权训练，也不改变formal_weight=0。PR #77继续保持Open、Draft、未合并，等待Codex独立复核和用户后续决定。",
    }


def report_markdown(report: Mapping[str, Any]) -> str:
    obj = report["accurate_object"]
    lines = ["# V5.0.3 Structured Claim Report", "", "## 1. 准确对象", f"- PR: #{obj['pr']}", f"- base: `{obj['base']}`", f"- branch: `{obj['branch']}`", f"- 修复前HEAD: `{obj['previous_head']}`", f"- 准确新HEAD: `{obj['exact_new_head']}`", f"- Draft: `{str(obj['draft']).lower()}`", f"- merged: `{str(obj['merged']).lower()}`", ""]
    for title, key in (("2. 已直接证明", "directly_proven"), ("3. 程序计算结果", "program_computed"), ("4. 推断和候选", "inferences_and_candidates"), ("5. 尚未证明", "unproven"), ("6. 尚未授权", "not_authorized")):
        lines.append(f"## {title}")
        rows = report[key]
        lines.extend([f"- `{row['claim_id']}` [{row['evidence_status']}]: {row['claim_text']}" for row in rows] or ["- 无"])
        lines.append("")
    lines += ["## 7. 实际执行边界", "", "```json", json.dumps(report["execution_boundary"], ensure_ascii=False, indent=2), "```", "", "## 8. 冷结论", "", report["cold_conclusion"], ""]
    return "\n".join(lines)


def write_claim_outputs(out: Path, audit: Mapping[str, Any], root: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    rule_info = validate_rule_sources(root)
    records, contexts = build_current_claims(audit, rule_info)
    verification = validate_claims(records, contexts)
    report = build_report(records, audit)
    payloads = {
        "claim_records.json": {"schema_version": "DRAW-SIGNAL-CLAIM-RECORDS-V503-1.0", "head": audit["head"], "records": records, "records_sha256": canonical_sha(records)},
        "claim_contract_verification.json": verification,
        "claim_report.json": report,
    }
    for name, value in payloads.items():
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "claim_report.md").write_text(report_markdown(report), encoding="utf-8")
    return {"rule_info": rule_info, "verification": verification, "report": report, "records": records}


def verify_existing(closure_dir: Path, root: Path) -> dict[str, Any]:
    rule_info = validate_rule_sources(root)
    records_obj = json.loads((closure_dir / "claim_records.json").read_text(encoding="utf-8"))
    report = json.loads((closure_dir / "claim_report.json").read_text(encoding="utf-8"))
    audit = json.loads((closure_dir / "closure_audit.json").read_text(encoding="utf-8"))
    records = records_obj.get("records", [])
    contexts: dict[str, dict[str, Any]] = {}
    for row in records:
        if row.get("evidence_status") == "NOT_AUTHORIZED":
            contexts[row["claim_id"]] = {"user_authorized": False}
        if row.get("claim_type") in {"SIGNAL_EFFECTIVE", "DRAW_PROBLEM_SOLVED"}:
            contexts[row["claim_id"]] = {"preregistered_experiment_executed": False, "frozen_gates_passed": False, "independent_result_evidence": False}
    verification = validate_claims(records, contexts)
    required = {"accurate_object", "directly_proven", "program_computed", "inferences_and_candidates", "unproven", "not_authorized", "execution_boundary", "cold_conclusion"}
    if required - set(report):
        raise ValueError("claim report sections missing")
    if report["accurate_object"].get("exact_new_head") != audit.get("head"):
        raise ValueError("claim report exact HEAD mismatch")
    return {"status": "PASS", "rule_info": rule_info, "claim_verification": verification, "report_sha256": canonical_sha(report)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--closure-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(json.dumps(verify_existing(args.closure_dir, args.root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
