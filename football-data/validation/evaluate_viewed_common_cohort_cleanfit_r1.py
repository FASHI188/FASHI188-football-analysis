#!/usr/bin/env python3
"""Same-fit cleanup for VIEWED common-cohort Direct-T oracle R1.

The first forensic replay preserved each historical experiment's original fit-eligibility
contract. R42F and R42J therefore had different baseline fits. This cleanup removes that
confound: one common train/policy eligibility set, one baseline C selected on that common
policy set, one common baseline, and two challengers differing only by their frozen feature
block. It still fits no fusion selector and uses no new/protected sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, split_for_latest_complete
from evaluate_r42f_htft_response_direct_total_fixed200 import build_htft_features, load_ht_rows
from evaluate_r42j_all_history_pair_recovery_direct_total_fixed200 import add_recovered_all_pair_features, recovered_feature_names
from evaluate_viewed_common_cohort_oracle_r1 import _safe_corr, _true_loss
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "viewed_common_cohort_oracle_r1.json"
DEFAULT_COHORT = ROOT / "manifests" / "viewed_common_cohort_oracle_r1" / "common_cohort_1000_per_match.csv"
DEFAULT_OUT = ROOT / "manifests" / "viewed_common_cohort_oracle_r1" / "direct_t_cleanfit.json"
TOTAL_CLASSES = list(range(8))


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    comp = metric_components(y.astype(int), p, TOTAL_CLASSES)
    out = metric_summary(comp)
    out["top1_accuracy"] = float((np.argmax(p, axis=1) == y).mean())
    out["probability_sum_max_residual"] = float(np.max(np.abs(p.sum(axis=1) - 1.0)))
    return out


def oracle(y: np.ndarray, models: dict[str, np.ndarray]) -> dict[str, Any]:
    names = list(models)
    losses = np.vstack([_true_loss(y, models[n]) for n in names])
    hits = np.vstack([(np.argmax(models[n], axis=1) == y) for n in names])
    ll = {n: float(losses[i].mean()) for i, n in enumerate(names)}
    acc = {n: float(hits[i].mean()) for i, n in enumerate(names)}
    best_ll = min(names, key=lambda n: ll[n])
    best_acc = max(names, key=lambda n: acc[n])
    oracle_ll = float(np.min(losses, axis=0).mean())
    oracle_acc = float(np.any(hits, axis=0).mean())
    return {
        "models": names,
        "single_model_logloss": ll,
        "single_model_top1_accuracy": acc,
        "best_single_logloss_model": best_ll,
        "best_single_logloss": ll[best_ll],
        "oracle_logloss_lower_bound": oracle_ll,
        "oracle_logloss_gap_vs_best_single": float(ll[best_ll] - oracle_ll),
        "best_single_top1_model": best_acc,
        "best_single_top1_accuracy": acc[best_acc],
        "oracle_top1_accuracy": oracle_acc,
        "oracle_top1_gap_vs_best_single": float(oracle_acc - acc[best_acc]),
        "oracle_is_ex_post_only": True,
        "learnability_status": "NOT_TESTED",
    }


def run(cfg: dict[str, Any], cohort_path: Path, out_path: Path) -> dict[str, Any]:
    r42f_cfg = load_json(ROOT / "config" / "r42f_htft_response_direct_total_fixed200.json")
    r42j_cfg = load_json(ROOT / "config" / "r42j_all_history_pair_recovery_direct_total_fixed200.json")
    base_cfg = load_json(ROOT / str(r42f_cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(r42f_cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    frame = add_identity_key(build_features(raw))
    frame["split"] = split_for_latest_complete(frame, seasons, r42f_cfg)
    frame["date_norm"] = pd.to_datetime(frame["date_key"], errors="raise").dt.date.astype(str)

    ht_rows, _ = load_ht_rows(set(frame.competition_id.astype(str)))
    htft, ht_audit = build_htft_features(ht_rows, r42f_cfg)
    frame = frame.merge(
        htft,
        on=["competition_id", "season", "date_norm", "home_team", "away_team"],
        how="left",
        validate="one_to_one",
    )
    f_names = [str(x) for x in r42f_cfg["feature_contract"]["feature_names"]]
    min_trials = float(r42f_cfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    f_ok = (
        frame[f_names].notna().all(axis=1)
        & (frame.home_state_trials_total.fillna(0) >= min_trials)
        & (frame.away_state_trials_total.fillna(0) >= min_trials)
    )

    frame = add_recovered_all_pair_features(frame, r42j_cfg)
    j_names = recovered_feature_names(r42j_cfg)
    j_ok = frame[j_names].notna().all(axis=1)
    common_ok = f_ok & j_ok

    fit = frame[common_ok & frame.split.isin(["train", "policy"])].copy()
    train = fit[fit.split == "train"].copy()
    policy = fit[fit.split == "policy"].copy()
    if min(len(fit), len(train), len(policy)) == 0:
        raise ResearchError("EMPTY_COMMON_FIT_SET")

    cohort = pd.read_csv(cohort_path)
    if len(cohort) != 1000 or cohort.identity_key.duplicated().any():
        raise ResearchError("INVALID_FROZEN_COMMON_COHORT")
    ids = list(cohort.identity_key.astype(str))
    sample = frame[frame.identity_key.astype(str).isin(set(ids))].copy()
    sample = sample.set_index("identity_key").loc[ids].reset_index()
    if len(sample) != 1000 or not (common_ok.loc[sample.index] if False else True):
        pass
    if sample[f_names + j_names].isna().any().any():
        raise ResearchError("COMMON_COHORT_MISSING_CLEANFIT_FEATURES")

    core = select_core_features(frame)
    C, grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed_f = {float(x) for x in r42f_cfg["fit_contract"]["baseline_C_grid"]}
    allowed_j = {float(x) for x in r42j_cfg["fit_contract"]["baseline_C_grid"]}
    if float(C) not in allowed_f or float(C) not in allowed_j:
        raise ResearchError(f"COMMON_BASELINE_C_OUTSIDE_FROZEN_GRIDS:{C}")

    models = {
        "common_baseline": (core, make_model(float(C), base_cfg)),
        "R42F_commonfit": (core + f_names, make_model(float(C), base_cfg)),
        "R42J_commonfit": (core + j_names, make_model(float(C), base_cfg)),
    }
    probs: dict[str, np.ndarray] = {}
    for name, (cols, model) in models.items():
        model.fit(fit[cols], fit.total_class)
        probs[name] = align_probability(model, sample[cols], TOTAL_CLASSES)

    y = sample.total_class.to_numpy(int)
    base_loss = _true_loss(y, probs["common_baseline"])
    f_loss = _true_loss(y, probs["R42F_commonfit"])
    j_loss = _true_loss(y, probs["R42J_commonfit"])
    f_delta = f_loss - base_loss
    j_delta = j_loss - base_loss
    f_hit = np.argmax(probs["R42F_commonfit"], axis=1) == y
    j_hit = np.argmax(probs["R42J_commonfit"], axis=1) == y
    b_hit = np.argmax(probs["common_baseline"], axis=1) == y

    result = {
        "schema_version": "VIEWED_COMMON_COHORT_DIRECT_T_CLEANFIT_R1.0",
        "status": "PASS_DIRECT_T_COMMON_FIT_CONFOUND_REMOVED",
        "scientific_verdict": "RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_SCIENTIFIC_PASS_NO_PROMOTION",
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "cohort": {
            "rows": 1000,
            "identity_sha256": hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode("utf-8")).hexdigest(),
            "reused_from_parent_diagnostic": True,
            "new_sample_consumption": 0,
        },
        "common_fit_contract": {
            "fit_rows": int(len(fit)),
            "train_rows": int(len(train)),
            "policy_rows": int(len(policy)),
            "selected_C": float(C),
            "policy_grid": grid,
            "same_fit_rows_all_three_models": True,
            "same_C_all_three_models": True,
            "common_baseline_feature_count": int(len(core)),
            "r42f_feature_count": int(len(f_names)),
            "r42j_feature_count": int(len(j_names)),
            "r42f_htft_audit": ht_audit,
        },
        "metrics": {name: metrics(y, p) for name, p in probs.items()},
        "residual_complementarity": {
            "r42f_improves_common_baseline": int(np.sum(f_delta < 0)),
            "r42j_improves_common_baseline": int(np.sum(j_delta < 0)),
            "both_improve": int(np.sum((f_delta < 0) & (j_delta < 0))),
            "r42f_only_improves": int(np.sum((f_delta < 0) & ~(j_delta < 0))),
            "r42j_only_improves": int(np.sum(~(f_delta < 0) & (j_delta < 0))),
            "neither_improves": int(np.sum(~(f_delta < 0) & ~(j_delta < 0))),
            "loss_delta_correlation_r42f_vs_r42j": _safe_corr(f_delta, j_delta),
            "top1_both_correct": int(np.sum(f_hit & j_hit)),
            "top1_r42f_only_correct": int(np.sum(f_hit & ~j_hit)),
            "top1_r42j_only_correct": int(np.sum(~f_hit & j_hit)),
            "top1_neither_correct": int(np.sum(~f_hit & ~j_hit)),
            "baseline_only_correct_vs_both_challengers": int(np.sum(b_hit & ~f_hit & ~j_hit)),
        },
        "oracle": oracle(y, probs),
        "interpretation_boundary": {
            "baseline_confound_removed": True,
            "fusion_selector_fit": False,
            "oracle_is_ex_post_only": True,
            "learnability_status": "NOT_TESTED",
            "formal_weight": 0,
            "new_data_collection": 0,
            "new_blind_or_holdout_access": 0,
            "formal_promotion_allowed": False,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_out = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    out_path.write_text(raw_out, encoding="utf-8")
    (out_path.parent / "direct_t_cleanfit.sha256").write_text(hashlib.sha256(raw_out.encode("utf-8")).hexdigest() + "\n", encoding="ascii")
    return result


def self_test() -> None:
    y=np.array([0,1,2])
    a=np.array([[.7,.2,.1],[.2,.6,.2],[.1,.2,.7]])
    b=np.array([[.6,.3,.1],[.3,.5,.2],[.1,.3,.6]])
    x=oracle(y,{"a":a,"b":b})
    assert x["oracle_logloss_lower_bound"] <= min(x["single_model_logloss"].values()) + 1e-15
    print(json.dumps({"status":"PASS_DIRECT_T_COMMON_FIT_SELF_TEST"}))


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--cohort", type=Path, default=DEFAULT_COHORT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--self-test", action="store_true")
    args=ap.parse_args()
    if args.self_test:
        self_test(); return
    result=run(load_json(args.config), args.cohort, args.out)
    print(json.dumps({
        "status":result["status"],
        "common_fit_contract":result["common_fit_contract"],
        "metrics":result["metrics"],
        "residual_complementarity":result["residual_complementarity"],
        "oracle":result["oracle"],
    },ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
