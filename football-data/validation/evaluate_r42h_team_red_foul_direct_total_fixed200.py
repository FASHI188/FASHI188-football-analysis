#!/usr/bin/env python3
"""R42H: strictly prior team red-card/foul history -> Direct-T.

This deliberately removes referee dependence from R42G and also removes yellow-card
features already represented in the older E3f-1A style family. Only prior red-card and
foul histories are challenged. Current-match discipline/result never enters its own
features and all same-date matches are frozen before history updates.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, select_fixed_identities, split_for_latest_complete
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import paired_bootstrap
from evaluate_r42g_discipline_referee_direct_total_fixed200 import load_discipline_rows, build_discipline_features, reproduce_prior2600, tail_binary
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42h_team_red_foul_direct_total_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42h_team_red_foul_direct_total_fixed200_status.json"
TOTAL_CLASSES = list(range(8))


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    excluded2600, prior_hashes = reproduce_prior2600(raw, seasons, cfg)

    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    features["date_norm"] = pd.to_datetime(features["date_key"], errors="raise").dt.date.astype(str)
    competitions = set(features.competition_id.astype(str))

    # Reuse the strict same-day discipline builder from R42G, but ignore referee and yellow
    # features in this challenger. Team-history eligibility does not depend on referee.
    drows, source_cov = load_discipline_rows(competitions)
    discipline, discipline_audit = build_discipline_features(drows, cfg)
    names = [str(x) for x in cfg["feature_contract"]["feature_names"]]
    if discipline.empty:
        merged = features.copy()
        for name in names:
            merged[name] = np.nan
        merged["discipline_team_history_ok"] = 0
    else:
        keep = ["competition_id", "season", "date_norm", "home_team", "away_team", "discipline_team_history_ok"] + names
        merged = features.merge(discipline[keep], on=["competition_id", "season", "date_norm", "home_team", "away_team"], how="left", validate="one_to_one")

    target = merged[
        (merged.split == "target_pool")
        & merged[names].notna().all(axis=1)
        & (merged.discipline_team_history_ok.fillna(0).astype(int) == 1)
    ].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2600)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior2600_exclusion"])

    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": int(len(excluded2600)), "hashes": prior_hashes},
        "coverage": {
            "raw_source_by_competition": source_cov,
            "discipline_feature_build": discipline_audit,
            "fresh_target_rows_after_prior2600_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "current_match_red_cards_fouls_or_result_used_in_own_features": 0,
            "yellow_features_used_by_challenger": 0,
            "referee_features_used_by_challenger": 0,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }

    if len(fresh) < minimum:
        result = {**base_receipt, "status": "STOP_R42H_TEAM_RED_FOUL_COVERAGE_LT200", "scientific_verdict": "DO_NOT_CONSUME_FIXED200_TEAM_RED_FOUL_COVERAGE_INSUFFICIENT", "sample": None, "model_fits": 0}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    selected, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(selected))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded2600:
        raise ResearchError("R42H sample identity contract failed")

    fit_rows = merged[
        merged.split.isin(["train", "policy"])
        & merged[names].notna().all(axis=1)
        & (merged.discipline_team_history_ok.fillna(0).astype(int) == 1)
    ].copy()
    train = fit_rows[fit_rows.split == "train"].copy()
    policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy), len(fit_rows)) == 0:
        raise ResearchError("empty R42H fit split")

    core = select_core_features(merged)
    selected_C, baseline_policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"R42H baseline policy selected C outside preregistered grid: {selected_C}")
    challenger_features = core + names
    baseline = make_model(float(selected_C), base_cfg)
    challenger = make_model(float(selected_C), base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class)
    challenger.fit(fit_rows[challenger_features], fit_rows.total_class)
    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES)
    p_ch = align_probability(challenger, sample[challenger_features], TOTAL_CLASSES)
    y = sample.total_class.to_numpy(int)
    base_comp = metric_components(y, p_base, TOTAL_CLASSES)
    ch_comp = metric_components(y, p_ch, TOTAL_CLASSES)
    base_metrics = metric_summary(base_comp)
    ch_metrics = metric_summary(ch_comp)
    boot = paired_bootstrap(base_comp, ch_comp, cfg)
    gate = {
        "logloss_p95_below_zero": bool(boot["logloss"]["p95"] < 0.0),
        "brier_nonworse": bool(ch_metrics["brier"] <= base_metrics["brier"]),
        "rps_nonworse": bool(ch_metrics["rps"] <= base_metrics["rps"]),
    }
    gate["all_required"] = bool(all(gate.values()))

    draw_mask = sample.goal_difference.to_numpy(int) == 0
    draw_diag = None
    if np.any(draw_mask):
        draw_diag = {"rows": int(draw_mask.sum()), "baseline_total_logloss": float(base_comp.loc[draw_mask, "logloss"].mean()), "challenger_total_logloss": float(ch_comp.loc[draw_mask, "logloss"].mean()), "delta": float(ch_comp.loc[draw_mask, "logloss"].mean() - base_comp.loc[draw_mask, "logloss"].mean())}

    result = {
        **base_receipt,
        "status": "PASS_R42H_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42H_TEAM_RED_FOUL_DIRECT_TOTAL_FIXED200" if gate["all_required"] else "FAIL_R42H_TEAM_RED_FOUL_DIRECT_TOTAL_NO_INCREMENT_FIXED200",
        "sample": {
            "rows": 200, "seed": int(cfg["sample_contract"]["seed"]), "identity_sha256": sample_sha,
            "overlap_with_prior_2600": 0, "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()), "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()), "labels_used_for_identity_selection": False, "blind_claim": False,
        },
        "model_contract": {
            "baseline_policy_selected_C": float(selected_C), "baseline_policy_grid": baseline_policy_grid,
            "same_C_used_by_challenger": True, "baseline_feature_count": int(len(core)),
            "team_red_foul_feature_count": int(len(names)), "challenger_feature_count": int(len(challenger_features)),
            "scientific_parameters_selected_on_fixed200": 0,
            "baseline_max_solver_iterations": int(np.max(baseline.named_steps["model"].n_iter_)),
            "challenger_max_solver_iterations": int(np.max(challenger.named_steps["model"].n_iter_)),
            "baseline_probability_sum_max_residual": float(np.max(np.abs(p_base.sum(axis=1) - 1.0))),
            "challenger_probability_sum_max_residual": float(np.max(np.abs(p_ch.sum(axis=1) - 1.0))),
        },
        "metrics": {
            "baseline": base_metrics, "challenger": ch_metrics,
            "delta_challenger_minus_baseline": {k: float(ch_metrics[k] - base_metrics[k]) for k in base_metrics},
            "paired_bootstrap": boot,
            "tail_T_ge_4": {"baseline": tail_binary(y, p_base), "challenger": tail_binary(y, p_ch)},
            "actual_draw_subset_total_logloss": draw_diag,
            "gate": gate,
        },
        "interpretation_limits": [
            "This isolates prior team red-card/foul history; yellow-card and referee-history features are excluded from the challenger.",
            "Current-match red cards, fouls and result never enter their own features.",
            "A PASS would justify replication only, not promotion or a Draw-solution claim.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    cfg = load_json(DEFAULT_CONFIG)
    assert len(cfg["feature_contract"]["feature_names"]) == 12
    assert all("yellow" not in x and "ref_" not in x for x in cfg["feature_contract"]["feature_names"])
    assert cfg["method_contract"]["manual_red_card_weight"] is False
    print(json.dumps({"status": "PASS_R42H_SELF_TEST", "feature_count": 12}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    result = run(load_json(args.config), args.out)
    print(json.dumps({"status": result["status"], "scientific_verdict": result["scientific_verdict"], "coverage": result["coverage"], "sample": result.get("sample"), "model_contract": result.get("model_contract"), "metrics": result.get("metrics")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
