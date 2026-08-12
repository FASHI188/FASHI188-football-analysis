#!/usr/bin/env python3
"""R1 test of whether the VIEWED Direct-T oracle gap is learnable pre-result.

Expert models are frozen variants of the common Direct-T core. For selector training,
experts fit only historical `train` rows and generate policy-season probabilities. Three
fixed Ridge models learn to forecast each expert's per-match logloss from probability-
disagreement geometry only. Target experts are then refit on train+policy and the selector
chooses an expert for each target row before target labels are used for evaluation.

The primary evaluation set is the common target pool minus the 1,000 identities already
used in PR #178's oracle diagnostic. This is retrospective VIEWED research, not blind or
confirmatory evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, split_for_latest_complete
from evaluate_r42f_htft_response_direct_total_fixed200 import build_htft_features, load_ht_rows
from evaluate_r42j_all_history_pair_recovery_direct_total_fixed200 import add_recovered_all_pair_features, recovered_feature_names
from evaluate_viewed_common_cohort_oracle_r1 import _true_loss, replay_r40f
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "direct_t_oracle_learnability_r1.json"
DEFAULT_PARENT_CONFIG = ROOT / "config" / "viewed_common_cohort_oracle_r1.json"
DEFAULT_PARENT_STATUS = ROOT / "manifests" / "viewed_common_cohort_oracle_r1" / "status.json"
DEFAULT_PARENT_COHORT = ROOT / "manifests" / "viewed_common_cohort_oracle_r1" / "common_cohort_1000_per_match.csv"
DEFAULT_OUT_DIR = ROOT / "manifests" / "direct_t_oracle_learnability_r1"
TOTAL_CLASSES = list(range(8))
EXPERTS = ("common_baseline", "R42F", "R42J")


def build_frame() -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any], list[str], list[str], pd.Series, dict[str, list[str]]]:
    fcfg = load_json(ROOT / "config" / "r42f_htft_response_direct_total_fixed200.json")
    jcfg = load_json(ROOT / "config" / "r42j_all_history_pair_recovery_direct_total_fixed200.json")
    base_cfg = load_json(ROOT / str(fcfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(fcfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    frame = add_identity_key(build_features(raw))
    frame["split"] = split_for_latest_complete(frame, seasons, fcfg)
    frame["date_norm"] = pd.to_datetime(frame["date_key"], errors="raise").dt.date.astype(str)
    ht_rows, _ = load_ht_rows(set(frame.competition_id.astype(str)))
    htft, ht_audit = build_htft_features(ht_rows, fcfg)
    frame = frame.merge(
        htft,
        on=["competition_id", "season", "date_norm", "home_team", "away_team"],
        how="left",
        validate="one_to_one",
    )
    fnames = [str(x) for x in fcfg["feature_contract"]["feature_names"]]
    min_trials = float(fcfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    f_ok = (
        frame[fnames].notna().all(axis=1)
        & (frame.home_state_trials_total.fillna(0) >= min_trials)
        & (frame.away_state_trials_total.fillna(0) >= min_trials)
    )
    frame = add_recovered_all_pair_features(frame, jcfg)
    jnames = recovered_feature_names(jcfg)
    j_ok = frame[jnames].notna().all(axis=1)
    common_ok = f_ok & j_ok
    audit = {"data_identity": identity, "excluded_incomplete_latest_seasons": excluded_latest, "r42f_htft": ht_audit}
    return frame, base_cfg, fcfg, jcfg, fnames, jnames, common_ok, seasons


def fit_experts(fit: pd.DataFrame, target: pd.DataFrame, core: list[str], fnames: list[str], jnames: list[str], C: float, base_cfg: dict[str, Any]) -> dict[str, np.ndarray]:
    specs = {
        "common_baseline": core,
        "R42F": core + fnames,
        "R42J": core + jnames,
    }
    out: dict[str, np.ndarray] = {}
    for name, cols in specs.items():
        m = make_model(C, base_cfg)
        m.fit(fit[cols], fit.total_class)
        out[name] = align_probability(m, target[cols], TOTAL_CLASSES)
    return out


def meta_features(probs: dict[str, np.ndarray]) -> np.ndarray:
    mats = [np.asarray(probs[n], dtype=float) for n in EXPERTS]
    n = len(mats[0])
    if any(m.shape != (n, 8) for m in mats):
        raise ResearchError("META_EXPERT_PROBABILITY_SHAPE_MISMATCH")
    blocks: list[np.ndarray] = []
    blocks.extend(mats)  # 24
    for p in mats:
        q = np.clip(p, 1e-15, 1.0)
        blocks.append((-np.sum(q * np.log(q), axis=1))[:, None])
    for p in mats:
        blocks.append(np.max(p, axis=1)[:, None])
    for p in mats:
        s = np.sort(p, axis=1)
        blocks.append((s[:, -1] - s[:, -2])[:, None])
    pairs = ((0, 1), (0, 2), (1, 2))
    for a, b in pairs:
        blocks.append(np.sum(np.abs(mats[a] - mats[b]), axis=1)[:, None])
    for a, b in pairs:
        blocks.append((np.argmax(mats[a], axis=1) == np.argmax(mats[b], axis=1)).astype(float)[:, None])
    X = np.concatenate(blocks, axis=1)
    if X.shape[1] != 39 or not np.isfinite(X).all():
        raise ResearchError(f"INVALID_META_FEATURE_MATRIX:{X.shape}")
    return X


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    comp = metric_components(y.astype(int), p, TOTAL_CLASSES)
    out = metric_summary(comp)
    out["top1_accuracy"] = float((np.argmax(p, axis=1) == y).mean())
    return {str(k): float(v) for k, v in out.items()}


def bootstrap_delta(y: np.ndarray, p_ref: np.ndarray, p_ch: np.ndarray, resamples: int, seed: int) -> dict[str, Any]:
    ref = metric_components(y.astype(int), p_ref, TOTAL_CLASSES)
    ch = metric_components(y.astype(int), p_ch, TOTAL_CLASSES)
    cols = ["logloss", "brier", "rps"]
    deltas = {c: ch[c].to_numpy(float) - ref[c].to_numpy(float) for c in cols}
    rng = np.random.default_rng(seed)
    n = len(y)
    draws = {c: np.empty(resamples, dtype=float) for c in cols}
    for i in range(resamples):
        idx = rng.integers(0, n, size=n)
        for c in cols:
            draws[c][i] = float(np.mean(deltas[c][idx]))
    return {
        c: {
            "point": float(np.mean(deltas[c])),
            "p05": float(np.quantile(draws[c], 0.05)),
            "median": float(np.quantile(draws[c], 0.50)),
            "p95": float(np.quantile(draws[c], 0.95)),
            "p_improve": float(np.mean(draws[c] < 0.0)),
        }
        for c in cols
    }


def oracle(y: np.ndarray, probs: dict[str, np.ndarray]) -> dict[str, Any]:
    losses = np.vstack([_true_loss(y, probs[n]) for n in EXPERTS])
    hits = np.vstack([np.argmax(probs[n], axis=1) == y for n in EXPERTS])
    ll = {n: float(losses[i].mean()) for i, n in enumerate(EXPERTS)}
    acc = {n: float(hits[i].mean()) for i, n in enumerate(EXPERTS)}
    best_ll = min(EXPERTS, key=lambda n: ll[n]); best_acc = max(EXPERTS, key=lambda n: acc[n])
    return {
        "single_logloss": ll,
        "single_top1": acc,
        "best_single_logloss": float(ll[best_ll]),
        "best_single_logloss_model": best_ll,
        "oracle_logloss": float(np.min(losses, axis=0).mean()),
        "oracle_logloss_gap": float(ll[best_ll] - np.min(losses, axis=0).mean()),
        "best_single_top1": float(acc[best_acc]),
        "best_single_top1_model": best_acc,
        "oracle_top1": float(np.any(hits, axis=0).mean()),
        "oracle_top1_gap": float(np.any(hits, axis=0).mean() - acc[best_acc]),
    }


def run(cfg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    parent_cfg = load_json(DEFAULT_PARENT_CONFIG)
    parent = load_json(DEFAULT_PARENT_STATUS)
    cohort = pd.read_csv(DEFAULT_PARENT_COHORT)
    pcfg = cfg["parent_diagnostic"]
    if parent["cohort"]["common_target_pool_rows"] != int(pcfg["common_target_pool_rows"]):
        raise ResearchError("PARENT_COMMON_POOL_COUNT_MISMATCH")
    if parent["cohort"]["identity_sha256"] != str(pcfg["diagnostic_1000_identity_sha256"]):
        raise ResearchError("PARENT_DIAGNOSTIC_IDENTITY_MISMATCH")
    if len(cohort) != int(pcfg["diagnostic_rows_to_exclude"]):
        raise ResearchError("PARENT_DIAGNOSTIC_ROW_COUNT_MISMATCH")
    excluded_ids = set(cohort.identity_key.astype(str))

    frame, base_cfg, fcfg, jcfg, fnames, jnames, common_ok, seasons = build_frame()
    core = select_core_features(frame)
    if len(core) != int(cfg["expert_contract"]["core_feature_count"]):
        raise ResearchError("CORE_FEATURE_COUNT_MISMATCH")
    C = float(cfg["expert_contract"]["fixed_C"])

    # Reconstruct the exact parent common-target definition, including R40F availability.
    r40f, r40_audit = replay_r40f(parent_cfg)
    r40_keys = set(r40f.identity_key.astype(str))
    target_all = frame[(frame.split == "target_pool") & common_ok].copy()
    target_all = target_all[target_all.identity_key.astype(str).isin(r40_keys)].copy()
    if len(target_all) != int(pcfg["common_target_pool_rows"]):
        raise ResearchError(f"RECONSTRUCTED_COMMON_POOL_MISMATCH:{len(target_all)}")
    evaluation = target_all[~target_all.identity_key.astype(str).isin(excluded_ids)].sort_values("identity_key").copy()
    if len(evaluation) != int(pcfg["expected_primary_evaluation_rows"]):
        raise ResearchError(f"PRIMARY_EVALUATION_ROW_MISMATCH:{len(evaluation)}")

    train = frame[common_ok & (frame.split == "train")].copy()
    policy = frame[common_ok & (frame.split == "policy")].copy()
    fit_target = frame[common_ok & frame.split.isin(["train", "policy"])].copy()
    if min(len(train), len(policy), len(fit_target)) == 0:
        raise ResearchError("EMPTY_CHRONOLOGICAL_SELECTOR_SPLIT")

    # Policy predictions from experts trained on train only.
    p_policy = fit_experts(train, policy, core, fnames, jnames, C, base_cfg)
    y_policy = policy.total_class.to_numpy(int)
    policy_losses = {n: _true_loss(y_policy, p_policy[n]) for n in EXPERTS}
    static_policy_ll = {n: float(policy_losses[n].mean()) for n in EXPERTS}
    best_static_name = min(EXPERTS, key=lambda n: static_policy_ll[n])

    X_policy = meta_features(p_policy)
    alpha = float(cfg["selector_contract"]["ridge_alpha"])
    forecasters: dict[str, Any] = {}
    for n in EXPERTS:
        m = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
        m.fit(X_policy, policy_losses[n])
        forecasters[n] = m

    # Target expert probabilities are frozen before target labels are used for scoring.
    p_eval = fit_experts(fit_target, evaluation, core, fnames, jnames, C, base_cfg)
    X_eval = meta_features(p_eval)
    predicted_losses = np.column_stack([forecasters[n].predict(X_eval) for n in EXPERTS])
    choices = np.argmin(predicted_losses, axis=1)
    p_selector = np.empty_like(p_eval[EXPERTS[0]])
    for i, n in enumerate(EXPERTS):
        mask = choices == i
        p_selector[mask] = p_eval[n][mask]
    p_static = p_eval[best_static_name]
    p_equal = sum(p_eval[n] for n in EXPERTS) / float(len(EXPERTS))

    # Only now use evaluation labels for metrics.
    y = evaluation.total_class.to_numpy(int)
    static_metrics = metrics(y, p_static)
    selector_metrics = metrics(y, p_selector)
    equal_metrics = metrics(y, p_equal)
    expert_metrics = {n: metrics(y, p_eval[n]) for n in EXPERTS}
    boot = bootstrap_delta(
        y,
        p_static,
        p_selector,
        int(cfg["primary_gate"]["paired_bootstrap_resamples"]),
        int(cfg["primary_gate"]["bootstrap_seed"]),
    )
    choice_counts = {n: int(np.sum(choices == i)) for i, n in enumerate(EXPERTS)}
    selected_experts = int(sum(v > 0 for v in choice_counts.values()))
    gate = {
        "selector_vs_best_static_logloss_delta_p95_lt_zero": bool(boot["logloss"]["p95"] < 0.0),
        "selector_brier_nonworse": bool(selector_metrics["brier"] <= static_metrics["brier"]),
        "selector_rps_nonworse": bool(selector_metrics["rps"] <= static_metrics["rps"]),
        "minimum_two_experts_selected": bool(selected_experts >= 2),
    }
    gate["all_required"] = bool(all(gate.values()))

    ex_oracle = oracle(y, p_eval)
    denom = static_metrics["logloss"] - ex_oracle["oracle_logloss"]
    captured = (static_metrics["logloss"] - selector_metrics["logloss"]) / denom if denom > 1e-15 else None
    eval_ids_sorted = sorted(evaluation.identity_key.astype(str))
    eval_sha = hashlib.sha256(("\n".join(eval_ids_sorted) + "\n").encode("utf-8")).hexdigest()

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_DIRECT_T_ORACLE_LEARNABILITY_EXECUTION_COMPLETE",
        "scientific_verdict": "LEARNABLE_VIEWED_ORACLE_GAP_DIAGNOSTIC" if gate["all_required"] else "FAIL_DIRECT_T_ORACLE_GAP_NOT_LEARNABLE_UNDER_FROZEN_SELECTOR",
        "parent_diagnostic": pcfg,
        "data_audit": {
            "common_pool_rows": int(len(target_all)),
            "excluded_parent_diagnostic_rows": int(len(excluded_ids)),
            "primary_evaluation_rows": int(len(evaluation)),
            "primary_evaluation_identity_sha256": eval_sha,
            "overlap_with_parent_diagnostic": int(len(set(evaluation.identity_key.astype(str)) & excluded_ids)),
            "train_rows": int(len(train)),
            "policy_rows": int(len(policy)),
            "target_expert_fit_rows": int(len(fit_target)),
            "r40f_frozen_summary_reproduced": bool(r40_audit["frozen_summary_reproduced"]),
            "target_labels_used_for_selector_fit": False,
            "parent_diagnostic_labels_used_for_selector_fit": False,
            "evaluation_identity_selected_with_labels": False,
            "blind_claim": False,
        },
        "selector": {
            "method": cfg["selector_contract"]["method"],
            "ridge_alpha": alpha,
            "meta_feature_count": int(X_policy.shape[1]),
            "fixed_C": C,
            "policy_static_logloss": static_policy_ll,
            "best_static_expert_selected_on_policy": best_static_name,
            "choice_counts_on_primary_evaluation": choice_counts,
            "selected_expert_count": selected_experts,
        },
        "primary_evaluation": {
            "expert_metrics": expert_metrics,
            "best_static_metrics": static_metrics,
            "equal_weight_average_metrics": equal_metrics,
            "selector_metrics": selector_metrics,
            "selector_minus_static": {k: float(selector_metrics[k] - static_metrics[k]) for k in static_metrics},
            "paired_bootstrap_selector_minus_static": boot,
            "ex_post_oracle": ex_oracle,
            "selector_fraction_of_static_to_oracle_logloss_gap_captured": None if captured is None else float(captured),
            "gate": gate,
        },
        "interpretation_boundary": {
            "post_selection_motivated_by_parent_oracle": True,
            "evaluation_is_disjoint_from_parent_diagnostic1000": True,
            "evaluation_is_retrospective_viewed_not_blind": True,
            "scientific_pass_claim_allowed": False,
            "confirmation_pass_claim_allowed": False,
            "formal_promotion_allowed": False,
            "formal_weight": 0,
        },
        "governance": cfg["governance"],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (out_dir / "status.json").write_text(text, encoding="utf-8")
    (out_dir / "status.sha256").write_text(hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="ascii")
    return result


def self_test() -> None:
    p={n:np.tile(np.array([[.1,.2,.3,.15,.1,.07,.05,.03]]),(4,1)) for n in EXPERTS}
    p["R42F"]=np.roll(p["R42F"],1,axis=1); p["R42J"]=np.roll(p["R42J"],2,axis=1)
    X=meta_features(p)
    assert X.shape==(4,39) and np.isfinite(X).all()
    print(json.dumps({"status":"PASS_DIRECT_T_ORACLE_LEARNABILITY_SELF_TEST","meta_features":39}))


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    ap.add_argument("--out-dir",type=Path,default=DEFAULT_OUT_DIR)
    ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    if args.self_test:
        self_test(); return
    x=run(load_json(args.config),args.out_dir)
    print(json.dumps({
        "status":x["status"],
        "scientific_verdict":x["scientific_verdict"],
        "data_audit":x["data_audit"],
        "selector":x["selector"],
        "primary_evaluation":x["primary_evaluation"],
    },ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
