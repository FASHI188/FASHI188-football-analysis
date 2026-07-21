#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import recent_xg_forward_shadow_v513 as core
from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from bayesian_dynamic_state_oof_v500 import _candidate_from_baseline, _metric_row
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, read_processed_matches


def domain(competition_id: str, cfg):
    season = "2025/26"
    report = core._load_json(REPORT_ROOT / f"{competition_id}.json")
    fold = _fold_for_season(report, season)
    selected_parameters = fold.get("selected_parameters")
    if not isinstance(selected_parameters, dict):
        raise PlatformError("missing formal point-in-time parameters")

    all_matches = read_processed_matches(competition_id)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    if not matches:
        raise PlatformError("no 2025/26 processed matches")
    linked = core._load_jsonl(core.LINK_ROOT / f"{competition_id}.jsonl")
    temperature, calibration_mode = _target_season_temperature(competition_id, season)
    split_index = int(math.floor(len(matches) * float(cfg["chronology"]["profile_selection_fraction"])))

    contexts = []
    baseline_skipped = 0
    xg_skipped = 0
    chronology_violations = 0

    # First pass: build the formal baseline and prior-only xG state exactly once per match.
    for index, match in enumerate(matches):
        target_date = match.date.date().isoformat()
        try:
            baseline = _predict_from_loaded_matches(
                all_matches, match.home_team, match.away_team, match.date, season, selected_parameters
            )
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
        except Exception:
            baseline_skipped += 1
            continue
        try:
            dynamic_home, dynamic_away, xg_audit = core._xg_dynamic_rates(
                linked, target_date, match.home_team, match.away_team, cfg["xg_state"]
            )
        except Exception:
            xg_skipped += 1
            continue
        if str(xg_audit["latest_history_date"]) >= target_date:
            chronology_violations += 1
            continue
        contexts.append({
            "index": index,
            "phase": "selection" if index < split_index else "forward",
            "match": match,
            "date": target_date,
            "match_key": f"{competition_id}:{target_date}:{match.home_team}:{match.away_team}",
            "baseline": baseline,
            "baseline_metrics": _metric_row(baseline, match),
            "dynamic_home": dynamic_home,
            "dynamic_away": dynamic_away,
            "xg_audit": xg_audit,
        })

    selection_contexts = [c for c in contexts if c["phase"] == "selection"]
    forward_contexts = [c for c in contexts if c["phase"] == "forward"]
    if len(selection_contexts) < 40:
        raise PlatformError(f"selection rows below minimum: {len(selection_contexts)}")
    if not forward_contexts:
        raise PlatformError("no forward validation contexts")

    selection_scores = []
    max_prob_residual = 0.0
    for profile in cfg["profiles"]:
        rows = []
        for context in selection_contexts:
            candidate, tilt_audit = _candidate_from_baseline(
                context["baseline"], context["dynamic_home"], context["dynamic_away"], profile
            )
            metrics = _metric_row(candidate, context["match"])
            max_prob_residual = max(
                max_prob_residual,
                abs(float(metrics["probability_sum_residual"])),
                abs(float(tilt_audit["probability_sum_residual"])),
            )
            rows.append(metrics)
        selection_scores.append({
            "profile_id": profile["id"],
            "selection_rows": len(rows),
            "objective": core._selection_objective(rows),
        })
    selection_scores.sort(key=lambda item: (item["objective"], item["profile_id"]))
    selected_id = selection_scores[0]["profile_id"]
    selected_profile = next(p for p in cfg["profiles"] if p["id"] == selected_id)

    # Second pass: the later 55% only sees the already-frozen selected profile.
    forward_rows = []
    for context in forward_contexts:
        candidate, tilt_audit = _candidate_from_baseline(
            context["baseline"], context["dynamic_home"], context["dynamic_away"], selected_profile
        )
        cand_metrics = _metric_row(candidate, context["match"])
        max_prob_residual = max(
            max_prob_residual,
            abs(float(cand_metrics["probability_sum_residual"])),
            abs(float(tilt_audit["probability_sum_residual"])),
        )
        base = context["baseline_metrics"]
        row = {
            "match_key": context["match_key"],
            "date": context["date"],
            "selected_profile": selected_id,
        }
        for metric in (
            "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
            "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"
        ):
            row[f"baseline_{metric}"] = base[metric]
            row[f"candidate_{metric}"] = cand_metrics[metric]
        forward_rows.append(row)

    pooled = core._paired_summary(forward_rows)
    bcfg = cfg["bootstrap"]
    draws = int(bcfg["draws"])
    block_size = int(bcfg["block_size"])
    seed = int(bcfg["seed"])
    ci = {
        "one_x_two_brier": core._bootstrap(forward_rows, "candidate_one_x_two_brier", "baseline_one_x_two_brier", seed + 1, draws, block_size),
        "one_x_two_rps": core._bootstrap(forward_rows, "candidate_one_x_two_rps", "baseline_one_x_two_rps", seed + 2, draws, block_size),
        "joint_log": core._bootstrap(forward_rows, "candidate_joint_log", "baseline_joint_log", seed + 3, draws, block_size),
        "total_rps": core._bootstrap(forward_rows, "candidate_total_rps", "baseline_total_rps", seed + 4, draws, block_size),
    }

    gate = cfg["forward_gate"]
    minimum_rows = int(gate["minimum_forward_rows_20_team"] if len(matches) >= 350 else gate["minimum_forward_rows_18_team"])
    brier_improves = ci["one_x_two_brier"]["ci95_upper"] < 0.0
    rps_improves = ci["one_x_two_rps"]["ci95_upper"] < 0.0
    noninferior = float(gate["other_1x2_proper_score_ci_upper_noninferiority"])
    other_proper_noninferior = (
        (brier_improves and ci["one_x_two_rps"]["ci95_upper"] <= noninferior)
        or (rps_improves and ci["one_x_two_brier"]["ci95_upper"] <= noninferior)
        or (brier_improves and rps_improves)
    )
    checks = {
        "selected_profile_nonbaseline": selected_id != "baseline_zero",
        "minimum_forward_rows": len(forward_rows) >= minimum_rows,
        "at_least_one_1x2_proper_score_ci_improves": brier_improves or rps_improves,
        "other_1x2_proper_score_ci_noninferior": other_proper_noninferior,
        "joint_log_ci_noninferior": ci["joint_log"]["ci95_upper"] <= float(gate["joint_log_ci_upper_noninferiority"]),
        "total_rps_ci_noninferior": ci["total_rps"]["ci95_upper"] <= float(gate["total_rps_ci_upper_noninferiority"]),
        "one_x_two_accuracy_nonworse": pooled["one_x_two_accuracy"]["candidate"] + 1e-12 >= pooled["one_x_two_accuracy"]["baseline"],
        "score_top1_nonworse": pooled["score_top1"]["candidate"] + 1e-12 >= pooled["score_top1"]["baseline"],
        "score_top3_nonworse": pooled["score_top3"]["candidate"] + 1e-12 >= pooled["score_top3"]["baseline"],
        "total_top1_nonworse": pooled["total_top1"]["candidate"] + 1e-12 >= pooled["total_top1"]["baseline"],
        "total_top2_nonworse": pooled["total_top2"]["candidate"] + 1e-12 >= pooled["total_top2"]["baseline"],
        "probability_conservation": max_prob_residual <= float(gate["probability_sum_tolerance"]),
        "chronology_no_same_day_or_future_xg": chronology_violations == 0,
    }
    pass_signal = all(checks.values())

    return {
        "schema_version": "V5.1.3-recent-xg-forward-shadow-domain-r2",
        "competition_id": competition_id,
        "season": season,
        "status": "RECENT_XG_FORWARD_SIGNAL_PASS_SHADOW_ONLY" if pass_signal else "REJECT_KEEP_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_xg_eligible": False,
        "formal_pit_market_eligible": False,
        "processed_match_count": len(matches),
        "selection_split_index": split_index,
        "selection_fraction": float(cfg["chronology"]["profile_selection_fraction"]),
        "selection_context_count": len(selection_contexts),
        "baseline_skipped_count": baseline_skipped,
        "xg_skipped_count": xg_skipped,
        "selected_profile": selected_id,
        "profile_selection": selection_scores,
        "forward_prediction_count": len(forward_rows),
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "max_probability_sum_residual": max_prob_residual,
        "chronology_violation_count": chronology_violations,
        "checks": checks,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "execution_optimization": "two_pass_selection_then_single_profile_forward; mathematically identical frozen profile design",
        "retrospective_ah_status": "REFERENCE_AVAILABLE_NOT_USED_FOR_FORMAL_PROMOTION",
        "policy": "2025/26-only xG shadow. Profile chosen from the first chronological 45%; final metrics use only the later 55%. Every xG feature uses prior dates only. No older-season xG, random split, target-match xG or formal probability mutation."
    }
