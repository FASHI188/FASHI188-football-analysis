#!/usr/bin/env python3
"""R42O: BCP dependence-only rolling-OOS challenge for football score structure.

Research-only challenge adapted from arXiv:2608.07168. This is NOT an exact
Bayesian/HMC reproduction of the paper. The experiment isolates the dependence
question: baseline and challenger share the same strict-prior home/away marginal
Poisson rates. Baseline fixes phi=0 (independence); challenger fits one BCP phi
that may be positive or negative. Direction is selected on the policy season only.
No fresh fixed200 is consumed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import gammaln
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import metric_components, metric_summary

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42o_bcp_dependence_rolling_oos.json"
DEFAULT_OUT = ROOT / "manifests" / "r42o_bcp_dependence_rolling_oos_status.json"
HDA_CLASSES = [0, 1, 2]  # home, draw, away
TOTAL_CLASSES = list(range(8))


def make_rate_model(cfg: dict[str, Any]) -> Pipeline:
    c = cfg["marginal_rate_contract"]
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
        ("model", PoissonRegressor(
            alpha=float(c["alpha"]),
            max_iter=int(c["max_iter"]),
            tol=float(c["tol"]),
            fit_intercept=bool(c["fit_intercept"]),
        )),
    ])


def fit_rate_pair(rows: pd.DataFrame, features: list[str], cfg: dict[str, Any]) -> tuple[Pipeline, Pipeline]:
    home = make_rate_model(cfg)
    away = make_rate_model(cfg)
    home.fit(rows[features], rows.home_goals_90.astype(float))
    away.fit(rows[features], rows.away_goals_90.astype(float))
    return home, away


def predict_rates(models: tuple[Pipeline, Pipeline], rows: pd.DataFrame, features: list[str], cfg: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    lam_h = np.asarray(models[0].predict(rows[features]), dtype=float)
    lam_a = np.asarray(models[1].predict(rows[features]), dtype=float)
    lo, hi = [float(x) for x in cfg["marginal_rate_contract"]["prediction_rate_clip"]]
    lam_h = np.clip(lam_h, lo, hi)
    lam_a = np.clip(lam_a, lo, hi)
    if not np.isfinite(lam_h).all() or not np.isfinite(lam_a).all():
        raise ResearchError("nonfinite marginal Poisson rate")
    return lam_h, lam_a


def directional_arrays(
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    lam_h: np.ndarray,
    lam_a: np.ndarray,
    direction: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if direction == "H_TO_A":
        return home_goals, away_goals, lam_h, lam_a
    if direction == "A_TO_H":
        return away_goals, home_goals, lam_a, lam_h
    raise ResearchError(f"unknown BCP direction: {direction}")


def bcp_logpmf_arrays(
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    lam_h: np.ndarray,
    lam_a: np.ndarray,
    phi: float,
    direction: str,
) -> np.ndarray:
    y1, y2, lam1, lam2 = directional_arrays(
        np.asarray(home_goals, dtype=float),
        np.asarray(away_goals, dtype=float),
        np.asarray(lam_h, dtype=float),
        np.asarray(lam_a, dtype=float),
        direction,
    )
    delta = np.expm1(float(phi))
    log_mu2 = np.log(lam2) - lam1 * delta
    cond_log_mean = log_mu2 + float(phi) * y1
    # Preregistered phi/rate bounds keep the football support numerically finite.
    cond_mean = np.exp(cond_log_mean)
    out = (
        y1 * np.log(lam1) - lam1 - gammaln(y1 + 1.0)
        + y2 * cond_log_mean - cond_mean - gammaln(y2 + 1.0)
    )
    if not np.isfinite(out).all():
        raise ResearchError("nonfinite BCP log probability")
    return out


def fit_phi(
    rows: pd.DataFrame,
    lam_h: np.ndarray,
    lam_a: np.ndarray,
    direction: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    bounds = [float(x) for x in cfg["bcp_contract"]["phi_bounds"]]
    if len(bounds) != 2 or not bounds[0] < 0 < bounds[1]:
        raise ResearchError("invalid phi bounds")
    hg = rows.home_goals_90.to_numpy(float)
    ag = rows.away_goals_90.to_numpy(float)

    def objective(phi: float) -> float:
        return float(-bcp_logpmf_arrays(hg, ag, lam_h, lam_a, phi, direction).mean())

    result = minimize_scalar(
        objective,
        bounds=(bounds[0], bounds[1]),
        method="bounded",
        options={"xatol": float(cfg["bcp_contract"]["phi_xatol"])},
    )
    phi = float(result.x)
    boundary_distance = float(min(phi - bounds[0], bounds[1] - phi))
    return {
        "direction": direction,
        "phi": phi,
        "success": bool(result.success and np.isfinite(result.fun)),
        "objective_mean_neg_loglik": float(result.fun),
        "iterations": int(getattr(result, "nit", -1)),
        "boundary_distance": boundary_distance,
        "hit_boundary": bool(boundary_distance < 1e-5),
    }


def joint_aggregates(
    lam_h: np.ndarray,
    lam_a: np.ndarray,
    phi: float,
    direction: str,
    max_goal: int,
) -> dict[str, Any]:
    n = len(lam_h)
    hda = np.zeros((n, 3), dtype=float)
    total = np.zeros((n, 8), dtype=float)
    mass = np.zeros(n, dtype=float)
    best_prob = np.zeros(n, dtype=float)
    best_h = np.zeros(n, dtype=int)
    best_a = np.zeros(n, dtype=int)

    for h in range(max_goal + 1):
        hh = np.full(n, h, dtype=float)
        for a in range(max_goal + 1):
            aa = np.full(n, a, dtype=float)
            p = np.exp(bcp_logpmf_arrays(hh, aa, lam_h, lam_a, phi, direction))
            mass += p
            hda[:, 0 if h > a else 1 if h == a else 2] += p
            total[:, min(h + a, 7)] += p
            take = p > best_prob
            best_prob[take] = p[take]
            best_h[take] = h
            best_a[take] = a

    residual = np.maximum(0.0, 1.0 - mass)
    # Any omitted cell has at least one team scoring > max_goal, hence T>=7.
    total[:, 7] += residual
    total /= total.sum(axis=1, keepdims=True)
    hda_mass = hda.sum(axis=1, keepdims=True)
    if np.any(hda_mass <= 0):
        raise ResearchError("nonpositive finite-grid HDA mass")
    hda /= hda_mass
    return {
        "hda": hda,
        "total": total,
        "finite_grid_mass": mass,
        "residual": residual,
        "best_prob": best_prob,
        "best_home": best_h,
        "best_away": best_a,
    }


def binary_draw_metrics(draw_truth: np.ndarray, draw_prob: np.ndarray) -> dict[str, Any]:
    y = np.asarray(draw_truth, dtype=float)
    p = np.clip(np.asarray(draw_prob, dtype=float), 1e-15, 1 - 1e-15)
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    auc = None
    if len(np.unique(y)) == 2:
        auc = float(roc_auc_score(y, p))
    return {
        "logloss": float(ll.mean()),
        "brier": float(np.mean((p - y) ** 2)),
        "observed_rate": float(y.mean()),
        "mean_probability": float(p.mean()),
        "auc": auc,
    }


def outcome_index(h: np.ndarray, a: np.ndarray) -> np.ndarray:
    return np.where(h > a, 0, np.where(h == a, 1, 2)).astype(int)


def block_bootstrap(deltas: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    samples = int(cfg["bootstrap"]["samples"])
    q0, q1 = [float(x) for x in cfg["bootstrap"]["interval"]]
    grouped = deltas.groupby("block", sort=True)
    blocks = list(grouped.groups)
    if len(blocks) < 2:
        raise ResearchError("too few competition-season bootstrap blocks")
    counts = grouped.size().reindex(blocks).to_numpy(float)
    rng = np.random.default_rng(int(cfg["bootstrap"]["seed"]))
    picks = rng.integers(0, len(blocks), size=(samples, len(blocks)))
    denom = counts[picks].sum(axis=1)
    out: dict[str, Any] = {"blocks": len(blocks), "samples": samples}
    for metric in [c for c in deltas.columns if c not in {"block", "fold"}]:
        sums = grouped[metric].sum().reindex(blocks).to_numpy(float)
        means = sums[picks].sum(axis=1) / denom
        out[metric] = {
            "point_delta": float(deltas[metric].mean()),
            "bootstrap_mean": float(means.mean()),
            "p05": float(np.quantile(means, q0)),
            "p95": float(np.quantile(means, q1)),
            "probability_challenger_better": float((means < 0).mean()),
        }
    return out


def scoreline_diagnostics(hg: np.ndarray, ag: np.ndarray, lam_h: np.ndarray, lam_a: np.ndarray, phi: float, direction: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h, a in [(0, 0), (1, 1), (2, 2)]:
        ph = np.full(len(hg), h, dtype=float)
        pa = np.full(len(hg), a, dtype=float)
        prob = np.exp(bcp_logpmf_arrays(ph, pa, lam_h, lam_a, phi, direction))
        truth = ((hg == h) & (ag == a)).astype(float)
        out[f"{h}-{a}"] = {
            "observed_rate": float(truth.mean()),
            "mean_probability": float(prob.mean()),
            "binary_brier": float(np.mean((prob - truth) ** 2)),
        }
    return out


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    raw_keyed = add_identity_key(raw)
    if raw_keyed.identity_key.duplicated().any():
        raise ResearchError("duplicate raw identity_key")
    features = add_identity_key(build_features(raw))
    labels = raw_keyed[["identity_key", "home_goals_90", "away_goals_90", "total_goals"]].copy()
    frame = features.merge(labels, on="identity_key", how="left", validate="one_to_one")
    if frame[["home_goals_90", "away_goals_90", "total_goals"]].isna().any().any():
        raise ResearchError("missing exact goal labels after identity merge")
    core = select_core_features(frame)
    if len(core) != 47:
        raise ResearchError(f"expected V5.1 47 core features, got {len(core)}")

    expected_positions = [int(x) for x in base_cfg["split_contract"]["rolling_test_positions_zero_based"]]
    configured_positions = [int(x) for x in cfg["split_contract"]["rolling_test_positions_zero_based"]]
    if configured_positions != expected_positions:
        raise ResearchError(f"rolling positions mismatch {configured_positions} != {expected_positions}")

    all_delta: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    all_converged = True
    all_not_boundary = True
    max_mass_residual = 0.0
    fold_score_better = 0

    for test_position in configured_positions:
        fold = frame.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        train = fold[fold.split == "train"].copy()
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])].copy()
        test = fold[fold.split == "test"].copy()
        if min(len(train), len(policy), len(test)) == 0:
            raise ResearchError(f"empty R42O split at test_position={test_position}")

        # Stage 1: direction selection uses train-fitted marginals and policy labels only.
        train_models = fit_rate_pair(train, core, cfg)
        lam_h_train, lam_a_train = predict_rates(train_models, train, core, cfg)
        lam_h_policy, lam_a_policy = predict_rates(train_models, policy, core, cfg)
        direction_rows: list[dict[str, Any]] = []
        for direction in [str(x) for x in cfg["bcp_contract"]["directions"]]:
            train_phi = fit_phi(train, lam_h_train, lam_a_train, direction, cfg)
            policy_ll = float(-bcp_logpmf_arrays(
                policy.home_goals_90.to_numpy(float),
                policy.away_goals_90.to_numpy(float),
                lam_h_policy,
                lam_a_policy,
                float(train_phi["phi"]),
                direction,
            ).mean())
            direction_rows.append({**train_phi, "policy_joint_score_logloss": policy_ll})
        selected = min(direction_rows, key=lambda x: (x["policy_joint_score_logloss"], x["direction"]))
        direction = str(selected["direction"])

        # Stage 2: refit shared marginals and selected phi on train+policy, then freeze for test.
        fit_models = fit_rate_pair(fit, core, cfg)
        lam_h_fit, lam_a_fit = predict_rates(fit_models, fit, core, cfg)
        final_phi = fit_phi(fit, lam_h_fit, lam_a_fit, direction, cfg)
        lam_h_test, lam_a_test = predict_rates(fit_models, test, core, cfg)
        all_converged = all_converged and all(bool(x["success"]) for x in direction_rows) and bool(final_phi["success"])
        all_not_boundary = all_not_boundary and all(not bool(x["hit_boundary"]) for x in direction_rows) and not bool(final_phi["hit_boundary"])

        hg = test.home_goals_90.to_numpy(int)
        ag = test.away_goals_90.to_numpy(int)
        score_base = -bcp_logpmf_arrays(hg, ag, lam_h_test, lam_a_test, 0.0, direction)
        score_ch = -bcp_logpmf_arrays(hg, ag, lam_h_test, lam_a_test, float(final_phi["phi"]), direction)

        max_goal = int(cfg["bcp_contract"]["joint_grid_max_goals_each_team"])
        agg_base = joint_aggregates(lam_h_test, lam_a_test, 0.0, direction, max_goal)
        agg_ch = joint_aggregates(lam_h_test, lam_a_test, float(final_phi["phi"]), direction, max_goal)
        max_mass_residual = max(
            max_mass_residual,
            float(np.max(agg_base["residual"])),
            float(np.max(agg_ch["residual"])),
        )

        y_hda = outcome_index(hg, ag)
        y_total = np.minimum(hg + ag, 7).astype(int)
        hb = metric_components(y_hda, agg_base["hda"], HDA_CLASSES)
        hc = metric_components(y_hda, agg_ch["hda"], HDA_CLASSES)
        tb = metric_components(y_total, agg_base["total"], TOTAL_CLASSES)
        tc = metric_components(y_total, agg_ch["total"], TOTAL_CLASSES)
        draw_truth = (hg == ag).astype(float)
        db = binary_draw_metrics(draw_truth, agg_base["hda"][:, 1])
        dc = binary_draw_metrics(draw_truth, agg_ch["hda"][:, 1])

        fold_score_delta = float(np.mean(score_ch - score_base))
        fold_score_better += int(fold_score_delta < 0)
        block = test.competition_id.astype(str) + "::" + test.season.astype(str)
        delta = pd.DataFrame({
            "block": block.to_numpy(str),
            "fold": np.full(len(test), test_position, dtype=int),
            "score_logloss": score_ch - score_base,
            "hda_logloss": hc.logloss.to_numpy(float) - hb.logloss.to_numpy(float),
            "hda_brier": hc.brier.to_numpy(float) - hb.brier.to_numpy(float),
            "hda_rps": hc.rps.to_numpy(float) - hb.rps.to_numpy(float),
            "total_logloss": tc.logloss.to_numpy(float) - tb.logloss.to_numpy(float),
            "total_brier": tc.brier.to_numpy(float) - tb.brier.to_numpy(float),
            "total_rps": tc.rps.to_numpy(float) - tb.rps.to_numpy(float),
            "draw_logloss": (
                -(draw_truth * np.log(np.clip(agg_ch["hda"][:, 1], 1e-15, 1 - 1e-15))
                  + (1 - draw_truth) * np.log(np.clip(1 - agg_ch["hda"][:, 1], 1e-15, 1 - 1e-15)))
                + (draw_truth * np.log(np.clip(agg_base["hda"][:, 1], 1e-15, 1 - 1e-15))
                   + (1 - draw_truth) * np.log(np.clip(1 - agg_base["hda"][:, 1], 1e-15, 1 - 1e-15)))
            ),
        })
        all_delta.append(delta)

        base_top = outcome_index(agg_base["best_home"], agg_base["best_away"])
        ch_top = outcome_index(agg_ch["best_home"], agg_ch["best_away"])
        folds.append({
            "test_position": test_position,
            "train_rows": int(len(train)),
            "policy_rows": int(len(policy)),
            "test_rows": int(len(test)),
            "direction_selection": direction_rows,
            "selected_direction": direction,
            "final_phi": final_phi,
            "baseline_joint_score_logloss": float(score_base.mean()),
            "challenger_joint_score_logloss": float(score_ch.mean()),
            "joint_score_logloss_delta": fold_score_delta,
            "hda": {
                "baseline": metric_summary(hb),
                "challenger": metric_summary(hc),
                "draw_baseline": db,
                "draw_challenger": dc,
                "top1_draws_baseline": int((base_top == 1).sum()),
                "top1_draws_challenger": int((ch_top == 1).sum()),
                "actual_draws": int(draw_truth.sum()),
            },
            "direct_total": {
                "baseline": metric_summary(tb),
                "challenger": metric_summary(tc),
            },
            "scoreline_diagonal": {
                "baseline": scoreline_diagnostics(hg, ag, lam_h_test, lam_a_test, 0.0, direction),
                "challenger": scoreline_diagnostics(hg, ag, lam_h_test, lam_a_test, float(final_phi["phi"]), direction),
            },
            "probability_mass": {
                "baseline_max_residual": float(np.max(agg_base["residual"])),
                "challenger_max_residual": float(np.max(agg_ch["residual"])),
            },
        })

    deltas = pd.concat(all_delta, ignore_index=True)
    boot = block_bootstrap(deltas, cfg)
    point = {metric: float(deltas[metric].mean()) for metric in deltas.columns if metric not in {"block", "fold"}}
    mass_limit = float(cfg["bcp_contract"]["max_probability_mass_residual"])
    dcfg = cfg["decision_contract"]
    gate = {
        "joint_score_logloss_p95_below_zero": bool(boot["score_logloss"]["p95"] < 0),
        "minimum_folds_joint_score_better": bool(fold_score_better >= int(dcfg["minimum_folds_with_better_joint_score_logloss"])),
        "hda_logloss_nonworse": bool(point["hda_logloss"] <= 0),
        "hda_brier_nonworse": bool(point["hda_brier"] <= 0),
        "hda_rps_nonworse": bool(point["hda_rps"] <= 0),
        "direct_total_logloss_nonworse": bool(point["total_logloss"] <= 0),
        "direct_total_brier_nonworse": bool(point["total_brier"] <= 0),
        "draw_binary_logloss_nonworse": bool(point["draw_logloss"] <= 0),
        "probability_mass_audit": bool(max_mass_residual <= mass_limit),
        "optimizer_convergence": bool(all_converged),
        "phi_not_at_boundary": bool(all_not_boundary),
    }
    gate["all_required"] = bool(all(gate.values()))

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R42O_ROLLING_OOS_EXECUTION_COMPLETE",
        "scientific_verdict": (
            "PASS_R42O_BCP_DEPENDENCE_ROLLING_OOS_AUTHORIZE_FRESH_FIXED200"
            if gate["all_required"]
            else "FAIL_R42O_BCP_DEPENDENCE_NO_ROLLING_OOS_INCREMENT"
        ),
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "research_source": cfg["research_source"],
        "model_contract": {
            "baseline": "same strict-prior Poisson marginals with BCP phi=0",
            "challenger": "same strict-prior Poisson marginals plus one fitted BCP phi; direction policy-selected",
            "core_feature_count": len(core),
            "marginal_rate_contract": cfg["marginal_rate_contract"],
            "bcp_contract": cfg["bcp_contract"],
            "test_labels_used_for_direction_selection": False,
            "test_labels_used_for_parameter_selection": False,
            "fixed200_consumed": 0,
        },
        "folds": folds,
        "pooled_test_rows": int(len(deltas)),
        "pooled_competition_season_blocks": int(deltas.block.nunique()),
        "folds_with_better_joint_score_logloss": int(fold_score_better),
        "point_deltas_challenger_minus_baseline": point,
        "block_bootstrap": boot,
        "max_probability_mass_residual": float(max_mass_residual),
        "gate": gate,
        "governance": cfg["governance"],
        "interpretation_limits": [
            "R42O is a frequentist dependence-only adaptation of the BCP distribution in arXiv:2608.07168, not an exact reproduction of its Bayesian/HMC model.",
            "The rolling OOS evidence is retrospective/viewed and can only authorize a fresh fixed200 challenge, never formal promotion.",
            "Baseline and challenger share identical marginal home/away rate models; the tested increment is the BCP dependence parameter and policy-selected direction only.",
            "No market, web, current-match or new-provider data are used.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    # phi=0 must reduce exactly to independent Poisson regardless of direction.
    hg = np.asarray([0, 1, 2, 3], dtype=float)
    ag = np.asarray([0, 2, 1, 3], dtype=float)
    lh = np.asarray([1.2, 1.4, 1.6, 1.8])
    la = np.asarray([0.9, 1.1, 1.3, 1.5])
    expected = hg * np.log(lh) - lh - gammaln(hg + 1) + ag * np.log(la) - la - gammaln(ag + 1)
    a = bcp_logpmf_arrays(hg, ag, lh, la, 0.0, "H_TO_A")
    b = bcp_logpmf_arrays(hg, ag, lh, la, 0.0, "A_TO_H")
    assert np.max(np.abs(a - expected)) < 1e-12
    assert np.max(np.abs(b - expected)) < 1e-12
    cfg = load_json(DEFAULT_CONFIG)
    assert cfg["governance"]["formal_weight"] == 0
    assert cfg["governance"]["fixed200_consumed"] == 0
    assert cfg["split_contract"]["test_labels_used_for_direction_selection"] is False
    print(json.dumps({"status": "PASS_R42O_SELF_TEST", "phi0_independence_residual": float(np.max(np.abs(a - expected)))}, ensure_ascii=False))


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
        "pooled_test_rows": result["pooled_test_rows"],
        "folds_with_better_joint_score_logloss": result["folds_with_better_joint_score_logloss"],
        "point_deltas_challenger_minus_baseline": result["point_deltas_challenger_minus_baseline"],
        "gate": result["gate"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
