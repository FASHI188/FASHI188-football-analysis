#!/usr/bin/env python3
"""V6.9.0 full-system issue registry.

This registry separates actual engineering defects from research gates, external-data gaps and
irreducible uncertainty. It prevents the project from declaring a model 'fixed' merely because
code runs, and prevents already-fixed defects from being repeatedly reopened without new evidence.
It never changes CURRENT or any formal probability.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "manifests"
OUT = M / "v6_system_issue_registry_v690_status.json"
SOURCES = {
    "formal": "v501_upgrade_status.json",
    "market_forward": "v6_market_first_forward_evaluation_v651_status.json",
    "draw": "v6_draw_resolution_registry_v674_status.json",
    "team_fetch": "v6_team_configuration_fetch_v660_status.json",
    "team_audit": "v6_team_configuration_weekly_v660_status.json",
    "ladder": "v6_full_market_ladder_v680_status.json",
    "total_ident": "v6_total_ladder_identifiability_v681_status.json",
    "matrix_solver": "v6_multiline_market_matrix_projection_v682_status.json",
    "consensus": "v6_market_consensus_refresh_v683_status.json",
    "matrix_readiness": "prospective_market_matrix_validation_v548_status.json",
}


def load(name: str) -> dict[str, Any]:
    path = M / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def sha(name: str) -> str | None:
    path = M / name
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def issue(issue_id: str, area: str, status: str, before: str, repair: str, evidence: str, remaining: str, blocks_formal_upgrade: bool) -> dict[str, Any]:
    return {
        "issue_id": issue_id, "area": area, "status": status, "before": before,
        "repair_applied": repair, "current_evidence": evidence,
        "remaining_blocker": remaining, "blocks_formal_upgrade": blocks_formal_upgrade,
    }


def main() -> int:
    x = {key: load(value) for key, value in SOURCES.items()}
    team_fetch = x["team_fetch"]
    team_audit = x["team_audit"]
    ident = x["total_ident"]
    consensus = x["consensus"]
    readiness = x["matrix_readiness"]
    forward = x["market_forward"]
    issues = [
        issue("SYS-001", "formal rule authority", "FIXED",
              "risk of research/current mixing across conversations",
              "unique CURRENT verification plus hash-bound repo runtime authority",
              f"{x['formal'].get('status')} / CURRENT={x['formal'].get('formal_rule_version')}",
              "none; V6 research still cannot auto-promote", False),
        issue("SYS-002", "1X2 primary architecture", "FORWARD_GATED",
              "legacy model primary accuracy was below market on matched samples",
              "research champion changed to synchronized de-vigged market primary with frozen selective margin gate; legacy model is residual/fallback only",
              f"forward={forward.get('evaluation_status')}; settled={forward.get('settled_count')}",
              "fresh post-freeze settled sample and Wilson gate", True),
        issue("SYS-003", "draw probability / Top-1 confusion", "FIXED",
              "low draw Top-1 recall was treated as if draw probability itself were broken",
              "calibration audit separated probability quality from argmax; forced draw rewrites rejected and locked",
              f"draw_registry={x['draw'].get('status')}; heldout_ECE={((x['draw'].get('diagnosis') or {}).get('heldout_draw_probability_ece'))}",
              "only orthogonal PIT context may challenge prospectively", False),
        issue("SYS-004", "weekly team roster coverage", "FIXED",
              "K League missing; seven ESPN empty rosters; hundreds of commits per weekly scan",
              "17-domain roster fallback + official K League membership crosscheck + one aggregate weekly snapshot",
              f"fetch={team_fetch.get('status')}; domains={team_fetch.get('domains_with_snapshots')}; teams={team_fetch.get('snapshots_written')}; errors={len(team_fetch.get('errors') or [])}",
              "none for roster baseline", False),
        issue("SYS-005", "team context semantics", "EXTERNAL_DATA_GAP",
              "empty coach/injury/transaction fields could look complete",
              "field-specific source-health and fail-closed eligibility gates",
              f"eligibility={team_audit.get('feature_eligibility')}",
              "manager source coverage and K League injury/transaction/depth context require independent current sources; weekly ChatGPT scan can enrich these but they cannot be invented", True),
        issue("SYS-006", "market surface information loss", "FIXED",
              "canonical snapshot retained only one AH/OU main line while raw provider exposed many lines",
              "V6.8.0 preserves all full-time prematch 1X2/AH/total ladders with raw hash linkage",
              f"ladder={x['ladder'].get('status')}; bundles={x['ladder'].get('bundle_count')}; mean_total_lines={x['ladder'].get('mean_distinct_total_lines')}; mean_ah_lines={x['ladder'].get('mean_distinct_ah_lines')}",
              "none at single-provider extraction layer", False),
        issue("SYS-007", "0-7+ total-goals identifiability", "FIXED_BY_FAIL_CLOSED_POLICY",
              "risk of extrapolating a complete integer distribution from one O/U line",
              "direct CDF identification only from observed half-goal lines; exact market-only 0-7+ requires 0.5..6.5 all present; missing buckets are never fabricated",
              f"bundles={ident.get('bundle_count')}; outputs={ident.get('operational_counts')}; monotonicity_failures={ident.get('monotonicity_failure_count')}",
              "current ladders identify only partial CDF; complete distribution therefore needs an explicit prior plus audited market projection", False),
        issue("SYS-008", "joint total/score market projection", "FORWARD_GATED",
              "old shadow solver was hard-coded to 1X2+OU2.5",
              "V6.8.2 minimum-KL/IPF solver accepts synchronized 1X2 plus multiple half-goal total constraints and emits convergence/residual/probability/KL audits",
              f"engineering={x['matrix_solver'].get('status')}; residual={x['matrix_solver'].get('max_constraint_residual')}; sum_residual={x['matrix_solver'].get('probability_sum_residual')}",
              "must freeze real formal priors before kickoff and accumulate settled OOS score/total evidence", True),
        issue("SYS-009", "independent market consensus audit", "FIXED",
              "legacy consensus receipt stayed at zero after newer exact-line n=2 consensus files were created",
              "V6.8.3 scans the evidence directory itself and refreshes the legacy receipt for all downstream consumers",
              f"status={consensus.get('status')}; valid={consensus.get('valid_consensus_count')}; invalid={consensus.get('invalid_consensus_count')}; comps={len(consensus.get('competition_counts') or {})}",
              "none for inventory; outcome evidence is separate", False),
        issue("SYS-010", "prospective matrix readiness semantics", "FORWARD_GATED",
              "stale downstream report incorrectly implied no consensus",
              "readiness now retriggers on refreshed consensus and explicitly separates consensus availability from settled outcome scoring",
              f"consensus={readiness.get('consensus_audit_status')}; scored={readiness.get('unique_scored_match_count')}; status={readiness.get('status')}",
              "settled shadow outcome rows and minimum sample/bootstrap gates", True),
        issue("SYS-011", "exact score overconfidence", "FIXED_BY_OUTPUT_GOVERNANCE",
              "exact-score Top-1 historically has low absolute hit probability and can be mistaken for a high-confidence point forecast",
              "exact score remains a probability ranking from one audited joint matrix; no separate hand-picked score, and unavailable/nonconverged matrices fail closed",
              "V6.8.2 exposes Top1, Top2, gap, Top3 cumulative and entropy without claiming prediction accuracy",
              "irreducible score uncertainty remains; predictive improvement requires future joint-matrix outcome evidence", False),
        issue("SYS-012", "EV / price integrity", "FIXED_BY_EXISTING_HARD_GATE",
              "risk of value claims without actual tradable frozen prices or with failed matrix",
              "retain CURRENT rule: no EV/minimum price/value without actual frozen price; score/AH/OU EV unavailable when unified matrix fails",
              f"formal_current={x['formal'].get('formal_rule_version')}",
              "none; this is a permanent hard gate", False),
    ]
    open_code_defects = [row for row in issues if row["status"] == "OPEN_CODE_DEFECT"]
    formal_blockers = [row["issue_id"] for row in issues if row["blocks_formal_upgrade"]]
    payload = {
        "schema_version": "V6.9.0-full-system-issue-registry-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_NO_KNOWN_OPEN_CODE_DEFECTS" if not open_code_defects else "FAIL_OPEN_CODE_DEFECTS",
        "formal_current_version": x["formal"].get("formal_rule_version"),
        "formal_current_unchanged_by_this_registry": True,
        "issue_counts": {
            "total": len(issues),
            "fixed_or_fail_closed": sum(row["status"].startswith("FIXED") for row in issues),
            "forward_gated": sum(row["status"] == "FORWARD_GATED" for row in issues),
            "external_data_gap": sum(row["status"] == "EXTERNAL_DATA_GAP" for row in issues),
            "open_code_defects": len(open_code_defects),
        },
        "formal_upgrade_blockers": formal_blockers,
        "issues": issues,
        "source_receipts": {key: {"path": value, "sha256": sha(value)} for key, value in SOURCES.items()},
        "governance": {
            "research_only": True,
            "no_current_rule_change_by_registry": True,
            "no_formal_weight_change": True,
            "no_runtime_probability_change": True,
            "a_forward_gate_is_not_a_code_defect": True,
            "external_missing_information_is_never_fabricated": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not open_code_defects else 2


if __name__ == "__main__":
    raise SystemExit(main())
