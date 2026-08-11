#!/usr/bin/env python3
"""R42N: retrospective OU2.5 increment for the historical Direct-T head.

Question under test: does one frozen de-vigged closing/reference OU2.5 probability add
incremental information to the current 47-feature historical Direct-T core for
P(T=0,1,2,3,4,5,6,7+)?

Historical market prices do not have original quote timestamps, so this is retrospective
market-reference research only. It cannot establish formal PIT validity or promote the
formal chain. The fixed200 selects no feature, parameter or threshold.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41_priority_fixed200_battery import materialize_market
from evaluate_r41a_fixed200_joint_error_decomposition import (
    add_identity_key,
    load_json,
    select_fixed_identities,
    split_for_latest_complete,
)
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import paired_bootstrap
from evaluate_r42f_htft_response_direct_total_fixed200 import build_htft_features, load_ht_rows
from evaluate_r42g_discipline_referee_direct_total_fixed200 import tail_binary
from evaluate_r42j_all_history_pair_recovery_direct_total_fixed200 import (
    add_recovered_all_pair_features,
    recovered_feature_names,
)
from evaluate_r42m_weak_signal_fusion_direct_total_fixed200 import reproduce_prior3600
from v510_historical_structure_features_r1 import (
    ResearchError,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import (
    align_probability,
    make_model,
    metric_components,
    metric_summary,
    select_C,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42n_ou25_direct_total_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42n_ou25_direct_total_fixed200_status.json"
TOTAL_CLASSES = list(range(8))


def reproduce_prior3800(
    raw: pd.DataFrame,
    seasons: dict[str, list[str]],
    base_features: pd.DataFrame,
    cfg: dict[str, Any],
) -> tuple[set[str], dict[str, str]]:
    """Reproduce all 3,800 already-consumed fixed200 identities through R42M."""
    r42m_cfg = load_json(ROOT / "config" / "r42m_weak_signal_fusion_direct_total_fixed200.json")
    excluded3600, hashes = reproduce_prior3600(raw, seasons, base_features, r42m_cfg)
    if len(excluded3600) != 3600:
        raise ResearchError(f"expected prior3600, got {len(excluded3600)}")

    r42j_cfg = load_json(ROOT / str(r42m_cfg["feature_contract"]["all_pair_source_config"]))
    frame = add_recovered_all_pair_features(base_features, r42j_cfg)
    pair_names = recovered_feature_names(r42j_cfg)

    r42f_cfg = load_json(ROOT / str(r42m_cfg["feature_contract"]["htft_source_config"]))
    frame["date_norm"] = pd.to_datetime(frame["date_key"], errors="raise").dt.date.astype(str)
    ht_rows, _ = load_ht_rows(set(frame.competition_id.astype(str)))
    htft, _ = build_htft_features(ht_rows, r42f_cfg)
    ht_names = [str(x) for x in r42f_cfg["feature_contract"]["feature_names"]]
    keep = [
        "competition_id", "season", "date_norm", "home_team", "away_team",
        "home_state_trials_total", "away_state_trials_total",
    ] + ht_names
    frame = frame.merge(
        htft[keep],
        on=["competition_id", "season", "date_norm", "home_team", "away_team"],
        how="left",
        validate="one_to_one",
    )
    names = ht_names + pair_names
    min_trials = float(r42m_cfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    target = frame[
        (frame.split == "target_pool")
        & frame[names].notna().all(axis=1)
        & (frame.home_state_trials_total.fillna(0) >= min_trials)
        & (frame.away_state_trials_total.fillna(0) >= min_trials)
    ].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded3600)].copy()
    ids, sha = select_fixed_identities(
        fresh,
        int(r42m_cfg["sample_contract"]["sample_size"]),
        int(r42m_cfg["sample_contract"]["seed"]),
    )
    expected = str(cfg["sample_contract"]["exclude_R42M_identity_sha256"])
    if sha != expected:
        raise ResearchError(f"R42M identity mismatch {sha} != {expected}")
    excluded3800 = excluded3600 | set(ids)
    expected_rows = int(cfg["sample_contract"]["prior_consumed_rows_before_R42N"])
    if len(excluded3800) != expected_rows:
        raise ResearchError(f"expected prior{expected_rows}, got {len(excluded3800)}")
    out = dict(hashes)
    out["R42M"] = sha
    return excluded3800, out


def binary_over25(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    truth = (np.asarray(y, dtype=int) >= 3).astype(float)
    prob = np.clip(np.asarray(p, dtype=float)[:, 3:].sum(axis=1), 1e-15, 1 - 1e-15)
    ll = -(truth * np.log(prob) + (1 - truth) * np.log(1 - prob))
    return {
        "logloss": float(ll.mean()),
        "brier": float(np.mean((prob - truth) ** 2)),
        "observed_rate": float(truth.mean()),
        "mean_probability": float(prob.mean()),
    }


def market_over25(y: np.ndarray, p_over: np.ndarray) -> dict[str, float]:
    truth = (np.asarray(y, dtype=int) >= 3).astype(float)
    prob = np.clip(np.asarray(p_over, dtype=float), 1e-15, 1 - 1e-15)
    ll = -(truth * np.log(prob) + (1 - truth) * np.log(1 - prob))
    return {
        "logloss": float(ll.mean()),
        "brier": float(np.mean((prob - truth) ** 2)),
        "observed_rate": float(truth.mean()),
        "mean_probability": float(prob.mean()),
    }


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    excluded3800, prior_hashes = reproduce_prior3800(raw, seasons, features, cfg)

    market_cfg = load_json(ROOT / str(cfg["feature_contract"]["market_source_config"]))
    market = materialize_market(raw, market_cfg["market_contract"])
    keep_market = ["identity_key", "ou_over_prob", "ou_source", "source_file", "row_number"]
    frame = features.merge(market[keep_market], on="identity_key", how="left", validate="one_to_one")
    eps = 1e-6
    p = frame.ou_over_prob.astype(float)
    frame["ou25_logit_over"] = np.log(np.clip(p, eps, 1 - eps) / np.clip(1 - p, eps, 1 - eps))

    eligible = frame.ou_over_prob.notna() & np.isfinite(frame.ou_over_prob.astype(float))
    eligible &= (frame.ou_over_prob.astype(float) > 0) & (frame.ou_over_prob.astype(float) < 1)
    target = frame[(frame.split == "target_pool") & eligible].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded3800)].copy()
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior3800_exclusion"])

    coverage_by_comp = {
        str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()
    }
    coverage_by_source = {
        str(k): int(v) for k, v in fresh.groupby("ou_source", dropna=False).size().sort_index().items()
    }
    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": len(excluded3800), "hashes": prior_hashes},
        "coverage": {
            "market_materialized_identities": int(len(market)),
            "valid_ou25_identities_all_splits": int(eligible.sum()),
            "target_pool_valid_ou25_before_prior_exclusion": int(len(target)),
            "fresh_target_rows_after_prior3800_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "fresh_target_rows_by_ou_source": coverage_by_source,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "target_labels_used_for_identity_selection": False,
            "fixed200_used_for_C_selection": False,
            "fixed200_used_for_feature_selection": False,
            "fixed200_used_for_threshold_selection": False,
            "model_fits_before_coverage_gate": 0,
        },
        "market_boundary": {
            "totals_line": float(cfg["feature_contract"]["totals_line"]),
            "retrospective_reference_only": True,
            "original_quote_timestamps_available": False,
            "formal_PIT_claim": False,
            "de_vig_method": "reciprocal odds normalized within Over/Under pair",
        },
        "governance": cfg["governance"],
    }

    if len(fresh) < minimum:
        result = {
            **base_receipt,
            "status": "STOP_R42N_OU25_COVERAGE_LT200",
            "scientific_verdict": "DO_NOT_CONSUME_FIXED200_OU25_COVERAGE_INSUFFICIENT",
            "sample": None,
            "model_fits": 0,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    ids, sample_sha = select_fixed_identities(
        fresh,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    sample = fresh[fresh.identity_key.astype(str).isin(set(ids))].copy().sort_values("identity_key")
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded3800:
        raise ResearchError("R42N sample identity contract failed")

    fit_rows = frame[frame.split.isin(["train", "policy"]) & eligible].copy()
    train = fit_rows[fit_rows.split == "train"].copy()
    policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy)) == 0:
        raise ResearchError("empty R42N fit split")

    core = select_core_features(frame)
    feature_name = str(cfg["feature_contract"]["feature_name"])
    if feature_name != "ou25_logit_over" or int(cfg["feature_contract"]["feature_count"]) != 1:
        raise ResearchError("R42N frozen OU feature contract mismatch")
    challenger_features = core + [feature_name]

    selected_C, policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"R42N selected C outside grid: {selected_C}")

    baseline = make_model(float(selected_C), base_cfg)
    challenger = make_model(float(selected_C), base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class)
    challenger.fit(fit_rows[challenger_features], fit_rows.total_class)

    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES)
    p_ch = align_probability(challenger, sample[challenger_features], TOTAL_CLASSES)
    y = sample.total_class.to_numpy(int)
    bc = metric_components(y, p_base, TOTAL_CLASSES)
    cc = metric_components(y, p_ch, TOTAL_CLASSES)
    bm = metric_summary(bc)
    cm = metric_summary(cc)
    boot = paired_bootstrap(bc, cc, cfg)

    gate = {
        "logloss_p95_below_zero": bool(boot["logloss"]["p95"] < 0),
        "brier_nonworse": bool(cm["brier"] <= bm["brier"]),
        "rps_nonworse": bool(cm["rps"] <= bm["rps"]),
    }
    gate["all_required"] = bool(all(gate.values()))

    draw_mask = sample.goal_difference.to_numpy(int) == 0
    draw_diag = None
    if np.any(draw_mask):
        draw_diag = {
            "rows": int(draw_mask.sum()),
            "baseline_total_logloss": float(bc.loc[draw_mask, "logloss"].mean()),
            "challenger_total_logloss": float(cc.loc[draw_mask, "logloss"].mean()),
            "delta": float(cc.loc[draw_mask, "logloss"].mean() - bc.loc[draw_mask, "logloss"].mean()),
        }

    result = {
        **base_receipt,
        "status": "PASS_R42N_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": (
            "PASS_R42N_OU25_DIRECT_TOTAL_INCREMENT_FIXED200"
            if gate["all_required"]
            else "FAIL_R42N_OU25_DIRECT_TOTAL_NO_INCREMENT_FIXED200"
        ),
        "sample": {
            "rows": 200,
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "overlap_with_prior_3800": 0,
            "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {
                str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()
            },
            "ou_source_counts": {
                str(k): int(v) for k, v in sample.groupby("ou_source", dropna=False).size().sort_index().items()
            },
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {
                str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()
            },
            "actual_draw_rows": int(draw_mask.sum()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "model_contract": {
            "baseline_policy_selected_C": float(selected_C),
            "baseline_policy_grid": policy_grid,
            "same_C_used_by_challenger": True,
            "baseline_feature_count": len(core),
            "ou_feature_count": 1,
            "ou_feature_name": feature_name,
            "challenger_feature_count": len(challenger_features),
            "scientific_parameters_selected_on_fixed200": 0,
            "manual_feature_weight": False,
            "baseline_max_solver_iterations": int(np.max(baseline.named_steps["model"].n_iter_)),
            "challenger_max_solver_iterations": int(np.max(challenger.named_steps["model"].n_iter_)),
            "baseline_probability_sum_max_residual": float(np.max(np.abs(p_base.sum(axis=1) - 1))),
            "challenger_probability_sum_max_residual": float(np.max(np.abs(p_ch.sum(axis=1) - 1))),
        },
        "metrics": {
            "baseline": bm,
            "challenger": cm,
            "delta_challenger_minus_baseline": {k: float(cm[k] - bm[k]) for k in bm},
            "paired_bootstrap": boot,
            "market_over25_reference": market_over25(y, sample.ou_over_prob.to_numpy(float)),
            "baseline_over25_binary": binary_over25(y, p_base),
            "challenger_over25_binary": binary_over25(y, p_ch),
            "tail_T_ge_4": {"baseline": tail_binary(y, p_base), "challenger": tail_binary(y, p_ch)},
            "actual_draw_subset_total_logloss": draw_diag,
            "gate": gate,
        },
        "interpretation_limits": [
            "Historical OU2.5 prices have no original quote timestamps and remain retrospective market references only.",
            "This experiment tests OU2.5 as an incremental feature for Direct-T, not as a Draw/HDA correction.",
            "A PASS authorizes only an exact-method disjoint replication; it does not promote the formal Direct-T track.",
            "A FAIL closes this frozen single-feature static OU2.5 increment and does not authorize post-result transform tuning on the same fixed200.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    p = np.asarray([0.2, 0.5, 0.8])
    z = np.log(p / (1 - p))
    assert np.all(np.isfinite(z))
    fake = np.zeros((3, 8), dtype=float)
    fake[0, 0] = 1.0
    fake[1, 3] = 1.0
    fake[2, 7] = 1.0
    d = binary_over25(np.asarray([0, 3, 7]), fake)
    assert d["observed_rate"] == 2 / 3
    cfg = load_json(DEFAULT_CONFIG)
    assert cfg["feature_contract"]["feature_count"] == 1
    assert cfg["method_contract"]["formal_PIT_claim"] is False
    print(json.dumps({"status": "PASS_R42N_SELF_TEST", "ou_feature_count": 1, "formal_PIT_claim": False}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run(load_json(args.config), args.out)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "coverage": result["coverage"],
        "sample": result.get("sample"),
        "model_contract": result.get("model_contract"),
        "metrics": result.get("metrics"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
