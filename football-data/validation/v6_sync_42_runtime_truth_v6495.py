#!/usr/bin/env python3
"""Synchronize the 42-item runtime-truth registry from accepted forward/context ledgers.

Audit-only utility. It never changes probabilities, model weights, picks, CURRENT, or historical
events. Status classes remain governed by the existing registry; only evidence text and live counts
are refreshed from manifests that have already passed their own ledger audits.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "manifests"

TRUTH = M / "v6_42_issue_remediation_v6489_status.json"
CTX = M / "v6_context_enriched_forward_v6486_status.json"
AXES = M / "v6_match_context_axes_v6490_status.json"
DELTA = M / "v6_manager_delta_from_verified_baseline_v6491_status.json"
FRESH = M / "v6_fresh_market_forward_challengers_v6492_status.json"
CONDITIONED = M / "v6_context_conditioned_selector_v6495_status.json"
SHADOW = M / "v6_unseen_domain_shadow_v6496_status.json"
MATRIX_STRUCTURE = M / "v6_fresh_matrix_structural_audit_v6498_status.json"
JOINT_COHERENCE = M / "v6_joint_surface_coherence_v6499_status.json"
KL_JOINT = M / "v6_kl_joint_projection_v6500_status.json"
SHRUNK_TOTAL = M / "v6_shrunk_direct_total_v6501_status.json"
KL_SHAPE = M / "v6_kl_shape_audit_v6502_status.json"
QUEUE = M / "v6_context_priority_queue_v6493_status.json"
UNIFIED = M / "v6_unified_forward_pipeline_v6482_status.json"


def load(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"not an object: {path}")
    return obj


def require_pass(name: str, obj: dict[str, Any]) -> None:
    if obj.get("status") != "PASS":
        raise RuntimeError(f"{name} status is not PASS: {obj.get('status')!r}")
    audit = obj.get("ledger_audit")
    if isinstance(audit, dict) and audit.get("status") != "PASS":
        raise RuntimeError(f"{name} ledger audit is not PASS")


def item_by_id(truth: dict[str, Any], item_id: str) -> dict[str, Any]:
    for row in truth.get("items") or []:
        if isinstance(row, dict) and row.get("id") == item_id:
            return row
    raise RuntimeError(f"missing item {item_id}")


def count_statuses(truth: dict[str, Any]) -> dict[str, int]:
    out = {"PASS": 0, "PARTIAL": 0, "FORWARD_GATED": 0, "EXTERNAL_DATA_GAP": 0, "TODO": 0, "BLOCKED": 0}
    for row in truth.get("items") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        out[status] = out.get(status, 0) + 1
    return out


def maybe_load(path: Path) -> dict[str, Any] | None:
    return load(path) if path.exists() else None


def main() -> int:
    truth = load(TRUTH)
    ctx = load(CTX)
    axes = load(AXES)
    delta = load(DELTA)
    fresh = load(FRESH)
    conditioned = load(CONDITIONED)
    shadow = maybe_load(SHADOW)
    matrix_structure = maybe_load(MATRIX_STRUCTURE)
    joint_coherence = maybe_load(JOINT_COHERENCE)
    kl_joint = maybe_load(KL_JOINT)
    shrunk_total = maybe_load(SHRUNK_TOTAL)
    kl_shape = maybe_load(KL_SHAPE)
    queue = load(QUEUE)
    unified = load(UNIFIED)

    checks = [
        ("context", ctx), ("axes", axes), ("manager_delta", delta), ("fresh_forward", fresh),
        ("conditioned_selector", conditioned), ("priority_queue", queue), ("unified_pipeline", unified),
    ]
    for name, obj in (("unseen_domain_shadow", shadow), ("matrix_structure", matrix_structure),
                      ("joint_coherence", joint_coherence), ("kl_joint", kl_joint)):
        if obj is not None:
            checks.append((name, obj))
    for name, obj in checks:
        require_pass(name, obj)

    ctx_audit = ctx.get("ledger_audit") or {}; ctx_features = ctx.get("feature_counts") or {}
    ctx_count = int(ctx_audit.get("event_count") or 0)
    full_xi = int(ctx_features.get("full_predicted_xi") or 0)
    home_av = int(ctx_features.get("home_availability") or 0)
    away_av = int(ctx_features.get("away_availability") or 0)

    axes_audit = axes.get("ledger_audit") or {}; axes_cov = axes.get("coverage") or {}
    axes_count = int(axes_audit.get("event_count") or 0)
    both_mgr = int(axes_cov.get("manager_both_verified") or 0)
    confirmed_deltas = int(axes_cov.get("confirmed_material_roster_deltas") or 0)
    pending_deltas = int(axes_cov.get("pending_deltas_not_applied") or 0)

    delta_counts = delta.get("delta_counts") or {}
    same_mgr = int(delta_counts.get("SAME_MANAGER") or 0)
    changed_mgr = int(delta_counts.get("MANAGER_CHANGED") or 0)
    no_baseline = int(delta_counts.get("NO_PRIOR_VERIFIED_BASELINE") or 0)
    manager_observations = int(delta.get("current_manager_observation_count") or 0)

    selector_events = int((fresh.get("selector_ledger_audit") or {}).get("event_count") or 0)
    matrix_events = int((fresh.get("matrix_ledger_audit") or {}).get("event_count") or 0)
    selector_eval = fresh.get("selector_evaluation") or {}; matrix_eval = fresh.get("matrix_evaluation") or {}
    selector_all_settled = int((selector_eval.get("all_settled") or {}).get("count") or 0)
    selector_selected_settled = int((selector_eval.get("selected_settled") or {}).get("count") or 0)
    matrix_settled = int((matrix_eval.get("candidate") or {}).get("count") or 0)

    conditioned_events = int((conditioned.get("ledger_audit") or {}).get("event_count") or 0)
    conditioned_selected_settled = int(((conditioned.get("evaluation") or {}).get("selected_settled") or {}).get("count") or 0)

    shadow_pop = shadow.get("population") if shadow else {}; shadow_eval = shadow.get("evaluation") if shadow else {}
    shadow_unseen = int((shadow_pop or {}).get("eligible_unseen_prediction_count") or 0)
    shadow_selected = int((shadow_pop or {}).get("shadow_selected_count") or 0)
    shadow_selected_settled = int(((shadow_eval or {}).get("shadow_selected_settled") or {}).get("count") or 0)
    shadow_decision = (shadow_eval or {}).get("decision") if shadow else None
    shadow_domains = list(((shadow or {}).get("freeze") or {}).get("unseen_target_domains") or []) if shadow else []

    structure_diag = (matrix_structure or {}).get("total_goal_diagnostic") or {}
    score_diag = (matrix_structure or {}).get("exact_score_diagnostic") or {}
    structure_count = int((matrix_structure or {}).get("prediction_count") or 0)
    mode2_count = int((structure_diag.get("candidate_mode_counts") or {}).get("2") or 0)
    one_one_count = int(score_diag.get("one_one_top1_count") or 0)
    unique_score_top1 = int(score_diag.get("unique_top1_score_count") or 0)

    coherence = (joint_coherence or {}).get("candidate_vs_f05") or {}
    coherence_count = int(coherence.get("count") or 0)
    coherence_top1_rate = coherence.get("top1_agree_rate")
    coherence_mean_max = coherence.get("mean_max_abs_probability_delta")
    coherence_max = coherence.get("max_abs_probability_delta")
    coherence_ready = (joint_coherence or {}).get("formal_joint_surface_ready_without_coordination")

    kl_diag = (kl_joint or {}).get("projection_diagnostics") or {}
    kl_eval = (kl_joint or {}).get("evaluation") or {}
    kl_count = int(kl_diag.get("count") or 0)
    kl_settled = int((kl_eval.get("projected") or {}).get("count") or 0)
    kl_all_converged = kl_diag.get("all_converged")

    priority_counts = queue.get("priority_counts") or {}

    item_by_id(truth, "E03")["evidence"] = (
        f"Latest verified context ledger={ctx_count} hash-audited context+market decisions; full predicted XI {full_xi}/{ctx_count}; "
        f"home availability evidence {home_av}; away availability evidence {away_av}; competition coverage={ctx.get('by_competition') or {}}. "
        "Missing or conflicted availability remains UNKNOWN and is never converted to no absence."
    )
    item_by_id(truth, "E04")["evidence"] = (
        f"Latest verified manager/roster-axis ledger={axes_count} fixtures; both managers verified for {both_mgr}; "
        f"by competition={axes.get('by_competition') or {}}. Broad future-fixture coverage remains a live point-in-time requirement."
    )
    item_by_id(truth, "E05")["evidence"] = (
        f"Verified current manager observations={manager_observations}; baseline comparison: SAME_MANAGER={same_mgr}, "
        f"MANAGER_CHANGED={changed_mgr}, NO_PRIOR_VERIFIED_BASELINE={no_baseline}. Current identity alone never means no change; "
        "a strictly earlier verified baseline is required."
    )
    item_by_id(truth, "E06")["evidence"] = (
        f"End-to-end decision-freeze refresh is verified on {ctx_count} context+market decisions and {both_mgr} both-manager axis decisions. "
        f"Current priority queue={priority_counts}. Runtime truth is sourced from accepted ledgers, not stale scheduling state."
    )

    shadow_text = ""
    if shadow is not None:
        shadow_text = (
            f" V6.49.6 unseen-domain shadow is diagnostic-only: unseen population={shadow_unseen}, shadow-selected={shadow_selected}, "
            f"shadow-selected-settled={shadow_selected_settled}, domains={shadow_domains}, decision={shadow_decision}; these shadow selections "
            "do not count toward active V6.49.2 selected N and cannot change probabilities, threshold or formal weight."
        )
    item_by_id(truth, "F05")["evidence"] = (
        "Historical source only: V6.47.4 fixed1000 challenge=208/322=64.60%. "
        f"Current V6.49.2 chain has {selector_events} frozen 1X2 events, {selector_all_settled} all-settled and "
        f"{selector_selected_settled} selected-settled; decision={selector_eval.get('decision')}. V6.49.5 has {conditioned_events} "
        f"post-epoch context-conditioned events and {conditioned_selected_settled} selected-settled.{shadow_text} "
        ">=100 active selected settled plus quality/domain gates remain required before formal promotion; the separate V6.49.6 shadow "
        "has its own >=100 unseen-domain review gate and never auto-promotes."
    )

    f06_extra = ""
    if matrix_structure is not None:
        f06_extra += (
            f" V6.49.8 independent structure audit PASS on {structure_count} matrices: total mode=2 on {mode2_count}/{structure_count}; "
            f"score Top-1 has only {unique_score_top1} unique values and 1-1 occurs {one_one_count}/{structure_count}; this is a model-capacity "
            "collapse finding, not a probability-conservation failure."
        )
    if joint_coherence is not None:
        f06_extra += (
            f" V6.49.9 F05/F06 coherence audit: Top-1 agreement={coherence_top1_rate}, mean max probability delta={coherence_mean_max}, "
            f"max delta={coherence_max}, formal_joint_surface_ready_without_coordination={coherence_ready}."
        )
    if kl_joint is not None:
        f06_extra += (
            f" V6.50.0 prospective KL/IPF coherence challenge has {kl_count} frozen projections, {kl_settled} settled; "
            f"all_converged={kl_all_converged}, formal_weight=0 and no automatic promotion."
        )
    if shrunk_total is not None:
        sc = (shrunk_total.get("freeze") or {}).get("calibration") or {}; sd = shrunk_total.get("prospective_diagnostics") or {}
        f06_extra += (
            f" V6.50.1 shrunk direct-total challenge status={shrunk_total.get('status')}, chosen_weight={sc.get('chosen_weight')}, "
            f"prospective_count={sd.get('count')}; remains formal_weight=0."
        )
    if kl_shape is not None:
        ss = kl_shape.get("score_shape") or {}
        f06_extra += (
            f" V6.50.2 KL score-shape audit status={kl_shape.get('status')}, unique Top-1 scores={ss.get('unique_top1_score_count')}, "
            f"1-1 Top-1 rate={ss.get('one_one_top1_rate')}."
        )
    item_by_id(truth, "F06")["evidence"] = (
        "Historical source only: V6.47.8 nominated total=comp|margin=full. "
        f"Current V6.49.2 chain has {matrix_events} frozen matrix events and {matrix_settled} settled candidate evaluations; "
        f"decision={matrix_eval.get('decision')}.{f06_extra} F06 remains FORWARD_GATED: raw V6.49.2 is not a coherent formal joint surface, "
        "and all replacement/challenge paths require true post-freeze proper-score evidence before any promotion."
    )

    truth["schema_version"] = "V6.50.0-42-issue-runtime-truth-registry-r8"
    truth["generated_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    truth["formal_current_version"] = "V5.0.1"
    truth["formal_probability_change"] = False
    truth["formal_weight_change"] = False
    truth["summary"] = count_statuses(truth)

    acq = truth.setdefault("current_forward_acquisition", {})
    pipeline_infra = unified.get("market_result_infrastructure") or {}
    acq.update({
        "full17_identity_domains": int((unified.get("acquisition") or {}).get("identity_available_domain_count") or 0),
        "market_result_infrastructure_prediction_count": int(pipeline_infra.get("prediction_count") or 0),
        "market_result_infrastructure_settled_count": int(pipeline_infra.get("settlement_count") or 0),
        "current_v6492_selector_events": selector_events,
        "current_v6492_matrix_events": matrix_events,
        "current_v6492_settled": selector_all_settled,
        "current_v6492_selected_settled": selector_selected_settled,
        "current_v6492_matrix_settled": matrix_settled,
        "clean_context_market_decision_events_verified": ctx_count,
        "full_predicted_xi_context_events_verified": full_xi,
        "home_availability_context_events_verified": home_av,
        "away_availability_context_events_verified": away_av,
        "manager_axis_events_verified": axes_count,
        "manager_both_verified_events": both_mgr,
        "confirmed_material_roster_deltas": confirmed_deltas,
        "pending_roster_deltas_not_applied": pending_deltas,
        "manager_delta_same": same_mgr,
        "manager_delta_changed": changed_mgr,
        "manager_delta_no_prior_verified_baseline": no_baseline,
        "v6495_context_effect_epoch": (conditioned.get("freeze") or {}).get("freeze_timestamp_utc"),
        "v6495_latest_verified_event_count": conditioned_events,
        "v6495_latest_selected_settled": conditioned_selected_settled,
        "v6495_latest_verified_status": conditioned.get("status"),
        "v6496_unseen_shadow_population": shadow_unseen,
        "v6496_unseen_shadow_selected": shadow_selected,
        "v6496_unseen_shadow_selected_settled": shadow_selected_settled,
        "v6496_unseen_shadow_domains": shadow_domains,
        "v6496_unseen_shadow_decision": shadow_decision,
        "v6498_matrix_structure_count": structure_count,
        "v6498_total_mode2_count": mode2_count,
        "v6498_score_top1_unique_count": unique_score_top1,
        "v6498_one_one_top1_count": one_one_count,
        "v6499_coherence_count": coherence_count,
        "v6499_f05_f06_top1_agreement_rate": coherence_top1_rate,
        "v6499_f05_f06_mean_max_probability_delta": coherence_mean_max,
        "v6499_f05_f06_max_probability_delta": coherence_max,
        "v6499_formal_joint_surface_ready_without_coordination": coherence_ready,
        "v6500_kl_projection_count": kl_count,
        "v6500_kl_settled": kl_settled,
        "v6500_kl_all_converged": kl_all_converged,
        "v6501_shrunk_total_status": shrunk_total.get("status") if shrunk_total else None,
        "v6501_shrunk_total_chosen_weight": (((shrunk_total or {}).get("freeze") or {}).get("calibration") or {}).get("chosen_weight") if shrunk_total else None,
        "v6501_shrunk_total_prediction_count": ((shrunk_total or {}).get("prospective_diagnostics") or {}).get("count") if shrunk_total else None,
        "v6502_kl_shape_status": kl_shape.get("status") if kl_shape else None,
        "v6502_kl_shape_unique_top1": ((kl_shape or {}).get("score_shape") or {}).get("unique_top1_score_count") if kl_shape else None,
        "v6493_priority_queue_generated_at_utc": queue.get("generated_at_utc"),
        "v6493_priority_counts": priority_counts,
    })
    truth["runtime_sync_sources"] = {
        "context": ctx.get("generated_at_utc"), "axes": axes.get("generated_at_utc"), "manager_delta": delta.get("generated_at_utc"),
        "fresh_forward": fresh.get("generated_at_utc"), "conditioned_selector": conditioned.get("generated_at_utc"),
        "unseen_domain_shadow": shadow.get("generated_at_utc") if shadow else None,
        "matrix_structure": matrix_structure.get("generated_at_utc") if matrix_structure else None,
        "joint_coherence": joint_coherence.get("generated_at_utc") if joint_coherence else None,
        "kl_joint": kl_joint.get("generated_at_utc") if kl_joint else None,
        "shrunk_total": shrunk_total.get("generated_at_utc") if shrunk_total else None,
        "kl_shape": kl_shape.get("generated_at_utc") if kl_shape else None,
        "priority_queue": queue.get("generated_at_utc"), "unified_pipeline": unified.get("generated_at_utc"),
    }
    hard = truth.setdefault("hard_rules", {})
    hard["runtime_truth_counts_are_ledger_derived"] = True
    hard["status_sync_cannot_change_probabilities_or_weights"] = True
    hard["unseen_domain_shadow_never_counts_as_active_selected_without_separate_forward_review"] = True
    hard["f05_f06_coherence_gap_cannot_be_hidden_by_text"] = True
    hard["kl_projection_remains_weight0_until_forward_proper_score_gate"] = True
    hard["total_goal_diversity_cannot_override_proper_score_gates"] = True

    if truth["summary"].get("PASS") != 36 or truth["summary"].get("PARTIAL") != 4 or truth["summary"].get("FORWARD_GATED") != 2:
        raise RuntimeError(f"unexpected 42-item status composition: {truth['summary']}")
    if ctx_count < full_xi or axes_count < both_mgr:
        raise RuntimeError("coverage count invariant failed")
    if fresh.get("governance", {}).get("formal_weight") != 0:
        raise RuntimeError("fresh forward formal-weight guard failed")
    if conditioned.get("governance", {}).get("context_probability_adjustment") is not False:
        raise RuntimeError("conditioned probability-adjustment guard failed")
    if shadow is not None:
        sg = shadow.get("governance") or {}
        if sg.get("active_selector_change") is not False or sg.get("probability_mutation") is not False:
            raise RuntimeError("unseen-domain shadow mutated active selector or probability")
        if sg.get("threshold_change") is not False or sg.get("formal_weight") != 0:
            raise RuntimeError("unseen-domain shadow threshold/weight guard failed")
        if sg.get("historical_backfill") is not False or sg.get("automatic_promotion") is not False:
            raise RuntimeError("unseen-domain shadow governance guard failed")
    if matrix_structure is not None:
        mg = matrix_structure.get("governance") or {}
        if mg.get("probability_mutation") is not False or mg.get("matrix_mutation") is not False or mg.get("formal_weight") != 0:
            raise RuntimeError("matrix structure audit mutation guard failed")
    if joint_coherence is not None:
        cg = joint_coherence.get("governance") or {}
        if cg.get("probability_mutation") is not False or cg.get("matrix_mutation") is not False or cg.get("selector_mutation") is not False or cg.get("formal_weight") != 0:
            raise RuntimeError("joint coherence audit mutation guard failed")
    if kl_joint is not None:
        kg = kl_joint.get("governance") or {}
        if kg.get("source_probability_mutation") is not False or kg.get("source_matrix_mutation") is not False or kg.get("selector_mutation") is not False or kg.get("formal_weight") != 0:
            raise RuntimeError("KL challenge governance guard failed")
        kd = kl_joint.get("projection_diagnostics") or {}
        if kd.get("count", 0) and kd.get("all_converged") is not True:
            raise RuntimeError("KL projection convergence guard failed")

    TRUTH.write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "context_events": ctx_count, "both_managers": both_mgr,
        "selector_events": selector_events, "matrix_events": matrix_events, "conditioned_events": conditioned_events,
        "unseen_shadow_population": shadow_unseen, "unseen_shadow_selected": shadow_selected,
        "matrix_structure_count": structure_count, "matrix_total_mode2": mode2_count,
        "joint_top1_agreement": coherence_top1_rate, "kl_projection_count": kl_count,
        "priority_counts": priority_counts, "summary": truth["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
