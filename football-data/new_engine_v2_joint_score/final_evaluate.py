from final_eval_common import *

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-dir", required=True)
    args = ap.parse_args()
    v1_dir = Path(args.v1_dir)

    universe = json.loads((EVIDENCE / "universe_manifest.json").read_text(encoding="utf-8"))
    research_result = json.loads((EVIDENCE / "research_result.json").read_text(encoding="utf-8"))
    lock = json.loads((EVIDENCE / "final_model_lock.json").read_text(encoding="utf-8"))
    receipt = json.loads((FINAL_RUN / "protocol_receipt.json").read_text(encoding="utf-8"))
    v2_all, v2_meta = load_v2()
    v2_all_metrics = evaluate_predictions(v2_all)

    base_items = []
    for x in v2_all:
        cid = str(x["competition_id"])
        disp = lock["competition_dispersion"].get(cid, [20.0, 20.0])
        matrix = joint_matrix(lock["joint_family"], x["model_features"], dispersion_home=float(disp[0]), dispersion_away=float(disp[1]),
                              dependence=float(lock["dependence"]), max_goals=int(lock["max_goals"]))
        base_items.append({**x, "matrix": matrix, "probs": matrix_1x2(matrix)})
    base_metrics = evaluate_predictions(base_items)

    v1_pure, v1_legacy = load_v1_reference(v1_dir)
    paired_v2, paired_v1 = paired_v1_items(v2_meta, v1_pure)
    if not paired_v2:
        raise GovernanceError("no V1/V2 same-match intersection")
    m2 = evaluate_predictions(paired_v2)
    m1 = evaluate_predictions(paired_v1)
    v500 = evaluate_1x2(v500_items(v2_meta, v1_legacy))
    worst = worst_group_delta(paired_v2, paired_v1)

    v1_recall = m1["draw_recall"] or 0.0
    v1_precision = m1["draw_precision"] or 0.0
    required_draw_recall = max(v1_recall + 0.05, 0.08)
    required_draw_precision = max(v1_precision - 0.03, 0.20)
    v2_coverage = len(v2_all) / int(universe["final_n"])
    comparison_coverage = len(paired_v2) / int(universe["final_n"])
    adversarial = json.loads((EVIDENCE / "adversarial_probe.json").read_text(encoding="utf-8"))
    cold = json.loads((EVIDENCE / "cold_start_audit.json").read_text(encoding="utf-8"))
    gates = {
        "logloss_improvement_ge_0_003": (m1["logloss"] - m2["logloss"]) >= 0.0030,
        "brier_improvement_ge_0_0015": (m1["brier"] - m2["brier"]) >= 0.0015,
        "rps_improvement_ge_0_00075": (m1["rps"] - m2["rps"]) >= 0.00075,
        "top1_not_worse_0_15pp": (m2["top1"] - m1["top1"]) >= -0.0015,
        "v2_coverage_ge_99pct": v2_coverage >= 0.99,
        "v2_coverage_not_below_comparison_by_0_25pp": v2_coverage >= comparison_coverage - 0.0025,
        "macro_ece_absolute_and_relative": m2["macro_ece"] <= 0.030 and m2["macro_ece"] <= m1["macro_ece"] + 0.005,
        "draw_binary_logloss_guard": m2["draw_binary_logloss"] <= m1["draw_binary_logloss"] + 0.002,
        "draw_brier_guard": m2["draw_brier"] <= m1["draw_brier"] + 0.001,
        "draw_recall_gate": (m2["draw_recall"] or 0.0) >= required_draw_recall if m2["draw_actual_n"] >= 100 else False,
        "draw_precision_gate": (m2["draw_precision"] or 0.0) >= required_draw_precision if m2["draw_actual_n"] >= 100 else False,
        "exact_score_kl_guard": v2_all_metrics["exact_score_logloss"] <= base_metrics["exact_score_logloss"] + 0.005,
        "one_one_binary_ll_guard": m2["score_events"]["1-1"]["binary_logloss"] <= m1["score_events"]["1-1"]["binary_logloss"] + 0.002,
        "one_one_brier_guard": m2["score_events"]["1-1"]["brier"] <= m1["score_events"]["1-1"]["brier"] + 0.002,
        "one_one_calibration_guard": m2["score_events"]["1-1"]["absolute_calibration_error"] <= max(m1["score_events"]["1-1"]["absolute_calibration_error"], 0.015),
        "zero_zero_brier_guard": m2["score_events"]["0-0"]["brier"] <= m1["score_events"]["0-0"]["brier"] + 0.002,
        "two_two_brier_guard": m2["score_events"]["2-2"]["brier"] <= m1["score_events"]["2-2"]["brier"] + 0.002,
        "worst_n_ge_100_group_guard": worst.get("worst", {}).get("delta", 999.0) <= 0.025,
        "runtime_failure_rate_le_0_005": len(v2_all) == int(universe["final_n"]),
        "label_isolation_protocol": receipt.get("blind_predictor_received_final_label_path") is False and receipt.get("scorer_process_separate") is True,
        "same_cutoff_atomicity": receipt.get("same_cutoff_predict_all_before_update") is True,
        "dataset_same_cutoff_boundary": universe.get("same_cutoff_not_split") is True,
        "adversarial_probe_passed": adversarial.get("passed") is True,
        "cold_start_structural_audit_passed": cold.get("passed") is True,
    }
    scientific_status = "MODEL_CANDIDATE_PASSED" if all(gates.values()) else "NOT_PROMOTED"

    result = {
        "schema_version": "football3-v2-historical-final-result-v1",
        "scientific_status": scientific_status,
        "data": {
            "n": universe["n"],
            "research_n": universe["research_n"],
            "final_n": universe["final_n"],
            "first_cutoff": universe["first_cutoff"],
            "last_cutoff": universe["last_cutoff"],
            "final_first_cutoff": universe["final_first_cutoff"],
            "competitions": universe["competitions"],
            "blocked_competitions": universe["audit"]["blocked_competitions"],
            "universe_sha256": universe["universe_sha256"],
            "source_files": universe["audit"]["source_files"],
        },
        "selected_model": {
            "joint_family": lock["joint_family"],
            "fitness_retained": lock["fitness_retained"],
            "dual_head_retained": lock["dual_head_retained"],
            "layer_status": lock["layer_status"],
        },
        "outer_research": {
            "fold_count": research_result["outer_fold_count"],
            "joint_candidates": research_result["joint_candidates"],
            "fitness_ablation": research_result["fitness_ablation"],
            "dual_head_candidate": research_result["dual_head_candidate"],
        },
        "v2_final_all": v2_all_metrics,
        "pre_kl_joint_final_all": base_metrics,
        "paired_with_v1": {"n": len(paired_v2), "v2": m2, "v1": m1},
        "v500_same_v2_universe_available": v500,
        "gates": {
            "checks": gates,
            "actuals": {
                "logloss_improvement": m1["logloss"] - m2["logloss"],
                "brier_improvement": m1["brier"] - m2["brier"],
                "rps_improvement": m1["rps"] - m2["rps"],
                "top1_delta": m2["top1"] - m1["top1"],
                "v2_coverage": v2_coverage,
                "comparison_coverage": comparison_coverage,
                "macro_ece_v2": m2["macro_ece"],
                "macro_ece_v1": m1["macro_ece"],
                "draw_recall_v2": m2["draw_recall"],
                "draw_recall_v1": m1["draw_recall"],
                "required_draw_recall": required_draw_recall,
                "draw_precision_v2": m2["draw_precision"],
                "draw_precision_v1": m1["draw_precision"],
                "required_draw_precision": required_draw_precision,
                "worst_group_delta": worst.get("worst"),
            },
        },
        "slices": group_metrics(v2_all),
        "worst_paired_group": worst,
        "blocked_or_proxy_slices": {
            "player_lineup": "LINEUP_UNKNOWN for this frozen historical universe; PIT player/lineup layer blocked and not retained.",
            "coach_change": "BLOCKED_DATA; no verified coach-regime ledger.",
            "true_promoted_team": "BLOCKED_DATA; only new-to-competition/cold-start proxy is reported.",
            "new_league": "Synthetic hierarchical cold-start audit only; no fabricated historical new-league label.",
            "match_process": "BLOCKED_DATA; no lawful minute-level event history.",
        },
        "governance": {
            "final_rule_freeze_sha256": sha256_file(EVIDENCE / "final_rule_freeze.json"),
            "final_protocol_receipt_sha256": sha256_file(FINAL_RUN / "protocol_receipt.json"),
            "labels_isolated": True,
            "pure_market_input": False,
        },
    }
    (EVIDENCE / "historical_final_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    final_status = {
        "schema_version": "football3-v2-final-status-v1",
        "scientific_status": scientific_status,
        "engineering_status": "HISTORICAL_REMOTE_ACCEPTANCE_PENDING",
        "forward_status": "ELIGIBLE_TO_PREREGISTER" if scientific_status == "MODEL_CANDIDATE_PASSED" else "BLOCKED_SCIENTIFIC_GATE",
        "formal_activation": False,
    }
    (EVIDENCE / "final_status.json").write_text(json.dumps(final_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scientific_status": scientific_status, "paired_n": len(paired_v2), "failed_gates": [k for k, v in gates.items() if not v]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
