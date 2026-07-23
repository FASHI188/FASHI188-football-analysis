#!/usr/bin/env python3
"""V6.9.5 full-system issue registry.

Separates engineering defects from forward validation gates, external-data gaps and irreducible
uncertainty. V5.0.1 is formal CURRENT.

Roster semantics are taken from the validated mutually-exclusive effective ledger:
  STRICT_CURRENT > ACTIVE_MATCH_POOL > PROVISIONAL_ONLY > NO_ROSTER_CONTEXT.
Only STRICT_CURRENT satisfies the strict-current roster gate.

Forward semantics are also separated deliberately:
- V6.5.1 is the frozen market-first research champion and its own forward gate is authoritative for
  market-first promotion.
- V6.1.4 is a separate pristine-forward reference audit; its proper scores are diagnostic evidence,
  never a substitute for V6.5.1's frozen market-first sample.
- V6.5.1 readiness explains whether new post-freeze PIT evidence actually enters the frozen 1-72h
  prediction window. Acquisition problems are not repaired by widening/backfilling that window.

V6.9.5-r8 additionally hard-binds the V6.8.0 ladder receipt to the V6.8.1 identifiability receipt.
A stale downstream identifiability receipt is an engineering defect, not a valid fail-closed result.
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
    "market_readiness": "v6_market_first_forward_readiness_v651_status.json",
    "market_result_resolver": "v6_market_first_result_resolver_v651_status.json",
    "active_kambi": "v6_active_kambi_market_capture_v652_status.json",
    "pristine_probability": "v6_pristine_forward_probability_audit_v614_status.json",
    "pristine_audit": "v6_pristine_forward_audit_v613_status.json",
    "draw": "v6_draw_resolution_registry_v674_status.json",
    "team_fetch": "v6_team_configuration_fetch_v660_status.json",
    "team_repair": "v6_team_roster_repair_v664_status.json",
    "team_audit": "v6_team_configuration_weekly_v660_status.json",
    "current_roster_overlay": "v6_current_roster_overlay_v669_status.json",
    "team_effective": "v6_team_context_effective_v6610_status.json",
    "roster_gap": "v6_roster_gap_inventory_v6611_status.json",
    "ladder": "v6_full_market_ladder_v680_status.json",
    "total_ident": "v6_total_ladder_identifiability_v681_status.json",
    "matrix_solver": "v6_multiline_market_matrix_projection_v682_status.json",
    "consensus": "v6_market_consensus_refresh_v683_status.json",
    "legacy_matrix_readiness": "prospective_market_matrix_validation_v548_status.json",
    "matrix_forward_freeze": "v6_multiline_matrix_forward_freeze_v685_status.json",
    "matrix_forward": "v6_multiline_matrix_forward_v685_status.json",
}


def load(name: str) -> dict[str, Any]:
    path = M / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def sha(name: str) -> str | None:
    path = M / name
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def issue(i, area, status, before, repair, evidence, remaining, block):
    return {
        "issue_id": i,
        "area": area,
        "status": status,
        "before": before,
        "repair_applied": repair,
        "current_evidence": evidence,
        "remaining_blocker": remaining,
        "blocks_formal_upgrade": block,
    }


def main() -> int:
    x = {key: load(value) for key, value in SOURCES.items()}
    tr = x["team_repair"]
    ta = x["team_audit"]
    cro = x["current_roster_overlay"]
    eff = x["team_effective"]
    gaps = x["roster_gap"]
    ladder = x["ladder"]
    ident = x["total_ident"]
    con = x["consensus"]
    lr = x["legacy_matrix_readiness"]
    f = x["market_forward"]
    rd = x["market_readiness"]
    mr = x["market_result_resolver"]
    ak = x["active_kambi"]
    pp = x["pristine_probability"]
    pa = x["pristine_audit"]
    mf = x["matrix_forward_freeze"]
    me = x["matrix_forward"]

    elig = ta.get("feature_eligibility") or {}
    base_strict = int(elig.get("roster") or 0)
    latest = int(ta.get("latest_team_snapshots") or 0)
    states = eff.get("roster_context_states") or {}
    effective_valid = eff.get("status") == "PASS" and int(eff.get("team_count") or 0) == latest and latest > 0
    if effective_valid:
        strict_rosters = int(states.get("STRICT_CURRENT") or 0)
        active_pool = int(states.get("ACTIVE_MATCH_POOL") or 0)
        provisional_only = int(states.get("PROVISIONAL_ONLY") or 0)
        no_roster_context = int(states.get("NO_ROSTER_CONTEXT") or 0)
    else:
        overlay_status = str(cro.get("status") or "")
        overlay_valid = (
            overlay_status in {"PASS", "WARN_INVALID_RECORDS", "WARN_INVALID_CURRENT_EVIDENCE"}
            and int(cro.get("team_baseline_count") or 0) == latest
            and latest > 0
        )
        strict_rosters = max(
            base_strict,
            int(cro.get("effective_strict_current_roster_count") or 0) if overlay_valid else base_strict,
        )
        active_pool = 0
        provisional_only = 0
        no_roster_context = max(0, latest - strict_rosters)

    roster_gap = max(0, latest - strict_rosters)
    roster_closed = latest > 0 and roster_gap == 0
    state_conservation = (
        strict_rosters + active_pool + provisional_only + no_roster_context == latest
        if latest
        else False
    )
    gap_receipt_valid = (
        gaps.get("status") == "PASS"
        and int(gaps.get("team_count") or 0) == latest
        and int(gaps.get("unresolved_strict_current_gap_count") or -1) == roster_gap
    )

    manager_full = int(elig.get("full_context") or 0)
    manager_eligible = int(elig.get("manager") or 0)
    manager_closed = latest > 0 and manager_full == latest

    models = pp.get("probability_models") or {}
    formal_ref = models.get("formal") or {}
    pooled_ref = models.get("pooled") or {}
    direct_ref = models.get("direct") or {}
    readiness_eligible = int(rd.get("eligible_unique_matches") or 0)
    active_written = int(ak.get("formal_snapshot_count_written") or 0)
    active_eligible = int(ak.get("v651_timing_eligible_snapshot_count") or 0)
    market_blocker = (
        "restore recurring post-freeze active-league PIT snapshots inside the frozen 1-72h window; do not widen/backfill the window"
        if int(f.get("prediction_count") or 0) == 0 and readiness_eligible == 0
        else "accumulate fresh post-freeze market-first predictions, settled outcomes, competition coverage and Wilson/proper-score gates"
    )

    ladder_bundle_count = int(ladder.get("bundle_count") or 0)
    ident_bundle_count = int(ident.get("bundle_count") or 0)
    ident_source_declared = int(ident.get("source_declared_bundle_count") or 0)
    ident_source_actual = int(ident.get("source_actual_bundle_count") or 0)
    ident_source_match = ident.get("source_bundle_count_matches") is True
    ladder_ident_sync = (
        ladder.get("status") == "PASS"
        and ident.get("status") == "PASS"
        and ladder_bundle_count > 0
        and ladder_bundle_count == ident_bundle_count
        and ladder_bundle_count == ident_source_declared
        and ladder_bundle_count == ident_source_actual
        and ident_source_match
    )

    issues = [
        issue(
            "SYS-001",
            "formal rule authority",
            "FIXED",
            "risk of research/current mixing across conversations",
            "unique CURRENT verification plus hash-bound repo runtime authority",
            f"{x['formal'].get('status')} / CURRENT={x['formal'].get('formal_rule_version')}",
            "none; V6 research still cannot auto-promote",
            False,
        ),
        issue(
            "SYS-002",
            "1X2 primary architecture",
            "FORWARD_GATED",
            "legacy model primary accuracy was below market on matched samples",
            "research champion changed to synchronized de-vigged market primary with frozen selective margin gate; settlement uses an independent official-result resolver; separate pristine-forward proper scores are retained only as reference evidence",
            f"market_forward={f.get('evaluation_status')}; predictions={f.get('prediction_count')}; settled={f.get('settled_count')}; readiness={rd.get('status')}; eligible_post_freeze_matches={readiness_eligible}; readiness_lead_buckets={rd.get('post_freeze_file_lead_buckets')}; active_kambi={ak.get('status')}; active_kambi_written={active_written}; active_kambi_v651_eligible={active_eligible}; market_result_resolver={mr.get('status')}; pristine_reference_n={pp.get('settled_valid_count')}; pristine_audit={pa.get('status')}; pristine_formal_top1={formal_ref.get('top1_accuracy')}; pristine_formal_log={formal_ref.get('mean_log_score')}; pristine_direct_top1={direct_ref.get('top1_accuracy')}; pristine_pooled_top1={pooled_ref.get('top1_accuracy')}",
            market_blocker,
            True,
        ),
        issue(
            "SYS-003",
            "draw probability / Top-1 confusion",
            "FIXED",
            "low draw Top-1 recall was treated as if draw probability itself were broken",
            "calibration audit separated probability quality from argmax; forced draw rewrites rejected and locked",
            f"draw_registry={x['draw'].get('status')}; heldout_ECE={((x['draw'].get('diagnosis') or {}).get('heldout_draw_probability_ece'))}",
            "only orthogonal PIT context may challenge prospectively",
            False,
        ),
        issue(
            "SYS-004",
            "weekly team roster coverage",
            "FIXED" if roster_closed else "EXTERNAL_DATA_GAP",
            "record presence and recent-match activity could be mistaken for strict registered/current roster completeness",
            "V6.6.4 same-provider repair + V6.6.15 strict CURRENT overlay + V6.6.12 four-state effective ledger. Only STRICT_CURRENT passes; ACTIVE_MATCH_POOL and prior-season continuity remain explicitly non-strict.",
            f"domains={ta.get('domains_with_snapshots')}; teams={latest}; baseline_strict={base_strict}; overlay_schema={cro.get('schema_version')}; overlay_status={cro.get('status')}; overlay_additions={cro.get('strict_roster_additions')}; operational_invalid={cro.get('operational_invalid_record_count',len(cro.get('invalid_records') or []))}; superseded_invalid={cro.get('superseded_invalid_record_count',0)}; states={{STRICT_CURRENT:{strict_rosters},ACTIVE_MATCH_POOL:{active_pool},PROVISIONAL_ONLY:{provisional_only},NO_ROSTER_CONTEXT:{no_roster_context}}}; conservation={state_conservation}; gap_receipt_valid={gap_receipt_valid}; sub18_repair_attempts={tr.get('repair_attempt_count')}; direct_strict_repairs={tr.get('strict_repairs_created')}",
            "none"
            if roster_closed
            else f"obtain contract-qualified current first-team/registered-squad evidence for the remaining {roster_gap} teams; active match pools and prior-season continuity cannot satisfy the strict gate",
            not roster_closed,
        ),
        issue(
            "SYS-005",
            "team context semantics",
            "FIXED" if manager_closed else "EXTERNAL_DATA_GAP",
            "empty coach/injury/transaction fields could look complete and machine coach observations could bypass the dedicated manager evidence contract",
            "field-specific source-health gates plus verified manager overlay; machine coach is descriptive-only; official-or-two-independent-source manager gate is required; roster/manager/availability dimensions stay separate",
            f"manager_eligible={manager_eligible}/{latest}; full_context={manager_full}/{latest}; feature_eligibility={elig}; verified_manager_records={ta.get('verified_manager_records',0)}; resolved_manager_records={ta.get('resolved_manager_records',0)}; manager_validation_errors={len(ta.get('manager_validation_errors') or [])}",
            "none"
            if manager_closed
            else "acquire fresh verified manager/change and remaining availability/transaction context; no algorithmic shortcut is permitted",
            not manager_closed,
        ),
        issue(
            "SYS-006",
            "market surface information loss",
            "FIXED",
            "canonical snapshot retained only one AH/OU main line while raw provider exposed many lines",
            "V6.8.0 preserves all full-time prematch 1X2/AH/total ladders with raw hash linkage",
            f"ladder={ladder.get('status')}; bundles={ladder_bundle_count}; mean_total_lines={ladder.get('mean_distinct_total_lines')}; mean_ah_lines={ladder.get('mean_distinct_ah_lines')}",
            "none at single-provider extraction layer",
            False,
        ),
        issue(
            "SYS-007",
            "0-7+ total-goals identifiability",
            "FIXED_BY_FAIL_CLOSED_POLICY" if ladder_ident_sync else "OPEN_CODE_DEFECT",
            "risk of extrapolating a complete integer distribution from one O/U line, plus risk of a stale identifiability receipt lagging the current ladder inventory",
            "direct CDF identification only from observed half-goal lines; exact market-only 0-7+ requires 0.5..6.5 all present; missing buckets are never fabricated; V6.8.1 r2 binds its source ladder counts and V6.9 hard-checks cross-receipt synchronization",
            f"ladder_bundles={ladder_bundle_count}; ident_bundles={ident_bundle_count}; ident_source_declared={ident_source_declared}; ident_source_actual={ident_source_actual}; ident_source_match={ident_source_match}; synchronized={ladder_ident_sync}; outputs={ident.get('operational_counts')}; monotonicity_failures={ident.get('monotonicity_failure_count')}",
            "current ladders identify only partial CDF; complete distribution therefore needs an explicit prior plus audited market projection"
            if ladder_ident_sync
            else "refresh V6.8.1 from the current V6.8.0 ladder inventory before any total-goals identifiability claim is accepted",
            not ladder_ident_sync,
        ),
        issue(
            "SYS-008",
            "joint total/score market projection",
            "FORWARD_GATED",
            "old shadow solver was hard-coded to 1X2+OU2.5 and the old outcome scorer could receive a formal matrix again at settlement time",
            "V6.8.2 supports multiple half-goal total constraints; V6.8.5 consumes only validated immutable formal freezes, stores both matrices plus hashes pre-match, and settlement cannot reproject/recalculate",
            f"solver={x['matrix_solver'].get('status')}; solver_residual={x['matrix_solver'].get('max_constraint_residual')}; freeze_chain={mf.get('status')}; sidecars={mf.get('sidecar_count')}; settled={me.get('settled_count')}",
            "accumulate post-epoch validated formal freezes with sufficiently fresh market ladders, then settled OOS score/total evidence",
            True,
        ),
        issue(
            "SYS-009",
            "independent market consensus audit",
            "FIXED",
            "legacy consensus receipt stayed at zero after newer exact-line n=2 consensus files were created",
            "V6.8.3 scans the evidence directory itself and refreshes the legacy receipt for all downstream consumers",
            f"status={con.get('status')}; valid={con.get('valid_consensus_count')}; invalid={con.get('invalid_consensus_count')}; comps={len(con.get('competition_counts') or {})}",
            "none for inventory; outcome evidence is separate",
            False,
        ),
        issue(
            "SYS-010",
            "prospective matrix readiness semantics",
            "FORWARD_GATED",
            "stale downstream report incorrectly implied no consensus and legacy scoring did not bind both matrices at prediction time",
            "consensus inventory refresh plus V6.8.5 named no-backfill epoch and immutable stored-matrix-only settlement",
            f"legacy_consensus={lr.get('consensus_audit_status')}; v685={me.get('evaluation_status')}; settled={me.get('settled_count')}; promotion_eligible_settled={me.get('promotion_eligible_settled_count')}",
            "minimum prospective sample, competition coverage and paired proper-score/bootstrap gates",
            True,
        ),
        issue(
            "SYS-011",
            "exact score overconfidence",
            "FIXED_BY_OUTPUT_GOVERNANCE",
            "exact-score Top-1 can be mistaken for a high-confidence point forecast",
            "exact score remains a probability ranking from one audited joint matrix; no hand-picked score; failed/nonconverged matrices fail closed",
            "V6.8.2/V6.8.5 expose frozen Top1, Top2, gap, Top3 cumulative and score metrics without claiming certainty",
            "irreducible score uncertainty remains; improvement requires forward evidence",
            False,
        ),
        issue(
            "SYS-012",
            "EV / price integrity",
            "FIXED_BY_EXISTING_HARD_GATE",
            "risk of value claims without actual tradable frozen prices or with failed matrix",
            "retain CURRENT rule: no EV/minimum price/value without actual frozen price; score/AH/OU EV unavailable when unified matrix fails",
            f"formal_current={x['formal'].get('formal_rule_version')}",
            "none; permanent hard gate",
            False,
        ),
    ]

    open_def = [row for row in issues if row["status"] == "OPEN_CODE_DEFECT"]
    blockers = [row["issue_id"] for row in issues if row["blocks_formal_upgrade"]]
    payload = {
        "schema_version": "V6.9.5-full-system-issue-registry-r8",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_NO_KNOWN_OPEN_CODE_DEFECTS" if not open_def else "FAIL_OPEN_CODE_DEFECTS",
        "formal_current_version": x["formal"].get("formal_rule_version"),
        "formal_current_unchanged_by_this_registry": True,
        "issue_counts": {
            "total": len(issues),
            "fixed_or_fail_closed": sum(row["status"].startswith("FIXED") for row in issues),
            "forward_gated": sum(row["status"] == "FORWARD_GATED" for row in issues),
            "external_data_gap": sum(row["status"] == "EXTERNAL_DATA_GAP" for row in issues),
            "open_code_defects": len(open_def),
        },
        "formal_upgrade_blockers": blockers,
        "issues": issues,
        "source_receipts": {key: {"path": value, "sha256": sha(value)} for key, value in SOURCES.items()},
        "team_context_summary": {
            "effective_ledger_valid": effective_valid,
            "gap_receipt_valid": gap_receipt_valid,
            "state_conservation": state_conservation,
            "states": {
                "STRICT_CURRENT": strict_rosters,
                "ACTIVE_MATCH_POOL": active_pool,
                "PROVISIONAL_ONLY": provisional_only,
                "NO_ROSTER_CONTEXT": no_roster_context,
            },
            "strict_current_gap": roster_gap,
            "manager_eligible": manager_eligible,
            "full_context": manager_full,
        },
        "market_ladder_chain_summary": {
            "ladder_status": ladder.get("status"),
            "ladder_bundle_count": ladder_bundle_count,
            "ident_status": ident.get("status"),
            "ident_bundle_count": ident_bundle_count,
            "ident_source_declared_bundle_count": ident_source_declared,
            "ident_source_actual_bundle_count": ident_source_actual,
            "ident_source_bundle_count_matches": ident_source_match,
            "synchronized": ladder_ident_sync,
        },
        "forward_summary": {
            "market_first": {
                "evaluation_status": f.get("evaluation_status"),
                "prediction_count": f.get("prediction_count"),
                "settled_count": f.get("settled_count"),
                "readiness_status": rd.get("status"),
                "eligible_post_freeze_matches": readiness_eligible,
                "active_kambi_status": ak.get("status"),
                "active_kambi_written": active_written,
                "active_kambi_v651_eligible": active_eligible,
            },
            "pristine_reference": {
                "settled_valid_count": pp.get("settled_valid_count"),
                "sample_progress": pp.get("sample_progress"),
                "formal_top1": formal_ref.get("top1_accuracy"),
                "formal_log": formal_ref.get("mean_log_score"),
                "direct_top1": direct_ref.get("top1_accuracy"),
                "pooled_top1": pooled_ref.get("top1_accuracy"),
                "promotion_authority": False,
            },
        },
        "governance": {
            "research_only": True,
            "no_current_rule_change_by_registry": True,
            "no_formal_weight_change": True,
            "no_runtime_probability_change": True,
            "market_first_and_pristine_reference_not_conflated": True,
            "readiness_does_not_widen_frozen_window": True,
            "pristine_proper_scores_are_reference_only": True,
            "four_state_roster_context_is_authoritative": True,
            "only_strict_current_satisfies_roster_gate": True,
            "active_match_pool_never_promoted_to_registered_roster": True,
            "historical_superseded_invalid_evidence_is_audit_only": True,
            "provisional_roster_never_counts_as_strict_current": True,
            "machine_coach_bypass_closed": True,
            "a_forward_gate_is_not_a_code_defect": True,
            "external_missing_information_is_never_fabricated": True,
            "postmatch_reprojection_is_a_closed_engineering_defect": True,
            "ladder_identifiability_receipts_must_be_synchronized": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not open_def else 2


if __name__ == "__main__":
    raise SystemExit(main())
