#!/usr/bin/env python3
"""R2 post-view expected-loss routing anatomy.

This is a retrospective mechanism diagnostic on the exact 1,584 already-VIEWED rows
used by PR #179/#202. It does not consume B05+ labels and cannot claim scientific,
confirmation, or formal promotion PASS.

The test keeps the selector shell intentionally simple and fixed (StandardScaler+Ridge,
alpha=10). For router families that add the existing nullable Direct-T input state, it
reuses the established Direct-T preprocessing contract: policy-fit median imputation
before scaling. It separates four questions:
1) exact R1 absolute-loss routing from geometry39;
2) relative-loss target from geometry39;
3) relative-loss target + the existing 47 pre-match core features;
4) relative-loss target + all existing expert input-state features.

Any improvement is descriptive evidence about missing routing information, not a new
confirmation result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from evaluate_direct_t_oracle_learnability_r1 import (
    DEFAULT_CONFIG,
    DEFAULT_PARENT_COHORT,
    DEFAULT_PARENT_CONFIG,
    DEFAULT_PARENT_STATUS,
    EXPERTS,
    ROOT,
    bootstrap_delta,
    build_frame,
    fit_experts,
    meta_features,
    metrics,
)
from evaluate_r41a_fixed200_joint_error_decomposition import load_json
from evaluate_viewed_common_cohort_oracle_r1 import _true_loss, replay_r40f
from v510_historical_structure_features_r1 import ResearchError, select_core_features

CONFIG = ROOT / "config" / "direct_t_expected_loss_routing_r2.json"
OUT_DIR = ROOT / "manifests" / "direct_t_expected_loss_routing_r2"
EXPECTED_ROWS = 1584
EXPECTED_ID_SHA = "0e7c57c5168280cd8b3264fe3c04d46d5caa51b6b6f4218aee84826bf7d7908c"
EXPECTED_COMMON_LL = 1.852486746078988
EXPECTED_ORACLE_LL = 1.817277840385054


def _identity_sha(s: pd.Series) -> str:
    vals = sorted(s.astype(str))
    return hashlib.sha256(("\n".join(vals) + "\n").encode("utf-8")).hexdigest()


def _raw_block(df: pd.DataFrame, cols: list[str], name: str) -> np.ndarray:
    x = df[cols].to_numpy(dtype=float)
    if x.shape != (len(df), len(cols)):
        raise ResearchError(f"{name}_SHAPE_MISMATCH:{x.shape}")
    # NaN is an expected part of the established Direct-T feature contract and is
    # handled by policy-fit median imputation. Infinite values remain invalid.
    if np.isinf(x).any():
        bad = int(np.isinf(x).sum())
        raise ResearchError(f"{name}_INFINITE:{bad}")
    return x


def _compose_features(
    df: pd.DataFrame,
    probs: dict[str, np.ndarray],
    family: str,
    core: list[str],
    fnames: list[str],
    jnames: list[str],
) -> np.ndarray:
    g = meta_features(probs)
    if family in {"r1_absolute_geometry39", "relative_geometry39"}:
        x = g
    elif family == "relative_geometry_core86":
        x = np.concatenate([g, _raw_block(df, core, "CORE47")], axis=1)
    elif family == "relative_geometry_all122":
        x = np.concatenate(
            [
                g,
                _raw_block(df, core, "CORE47"),
                _raw_block(df, fnames, "R42F18"),
                _raw_block(df, jnames, "R42J18"),
            ],
            axis=1,
        )
    else:
        raise ResearchError(f"UNKNOWN_ROUTER_FAMILY:{family}")
    expected = {
        "r1_absolute_geometry39": 39,
        "relative_geometry39": 39,
        "relative_geometry_core86": 86,
        "relative_geometry_all122": 122,
    }[family]
    if x.shape != (len(df), expected) or np.isinf(x).any():
        raise ResearchError(f"INVALID_ROUTER_MATRIX:{family}:{x.shape}")
    return x


def _fit_absolute(
    x_policy: np.ndarray,
    policy_losses: dict[str, np.ndarray],
    x_eval: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    predicted = np.column_stack(
        [
            make_pipeline(StandardScaler(), Ridge(alpha=alpha))
            .fit(x_policy, policy_losses[n])
            .predict(x_eval)
            for n in EXPERTS
        ]
    )
    return np.argmin(predicted, axis=1), predicted


def _fit_relative(
    x_policy: np.ndarray,
    policy_losses: dict[str, np.ndarray],
    x_eval: np.ndarray,
    alpha: float,
    impute_missing: bool,
) -> tuple[np.ndarray, np.ndarray]:
    common = policy_losses["common_baseline"]
    preds = [np.zeros(len(x_eval), dtype=float)]
    for n in ("R42F", "R42J"):
        target = policy_losses[n] - common
        if impute_missing:
            model = make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True),
                StandardScaler(),
                Ridge(alpha=alpha),
            )
        else:
            model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
        model.fit(x_policy, target)
        preds.append(model.predict(x_eval))
    predicted_delta = np.column_stack(preds)
    return np.argmin(predicted_delta, axis=1), predicted_delta


def _selected_probabilities(
    probs: dict[str, np.ndarray],
    choices: np.ndarray,
) -> np.ndarray:
    out = np.empty_like(probs[EXPERTS[0]])
    for i, n in enumerate(EXPERTS):
        mask = choices == i
        out[mask] = probs[n][mask]
    return out


def _corr(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 2 or np.std(a) <= 1e-15 or np.std(b) <= 1e-15:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _relative_diagnostics(
    predicted: np.ndarray,
    realized_losses: dict[str, np.ndarray],
) -> dict[str, Any]:
    common = realized_losses["common_baseline"]
    out: dict[str, Any] = {}
    for i, n in enumerate(("R42F", "R42J"), start=1):
        actual = realized_losses[n] - common
        pred = predicted[:, i]
        out[n] = {
            "pearson_predicted_vs_realized_delta": _corr(pred, actual),
            "sign_accuracy_predicted_improvement": float(np.mean((pred < 0.0) == (actual < 0.0))),
            "predicted_improve_rate": float(np.mean(pred < 0.0)),
            "realized_improve_rate": float(np.mean(actual < 0.0)),
            "mean_predicted_delta": float(np.mean(pred)),
            "mean_realized_delta": float(np.mean(actual)),
        }
    return out


def run(cfg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    r1cfg = load_json(DEFAULT_CONFIG)
    parent_cfg = load_json(DEFAULT_PARENT_CONFIG)
    parent = load_json(DEFAULT_PARENT_STATUS)
    cohort = pd.read_csv(DEFAULT_PARENT_COHORT)
    pcfg = r1cfg["parent_diagnostic"]

    if len(cohort) != int(pcfg["diagnostic_rows_to_exclude"]):
        raise ResearchError("PARENT_DIAGNOSTIC_ROW_COUNT_MISMATCH")
    if parent["cohort"]["identity_sha256"] != str(pcfg["diagnostic_1000_identity_sha256"]):
        raise ResearchError("PARENT_DIAGNOSTIC_IDENTITY_MISMATCH")
    excluded_ids = set(cohort.identity_key.astype(str))

    frame, base_cfg, _fcfg, _jcfg, fnames, jnames, common_ok, _seasons = build_frame()
    core = select_core_features(frame)
    ecfg = cfg["expert_contract"]
    if len(core) != int(ecfg["core_feature_count"]):
        raise ResearchError(f"CORE_FEATURE_COUNT_MISMATCH:{len(core)}")
    if len(fnames) != int(ecfg["r42f_feature_count"]) or len(jnames) != int(ecfg["r42j_feature_count"]):
        raise ResearchError("EXPERT_FEATURE_COUNT_MISMATCH")
    C = float(ecfg["fixed_C"])

    r40f, r40_audit = replay_r40f(parent_cfg)
    if not bool(r40_audit["frozen_summary_reproduced"]):
        raise ResearchError("R40F_FROZEN_SUMMARY_NOT_REPRODUCED")
    r40_keys = set(r40f.identity_key.astype(str))
    target_all = frame[(frame.split == "target_pool") & common_ok].copy()
    target_all = target_all[target_all.identity_key.astype(str).isin(r40_keys)].copy()
    evaluation = target_all[~target_all.identity_key.astype(str).isin(excluded_ids)].sort_values("identity_key").copy()

    if len(evaluation) != EXPECTED_ROWS:
        raise ResearchError(f"PRIMARY_EVALUATION_ROW_MISMATCH:{len(evaluation)}")
    identity_sha = _identity_sha(evaluation.identity_key)
    if identity_sha != EXPECTED_ID_SHA:
        raise ResearchError(f"PRIMARY_EVALUATION_IDENTITY_MISMATCH:{identity_sha}")

    train = frame[common_ok & (frame.split == "train")].copy()
    policy = frame[common_ok & (frame.split == "policy")].copy()
    fit_target = frame[common_ok & frame.split.isin(["train", "policy"])].copy()
    if min(len(train), len(policy), len(fit_target)) == 0:
        raise ResearchError("EMPTY_CHRONOLOGICAL_SELECTOR_SPLIT")

    p_policy = fit_experts(train, policy, core, fnames, jnames, C, base_cfg)
    y_policy = policy.total_class.to_numpy(int)
    policy_losses = {n: _true_loss(y_policy, p_policy[n]) for n in EXPERTS}
    policy_ll = {n: float(np.mean(policy_losses[n])) for n in EXPERTS}
    best_static_name = min(EXPERTS, key=lambda n: policy_ll[n])

    p_eval = fit_experts(fit_target, evaluation, core, fnames, jnames, C, base_cfg)
    y = evaluation.total_class.to_numpy(int)
    eval_losses = {n: _true_loss(y, p_eval[n]) for n in EXPERTS}
    common_ll = float(np.mean(eval_losses["common_baseline"]))
    loss_matrix = np.column_stack([eval_losses[n] for n in EXPERTS])
    oracle_loss = np.min(loss_matrix, axis=1)
    oracle_ll = float(np.mean(oracle_loss))
    if abs(common_ll - EXPECTED_COMMON_LL) > 1e-12:
        raise ResearchError(f"COMMON_LL_REPRODUCTION_MISMATCH:{common_ll}")
    if abs(oracle_ll - EXPECTED_ORACLE_LL) > 1e-12:
        raise ResearchError(f"ORACLE_LL_REPRODUCTION_MISMATCH:{oracle_ll}")

    common_p = p_eval["common_baseline"]
    static_p = p_eval[best_static_name]
    equal_p = sum(p_eval[n] for n in EXPERTS) / float(len(EXPERTS))
    common_metrics = metrics(y, common_p)
    static_metrics = metrics(y, static_p)
    equal_metrics = metrics(y, equal_p)
    expert_metrics = {n: metrics(y, p_eval[n]) for n in EXPERTS}

    alpha = float(cfg["router_contract"]["ridge_alpha"])
    report = cfg["reporting_contract"]
    resamples = int(report["paired_bootstrap_resamples"])
    seed = int(report["bootstrap_seed"])
    full_gap = common_ll - oracle_ll

    per_match = evaluation[
        ["identity_key", "competition_id", "season", "date_key", "home_team", "away_team", "total_class"]
    ].copy()
    for n in EXPERTS:
        per_match[f"loss_{n}"] = eval_losses[n]
    per_match["row_oracle_expert"] = [EXPERTS[i] for i in np.argmin(loss_matrix, axis=1)]
    per_match["row_oracle_loss"] = oracle_loss
    per_match["row_oracle_gain_vs_common"] = eval_losses["common_baseline"] - oracle_loss

    family_results: dict[str, Any] = {}
    families = [
        "r1_absolute_geometry39",
        "relative_geometry39",
        "relative_geometry_core86",
        "relative_geometry_all122",
    ]
    for k, family in enumerate(families):
        x_policy = _compose_features(policy, p_policy, family, core, fnames, jnames)
        x_eval = _compose_features(evaluation, p_eval, family, core, fnames, jnames)
        impute_missing = family in {"relative_geometry_core86", "relative_geometry_all122"}

        if family == "r1_absolute_geometry39":
            choices, predicted = _fit_absolute(x_policy, policy_losses, x_eval, alpha)
            relative_diag = None
        else:
            choices, predicted = _fit_relative(
                x_policy, policy_losses, x_eval, alpha, impute_missing=impute_missing
            )
            relative_diag = _relative_diagnostics(predicted, eval_losses)

        p_sel = _selected_probabilities(p_eval, choices)
        m = metrics(y, p_sel)
        boot_common = bootstrap_delta(y, common_p, p_sel, resamples, seed + k)
        boot_static = bootstrap_delta(y, static_p, p_sel, resamples, seed + 100 + k)
        counts = {n: int(np.sum(choices == i)) for i, n in enumerate(EXPERTS)}
        selected_n = int(sum(v > 0 for v in counts.values()))
        gain_common = float(common_metrics["logloss"] - m["logloss"])
        captured = float(gain_common / full_gap) if full_gap > 1e-15 else None

        signal = (
            gain_common >= float(report["development_signal_threshold_logloss_gain_vs_common"])
            and boot_common["logloss"]["p95"] < 0.0
            and m["brier"] <= common_metrics["brier"]
            and m["rps"] <= common_metrics["rps"]
            and selected_n >= 2
        )
        family_results[family] = {
            "feature_count": int(x_eval.shape[1]),
            "missing_value_preprocessing": (
                "policy_fit_median_simpleimputer_keep_empty_features"
                if impute_missing else "none_required"
            ),
            "metrics": m,
            "choice_counts": counts,
            "selected_expert_count": selected_n,
            "delta_vs_common": {
                "logloss": float(m["logloss"] - common_metrics["logloss"]),
                "brier": float(m["brier"] - common_metrics["brier"]),
                "rps": float(m["rps"] - common_metrics["rps"]),
                "top1_accuracy_pp": float(100.0 * (m["top1_accuracy"] - common_metrics["top1_accuracy"])),
            },
            "delta_vs_policy_best_static": {
                "static_name": best_static_name,
                "logloss": float(m["logloss"] - static_metrics["logloss"]),
                "brier": float(m["brier"] - static_metrics["brier"]),
                "rps": float(m["rps"] - static_metrics["rps"]),
                "top1_accuracy_pp": float(100.0 * (m["top1_accuracy"] - static_metrics["top1_accuracy"])),
            },
            "bootstrap_vs_common": boot_common,
            "bootstrap_vs_policy_best_static": boot_static,
            "fraction_of_common_to_row_oracle_gap_captured": captured,
            "relative_loss_prediction_diagnostics": relative_diag,
            "development_signal_for_future_oos_design": bool(signal),
            "development_signal_is_scientific_pass": False,
        }
        per_match[f"choice_{family}"] = [EXPERTS[i] for i in choices]
        if family != "r1_absolute_geometry39":
            per_match[f"pred_delta_R42F_{family}"] = predicted[:, 1]
            per_match[f"pred_delta_R42J_{family}"] = predicted[:, 2]

    primary = family_results["relative_geometry_all122"]
    verdict = (
        "POSTVIEW_ROUTING_INFORMATION_SIGNAL_DESCRIPTIVE_ONLY"
        if primary["development_signal_for_future_oos_design"]
        else "POSTVIEW_ROUTING_INFORMATION_SIGNAL_NOT_ESTABLISHED"
    )
    result = {
        "schema_version": cfg["schema_version"],
        "status": "POSTVIEW_EXPECTED_LOSS_ROUTING_R2_COMPLETE_NO_PROMOTION",
        "scientific_verdict": verdict,
        "source_contract": {
            "parent_pr": 202,
            "parent_head": cfg["source_contract"]["parent_head"],
            "primary_rows": int(len(evaluation)),
            "primary_identity_sha256": identity_sha,
            "same_already_viewed_rows": True,
            "new_target_label_access": 0,
            "b05_b07_labels_opened": False,
        },
        "reproduction": {
            "common_baseline_logloss": common_ll,
            "full_row_oracle_logloss": oracle_ll,
            "full_row_oracle_gap": full_gap,
            "policy_selected_best_static": best_static_name,
            "policy_logloss": policy_ll,
            "r40f_frozen_summary_reproduced": True,
        },
        "comparators": {
            "common_baseline": common_metrics,
            "policy_best_static": static_metrics,
            "equal_weight_average": equal_metrics,
            "experts": expert_metrics,
        },
        "router_families": family_results,
        "primary_mechanism_family": "relative_geometry_all122",
        "boundary": {
            "retrospective_viewed_only": True,
            "post_selection_motivated": True,
            "scientific_pass_claim_allowed": False,
            "confirmation_pass_claim_allowed": False,
            "formal_promotion_allowed": False,
            "future_oos_label_open_authorized": False,
            "b01_b04_reused_as_confirmatory": False,
            "b05_b07_labels_opened": False,
            "new_data_collection": 0,
            "provider_requests": 0,
            "paid_api_requests": 0,
            "formal_weight": 0,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    per_match.to_csv(out_dir / "per_match.csv", index=False)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (out_dir / "summary.json").write_text(text, encoding="utf-8")
    (out_dir / "summary.sha256").write_text(
        hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="ascii"
    )
    return result


if __name__ == "__main__":
    print(json.dumps(run(load_json(CONFIG), OUT_DIR), ensure_ascii=False, indent=2))
