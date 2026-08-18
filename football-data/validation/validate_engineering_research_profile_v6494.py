#!/usr/bin/env python3
"""Validate the frozen V6.49.x engineering research profile.

This file is deliberately NOT a project-state or authorization validator.
Airtable《当前状态》 remains the only live dynamic project-state source.
The profile only preserves a reproducible engineering/research surface that may
be used after an explicit user-authorized/manual dispatch.

Default execution is read-only. A receipt is written only with --write-receipt,
which is intended for ephemeral CI/workflow checkouts.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "v6_engineering_research_profile_v6494.json"
OUT = ROOT / "manifests" / "v6_engineering_research_profile_v6494_status.json"
EXPECTED_TARGETS = ["90m_1x2", "direct_total_goals", "exact_score_from_unified_matrix"]
FORBIDDEN_ACTIVE_WORKFLOWS = (
    "football-v6494-active-research-scope-guard.yml",
    "football-v6494-current-state-audit.yml",
)


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return obj


def validate() -> dict[str, Any]:
    profile = load_json(PROFILE)
    errors: list[str] = []

    def require(ok: bool, code: str) -> None:
        if not ok:
            errors.append(code)

    require(profile.get("status") == "ENGINEERING_RESEARCH_PROFILE", "profile_status_invalid")
    require(profile.get("project_state_authority") is False, "project_state_authority_must_be_false")
    require(profile.get("execution_authority") == "EXPLICIT_DISPATCH_ONLY", "execution_authority_not_manual")
    require(profile.get("automatic_task_selection") is False, "automatic_task_selection_must_be_false")
    require(profile.get("prediction_targets_in_order") == EXPECTED_TARGETS, "prediction_target_order_drift")

    formal = profile.get("formal_current_binding") or {}
    require(formal.get("authority") is False, "historical_formal_binding_claims_authority")

    hard = profile.get("hard_guards") or {}
    for key in (
        "no_project_state_authority",
        "no_user_authorization_authority",
        "no_formal_current_authority",
        "no_automatic_research_resume",
        "manual_dispatch_only",
    ):
        require(hard.get(key) is True, f"hard_guard_missing:{key}")
    for key in ("formal_weight_change", "probability_change", "scientific_result_change"):
        require(hard.get(key) is False, f"forbidden_change_flag_true:{key}")

    repo = ROOT.parent
    workflow_root = repo / ".github" / "workflows"
    present_forbidden = [name for name in FORBIDDEN_ACTIVE_WORKFLOWS if (workflow_root / name).exists()]
    if present_forbidden:
        errors.append("legacy_active_current_workflows_present:" + ",".join(present_forbidden))

    payload = {
        "schema_version": "V6.49.5-engineering-research-profile-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not errors else "FAIL",
        "project_state_authority": False,
        "execution_authority": "EXPLICIT_DISPATCH_ONLY",
        "prediction_targets_in_order": profile.get("prediction_targets_in_order"),
        "historical_formal_rule_version_at_profile_freeze": formal.get("historical_version_at_profile_freeze"),
        "errors": errors,
        "governance": {
            "airtable_current_state_is_live_project_state": True,
            "repository_profile_is_project_state": False,
            "formal_weight_change": False,
            "probability_change": False,
            "scientific_result_change": False,
        },
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    payload = validate()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary or not args.write_receipt:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
