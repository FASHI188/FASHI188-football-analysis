#!/usr/bin/env python3
"""R42A: dynamic conditional diagonal tilt on one fresh disjoint fixed200.

Research-only retrospective experiment. The model does not predict Draw as a separate
HDA class. It starts from direct P(T) and conditional P(D|T), then learns a strictly
chronological offset-logistic correction to the D=0 mass only for T in {2,4,6}.
Non-diagonal relative probabilities are preserved. No manual 1-1 or Draw bonus exists.
The fixed200 is identity-selected after reproducing and excluding the seven previously
consumed fixed200 samples (1,400 identities total); its labels are never used for model,
feature, regularization, threshold, or sample selection.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from evaluate_r41a_fixed200_joint_error_decomposition import (
    add_identity_key,
    load_json,
    select_fixed_identities,
)
from evaluate_r41_priority_fixed200_battery import (
    draw_metrics,
    materialize_market,
    prepare_features,
    select_method_sample,
)
from evaluate_r41d_replication_fixed200 import reproduce_prior_samples
from v510_historical_structure_features_r1 import (
    TOTAL_CLASSES,
    ResearchError,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import (
    align_probability,
    empirical_probability,
    make_model,
    metric_components,
    metric_summary,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42a_dynamic_diagonal_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42a_dynamic_diagonal_fixed200_status.json"
HDA_CLASSES = [0, 1, 2]  # H, D, A
EPS = 1e-12


def support_for_total(total: int, base_cfg: dict[str, Any]) -> list[int]:
    if total < 7:
        return list(range(-total, total + 1, 2))
    contract = base_cfg["model_contract"]
    return list(range(int(contract["conditional_tail_support_min"]), int(contract["conditional_tail_support_max"]) + 1))


def add_season_position(frame: pd.DataFrame, seasons: dict[str, list[str]]) -> pd.DataFrame:
    out = frame.copy()
    values: list[int] = []
    for row in out[["competition_id", "season"]].itertuples(index=False):
        sequence = seasons[str(row.competition_id)]
        season = str(row.season)
        if season not in sequence:
            values.append(-1)
        else:
            values.append(int(sequence.index(season)))
    out["season_position"] = values
    return out


def add_diag_features(frame: pd.DataFrame, total_override: int | None = None) -> pd.DataFrame:
    out = frame.copy()
    out["diag_abs_pair_gd_diff"] = out["pair_gd_diff"].abs()
    out["diag_abs_pair_recent_gf_diff"] = out["pair_recent_gf_diff"].abs()
    total = out["total_class"].astype(int) if total_override is None else pd.Series(total_override, index=out.index)
    out["diag_total_4"] = (total == 4).astype(float)
    out["diag_total_6"] = (total == 6).astype(float)
    return out


def fixed_multiclass_model(
    train: pd.DataFrame,
    target: pd.DataFrame,
    features: list[str],
    target_name: str,
    classes: list[int],
    C: float,
    base_cfg: dict[str, Any],
) -> np.ndarray:
    model = make_model(float(C), base_cfg)
    model.fit(train[features], train[target_name])
    return align_probability(model, target[features], classes)


def baseline_conditional_probability(
    train: pd.DataFrame,
    target: pd.DataFrame,
    total: int,
    core_features: list[str],
    C: float,
    base_cfg: dict[str, Any],
) -> np.ndarray:
    classes = support_for_total(total, base_cfg)
    train_t = train[train.total_class.astype(int) == int(total)]
    if len(classes) == 1:
        return np.ones((len(target), 1), dtype=float)
    if total == 7:
        return empirical_probability(
            train_t,
            target,
            "goal_difference",
            classes,
            float(base_cfg["model_contract"]["tail_empirical_alpha"]),
        )
    if train_t.empty:
        raise ResearchError(f"empty baseline conditional training set for T={total}")
    return fixed_multiclass_model(train_t, target, core_features, "goal_difference", classes, C, base_cfg)


def logit(values: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 1e-8, 1 - 1e-8)
    return np.log(p / (1 - p))


def fit_offset_tilt(
    training: pd.DataFrame,
    feature_names: list[str],
    l2: float,
    max_iter: int,
    tol: float,
) -> tuple[SimpleImputer, StandardScaler, np.ndarray, dict[str, Any]]:
    if training.empty:
        raise ResearchError("empty dynamic diagonal OOF training frame")
    y = training["is_diag"].to_numpy(float)
    if len(np.unique(y)) < 2:
        raise ResearchError("dynamic diagonal OOF labels are single-class")
    offset = training["base_diag_logit"].to_numpy(float)
    imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    scaler = StandardScaler()
    X_imp = imputer.fit_transform(training[feature_names])
    X = scaler.fit_transform(X_imp)
    Xa = np.column_stack([np.ones(len(X)), X])
    penalty = np.ones(Xa.shape[1], dtype=float)
    penalty[0] = 0.0

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset + Xa @ beta
        p = expit(eta)
        loss = float(np.sum(np.logaddexp(0.0, eta) - y * eta) + 0.5 * l2 * np.sum((beta * penalty) ** 2))
        grad = Xa.T @ (p - y) + l2 * beta * penalty
        return loss, grad

    beta0 = np.zeros(Xa.shape[1], dtype=float)
    opt = minimize(
        fun=lambda b: objective(b)[0],
        x0=beta0,
        jac=lambda b: objective(b)[1],
        method="L-BFGS-B",
        options={"maxiter": int(max_iter), "ftol": float(tol), "gtol": float(tol)},
    )
    beta = np.asarray(opt.x, dtype=float)
    base_p = expit(offset)
    tilt_p = expit(offset + Xa @ beta)
    base_ll = float(np.mean(-(y * np.log(np.clip(base_p, EPS, 1.0)) + (1 - y) * np.log(np.clip(1 - base_p, EPS, 1.0)))))
    tilt_ll = float(np.mean(-(y * np.log(np.clip(tilt_p, EPS, 1.0)) + (1 - y) * np.log(np.clip(1 - tilt_p, EPS, 1.0)))))
    receipt = {
        "rows": int(len(training)),
        "draw_rows": int(y.sum()),
        "non_draw_rows": int(len(y) - y.sum()),
        "converged": bool(opt.success),
        "status": int(opt.status),
        "message": str(opt.message),
        "iterations": int(getattr(opt, "nit", -1)),
        "objective": float(opt.fun),
        "max_abs_gradient": float(np.max(np.abs(objective(beta)[1]))),
        "coefficient_count_including_intercept": int(len(beta)),
        "coefficient_l2_norm_excluding_intercept": float(np.linalg.norm(beta[1:])),
        "intercept_tilt": float(beta[0]),
        "oof_baseline_binary_logloss": base_ll,
        "oof_tilted_binary_logloss": tilt_ll,
        "oof_delta_tilt_minus_baseline": tilt_ll - base_ll,
    }
    if not opt.success:
        raise ResearchError(f"dynamic diagonal tilt optimizer did not converge: {receipt}")
    return imputer, scaler, beta, receipt


def predict_tilted_diag(
    frame: pd.DataFrame,
    total: int,
    base_diag: np.ndarray,
    feature_names: list[str],
    imputer: SimpleImputer,
    scaler: StandardScaler,
    beta: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    scenario = add_diag_features(frame, total_override=total)
    X = scaler.transform(imputer.transform(scenario[feature_names]))
    Xa = np.column_stack([np.ones(len(X)), X])
    theta = Xa @ beta
    q = expit(logit(base_diag) + theta)
    return np.asarray(q, dtype=float), np.asarray(theta, dtype=float)


def tilt_conditional(base: np.ndarray, classes: list[int], q_diag: np.ndarray) -> np.ndarray:
    if 0 not in classes:
        return base.copy()
    pos = classes.index(0)
    output = np.asarray(base, dtype=float).copy()
    old_diag = np.clip(output[:, pos], EPS, 1 - EPS)
    remaining = np.clip(1.0 - old_diag, EPS, None)
    scale = (1.0 - q_diag) / remaining
    output *= scale[:, None]
    output[:, pos] = q_diag
    row_sums = output.sum(axis=1, keepdims=True)
    output /= row_sums
    return output


def aggregate_hda(p_total: np.ndarray, conditional: dict[int, np.ndarray], base_cfg: dict[str, Any]) -> np.ndarray:
    out = np.zeros((len(p_total), 3), dtype=float)
    for total in TOTAL_CLASSES:
        classes = support_for_total(total, base_cfg)
        pD = conditional[total]
        home = pD[:, [i for i, d in enumerate(classes) if d > 0]].sum(axis=1) if any(d > 0 for d in classes) else np.zeros(len(pD))
        draw = pD[:, classes.index(0)] if 0 in classes else np.zeros(len(pD))
        away = pD[:, [i for i, d in enumerate(classes) if d < 0]].sum(axis=1) if any(d < 0 for d in classes) else np.zeros(len(pD))
        mass = p_total[:, total]
        out[:, 0] += mass * home
        out[:, 1] += mass * draw
        out[:, 2] += mass * away
    out /= out.sum(axis=1, keepdims=True)
    return out


def conditional_row_components(
    sample: pd.DataFrame,
    conditional: dict[int, np.ndarray],
    base_cfg: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for i, row in enumerate(sample.itertuples(index=False)):
        total = int(row.total_class)
        classes = support_for_total(total, base_cfg)
        p = np.clip(conditional[total][i].astype(float), 1e-15, 1.0)
        p /= p.sum()
        y = int(row.goal_difference)
        if y not in classes:
            raise ResearchError(f"conditional support misses sample D={y} for T={total}")
        yi = classes.index(y)
        one = np.zeros(len(classes), dtype=float)
        one[yi] = 1.0
        ll = -math.log(float(p[yi]))
        brier = float(np.sum((p - one) ** 2))
        if len(classes) > 1:
            rps = float(np.sum((np.cumsum(p)[:-1] - np.cumsum(one)[:-1]) ** 2) / (len(classes) - 1))
        else:
            rps = 0.0
        rows.append({"logloss": ll, "brier": brier, "rps": rps})
    return pd.DataFrame(rows, index=sample.index)


def paired_bootstrap(delta: np.ndarray, cfg: dict[str, Any], seed: int) -> dict[str, float]:
    values = np.asarray(delta, dtype=float)
    n = len(values)
    if n == 0:
        raise ResearchError("bootstrap requires non-empty delta")
    rng = np.random.default_rng(seed)
    count = int(cfg["decision_contract"]["bootstrap_samples"])
    picks = rng.integers(0, n, size=(count, n))
    means = values[picks].mean(axis=1)
    q0, q1 = [float(x) for x in cfg["decision_contract"]["bootstrap_interval"]]
    return {
        "mean": float(means.mean()),
        "p05": float(np.quantile(means, q0)),
        "p95": float(np.quantile(means, q1)),
        "probability_challenger_better": float((means < 0).mean()),
    }


def reproduce_all_prior_fixed200(
    raw: pd.DataFrame,
    seasons: dict[str, list[str]],
    cfg: dict[str, Any],
) -> tuple[set[str], dict[str, str]]:
    parent_cfg = load_json(ROOT / "config" / "r41d_replication_fixed200.json")
    market = materialize_market(raw, parent_cfg["market_contract"])
    market_frame = prepare_features(raw, market, seasons, parent_cfg)
    prior_sets, hashes = reproduce_prior_samples(market_frame, parent_cfg)
    excluded = set().union(*prior_sets.values())
    eligible_d = (market_frame.book_count.fillna(0).astype(int) >= 1) & market_frame.has_ah_ou.fillna(False)
    rep_sample, rep_sha = select_method_sample(
        market_frame,
        eligible_d,
        excluded,
        200,
        41161,
    )
    expected_by_id = {str(x["id"]): str(x["identity_sha256"]) for x in cfg["sample_contract"]["prior_samples"]}
    if rep_sha != expected_by_id["R41D_REPLICATION"]:
        raise ResearchError(f"R41D replication identity mismatch: {rep_sha}")
    rep_ids = set(rep_sample.identity_key.astype(str))
    for key, ids in prior_sets.items():
        if rep_ids & ids:
            raise ResearchError(f"R41D replication overlaps prior {key}")
    hashes["R41D_REPLICATION"] = rep_sha
    prior_sets["R41D_REPLICATION"] = rep_ids
    for key, actual in hashes.items():
        expected = expected_by_id[key]
        if actual != expected:
            raise ResearchError(f"prior identity mismatch {key}: {actual} != {expected}")
    all_ids = set().union(*prior_sets.values())
    if len(all_ids) != 1400:
        raise ResearchError(f"expected 1400 consumed identities, got {len(all_ids)}")
    return all_ids, hashes


def build_oof_diagonal_training(
    features: pd.DataFrame,
    core_features: list[str],
    cfg: dict[str, Any],
    base_cfg: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    totals = [int(x) for x in cfg["method_contract"]["conditioned_even_total_classes"]]
    C = float(cfg["fit_contract"]["fixed_base_C"])
    parts: list[pd.DataFrame] = []
    receipts: list[dict[str, Any]] = []
    for block in cfg["fit_contract"]["oof_blocks"]:
        train_pos = {int(x) for x in block["train_positions"]}
        pred_pos = int(block["predict_position"])
        historical = features[features.season_position.isin(train_pos)]
        target_block = features[features.season_position == pred_pos]
        for total in totals:
            train_t = historical[historical.total_class.astype(int) == total]
            target_t = target_block[target_block.total_class.astype(int) == total].copy()
            if target_t.empty:
                continue
            classes = support_for_total(total, base_cfg)
            p = fixed_multiclass_model(train_t, target_t, core_features, "goal_difference", classes, C, base_cfg)
            diag = p[:, classes.index(0)]
            target_t = add_diag_features(target_t)
            target_t["base_diag_probability"] = diag
            target_t["base_diag_logit"] = logit(diag)
            target_t["is_diag"] = (target_t.goal_difference.astype(int) == 0).astype(int)
            parts.append(target_t)
            receipts.append({
                "train_positions": sorted(train_pos),
                "predict_position": pred_pos,
                "total": total,
                "train_rows": int(len(train_t)),
                "oof_rows": int(len(target_t)),
                "oof_draws": int(target_t.is_diag.sum()),
                "probability_sum_max_residual": float(np.max(np.abs(p.sum(axis=1) - 1.0))),
            })
    if not parts:
        raise ResearchError("no OOF diagonal training rows")
    return pd.concat(parts, ignore_index=True), receipts


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    data_identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    prior_ids, prior_hashes = reproduce_all_prior_fixed200(raw, seasons, cfg)

    features = add_identity_key(build_features(raw))
    features = add_season_position(features, seasons)
    features = add_diag_features(features)
    core_features = select_core_features(features)
    dynamic_features = [str(x) for x in cfg["method_contract"]["dynamic_state_features"]]
    missing = sorted(set(dynamic_features) - set(features.columns))
    if missing:
        raise ResearchError(f"dynamic state features missing: {missing}")

    target_pool = features[(features.season_position == 4) & (~features.identity_key.isin(prior_ids))].copy()
    selected_ids, sample_sha = select_fixed_identities(
        target_pool,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    sample = features[features.identity_key.isin(set(selected_ids))].copy().sort_values("identity_key").reset_index(drop=True)
    if len(sample) != int(cfg["sample_contract"]["sample_size"]):
        raise ResearchError(f"R42A fixed200 reproduction failed: {len(sample)}")
    if not (sample.season_position == 4).all():
        raise ResearchError("R42A sample contains non-target-season row")
    overlap = int(len(set(sample.identity_key.astype(str)) & prior_ids))
    if overlap:
        raise ResearchError(f"R42A sample overlaps prior consumed identities: {overlap}")

    oof, oof_receipts = build_oof_diagonal_training(features, core_features, cfg, base_cfg)
    imputer, scaler, beta, tilt_receipt = fit_offset_tilt(
        oof,
        dynamic_features,
        float(cfg["fit_contract"]["diagonal_tilt_l2"]),
        int(cfg["fit_contract"]["optimizer_max_iter"]),
        float(cfg["fit_contract"]["optimizer_tolerance"]),
    )

    history_pos = {int(x) for x in cfg["fit_contract"]["history_positions_for_final_fit"]}
    fit = features[features.season_position.isin(history_pos)].copy()
    C = float(cfg["fit_contract"]["fixed_base_C"])

    total_classes = [int(x) for x in base_cfg["model_contract"]["direct_total_classes"]]
    p_total = fixed_multiclass_model(fit, sample, core_features, "total_class", total_classes, C, base_cfg)
    if float(np.max(np.abs(p_total.sum(axis=1) - 1.0))) > 1e-10:
        raise ResearchError("direct total probability conservation failed")

    baseline_cond: dict[int, np.ndarray] = {}
    challenger_cond: dict[int, np.ndarray] = {}
    tilt_test: dict[str, Any] = {}
    conditioned = {int(x) for x in cfg["method_contract"]["conditioned_even_total_classes"]}
    for total in TOTAL_CLASSES:
        classes = support_for_total(total, base_cfg)
        base_p = baseline_conditional_probability(fit, sample, total, core_features, C, base_cfg)
        baseline_cond[total] = base_p
        if total in conditioned:
            pos = classes.index(0)
            q, theta = predict_tilted_diag(sample, total, base_p[:, pos], dynamic_features, imputer, scaler, beta)
            challenger_cond[total] = tilt_conditional(base_p, classes, q)
            tilt_test[str(total)] = {
                "mean_baseline_diag": float(base_p[:, pos].mean()),
                "mean_tilted_diag": float(q.mean()),
                "mean_delta_diag": float((q - base_p[:, pos]).mean()),
                "mean_abs_delta_diag": float(np.mean(np.abs(q - base_p[:, pos]))),
                "theta_mean": float(theta.mean()),
                "theta_std": float(theta.std()),
                "theta_min": float(theta.min()),
                "theta_max": float(theta.max()),
            }
        else:
            challenger_cond[total] = base_p.copy()
        residual = float(np.max(np.abs(challenger_cond[total].sum(axis=1) - 1.0)))
        if residual > 1e-10:
            raise ResearchError(f"conditional probability conservation failed T={total}: {residual}")

    base_cond_comp = conditional_row_components(sample, baseline_cond, base_cfg)
    chall_cond_comp = conditional_row_components(sample, challenger_cond, base_cfg)
    non_tail = sample.total_class.astype(int).to_numpy() < 7
    base_core = base_cond_comp.loc[non_tail]
    chall_core = chall_cond_comp.loc[non_tail]
    cond_delta = chall_core - base_core
    primary_boot = paired_bootstrap(
        cond_delta.logloss.to_numpy(float), cfg, int(cfg["sample_contract"]["seed"]) + 1
    )

    base_hda = aggregate_hda(p_total, baseline_cond, base_cfg)
    chall_hda = aggregate_hda(p_total, challenger_cond, base_cfg)
    y_hda = np.where(sample.goal_difference.to_numpy(int) > 0, 0, np.where(sample.goal_difference.to_numpy(int) == 0, 1, 2)).astype(int)
    base_hda_comp = metric_components(y_hda, base_hda, HDA_CLASSES)
    chall_hda_comp = metric_components(y_hda, chall_hda, HDA_CLASSES)
    hda_delta = chall_hda_comp - base_hda_comp
    hda_boot = paired_bootstrap(hda_delta.logloss.to_numpy(float), cfg, int(cfg["sample_contract"]["seed"]) + 2)

    base_draw = draw_metrics(y_hda, base_hda)
    chall_draw = draw_metrics(y_hda, chall_hda)

    true_t = sample.total_class.to_numpy(int)
    true_t_prob = p_total[np.arange(len(sample)), true_t]
    base_true_d = np.zeros(len(sample), dtype=float)
    chall_true_d = np.zeros(len(sample), dtype=float)
    for i, row in enumerate(sample.itertuples(index=False)):
        total = int(row.total_class)
        classes = support_for_total(total, base_cfg)
        pos = classes.index(int(row.goal_difference))
        base_true_d[i] = baseline_cond[total][i, pos]
        chall_true_d[i] = challenger_cond[total][i, pos]
    base_joint_nll = -np.log(np.clip(true_t_prob * base_true_d, 1e-15, 1.0))
    chall_joint_nll = -np.log(np.clip(true_t_prob * chall_true_d, 1e-15, 1.0))

    gate = {
        "primary_logloss_mean_better": bool(cond_delta.logloss.mean() < 0),
        "primary_logloss_p95_below_zero": bool(primary_boot["p95"] < 0),
        "conditional_brier_nonworse": bool(cond_delta.brier.mean() <= 0),
        "conditional_rps_nonworse": bool(cond_delta.rps.mean() <= 0),
        "downstream_HDA_logloss_nonworse": bool(hda_delta.logloss.mean() <= 0),
        "downstream_HDA_brier_nonworse": bool(hda_delta.brier.mean() <= 0),
        "downstream_HDA_rps_nonworse": bool(hda_delta.rps.mean() <= 0),
    }
    gate["all_required"] = bool(all(gate.values()))
    verdict = (
        "PASS_R42A_DYNAMIC_DIAGONAL_INCREMENT_FIXED200"
        if gate["all_required"]
        else "FAIL_R42A_DYNAMIC_DIAGONAL_NO_INCREMENT_FIXED200"
    )

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R42A_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": verdict,
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {
            "rows": int(len(prior_ids)),
            "identity_sha256_by_stage": prior_hashes,
            "all_expected_hashes_match": True,
        },
        "sample": {
            "rows": int(len(sample)),
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "target_pool_rows_after_prior_exclusion": int(len(target_pool)),
            "overlap_with_prior_1400": overlap,
            "competitions_represented": int(sample.competition_id.nunique()),
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "method": {
            "name": cfg["method_contract"]["name"],
            "core_feature_count": int(len(core_features)),
            "dynamic_state_features": dynamic_features,
            "fixed_base_C": C,
            "OOF_baseline_blocks": oof_receipts,
            "tilt_fit": tilt_receipt,
            "test_tilt_by_total": tilt_test,
            "manual_draw_bonus": False,
            "manual_score_bonus": False,
            "fixed_1_1_adjustment": False,
        },
        "conditional_D_non_tail": {
            "rows": int(non_tail.sum()),
            "baseline": metric_summary(base_core),
            "challenger": metric_summary(chall_core),
            "delta_challenger_minus_baseline": {k: float(cond_delta[k].mean()) for k in cond_delta.columns},
            "paired_bootstrap_logloss_delta_90": primary_boot,
        },
        "factorized_joint": {
            "all_200_baseline_nll": float(base_joint_nll.mean()),
            "all_200_challenger_nll": float(chall_joint_nll.mean()),
            "all_200_delta": float((chall_joint_nll - base_joint_nll).mean()),
            "non_tail_baseline_nll": float(base_joint_nll[non_tail].mean()),
            "non_tail_challenger_nll": float(chall_joint_nll[non_tail].mean()),
            "non_tail_delta": float((chall_joint_nll[non_tail] - base_joint_nll[non_tail]).mean()),
        },
        "downstream_HDA": {
            "baseline": metric_summary(base_hda_comp),
            "challenger": metric_summary(chall_hda_comp),
            "delta_challenger_minus_baseline": {k: float(hda_delta[k].mean()) for k in hda_delta.columns},
            "paired_bootstrap_logloss_delta_90": hda_boot,
            "baseline_draw": base_draw,
            "challenger_draw": chall_draw,
            "probability_sum_max_residual_baseline": float(np.max(np.abs(base_hda.sum(axis=1) - 1.0))),
            "probability_sum_max_residual_challenger": float(np.max(np.abs(chall_hda.sum(axis=1) - 1.0))),
        },
        "gate": gate,
        "interpretation_limits": [
            "R42A tests a dynamic diagonal correction inside conditional P(D|T), not a standalone Draw classifier.",
            "The diagonal correction is learned only from chronological out-of-fold rows at T=2/4/6 and never from the fixed200 labels.",
            "T=0 is already deterministic D=0; odd totals cannot draw; T=7+ remains untilted because the bucket mixes exact totals and parities.",
            "The direct P(T) model is identical for baseline and challenger, so joint-score delta is entirely attributable to conditional-D structure.",
            "This is retrospective viewed historical evidence, not formal current-match evidence and not an untouched protected blind.",
        ],
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    base = np.asarray([[0.2, 0.5, 0.3], [0.4, 0.2, 0.4]], dtype=float)
    classes = [-2, 0, 2]
    q = np.asarray([0.6, 0.1], dtype=float)
    out = tilt_conditional(base, classes, q)
    assert np.max(np.abs(out.sum(axis=1) - 1.0)) < 1e-12
    assert np.allclose(out[:, 1], q)
    assert np.allclose(out[:, 0] / out[:, 2], base[:, 0] / base[:, 2])
    cfg = {"decision_contract": {"bootstrap_samples": 100, "bootstrap_interval": [0.05, 0.95]}}
    b = paired_bootstrap(np.asarray([-1.0, -0.5, -0.2]), cfg, 1)
    assert b["p95"] < 0
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
        "conditional_D_non_tail": result["conditional_D_non_tail"],
        "downstream_HDA": result["downstream_HDA"],
        "gate": result["gate"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
