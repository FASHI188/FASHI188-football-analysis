#!/usr/bin/env python3
"""R42K: strictly-prior team total-distribution shape -> Direct-T.

The V5.1 feature builder already computes team-level total volatility, clean-sheet rate,
failed-to-score rate and 7+ tail rate for all-history and venue-specific states, but the
current Direct-T core does not select these team-level shape variables. R42K leaves the
formal builder unchanged and tests a frozen 16-feature sum/diff block once on a fresh
fixed200 after excluding all 3,200 prior exploratory identities.
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
from evaluate_r42g_discipline_referee_direct_total_fixed200 import tail_binary
from evaluate_r42j_all_history_pair_recovery_direct_total_fixed200 import (
    reproduce_prior3000,
    add_recovered_all_pair_features,
    recovered_feature_names,
)
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42k_total_shape_direct_total_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42k_total_shape_direct_total_fixed200_status.json"
TOTAL_CLASSES = list(range(8))


def reproduce_prior3200(raw: pd.DataFrame, seasons: dict[str, list[str]], base_features: pd.DataFrame, cfg: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    r42j_cfg = load_json(ROOT / "config" / "r42j_all_history_pair_recovery_direct_total_fixed200.json")
    excluded3000, hashes = reproduce_prior3000(raw, seasons, base_features, r42j_cfg)
    if len(excluded3000) != 3000:
        raise ResearchError(f"expected prior3000, got {len(excluded3000)}")

    f = base_features.copy()
    f["split_r42j"] = split_for_latest_complete(f, seasons, r42j_cfg)
    frame = add_recovered_all_pair_features(f, r42j_cfg)
    names = recovered_feature_names(r42j_cfg)
    target = frame[(frame.split_r42j == "target_pool") & frame[names].notna().all(axis=1)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded3000)].copy()
    ids, sha = select_fixed_identities(fresh, int(r42j_cfg["sample_contract"]["sample_size"]), int(r42j_cfg["sample_contract"]["seed"]))
    expected = str(cfg["sample_contract"]["exclude_R42J_identity_sha256"])
    if sha != expected:
        raise ResearchError(f"R42J identity mismatch {sha} != {expected}")
    excluded3200 = excluded3000 | set(ids)
    if len(excluded3200) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42K"]):
        raise ResearchError(f"expected prior3200 identities, got {len(excluded3200)}")
    out = dict(hashes); out["R42J"] = sha
    return excluded3200, out


def shape_feature_names(cfg: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for scope in cfg["feature_contract"]["scopes"]:
        for token in cfg["feature_contract"]["tokens"]:
            for op in cfg["feature_contract"]["operations"]:
                names.append(f"{scope}_shape_{token}_{op}")
    return names


def add_shape_features(frame: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    for scope in cfg["feature_contract"]["scopes"]:
        left_prefix, right_prefix = ("home_all", "away_all") if scope == "all" else ("home_venue", "away_venue")
        for token in cfg["feature_contract"]["tokens"]:
            left, right = f"{left_prefix}_{token}", f"{right_prefix}_{token}"
            if left not in out.columns or right not in out.columns:
                raise ResearchError(f"R42K missing source columns: {left}, {right}")
            out[f"{scope}_shape_{token}_sum"] = out[left] + out[right]
            out[f"{scope}_shape_{token}_diff"] = out[left] - out[right]
    return out


def source_selection_audit(frame: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    core = set(select_core_features(frame))
    checks: dict[str, Any] = {}
    for scope in cfg["feature_contract"]["scopes"]:
        left_prefix, right_prefix = ("home_all", "away_all") if scope == "all" else ("home_venue", "away_venue")
        for token in cfg["feature_contract"]["tokens"]:
            left, right = f"{left_prefix}_{token}", f"{right_prefix}_{token}"
            checks[f"{scope}:{token}"] = {
                "left_source_exists": left in frame.columns,
                "right_source_exists": right in frame.columns,
                "left_source_selected_by_current_core": left in core,
                "right_source_selected_by_current_core": right in core,
            }
    return checks


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    excluded3200, prior_hashes = reproduce_prior3200(raw, seasons, features, cfg)

    frame = add_shape_features(features, cfg)
    names = shape_feature_names(cfg)
    if len(names) != int(cfg["feature_contract"]["feature_count"]):
        raise ResearchError("R42K feature-count contract mismatch")
    source_audit = source_selection_audit(frame, cfg)
    if any(v["left_source_selected_by_current_core"] or v["right_source_selected_by_current_core"] for v in source_audit.values()):
        raise ResearchError("R42K source shape features unexpectedly already selected by current core")

    target = frame[(frame.split == "target_pool") & frame[names].notna().all(axis=1)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded3200)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior3200_exclusion"])

    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": int(len(excluded3200)), "hashes": prior_hashes},
        "representation_audit": {
            "formal_builder_mutated": False,
            "shape_source_selection": source_audit,
            "challenger_feature_names": names,
        },
        "coverage": {
            "fresh_target_rows_after_prior3200_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "target_labels_used_for_identity_selection": False,
            "shape_features_use_strictly_prior_score_history_only": True,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }

    if len(fresh) < minimum:
        result = {**base_receipt, "status": "STOP_R42K_TOTAL_SHAPE_COVERAGE_LT200", "scientific_verdict": "DO_NOT_CONSUME_FIXED200_TOTAL_SHAPE_COVERAGE_INSUFFICIENT", "sample": None, "model_fits": 0}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    ids, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(ids))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded3200:
        raise ResearchError("R42K sample identity contract failed")

    fit_rows = frame[frame.split.isin(["train", "policy"]) & frame[names].notna().all(axis=1)].copy()
    train = fit_rows[fit_rows.split == "train"].copy(); policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy)) == 0:
        raise ResearchError("empty R42K fit split")
    core = select_core_features(frame)
    selected_C, policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"R42K baseline selected C outside registered grid: {selected_C}")
    challenger_features = core + names
    baseline = make_model(float(selected_C), base_cfg); challenger = make_model(float(selected_C), base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class); challenger.fit(fit_rows[challenger_features], fit_rows.total_class)
    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES)
    p_ch = align_probability(challenger, sample[challenger_features], TOTAL_CLASSES)

    y = sample.total_class.to_numpy(int)
    base_comp = metric_components(y, p_base, TOTAL_CLASSES); ch_comp = metric_components(y, p_ch, TOTAL_CLASSES)
    base_metrics = metric_summary(base_comp); ch_metrics = metric_summary(ch_comp)
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
        "status": "PASS_R42K_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42K_TOTAL_SHAPE_DIRECT_TOTAL_FIXED200" if gate["all_required"] else "FAIL_R42K_TOTAL_SHAPE_DIRECT_TOTAL_NO_INCREMENT_FIXED200",
        "sample": {
            "rows": 200, "seed": int(cfg["sample_contract"]["seed"]), "identity_sha256": sample_sha,
            "overlap_with_prior_3200": 0, "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()), "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()), "labels_used_for_identity_selection": False, "blind_claim": False,
        },
        "model_contract": {
            "baseline_policy_selected_C": float(selected_C), "baseline_policy_grid": policy_grid,
            "same_C_used_by_challenger": True, "baseline_feature_count": int(len(core)),
            "total_shape_feature_count": int(len(names)), "challenger_feature_count": int(len(challenger_features)),
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
            "This isolates team-level total-volatility/zero-mass/tail-shape information already computed from strictly prior scores but omitted from the current core selector.",
            "The formal V5.1 feature builder and selector are not mutated by this research PR.",
            "A PASS would authorize one disjoint fixed200 replication only, not promotion.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    cfg = load_json(DEFAULT_CONFIG)
    row: dict[str, list[float]] = {}
    for scope in cfg["feature_contract"]["scopes"]:
        lp, rp = ("home_all", "away_all") if scope == "all" else ("home_venue", "away_venue")
        for token in cfg["feature_contract"]["tokens"]:
            row[f"{lp}_{token}"] = [2.0]
            row[f"{rp}_{token}"] = [0.5]
    x = add_shape_features(pd.DataFrame(row), cfg)
    names = shape_feature_names(cfg)
    assert len(names) == 16
    for scope in cfg["feature_contract"]["scopes"]:
        for token in cfg["feature_contract"]["tokens"]:
            assert abs(float(x.loc[0, f"{scope}_shape_{token}_sum"]) - 2.5) < 1e-12
            assert abs(float(x.loc[0, f"{scope}_shape_{token}_diff"]) - 1.5) < 1e-12
    print(json.dumps({"status": "PASS_R42K_SELF_TEST", "feature_count": 16, "formal_builder_mutated": False}))


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
