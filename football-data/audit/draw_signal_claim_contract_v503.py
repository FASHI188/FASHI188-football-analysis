#!/usr/bin/env python3
"""V5.0.3 deterministic claim contract; governance only, no model/provider actions."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

RULE_VERSION = "Football Draw Signal Closure Audit V5.0.3"
RULE_PATH = "football-data/audit/football_draw_signal_closure_audit_v503.md"
AGENTS_PATH = "AGENTS.md"
PREVIOUS_VERSION = "Football Draw Signal Closure Audit V5.0.2"
PREVIOUS_EXACT_HEAD = "3330b1cdddbaaf34d9d9c496902e649b81870275"
EVIDENCE_STATUSES = frozenset({"PROVEN", "COMPUTED", "INFERRED", "UNPROVEN", "NOT_AUTHORIZED"})
INFERENCE_QUALIFIERS = frozenset({"提示", "可能", "候选", "值得预注册验证", "尚需实验", "当前证据倾向于"})
REQUIRED_CLAIM_FIELDS = frozenset({
    "claim_id", "claim_type", "claim_subject", "claim_text", "evidence_status", "scope",
    "exact_head", "evidence_refs", "execution_status", "pit_status", "holdout_status",
    "authorization_status", "limitations",
})
UNAUTHORIZED_ACTIONS = frozenset({
    "MODEL_TRAINING", "NEW_TARGET_PERIOD_SCORING", "HOLDOUT_ACCESS", "ROUND_EXPERIMENT",
    "PROVIDER_REQUEST", "SECRET_ACCESS", "FORMAL_WEIGHT_CHANGE", "FORMAL_ASSET_CHANGE",
    "PR_MERGE", "PR_READY_FOR_REVIEW",
})
STRONG_CLAIM_TYPES = frozenset({
    "EXHAUSTED", "TRAINING_AVAILABLE", "SIGNAL_EFFECTIVE", "FORMALLY_USABLE",
    "DRAW_PROBLEM_SOLVED", "ALL_FIELDS_TESTED", "REAL_NETWORK_VALIDATED",
    "PRODUCTION_PATH_VALIDATED", "BUSINESS_CONCLUSION_VALIDATED",
})
STATUS_SECTION_KEYS = {
    "PROVEN": "directly_proven", "COMPUTED": "program_computed",
    "INFERRED": "inferences_and_candidates", "UNPROVEN": "unproven",
    "NOT_AUTHORIZED": "not_authorized",
}
COLD_CONCLUSION = (
    "V5.0.3结构化结论已由当前准确HEAD的closure audit确定性生成并完成自一致性校验。"
    "该结果只证明结构化记录、JSON报告和Markdown报告与当前audit完全一致；"
    "不证明round具有预测价值，不证明平局信号有效，不授权训练、正式权重变更、Ready转换或PR合并。"
    "PR #77继续保持Open、Draft、未合并，等待Codex独立复核和用户后续决定。"
)


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def decode_git_stdout(raw: bytes) -> str:
    return raw.decode("utf-8", errors="strict").strip()


def git_utf8(root: Path, *args: str) -> str:
    run = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = decode_git_stdout(run.stdout)
    stderr = decode_git_stdout(run.stderr) if run.stderr else ""
    if run.returncode:
        raise subprocess.CalledProcessError(run.returncode, run.args, output=stdout, stderr=stderr)
    return stdout


def validate_rule_sources(root: Path) -> dict[str, Any]:
    rule, agents = root / RULE_PATH, root / AGENTS_PATH
    if not rule.is_file() or not agents.is_file():
        raise ValueError("V5.0.3 rule or root AGENTS.md missing")
    rule_text, agents_text = rule.read_text("utf-8"), agents.read_text("utf-8")
    if RULE_VERSION not in rule_text or "CURRENT_AUTHORITATIVE_RULE" not in rule_text:
        raise ValueError("V5.0.3 authority declaration missing")
    if agents_text.count(RULE_PATH) != 1:
        raise ValueError("AGENTS.md must reference the exact V5.0.3 rule path once")
    return {
        "rule_version": RULE_VERSION, "rule_path": RULE_PATH, "rule_sha256": sha256_file(rule),
        "agents_path": AGENTS_PATH, "agents_sha256": sha256_file(agents),
        "historical_version": PREVIOUS_VERSION, "historical_status": "HISTORICAL_VALIDATED_VERSION",
        "valid": True,
    }


def claim_record(
    claim_id: str, claim_type: str, subject: str, text: str, status: str,
    scope_level: str, scope_values: Sequence[str], exact_head: str, evidence_refs: Sequence[str], *,
    execution_status: str, pit_status: str, holdout_status: str, authorization_status: str,
    limitations: Sequence[str], evidence_metadata: Mapping[str, Any] | None = None,
    inference_qualifier: str | None = None, action: str | None = None, **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "claim_id": claim_id, "claim_type": claim_type, "claim_subject": subject, "claim_text": text,
        "evidence_status": status, "scope": {"level": scope_level, "values": list(scope_values)},
        "exact_head": exact_head, "evidence_refs": list(evidence_refs), "execution_status": execution_status,
        "pit_status": pit_status, "holdout_status": holdout_status,
        "authorization_status": authorization_status, "limitations": list(limitations),
    }
    if evidence_metadata is not None:
        row["evidence_metadata"] = dict(evidence_metadata)
    if inference_qualifier is not None:
        row["inference_qualifier"] = inference_qualifier
    if action is not None:
        row["action"] = action
    row.update(extra)
    return row


def _nonempty(value: Any) -> bool:
    if value is None or value == "":
        return False
    return not isinstance(value, (Sequence, Mapping, set, frozenset)) or bool(value)


def exhaustion_gate(c: Mapping[str, Any]) -> tuple[bool, list[str]]:
    required = {
        "asset_universes_independent_complete": True, "all_candidate_fields_def_use_reviewed": True,
        "pit_available_at_proven": True, "all_canonical_routes_final": True, "unresolved_routes": 0,
        "missing_result_evidence": 0, "global_candidates": 0, "strict_domain_candidates": 0,
        "reconstructed_domain_candidates": 0, "all_registered_experiments_have_results": True,
        "unverified_aliases": 0, "omitted_tracked_assets": 0, "required_production_paths_executed": True,
    }
    failures = [k for k, v in required.items() if c.get(k) != v]
    return not failures, failures


def training_gate(c: Mapping[str, Any]) -> tuple[bool, list[str]]:
    keys = ("preregistration_frozen", "pit_safe_proven", "holdout_proven_untouched",
            "inputs_target_splits_metrics_frozen", "target_leakage_absent", "user_training_authorized")
    failures = [k for k in keys if c.get(k) is not True]
    return not failures, failures


def signal_effectiveness_gate(c: Mapping[str, Any]) -> tuple[bool, list[str]]:
    keys = ("preregistered_experiment_executed", "frozen_gates_passed", "independent_result_evidence")
    failures = [k for k in keys if c.get(k) is not True]
    return not failures, failures


def formal_usability_gate(c: Mapping[str, Any]) -> tuple[bool, list[str]]:
    keys = ("validated_model_complete", "independent_holdout_complete", "calibration_stability_evidence",
            "scope_defined", "production_implementation_executed", "fallback_defined",
            "user_formal_asset_authorized")
    failures = [k for k in keys if c.get(k) is not True]
    return not failures, failures


def validate_claim(record: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    missing = sorted(REQUIRED_CLAIM_FIELDS - set(record))
    empty = sorted(k for k in REQUIRED_CLAIM_FIELDS & set(record) if not _nonempty(record[k]))
    if missing or empty:
        raise ValueError(f"claim record incomplete: missing={missing} empty={empty}")
    status, claim_type = str(record["evidence_status"]), str(record["claim_type"])
    if status not in EVIDENCE_STATUSES:
        raise ValueError(f"illegal evidence_status: {status}")
    metadata = record.get("evidence_metadata")
    if status == "PROVEN":
        need = {"evidence_generation_method", "time_boundary", "production_path_executed", "skipped_paths", "mock_used"}
        if not isinstance(metadata, Mapping) or need - set(metadata):
            raise ValueError("PROVEN metadata incomplete")
    if status == "COMPUTED":
        need = {"input_identity", "input_scope", "algorithm_identity", "algorithm_binding", "output_identity", "reproducible", "business_validity_not_implied"}
        if not isinstance(metadata, Mapping) or need - set(metadata) or metadata.get("business_validity_not_implied") is not True:
            raise ValueError("COMPUTED metadata incomplete")
    if status == "INFERRED" and record.get("inference_qualifier") not in INFERENCE_QUALIFIERS:
        raise ValueError("INFERRED claim requires approved qualifier")
    if status == "UNPROVEN" and not record["limitations"]:
        raise ValueError("UNPROVEN claim requires limitations")
    if status == "NOT_AUTHORIZED" and record["authorization_status"] != "NOT_AUTHORIZED":
        raise ValueError("NOT_AUTHORIZED status mismatch")
    scope = record["scope"]
    if not isinstance(scope, Mapping) or scope.get("level") not in {"GLOBAL", "DOMAIN_SPECIFIC", "WORKFLOW", "ARTIFACT", "REPOSITORY", "ACTION"}:
        raise ValueError("unsupported scope")
    if not isinstance(scope.get("values"), list) or not scope["values"]:
        raise ValueError("scope values required")
    if scope["level"] == "GLOBAL" and "KOR_KLeague1" in scope["values"]:
        raise ValueError("KOR scope cannot become global")

    if claim_type == "EXHAUSTED":
        ok, failures = exhaustion_gate(context)
        if not ok:
            raise ValueError(f"EXHAUSTED prohibited: {failures}")
    elif claim_type == "TRAINING_AVAILABLE":
        ok, failures = training_gate(context)
        if not ok:
            raise ValueError(f"training prohibited: {failures}")
    elif claim_type in {"SIGNAL_EFFECTIVE", "DRAW_PROBLEM_SOLVED"}:
        ok, failures = signal_effectiveness_gate(context)
        if not ok:
            raise ValueError(f"signal claim prohibited: {failures}")
    elif claim_type == "FORMALLY_USABLE":
        ok, failures = formal_usability_gate(context)
        if not ok:
            raise ValueError(f"formal use prohibited: {failures}")
    elif claim_type == "ALL_FIELDS_TESTED" and not context.get("field_dataflow_contracts_nonempty"):
        raise ValueError("FIELD_DATAFLOW_CONTRACTS empty")
    elif claim_type == "REAL_NETWORK_VALIDATED" and (context.get("network_mocked") or int(context.get("real_request_attempts", 0)) <= 0):
        raise ValueError("real network not executed")
    elif claim_type == "PRODUCTION_PATH_VALIDATED" and (context.get("live_job_conclusion") == "skipped" or not context.get("production_path_executed")):
        raise ValueError("production path skipped")
    elif claim_type == "BUSINESS_CONCLUSION_VALIDATED" and context.get("artifact_uploaded") and not context.get("business_evidence_independent"):
        raise ValueError("Artifact alone cannot validate business conclusion")
    if claim_type in STRONG_CLAIM_TYPES and status not in {"PROVEN", "COMPUTED"}:
        raise ValueError("strong claim status invalid")
    action = str(record.get("action") or "")
    if action in UNAUTHORIZED_ACTIONS and not context.get("user_authorized") and status != "NOT_AUTHORIZED":
        raise ValueError("unauthorized action must be NOT_AUTHORIZED")
    if context.get("pit_status") == "UNPROVEN" and claim_type in {"TRAINING_AVAILABLE", "FORMALLY_USABLE", "HOLDOUT_RELEASE_AVAILABLE"}:
        raise ValueError("unproved PIT blocks use")
    if claim_type == "HISTORICAL_CLAIM_WITHDRAWAL":
        needed = {"old_claim", "withdrawal_statement", "withdrawal_reason", "replacement_claim", "replacement_evidence_status"}
        if needed - set(record) or ("撤销" not in str(record.get("withdrawal_statement")) and "withdraw" not in str(record.get("withdrawal_statement")).lower()):
            raise ValueError("historical withdrawal incomplete")
        if record["replacement_evidence_status"] not in EVIDENCE_STATUSES:
            raise ValueError("replacement status invalid")
    return {"claim_id": record["claim_id"], "valid": True, "evidence_status": status}


def validate_claims(records: Sequence[Mapping[str, Any]], contexts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    if not records:
        raise ValueError("critical claim records missing")
    contexts, seen, results = contexts or {}, set(), []
    for row in records:
        if row["claim_id"] in seen:
            raise ValueError("duplicate claim_id")
        seen.add(row["claim_id"])
        results.append(validate_claim(row, contexts.get(row["claim_id"], {})))
    return {
        "schema_version": "DRAW-SIGNAL-CLAIM-CONTRACT-V503-1.1", "status": "PASS",
        "claim_count": len(records),
        "evidence_status_counts": {s: sum(r["evidence_status"] == s for r in records) for s in sorted(EVIDENCE_STATUSES)},
        "records_sha256": canonical_sha(records), "results": results,
    }


def build_current_claims(audit: Mapping[str, Any], rule_info: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    del rule_info
    head, feature = str(audit["head"]), audit.get("feature_difference", {})
    route, coverage, prereg = audit.get("experiment_route_closure", {}), audit.get("research_asset_coverage", {}), audit.get("preregistration") or {}
    candidates = feature.get("DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES", [])
    round_rows = [r for r in candidates if str(r.get("field", "")).lower() == "round"]
    domains = [str(x.get("competition")) for x in (round_rows[0].get("eligible_domain_specific_scopes", []) if len(round_rows) == 1 else [])] or ["KOR_KLeague1"]
    proven = {"evidence_generation_method": "exact-head repository parsing and deterministic rendering", "time_boundary": "repository state at exact_head", "production_path_executed": False, "skipped_paths": ["predictive production path outside governance-audit scope"], "mock_used": False}
    def computed(inp: str, scope: str, algorithm: str, output: str) -> dict[str, Any]:
        return {"input_identity": inp, "input_scope": scope, "algorithm_identity": algorithm, "algorithm_binding": head, "output_identity": output, "reproducible": True, "business_validity_not_implied": True}
    def rec(cid: str, ctype: str, subject: str, text: str, status: str, level: str, values: Sequence[str], refs: Sequence[str], execution: str, pit: str, holdout: str, auth: str, limits: Sequence[str], **extra: Any) -> dict[str, Any]:
        return claim_record(cid, ctype, subject, text, status, level, values, head, refs, execution_status=execution, pit_status=pit, holdout_status=holdout, authorization_status=auth, limitations=limits, **extra)
    records = [
        rec("rule-v503-authority", "RULE_AUTHORITY", "V5.0.3 authoritative rule", f"{RULE_VERSION} is the current authoritative claim-boundary rule.", "PROVEN", "REPOSITORY", [RULE_PATH], [RULE_PATH, AGENTS_PATH], "RULE_FILES_PARSED", "NOT_APPLICABLE", "NOT_APPLICABLE", "AUTHORIZED_FOR_AUDIT_ONLY", ["V5.0.2 remains historical"], evidence_metadata=proven),
        rec("asset-coverage", "ASSET_COVERAGE", "independent audit asset coverage", f"Expected={coverage.get('expected_path_count')} actual={coverage.get('actual_path_count')} missing={len(coverage.get('missing', []))} extra={len(coverage.get('extra', []))}.", "COMPUTED", "REPOSITORY", ["validation", "research", "manifests"], ["closure/closure_audit.json", "closure/complete_research_file_ledger.json"], "DETERMINISTIC_COMPUTATION_COMPLETE", "NOT_APPLICABLE", "NOT_APPLICABLE", "AUDIT_ONLY", ["coverage does not prove predictive value"], evidence_metadata=computed("frozen expected registry + live git ls-files", "repository tracked audit assets", "historical V5.0.2 asset ledger reused by V5.0.3", "research_asset_coverage")),
        rec("route-state", "ROUTE_STATE", "canonical route closure", f"UNRESOLVED={len(route.get('unresolved', []))}; missing_result_evidence={len(route.get('missing_result_evidence', []))}.", "COMPUTED", "REPOSITORY", ["canonical draw research routes"], ["closure/closure_audit.json", "closure/experiment_ledger.json"], "DETERMINISTIC_COMPUTATION_COMPLETE", "MIXED", "NOT_APPLICABLE", "AUDIT_ONLY", ["unresolved routes prohibit exhaustion"], evidence_metadata=computed("experiment route ledger", "registered canonical routes", "route closure classifier", "experiment_route_closure")),
        rec("decision-current", "AUDIT_DECISION", "current audit decision", str(audit.get("decision")), "COMPUTED", "REPOSITORY", ["draw-signal closure audit"], ["closure/decision.json", "closure/closure_audit.json"], "DETERMINISTIC_DECISION_COMPLETE", "RECONSTRUCTED_RESEARCH_ONLY", str(prereg.get("holdout_status") or "NOT_APPLICABLE"), "NOT_AUTHORIZED_FOR_RUN", ["decision is not an experiment result"], evidence_metadata=computed("candidate sets + route closure", "current repository audit", "decide_and_preregister", "decision")),
        rec("round-candidate", "DOMAIN_CANDIDATE", "round reconstructed research candidate", "round is only a reconstructed-research preregistration candidate within KOR_KLeague1; it has not run and its predictive value is unproved.", "INFERRED", "DOMAIN_SPECIFIC", domains, ["closure/feature_difference.json", "closure/decision.json"], "CANDIDATE_IDENTIFIED_NOT_RUN", "PIT_RECONSTRUCTED_SCHEDULE_FIELD_RESEARCH_ONLY", str(prereg.get("holdout_status") or "NOT_YET_PROVEN_UNTOUCHED"), "NOT_AUTHORIZED", ["predictive gain unproved", "scope cannot expand"], inference_qualifier="候选"),
        rec("round-predictive-value", "SIGNAL_EFFECTIVENESS_STATUS", "round predictive value", "round predictive value remains unproved.", "UNPROVEN", "DOMAIN_SPECIFIC", ["KOR_KLeague1"], ["closure/feature_difference.json", "closure/decision.json"], "PRE_REGISTERED_NOT_RUN", "RECONSTRUCTED_NOT_STRICT_PIT", "NOT_YET_PROVEN_UNTOUCHED", "NOT_AUTHORIZED", ["no preregistered result", "coverage does not prove value"]),
        rec("draw-signal-effectiveness", "SIGNAL_EFFECTIVENESS_STATUS", "draw signal effectiveness", "Current evidence does not prove an effective draw signal.", "UNPROVEN", "GLOBAL", ["all competitions"], ["closure/claim_records.json", "closure/feature_difference.json"], "NO_PROMOTION_GRADE_EXPERIMENT_RESULT", "UNPROVEN_FOR_GLOBAL_SIGNAL", "NOT_YET_PROVEN_UNTOUCHED", "NOT_AUTHORIZED", ["one domain candidate cannot prove global effectiveness"]),
        rec("draw-problem-solved", "DRAW_PROBLEM_STATUS", "draw problem solved", "The draw problem is not proved solved.", "UNPROVEN", "GLOBAL", ["actual target scope"], ["closure/claim_records.json"], "NO_COMPLETE_INDEPENDENT_RESULT", "UNPROVEN", "NOT_YET_PROVEN_UNTOUCHED", "NOT_AUTHORIZED", ["no complete independent leakage-free evidence"]),
        rec("training-authorization", "ACTION_AUTHORIZATION", "model training", "Training is not authorized and has not executed.", "NOT_AUTHORIZED", "ACTION", ["MODEL_TRAINING"], ["closure/closure_audit.json", "closure/decision.json"], "NOT_EXECUTED", "UNPROVEN_FOR_TRAINING", "NOT_YET_PROVEN_UNTOUCHED", "NOT_AUTHORIZED", ["separate approval required"], action="MODEL_TRAINING"),
        rec("round-run-authorization", "ACTION_AUTHORIZATION", "K-League round experiment", "The K-League round experiment is not authorized and has not run.", "NOT_AUTHORIZED", "ACTION", ["ROUND_EXPERIMENT"], ["closure/decision.json", "closure/closure_audit.json"], "PRE_REGISTERED_NOT_RUN", "RECONSTRUCTED_NOT_STRICT_PIT", "NOT_YET_PROVEN_UNTOUCHED", "NOT_AUTHORIZED", ["separate approval required"], action="ROUND_EXPERIMENT"),
        rec("provider-authorization", "ACTION_AUTHORIZATION", "Provider requests and secret access", "Provider requests and secret access are not authorized; audit business code made zero provider request attempts.", "NOT_AUTHORIZED", "ACTION", ["PROVIDER_REQUEST", "SECRET_ACCESS"], ["closure/closure_audit.json"], "NOT_EXECUTED", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_AUTHORIZED", ["workflow infrastructure network is separately reported"], action="PROVIDER_REQUEST"),
        rec("formal-asset-authorization", "ACTION_AUTHORIZATION", "formal assets and weight", "Formal asset and formal-weight changes are not authorized and did not occur.", "NOT_AUTHORIZED", "ACTION", ["FORMAL_ASSET_CHANGE", "FORMAL_WEIGHT_CHANGE"], ["closure/closure_audit.json"], "NOT_EXECUTED", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_AUTHORIZED", ["formal_weight remains zero"], action="FORMAL_ASSET_CHANGE"),
        rec("withdraw-exhausted", "HISTORICAL_CLAIM_WITHDRAWAL", "historical exhaustion claim", "The historical exhaustion claim is explicitly withdrawn and replaced.", "PROVEN", "REPOSITORY", ["draw-signal closure audit history"], [RULE_PATH, "closure/closure_audit.json", "closure/claim_records.json"], "GOVERNANCE_WITHDRAWAL_RECORDED", "RECONSTRUCTED_CANDIDATE_EXISTS", "NOT_YET_PROVEN_UNTOUCHED", "AUDIT_ONLY", ["withdrawal does not prove candidate effectiveness"], evidence_metadata=proven, old_claim="EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING", withdrawal_statement="撤销旧结论 EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING", withdrawal_reason="KOR_KLeague1 reconstructed round candidate exists and canonical routes remain unresolved", replacement_claim="PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET", replacement_evidence_status="COMPUTED"),
    ]
    contexts = {cid: {"user_authorized": False, **({"pit_status": "UNPROVEN"} if cid == "training-authorization" else {})} for cid in ("training-authorization", "round-run-authorization", "provider-authorization", "formal-asset-authorization")}
    return records, contexts


def build_report(records: Sequence[Mapping[str, Any]], audit: Mapping[str, Any], previous_head: str = PREVIOUS_EXACT_HEAD) -> dict[str, Any]:
    by = {s: [dict(r) for r in records if r["evidence_status"] == s] for s in EVIDENCE_STATUSES}
    return {
        "schema_version": "DRAW-SIGNAL-CLAIM-REPORT-V503-1.1",
        "accurate_object": {"pr": 77, "base": "main", "branch": "research/draw-challenger-v502", "previous_head": previous_head, "exact_new_head": audit["head"], "draft": True, "merged": False},
        "directly_proven": by["PROVEN"], "program_computed": by["COMPUTED"],
        "inferences_and_candidates": by["INFERRED"], "unproven": by["UNPROVEN"],
        "not_authorized": by["NOT_AUTHORIZED"], "execution_boundary": audit.get("execution_boundary", {}),
        "cold_conclusion": COLD_CONCLUSION,
    }


def report_markdown(report: Mapping[str, Any]) -> str:
    o = report["accurate_object"]
    lines = ["# V5.0.3 Structured Claim Report", "", "## 1. 准确对象", f"- PR: #{o['pr']}", f"- base: `{o['base']}`", f"- branch: `{o['branch']}`", f"- 修复前HEAD: `{o['previous_head']}`", f"- 准确新HEAD: `{o['exact_new_head']}`", f"- Draft: `{str(o['draft']).lower()}`", f"- merged: `{str(o['merged']).lower()}`", ""]
    for title, key in (("2. 已直接证明", "directly_proven"), ("3. 程序计算结果", "program_computed"), ("4. 推断和候选", "inferences_and_candidates"), ("5. 尚未证明", "unproven"), ("6. 尚未授权", "not_authorized")):
        lines += [f"## {title}"] + ([f"- `{r['claim_id']}` [{r['evidence_status']}]: {r['claim_text']}" for r in report[key]] or ["- 无"]) + [""]
    lines += ["## 7. 实际执行边界", "", "```json", json.dumps(report["execution_boundary"], ensure_ascii=False, indent=2), "```", "", "## 8. 冷结论", "", report["cold_conclusion"], ""]
    return "\n".join(lines)


def _expected_outputs(audit: Mapping[str, Any], root: Path) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    rule = validate_rule_sources(root)
    records, contexts = build_current_claims(audit, rule)
    verification = validate_claims(records, contexts)
    records_obj = {"schema_version": "DRAW-SIGNAL-CLAIM-RECORDS-V503-1.1", "head": audit["head"], "records": records, "records_sha256": canonical_sha(records)}
    report = build_report(records, audit)
    markdown = report_markdown(report)
    verification = dict(verification)
    verification["binding"] = {
        "audit_head": audit["head"], "audit_sha256": str(audit.get("audit_sha256") or canonical_sha(audit)),
        "claim_records_sha256": records_obj["records_sha256"], "claim_records_object_sha256": canonical_sha(records_obj),
        "claim_report_json_sha256": canonical_sha(report), "claim_report_markdown_sha256": sha256_bytes(markdown.encode("utf-8")),
        "deterministic_renderer": "build_current_claims+build_report+report_markdown@V503-1.1",
    }
    return records_obj, report, markdown, verification, rule


def write_claim_outputs(out: Path, audit: Mapping[str, Any], root: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    records, report, markdown, verification, rule = _expected_outputs(audit, root)
    for name, value in (("claim_records.json", records), ("claim_contract_verification.json", verification), ("claim_report.json", report)):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (out / "claim_report.md").write_text(markdown, "utf-8")
    return {"rule_info": rule, "verification": verification, "report": report, "records": records["records"]}


def _validate_report_section_purity(report: Mapping[str, Any]) -> None:
    for status, section in STATUS_SECTION_KEYS.items():
        rows = report.get(section)
        if not isinstance(rows, list) or any(r.get("evidence_status") != status for r in rows):
            raise ValueError(f"claim report section status mismatch: {section}")


def verify_existing(closure_dir: Path, root: Path) -> dict[str, Any]:
    audit = json.loads((closure_dir / "closure_audit.json").read_text("utf-8"))
    exp_records, exp_report, exp_md, exp_verify, rule = _expected_outputs(audit, root)
    got_records = json.loads((closure_dir / "claim_records.json").read_text("utf-8"))
    got_report = json.loads((closure_dir / "claim_report.json").read_text("utf-8"))
    got_md = (closure_dir / "claim_report.md").read_text("utf-8")
    got_verify = json.loads((closure_dir / "claim_contract_verification.json").read_text("utf-8"))
    if got_records.get("records_sha256") != canonical_sha(got_records.get("records", [])):
        raise ValueError("stored records_sha256 does not match stored records")
    _validate_report_section_purity(got_report)
    for label, actual, expected in (("claim_records.json", got_records, exp_records), ("claim_report.json", got_report, exp_report), ("claim_report.md", got_md, exp_md), ("claim_contract_verification.json", got_verify, exp_verify)):
        if actual != expected:
            raise ValueError(f"{label} does not equal deterministic audit-derived output")
    return {"status": "PASS", "rule_info": rule, "claim_verification": exp_verify, "bindings": exp_verify["binding"]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--closure-dir", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(json.dumps(verify_existing(args.closure_dir, args.root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
