#!/usr/bin/env python3
"""V6.49.5 active-scope guard.

Prevents two classes of retired surfaces from re-entering active execution:
1) handicap win-draw-loss / standalone Asian-handicap prediction research;
2) the legacy V6.9.x full-system issue registry, whose global team-context/blocker semantics
   predate the current match-bound V6.49.x runtime-truth registry.

Asian-handicap market fields remain valid synchronized input/audit data. Frozen V6.47.x
algorithm definitions may remain library dependencies, but their old forward ledgers have
execution authority 0. Formal CURRENT V5.0.1 is not modified.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCOPE = ROOT / "config" / "v6_current_research_scope_v6494.json"
OUT = ROOT / "manifests" / "v6_current_research_scope_v6494_status.json"

FORBIDDEN_NAME_TOKENS = (
    "asian-handicap-increment",
    "asian_handicap_increment",
    "handicap-1x2",
    "handicap_1x2",
    "rangqiu",
    "system-issue-registry-v690",
    "v6_system_issue_registry_v690",
)
FORBIDDEN_WORKFLOW_PHRASES = (
    "让球胜平负",
    "handicap accuracy",
    "handicap_accuracy",
    "handicap forward gate",
)
LEGACY_V690_ACTIVE_PATHS = (
    REPO / ".github" / "workflows" / "football-v6-system-issue-registry-v690.yml",
    ROOT / "validation" / "v6_system_issue_registry_v690.py",
    ROOT / "manifests" / "v6_system_issue_registry_v690_status.json",
)


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
    v690 = retired_surfaces.get("v6_system_issue_registry_v690") or {}
    if v690.get("status") != "RETIRED_ARCHIVE_ONLY" or int(v690.get("execution_authority", -1)) != 0:
        errors.append("legacy_v690_registry_not_retired")

    scanned_files = 0
    forbidden_files: list[str] = []
    for base in (REPO / ".github" / "workflows", ROOT / "validation"):
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
            if any(token in low_name for token in FORBIDDEN_NAME_TOKENS):
                forbidden_files.append(rel)
                continue
            if base.name == "workflows" or "/.github/workflows/" in f"/{rel}":
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                if any(phrase.lower() in text for phrase in FORBIDDEN_WORKFLOW_PHRASES):
                    forbidden_files.append(rel)

    if forbidden_files:
        errors.append("retired_active_files:" + ",".join(sorted(set(forbidden_files))))

    v690_paths_present = [str(p.relative_to(REPO)).replace("\\", "/") for p in LEGACY_V690_ACTIVE_PATHS if p.exists()]
    if v690_paths_present:
        errors.append("legacy_v690_active_paths_present:" + ",".join(v690_paths_present))

    market_policy = scope.get("market_input_policy") or {}
    if market_policy.get("asian_handicap") != "INPUT_AND_AUDIT_ONLY_NOT_PREDICTION_TARGET":
        errors.append("asian_handicap_market_policy_drift")
    if market_policy.get("synchronized_1x2_ah_ou_capture") != "KEEP":
        errors.append("synchronized_market_capture_should_remain")

    precedence = scope.get("execution_precedence") or {}
    if precedence.get("current_runtime_truth_registry") != "v6_42_issue_remediation_v6489_status.json":
        errors.append("current_runtime_truth_registry_drift")

    hard = scope.get("hard_guards") or {}
    if hard.get("do_not_reactivate_v690_legacy_system_registry") is not True:
        errors.append("v690_reactivation_guard_missing")
    if hard.get("single_current_runtime_truth_surface") is not True:
        errors.append("single_runtime_truth_guard_missing")
    if hard.get("context_news_never_manually_changes_1x2_probability") is not True:
        errors.append("context_manual_probability_guard_missing")

    status = {
        "schema_version": "V6.49.5-current-research-scope-status-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if not errors else "FAIL",
        "active_prediction_targets_in_order": active,
        "retired_handicap_prediction_target": True,
        "asian_handicap_market_input_retained": True,
        "retired_v690_system_registry": not v690_paths_present,
        "current_runtime_truth_registry": precedence.get("current_runtime_truth_registry"),
        "scanned_active_workflow_and_validation_files": scanned_files,
        "forbidden_active_files": sorted(set(forbidden_files)),
        "legacy_v690_active_paths_present": v690_paths_present,
        "errors": errors,
        "governance": {
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
