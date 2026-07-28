#!/usr/bin/env python3
"""V6.49.5 active-scope guard.

The current active prediction surface is unique:
    V6.49.2 90m 1X2 -> direct total goals -> exact score,
with V6.49.5 match-context failure-regime auditing and V6.5.1 official 90m settlement.

Historical code/evidence may remain for reproducibility, but retired prediction/evaluation
workflows must not regain execution authority. Asian-handicap data remain synchronized market
input/audit only. The V6.1.2 result-resolver Python module is explicitly allowed as a library
because current V6.5.1 settlement imports its ESPN identity/regulation-score helpers.
Formal CURRENT V5.0.1 is not modified.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCOPE = ROOT / "config" / "v6_current_research_scope_v6494.json"
OUT = ROOT / "manifests" / "v6_current_research_scope_v6494_status.json"

FORBIDDEN_GENERAL_NAME_TOKENS = (
    "asian-handicap-increment",
    "asian_handicap_increment",
    "handicap-1x2",
    "handicap_1x2",
    "rangqiu",
    "system-issue-registry-v690",
    "v6_system_issue_registry_v690",
)
FORBIDDEN_WORKFLOW_NAME_TOKENS = (
    "pristine-forward-autofeed-v612",
    "pristine-forward-ledger-v612",
    "pristine-forward-result-resolver-v612",
    "pristine-forward-audit-v613",
    "risk-controlled-forward-v632",
    "multiline-research-forward-v6853",
    "v6128-adaptive-forward",
    "asian-handicap-increment",
    "system-issue-registry-v690",
)
FORBIDDEN_WORKFLOW_PHRASES = (
    "让球胜平负",
    "handicap accuracy",
    "handicap_accuracy",
    "handicap forward gate",
)
RETIRED_WORKFLOW_PATHS = (
    REPO / ".github" / "workflows" / "football-v6-pristine-forward-autofeed-v612.yml",
    REPO / ".github" / "workflows" / "football-v6-pristine-forward-ledger-v612.yml",
    REPO / ".github" / "workflows" / "football-v6-pristine-forward-result-resolver-v612.yml",
    REPO / ".github" / "workflows" / "football-v6-pristine-forward-audit-v613.yml",
    REPO / ".github" / "workflows" / "football-v6-risk-controlled-forward-v632.yml",
    REPO / ".github" / "workflows" / "football-v6-multiline-research-forward-v6853.yml",
    REPO / ".github" / "workflows" / "football-v6128-adaptive-forward.yml",
    REPO / ".github" / "workflows" / "football-v6112-asian-handicap-increment.yml",
    REPO / ".github" / "workflows" / "football-v6-system-issue-registry-v690.yml",
)
LEGACY_V690_ACTIVE_PATHS = (
    REPO / ".github" / "workflows" / "football-v6-system-issue-registry-v690.yml",
    ROOT / "validation" / "v6_system_issue_registry_v690.py",
    ROOT / "manifests" / "v6_system_issue_registry_v690_status.json",
)
ALLOWED_LEGACY_LIBRARY = ROOT / "validation" / "v6_pristine_forward_result_resolver_v612.py"


def main() -> int:
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    active = [x.get("target") for x in scope.get("active_prediction_targets_in_order", [])]
    expected = ["90m_1x2", "direct_total_goals", "exact_score_from_unified_matrix"]
    errors: list[str] = []
    if active != expected:
        errors.append(f"active_target_order:{active!r}")

    retired = {x.get("target"): x for x in scope.get("retired_prediction_targets", [])}
    h = retired.get("handicap_1x2") or {}
    if h.get("status") != "RETIRED_NO_ACTIVE_RESEARCH" or h.get("forward_gate") is not False:
        errors.append("handicap_1x2_not_retired")

    retired_surfaces = {x.get("artifact_family"): x for x in scope.get("retired_execution_surfaces", [])}
    required_retired = {
        "v6_pristine_forward_v61x",
        "v6_risk_controlled_forward_v632",
        "v6_multiline_research_forward_v6853",
        "v6_system_issue_registry_v690",
        "v6_nested_adaptive_1x2_v6127_v6128",
    }
    missing_retired = sorted(required_retired - set(retired_surfaces))
    if missing_retired:
        errors.append("retired_surface_registry_missing:" + ",".join(missing_retired))
    for family in sorted(required_retired & set(retired_surfaces)):
        item = retired_surfaces[family] or {}
        if item.get("status") != "RETIRED_ARCHIVE_ONLY" or int(item.get("execution_authority", -1)) != 0:
            errors.append(f"retired_surface_authority_drift:{family}")

    scanned_files = 0
    forbidden_files: list[str] = []
    workflow_root = REPO / ".github" / "workflows"
    validation_root = ROOT / "validation"
    for base in (workflow_root, validation_root):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            scanned_files += 1
            rel = str(path.relative_to(REPO)).replace("\\", "/")
            low_name = path.name.lower()
            if path.name == Path(__file__).name:
                continue
            if any(token in low_name for token in FORBIDDEN_GENERAL_NAME_TOKENS):
                forbidden_files.append(rel)
                continue
            is_workflow = path.parent == workflow_root or "/.github/workflows/" in f"/{rel}"
            if is_workflow:
                if any(token in low_name for token in FORBIDDEN_WORKFLOW_NAME_TOKENS):
                    forbidden_files.append(rel)
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                if any(phrase.lower() in text for phrase in FORBIDDEN_WORKFLOW_PHRASES):
                    forbidden_files.append(rel)

    if forbidden_files:
        errors.append("retired_active_files:" + ",".join(sorted(set(forbidden_files))))

    retired_workflows_present = [
        str(p.relative_to(REPO)).replace("\\", "/") for p in RETIRED_WORKFLOW_PATHS if p.exists()
    ]
    if retired_workflows_present:
        errors.append("retired_workflows_present:" + ",".join(retired_workflows_present))

    v690_paths_present = [str(p.relative_to(REPO)).replace("\\", "/") for p in LEGACY_V690_ACTIVE_PATHS if p.exists()]
    if v690_paths_present:
        errors.append("legacy_v690_active_paths_present:" + ",".join(v690_paths_present))

    if not ALLOWED_LEGACY_LIBRARY.exists():
        errors.append("required_current_settlement_helper_missing:v6_pristine_forward_result_resolver_v612.py")

    allowed_libs = {x.get("artifact") for x in scope.get("allowed_legacy_library_dependencies", [])}
    if "v6_pristine_forward_result_resolver_v612.py" not in allowed_libs:
        errors.append("allowed_legacy_settlement_library_not_declared")

    market_policy = scope.get("market_input_policy") or {}
    if market_policy.get("asian_handicap") != "INPUT_AND_AUDIT_ONLY_NOT_PREDICTION_TARGET":
        errors.append("asian_handicap_market_policy_drift")
    if market_policy.get("synchronized_1x2_ah_ou_capture") != "KEEP":
        errors.append("synchronized_market_capture_should_remain")

    precedence = scope.get("execution_precedence") or {}
    if precedence.get("current_runtime_truth_registry") != "v6_42_issue_remediation_v6489_status.json":
        errors.append("current_runtime_truth_registry_drift")

    hard = scope.get("hard_guards") or {}
    required_guards = (
        "do_not_reactivate_v61x_pristine_forward_workflows",
        "do_not_reactivate_v632_risk_controlled_forward",
        "do_not_reactivate_v6853_fast100_forward",
        "do_not_reactivate_v690_legacy_system_registry",
        "do_not_reactivate_v6127_v6128_adaptive_forward",
        "single_current_runtime_truth_surface",
        "single_current_1x2_forward_chain_v6492",
        "single_current_total_score_forward_chain_v6492",
        "context_news_never_manually_changes_1x2_probability",
    )
    for key in required_guards:
        if hard.get(key) is not True:
            errors.append(f"hard_guard_missing:{key}")

    status = {
        "schema_version": "V6.49.5-current-research-scope-status-r3",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if not errors else "FAIL",
        "active_prediction_targets_in_order": active,
        "retired_handicap_prediction_target": True,
        "asian_handicap_market_input_retained": True,
        "retired_execution_families": sorted(required_retired),
        "retired_workflows_present": retired_workflows_present,
        "retired_v690_system_registry": not v690_paths_present,
        "current_runtime_truth_registry": precedence.get("current_runtime_truth_registry"),
        "allowed_legacy_settlement_library_present": ALLOWED_LEGACY_LIBRARY.exists(),
        "scanned_active_workflow_and_validation_files": scanned_files,
        "forbidden_active_files": sorted(set(forbidden_files)),
        "legacy_v690_active_paths_present": v690_paths_present,
        "errors": errors,
        "governance": {
            "historical_code_may_exist_without_execution_authority": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
