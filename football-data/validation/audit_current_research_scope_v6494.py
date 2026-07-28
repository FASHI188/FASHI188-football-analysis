#!/usr/bin/env python3
"""V6.49.4 guard: prevent retired handicap prediction stages from re-entering active execution.

The guard intentionally does NOT reject asian_handicap fields in market evidence or synchronized
1X2/AH/OU capture. It only rejects active workflow/executable names that look like retired
standalone handicap prediction research, and obvious retired-target wording in active workflows.
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

FORBIDDEN_NAME_TOKENS = (
    "asian-handicap-increment",
    "asian_handicap_increment",
    "handicap-1x2",
    "handicap_1x2",
    "rangqiu",
)
FORBIDDEN_WORKFLOW_PHRASES = (
    "让球胜平负",
    "handicap accuracy",
    "handicap_accuracy",
    "handicap forward gate",
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
        errors.append("retired_handicap_active_files:" + ",".join(sorted(set(forbidden_files))))

    market_policy = scope.get("market_input_policy") or {}
    if market_policy.get("asian_handicap") != "INPUT_AND_AUDIT_ONLY_NOT_PREDICTION_TARGET":
        errors.append("asian_handicap_market_policy_drift")
    if market_policy.get("synchronized_1x2_ah_ou_capture") != "KEEP":
        errors.append("synchronized_market_capture_should_remain")

    status = {
        "schema_version": "V6.49.4-current-research-scope-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if not errors else "FAIL",
        "active_prediction_targets_in_order": active,
        "retired_handicap_prediction_target": True,
        "asian_handicap_market_input_retained": True,
        "scanned_active_workflow_and_validation_files": scanned_files,
        "forbidden_active_files": sorted(set(forbidden_files)),
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
