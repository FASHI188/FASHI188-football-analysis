#!/usr/bin/env python3
"""R42C: two-stage favourite-win then Draw-vs-upset probability decomposition.

Research only. Historical closing/reference 1X2 prices have no original quote timestamps,
so they are retrospective market references only. The fixed200 is identity-selected after
excluding all previously consumed R41/R42A fixed200 identities. Test labels never choose
sample, features, C, thresholds or model form.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, select_fixed_identities
from evaluate_r41_priority_fixed200_battery import HDA_CLASSES, compare, fit_model, materialize_market, prepare_features, select_method_sample
from evaluate_r42a_dynamic_diagonal_fixed200 import add_season_position, reproduce_all_prior_fixed200
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42c_favourite_nonwin_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42c_favourite_nonwin_fixed200_status.json"


def add_favourite_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    eps = 1e-12
    out["fav_is_home"] = out.p_h.astype(float) >= out.p_a.astype(float)
    out["p_fav"] = np.where(out.fav_is_home, out.p_h, out.p_a).astype(float)
    out["p_upset"] = np.where(out.fav_is_home, out.p_a, out.p_h).astype(float)
    out["p_nonwin"] = 1.0 - out.p_fav
    out["log_p_fav"] = np.log(np.clip(out.p_fav, eps, 1.0))
    out["log_p_draw"] = np.log(np.clip(out.p_d.astype(float), eps, 1.0))
    out["log_p_upset"] = np.log(np.clip(out.p_upset, eps, 1.0))
    out["fav_gap"] = out.p_fav - out.p_upset
    out["draw_share_nonwin_market"] = out.p_d.astype(float) / np.clip(out.p_d.astype(float) + out.p_upset, eps, None)
    out["favourite_win"] = np.where(
        out.fav_is_home,
        out.outcome.astype(int) == 0,
        out.outcome.astype(int) == 2,
    ).astype(int)
    out["draw_given_favourite_nonwin"] = (out.outcome.astype(int) == 1).astype(int)
    return out


def reproduce_R42A_sample(raw: pd.DataFrame, seasons: dict[str, list[str]], prior_ids: set[str], cfg: dict[str, Any]) -> tuple[set[str], str]:
    features = add_identity_key(build_features(raw))
    features = add_season_position(features, seasons)
    target_pool = features[(features.season_position == 4) & (~features.identity_key.isin(prior_ids))].copy()
    ids, digest = select_fixed_identities(target_pool, 200, int(cfg["sample_contract"]["exclude_R42A_seed"]))
    expected = str(cfg["sample_contract"]["exclude_R42A_identity_sha256"])
    if digest != expected:
        raise ResearchError(f"R42A identity mismatch: {digest} != {expected}")
    selected = set(ids)
    if selected & prior_ids:
        raise ResearchError("R42A reproduction overlaps prior1400")
    return selected, digest


def build_two_stage_probability(
    sample: pd.DataFrame,
    p_stage1: np.ndarray,
    p_stage2: np.ndarray,
) -> np.ndarray:
    # Binary class ordering is [0,1], where class1 means favourite win or Draw|nonwin.
    p_fwin = np.clip(p_stage1[:, 1], 1e-12, 1 - 1e-12)
    p_draw_cond = np.clip(p_stage2[:, 1], 1e-12, 1 - 1e-12)
    p_draw = (1.0 - p_fwin) * p_draw_cond
    p_upset = (1.0 - p_fwin) * (1.0 - p_draw_cond)
    out = np.zeros((len(sample), 3), dtype=float)
    home_fav = sample.fav_is_home.to_numpy(bool)
    out[home_fav, 0] = p_fwin[home_fav]
    out[home_fav, 1] = p_draw[home_fav]
    out[home_fav, 2] = p_upset[home_fav]
    out[~home_fav, 2] = p_fwin[~home_fav]
    out[~home_fav, 1] = p_draw[~home_fav]
    out[~home_fav, 0] = p_upset[~home_fav]
    residual = float(np.max(np.abs(out.sum(axis=1) - 1.0)))
    if residual > 1e-10:
        raise ResearchError(f"two-stage probability conservation failed: {residual}")
    return out


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    r42a_cfg = load_json(ROOT / "config" / "r42a_dynamic_diagonal_fixed200.json")
    prior1400, prior_hashes = reproduce_all_prior_fixed200(raw, seasons, r42a_cfg)
    r42a_ids, r42a_sha = reproduce_R42A_sample(raw, seasons, prior1400, cfg)
    excluded_ids = prior1400 | r42a_ids
    if len(excluded_ids) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42C"]):
        raise ResearchError(f"expected 1600 prior consumed rows, got {len(excluded_ids)}")

    market = materialize_market(raw, cfg["market_contract"])
    parent_cfg = load_json(ROOT / "config" / "r41d_replication_fixed200.json")
    frame = prepare_features(raw, market, seasons, parent_cfg)
    frame = add_favourite_features(frame)
    eligible = frame.book_count.fillna(0).astype(int) >= 1

    sample, sample_sha = select_method_sample(
        frame,
        eligible,
        excluded_ids,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    overlap = int(len(set(sample.identity_key.astype(str)) & excluded_ids))
    if overlap:
        raise ResearchError(f"R42C overlaps prior consumed rows: {overlap}")

    eligible_frame = frame[eligible].copy()
    train = eligible_frame[eligible_frame.split == "train"].copy()
    policy = eligible_frame[eligible_frame.split == "policy"].copy()
    fit = eligible_frame[eligible_frame.split.isin(["train", "policy"])].copy()
    if min(len(train), len(policy), len(fit)) == 0:
        raise ResearchError("empty R42C fit split")

    baseline_features = [str(x) for x in cfg["method_contract"]["baseline_features"]]
    stage1_features = [str(x) for x in cfg["method_contract"]["stage1_features"]]
    stage2_features = [str(x) for x in cfg["method_contract"]["stage2_features"]]

    p_base, base_receipt = fit_model(
        train, policy, fit, sample, baseline_features, "outcome", HDA_CLASSES, cfg, base_cfg
    )
    p_stage1, stage1_receipt = fit_model(
        train, policy, fit, sample, stage1_features, "favourite_win", [0, 1], cfg, base_cfg
    )

    train2 = train[train.favourite_win.astype(int) == 0].copy()
    policy2 = policy[policy.favourite_win.astype(int) == 0].copy()
    fit2 = fit[fit.favourite_win.astype(int) == 0].copy()
    if min(len(train2), len(policy2), len(fit2)) == 0:
        raise ResearchError("empty R42C stage2 fit split")
    p_stage2, stage2_receipt = fit_model(
        train2, policy2, fit2, sample, stage2_features, "draw_given_favourite_nonwin", [0, 1], cfg, base_cfg
    )

    p_challenger = build_two_stage_probability(sample, p_stage1, p_stage2)
    comparison = compare(
        sample.outcome.to_numpy(int),
        p_base,
        p_challenger,
        cfg,
        int(cfg["sample_contract"]["seed"]) + 1,
    )
    pass_gate = bool(comparison["gate"]["all_required"])
    verdict = (
        "PASS_R42C_FAVOURITE_NONWIN_INCREMENT_FIXED200"
        if pass_gate
        else "FAIL_R42C_FAVOURITE_NONWIN_NO_INCREMENT_FIXED200"
    )

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R42C_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": verdict,
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {
            "rows": int(len(excluded_ids)),
            "R41A_through_R41D_replication_hashes": prior_hashes,
            "R42A_identity_sha256": r42a_sha,
            "all_expected_hashes_match": True,
        },
        "sample": {
            "rows": int(len(sample)),
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "overlap_with_prior_1600": overlap,
            "competitions_represented": int(sample.competition_id.nunique()),
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "actual_H": int((sample.outcome == 0).sum()),
            "actual_D": int((sample.outcome == 1).sum()),
            "actual_A": int((sample.outcome == 2).sum()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "model_contract": {
            "baseline": base_receipt,
            "stage1_favourite_win": stage1_receipt,
            "stage2_draw_given_nonwin": stage2_receipt,
            "stage2_train_rows": int(len(train2)),
            "stage2_policy_rows": int(len(policy2)),
            "probability_sum_max_residual": float(np.max(np.abs(p_challenger.sum(axis=1) - 1.0))),
            "manual_draw_bonus": False,
            "threshold": None,
        },
        "comparison": comparison,
        "market_boundary": {
            "scope": "retrospective closing/reference 1X2 decomposition",
            "formal_PIT_claim": False,
            "reason": "original quote timestamps are absent",
        },
        "interpretation_limits": [
            "R42C is a probability-factorization test, not a claim that favourite/non-favourite labels are causal football states.",
            "The challenger uses the same historical closing/reference 1X2 information family as the baseline, only a different conditional factorization.",
            "A PASS would justify replication only; it would not establish formal PIT validity or solve the Draw problem.",
        ],
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    sample = pd.DataFrame({"fav_is_home": [True, False]})
    p1 = np.asarray([[0.4, 0.6], [0.3, 0.7]])
    p2 = np.asarray([[0.5, 0.5], [0.25, 0.75]])
    out = build_two_stage_probability(sample, p1, p2)
    assert np.max(np.abs(out.sum(axis=1) - 1.0)) < 1e-12
    assert np.all(out >= 0)
    print(json.dumps({"status": "PASS", "self_test": True}))


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
        "sample": result["sample"],
        "comparison": result["comparison"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
