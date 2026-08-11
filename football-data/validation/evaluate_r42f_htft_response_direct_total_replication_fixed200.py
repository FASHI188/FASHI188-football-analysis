#!/usr/bin/env python3
"""Exact-method disjoint replication of R42F HT-to-FT response -> Direct-T.

No scientific parameter changes are permitted. This script reproduces the parent R42F
200 by identity, adds it to the prior 2,200 exclusion set, then identity-selects one new
200. Feature construction, same-day freeze, baseline policy C selection, challenger
feature block and all PASS gates are inherited unchanged from the parent contract.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, select_fixed_identities, split_for_latest_complete
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import reproduce_prior2200, paired_bootstrap, binary_low_event_metrics
from evaluate_r42f_htft_response_direct_total_fixed200 import load_ht_rows, build_htft_features
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42f_htft_response_direct_total_replication_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42f_htft_response_direct_total_replication_fixed200_status.json"
TOTAL_CLASSES = list(range(8))


def prepare_parent_frame(raw: pd.DataFrame, seasons: dict[str, list[str]], parent_cfg: dict[str, Any]) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, parent_cfg)
    features["date_norm"] = pd.to_datetime(features["date_key"], errors="raise").dt.date.astype(str)
    competitions = set(features.competition_id.astype(str))
    ht_rows, source_cov = load_ht_rows(competitions)
    htft, ht_audit = build_htft_features(ht_rows, parent_cfg)
    names = [str(x) for x in parent_cfg["feature_contract"]["feature_names"]]
    if htft.empty:
        raise ResearchError("R42F replication unexpectedly has no HTFT feature frame")
    merged = features.merge(
        htft,
        on=["competition_id", "season", "date_norm", "home_team", "away_team"],
        how="left",
        validate="one_to_one",
    )
    return merged, names, {"raw_htft_source_by_competition": source_cov, "htft_feature_build": ht_audit}


def eligible_target(merged: pd.DataFrame, names: list[str], parent_cfg: dict[str, Any]) -> pd.DataFrame:
    min_trials = float(parent_cfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    return merged[
        (merged.split == "target_pool")
        & merged[names].notna().all(axis=1)
        & (merged.home_state_trials_total.fillna(0) >= min_trials)
        & (merged.away_state_trials_total.fillna(0) >= min_trials)
    ].copy()


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    parent_cfg = load_json(ROOT / str(cfg["parent_config"]))
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    excluded2200, prior_hashes = reproduce_prior2200(raw, seasons, parent_cfg)
    if len(excluded2200) != 2200:
        raise ResearchError(f"expected prior2200 identities, got {len(excluded2200)}")

    merged, names, feature_audit = prepare_parent_frame(raw, seasons, parent_cfg)
    target = eligible_target(merged, names, parent_cfg)
    parent_pool = target[~target.identity_key.astype(str).isin(excluded2200)].copy()
    parent_ids, parent_sha = select_fixed_identities(
        parent_pool,
        int(parent_cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["exclude_parent_seed"]),
    )
    expected_parent = str(cfg["sample_contract"]["exclude_parent_identity_sha256"])
    if parent_sha != expected_parent:
        raise ResearchError(f"parent R42F identity mismatch: {parent_sha} != {expected_parent}")
    parent_set = set(parent_ids)
    if parent_set & excluded2200:
        raise ResearchError("parent R42F reproduction overlaps prior2200")

    excluded2400 = excluded2200 | parent_set
    if len(excluded2400) != int(cfg["sample_contract"]["prior_consumed_rows_before_replication"]):
        raise ResearchError(f"expected prior2400 identities, got {len(excluded2400)}")

    fresh = target[~target.identity_key.astype(str).isin(excluded2400)].copy()
    if len(fresh) < int(cfg["sample_contract"]["sample_size"]):
        result = {
            "schema_version": cfg["schema_version"],
            "status": "STOP_R42F_REPLICATION_COVERAGE_LT200",
            "scientific_verdict": "DO_NOT_CONSUME_REPLICATION_FIXED200_COVERAGE_INSUFFICIENT",
            "data_identity": identity,
            "excluded_incomplete_latest_seasons": excluded_latest,
            "prior_fixed200_exclusion": {"rows": 2400, "hashes": {**prior_hashes, "R42F_parent": parent_sha}},
            "coverage": {"fresh_target_rows_after_prior2400_exclusion": int(len(fresh)), "minimum_required": 200},
            "sample": None,
            "model_fits": 0,
            "governance": cfg["governance"],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    selected, sample_sha = select_fixed_identities(
        fresh,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    sample = fresh[fresh.identity_key.astype(str).isin(set(selected))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded2400:
        raise ResearchError("R42F replication sample identity contract failed")

    min_trials = float(parent_cfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    fit_rows = merged[
        merged.split.isin(["train", "policy"])
        & merged[names].notna().all(axis=1)
        & (merged.home_state_trials_total.fillna(0) >= min_trials)
        & (merged.away_state_trials_total.fillna(0) >= min_trials)
    ].copy()
    train = fit_rows[fit_rows.split == "train"].copy()
    policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy), len(fit_rows)) == 0:
        raise ResearchError("empty R42F replication fit split")

    core = select_core_features(merged)
    selected_C, baseline_policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in parent_cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"replication selected C outside parent grid: {selected_C}")
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

    compare_cfg = dict(parent_cfg)
    compare_cfg["sample_contract"] = dict(parent_cfg["sample_contract"])
    compare_cfg["sample_contract"]["seed"] = int(cfg["sample_contract"]["seed"])
    compare_cfg["decision_contract"] = cfg["decision_contract"]
    boot = paired_bootstrap(base_comp, ch_comp, compare_cfg)
    gate = {
        "logloss_p95_below_zero": bool(boot["logloss"]["p95"] < 0.0),
        "brier_nonworse": bool(ch_metrics["brier"] <= base_metrics["brier"]),
        "rps_nonworse": bool(ch_metrics["rps"] <= base_metrics["rps"]),
    }
    gate["all_required"] = bool(all(gate.values()))

    draw_mask = sample.goal_difference.to_numpy(int) == 0
    draw_diag = None
    if np.any(draw_mask):
        draw_diag = {
            "rows": int(draw_mask.sum()),
            "baseline_total_logloss": float(base_comp.loc[draw_mask, "logloss"].mean()),
            "challenger_total_logloss": float(ch_comp.loc[draw_mask, "logloss"].mean()),
            "delta": float(ch_comp.loc[draw_mask, "logloss"].mean() - base_comp.loc[draw_mask, "logloss"].mean()),
        }

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R42F_REPLICATION_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42F_HTFT_RESPONSE_DIRECT_TOTAL_REPLICATION_FIXED200" if gate["all_required"] else "FAIL_R42F_HTFT_RESPONSE_DIRECT_TOTAL_REPLICATION_NO_INCREMENT_FIXED200",
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": 2400, "hashes": {**prior_hashes, "R42F_parent": parent_sha}, "all_expected_hashes_match": True},
        "coverage": {
            **feature_audit,
            "target_rows_before_exclusion": int(len(target)),
            "fresh_target_rows_after_prior2400_exclusion": int(len(fresh)),
        },
        "sample": {
            "rows": 200,
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "overlap_with_prior_2400": 0,
            "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "exact_method_replication": {
            "parent_schema_version": parent_cfg["schema_version"],
            "parent_identity_sha256": parent_sha,
            "parent_feature_names": names,
            "feature_count": int(len(names)),
            "same_day_freeze": bool(parent_cfg["feature_contract"]["same_day_freeze_before_update"]),
            "baseline_C_grid": parent_cfg["fit_contract"]["baseline_C_grid"],
            "scientific_parameters_changed_from_parent": 0,
        },
        "model_contract": {
            "baseline_policy_selected_C": float(selected_C),
            "baseline_policy_grid": baseline_policy_grid,
            "same_C_used_by_challenger": True,
            "baseline_feature_count": int(len(core)),
            "htft_feature_count": int(len(names)),
            "challenger_feature_count": int(len(challenger_features)),
            "scientific_parameters_selected_on_fixed200": 0,
            "baseline_max_solver_iterations": int(np.max(baseline.named_steps["model"].n_iter_)),
            "challenger_max_solver_iterations": int(np.max(challenger.named_steps["model"].n_iter_)),
            "baseline_probability_sum_max_residual": float(np.max(np.abs(p_base.sum(axis=1) - 1.0))),
            "challenger_probability_sum_max_residual": float(np.max(np.abs(p_ch.sum(axis=1) - 1.0))),
        },
        "metrics": {
            "baseline": base_metrics,
            "challenger": ch_metrics,
            "delta_challenger_minus_baseline": {k: float(ch_metrics[k] - base_metrics[k]) for k in base_metrics},
            "paired_bootstrap": boot,
            "low_event_T_le_2": {"baseline": binary_low_event_metrics(y, p_base), "challenger": binary_low_event_metrics(y, p_ch)},
            "actual_draw_subset_total_logloss": draw_diag,
            "gate": gate,
        },
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    parent = load_json(ROOT / "config" / "r42f_htft_response_direct_total_fixed200.json")
    assert len(parent["feature_contract"]["feature_names"]) == 18
    assert parent["feature_contract"]["same_day_freeze_before_update"] is True
    assert parent["method_contract"]["manual_draw_bonus"] is False
    assert parent["method_contract"]["manual_total_adjustment"] is False
    print(json.dumps({"status": "PASS_R42F_REPLICATION_SELF_TEST", "scientific_parameters_changed": 0}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    result = run(load_json(args.config), args.out)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "coverage": result.get("coverage"),
        "sample": result.get("sample"),
        "exact_method_replication": result.get("exact_method_replication"),
        "metrics": result.get("metrics"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
