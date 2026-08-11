#!/usr/bin/env python3
"""R42J: recover non-colliding all-history pair features for Direct-T.

The V5.1 feature builder writes pair_* once from home_all/away_all and then writes the
same pair_* namespace again from home_venue/away_venue. The second write therefore
replaces the first. R42J does not mutate the formal builder. It reconstructs the missing
all-history pair sums/differences under a new all_pair_* namespace and challenges the
current Direct-T core once on a fresh fixed200.
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
from evaluate_r42f_htft_response_direct_total_fixed200 import load_ht_rows
from evaluate_r42g_discipline_referee_direct_total_fixed200 import tail_binary
from evaluate_r42i_two_half_generative_direct_total_fixed200 import reproduce_prior2800, build_half_label_frame
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42j_all_history_pair_recovery_direct_total_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42j_all_history_pair_recovery_direct_total_fixed200_status.json"
TOTAL_CLASSES = list(range(8))


def reproduce_prior3000(raw: pd.DataFrame, seasons: dict[str, list[str]], base_features: pd.DataFrame, cfg: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    r42i_cfg = load_json(ROOT / "config" / "r42i_two_half_generative_direct_total_fixed200.json")
    excluded2800, hashes = reproduce_prior2800(raw, seasons, base_features, r42i_cfg)
    if len(excluded2800) != 2800:
        raise ResearchError(f"expected prior2800, got {len(excluded2800)}")

    f = base_features.copy()
    f["split_r42i"] = split_for_latest_complete(f, seasons, r42i_cfg)
    f["date_norm"] = pd.to_datetime(f["date_key"], errors="raise").dt.date.astype(str)
    competitions = set(f.competition_id.astype(str))
    ht_rows, _ = load_ht_rows(competitions)
    half_labels, _ = build_half_label_frame(ht_rows)
    m = f.merge(half_labels, on=["competition_id", "season", "date_norm", "home_team", "away_team"], how="left", validate="one_to_one")
    historical = m[m.split_r42i.isin(["train", "policy"]) & (m.half_labels_observed.fillna(0).astype(int) == 1)].copy()
    min_fit = int(r42i_cfg["coverage_gate"]["minimum_train_policy_ht_rows_per_competition"])
    fit_counts = historical.groupby("competition_id").size()
    supported = {str(k) for k, v in fit_counts.items() if int(v) >= min_fit}
    target = m[(m.split_r42i == "target_pool") & m.competition_id.astype(str).isin(supported)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2800)].copy()
    ids, sha = select_fixed_identities(fresh, int(r42i_cfg["sample_contract"]["sample_size"]), int(r42i_cfg["sample_contract"]["seed"]))
    expected = str(cfg["sample_contract"]["exclude_R42I_identity_sha256"])
    if sha != expected:
        raise ResearchError(f"R42I identity mismatch {sha} != {expected}")
    excluded3000 = excluded2800 | set(ids)
    if len(excluded3000) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42J"]):
        raise ResearchError(f"expected prior3000 identities, got {len(excluded3000)}")
    out = dict(hashes); out["R42I"] = sha
    return excluded3000, out


def recovered_feature_names(cfg: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for token in cfg["feature_contract"]["pair_tokens"]:
        names.extend([f"all_pair_{token}_sum", f"all_pair_{token}_diff"])
    return names


def add_recovered_all_pair_features(frame: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    for token in cfg["feature_contract"]["pair_tokens"]:
        left = f"home_all_{token}"; right = f"away_all_{token}"
        if left not in out.columns or right not in out.columns:
            raise ResearchError(f"R42J missing underlying all-history columns for {token}")
        out[f"all_pair_{token}_sum"] = out[left] + out[right]
        out[f"all_pair_{token}_diff"] = out[left] - out[right]
    return out


def collision_audit(frame: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for token in cfg["feature_contract"]["pair_tokens"]:
        existing_sum = f"pair_{token}_sum"; existing_diff = f"pair_{token}_diff"
        hv, av = f"home_venue_{token}", f"away_venue_{token}"
        alls, alld = f"all_pair_{token}_sum", f"all_pair_{token}_diff"
        required = [existing_sum, existing_diff, hv, av, alls, alld]
        if any(x not in frame.columns for x in required):
            raise ResearchError(f"R42J collision audit missing columns for {token}")
        vals = frame[required].replace([np.inf, -np.inf], np.nan).dropna()
        if vals.empty:
            rows[token] = {"rows": 0}
            continue
        venue_sum = vals[hv] + vals[av]
        venue_diff = vals[hv] - vals[av]
        rows[token] = {
            "rows": int(len(vals)),
            "existing_pair_vs_venue_sum_max_abs": float(np.max(np.abs(vals[existing_sum] - venue_sum))),
            "existing_pair_vs_venue_diff_max_abs": float(np.max(np.abs(vals[existing_diff] - venue_diff))),
            "existing_pair_vs_all_sum_mean_abs": float(np.mean(np.abs(vals[existing_sum] - vals[alls]))),
            "existing_pair_vs_all_diff_mean_abs": float(np.mean(np.abs(vals[existing_diff] - vals[alld]))),
        }
    return rows


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    excluded3000, prior_hashes = reproduce_prior3000(raw, seasons, features, cfg)

    frame = add_recovered_all_pair_features(features, cfg)
    names = recovered_feature_names(cfg)
    if len(names) != int(cfg["feature_contract"]["feature_count"]):
        raise ResearchError("R42J feature-count contract mismatch")
    collision = collision_audit(frame, cfg)

    target = frame[(frame.split == "target_pool") & frame[names].notna().all(axis=1)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded3000)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior3000_exclusion"])
    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": int(len(excluded3000)), "hashes": prior_hashes},
        "representation_audit": {
            "pair_namespace_collision": collision,
            "formal_builder_mutated": False,
            "recovered_feature_namespace": "all_pair_*",
        },
        "coverage": {
            "fresh_target_rows_after_prior3000_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "target_labels_used_for_identity_selection": False,
            "recovered_features_use_strictly_prior_score_history_only": True,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }
    if len(fresh) < minimum:
        result = {**base_receipt, "status": "STOP_R42J_ALL_PAIR_COVERAGE_LT200", "scientific_verdict": "DO_NOT_CONSUME_FIXED200_ALL_PAIR_COVERAGE_INSUFFICIENT", "sample": None, "model_fits": 0}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    ids, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(ids))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded3000:
        raise ResearchError("R42J sample identity contract failed")

    fit_rows = frame[frame.split.isin(["train", "policy"]) & frame[names].notna().all(axis=1)].copy()
    train = fit_rows[fit_rows.split == "train"].copy(); policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy)) == 0:
        raise ResearchError("empty R42J fit split")
    core = select_core_features(frame)
    selected_C, policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"R42J baseline selected C outside registered grid: {selected_C}")
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
        "status": "PASS_R42J_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42J_ALL_HISTORY_PAIR_RECOVERY_FIXED200" if gate["all_required"] else "FAIL_R42J_ALL_HISTORY_PAIR_RECOVERY_NO_INCREMENT_FIXED200",
        "sample": {
            "rows": 200, "seed": int(cfg["sample_contract"]["seed"]), "identity_sha256": sample_sha,
            "overlap_with_prior_3000": 0, "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()), "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()), "labels_used_for_identity_selection": False, "blind_claim": False,
        },
        "model_contract": {
            "baseline_policy_selected_C": float(selected_C), "baseline_policy_grid": policy_grid,
            "same_C_used_by_challenger": True, "baseline_feature_count": int(len(core)),
            "recovered_all_pair_feature_count": int(len(names)), "challenger_feature_count": int(len(challenger_features)),
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
            "This tests recovery of an existing strictly-prior all-history representation that is absent from the current pair_* namespace after venue overwrite.",
            "The formal V5.1 feature builder is not mutated by this research PR.",
            "A PASS would authorize one disjoint fixed200 replication only, not promotion.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    cfg = load_json(DEFAULT_CONFIG)
    row: dict[str, list[float]] = {}
    for token in cfg["feature_contract"]["pair_tokens"]:
        row[f"home_all_{token}"] = [2.0]; row[f"away_all_{token}"] = [1.0]
        row[f"home_venue_{token}"] = [4.0]; row[f"away_venue_{token}"] = [3.0]
        row[f"pair_{token}_sum"] = [7.0]; row[f"pair_{token}_diff"] = [1.0]
    x = add_recovered_all_pair_features(pd.DataFrame(row), cfg)
    assert len(recovered_feature_names(cfg)) == 18
    for token in cfg["feature_contract"]["pair_tokens"]:
        assert abs(float(x.loc[0, f"all_pair_{token}_sum"]) - 3.0) < 1e-12
        assert abs(float(x.loc[0, f"all_pair_{token}_diff"]) - 1.0) < 1e-12
    audit = collision_audit(x, cfg)
    assert all(v["existing_pair_vs_venue_sum_max_abs"] < 1e-12 for v in audit.values())
    assert all(v["existing_pair_vs_all_sum_mean_abs"] > 0 for v in audit.values())
    print(json.dumps({"status": "PASS_R42J_SELF_TEST", "recovered_feature_count": 18, "formal_builder_mutated": False}))


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
