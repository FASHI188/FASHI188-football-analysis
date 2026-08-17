#!/usr/bin/env python3
"""R8 post-view total-goal parity mechanism diagnostic.

Exact frozen question: on the same already-VIEWED 1,584 identities used by the
Direct-T oracle/routing diagnostics, do the strict-pre-match historical response
blocks R42F18 and R42J18 improve even-vs-odd exact 90-minute total-goal
probability beyond core47?

This is retrospective mechanism research only. It opens no B05+ labels and cannot
claim scientific confirmation or formal promotion.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from evaluate_direct_t_oracle_learnability_r1 import (
    DEFAULT_CONFIG as ORACLE_R1_CONFIG,
    DEFAULT_PARENT_COHORT,
    DEFAULT_PARENT_CONFIG,
    DEFAULT_PARENT_STATUS,
    ROOT,
    build_frame,
)
from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json
from evaluate_viewed_common_cohort_oracle_r1 import replay_r40f
from v510_historical_structure_features_r1 import ResearchError, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model

CONFIG = ROOT / "config" / "r42fj_total_parity_diagnostic_r8.json"
OUT_DIR = ROOT / "manifests" / "r42fj_total_parity_diagnostic_r8"
EXPECTED_ROWS = 1584
EXPECTED_ID_SHA = "0e7c57c5168280cd8b3264fe3c04d46d5caa51b6b6f4218aee84826bf7d7908c"
BINARY_CLASSES = [0, 1]


def _identity_sha(values: pd.Series) -> str:
    ids = sorted(values.astype(str))
    return hashlib.sha256(("\n".join(ids) + "\n").encode("utf-8")).hexdigest()


def _attach_exact_total(frame: pd.DataFrame, fcfg: dict[str, Any]) -> pd.DataFrame:
    raw = pd.read_csv(ROOT / str(fcfg["input_ledger"]))
    raw = add_identity_key(raw)
    if not raw["identity_key"].is_unique:
        raise ResearchError("RAW_IDENTITY_NOT_UNIQUE")
    exact = raw[["identity_key", "total_goals"]].copy()
    exact["total_goals_exact"] = exact["total_goals"].astype(int)
    exact = exact.drop(columns=["total_goals"])
    out = frame.merge(exact, on="identity_key", how="left", validate="one_to_one")
    if out["total_goals_exact"].isna().any():
        raise ResearchError("EXACT_TOTAL_JOIN_MISSING")
    out["even_total"] = (out["total_goals_exact"].astype(int) % 2 == 0).astype(int)
    return out


def _metrics(y: np.ndarray, p_even: np.ndarray) -> dict[str, float]:
    p = np.clip(np.asarray(p_even, dtype=float), 1e-15, 1.0 - 1e-15)
    truth = np.asarray(y, dtype=int)
    ll = -np.mean(truth * np.log(p) + (1 - truth) * np.log(1.0 - p))
    return {
        "binary_logloss": float(ll),
        "brier": float(np.mean((p - truth) ** 2)),
        "auc": float(roc_auc_score(truth, p)),
        "accuracy_at_0_5": float(np.mean((p >= 0.5).astype(int) == truth)),
        "observed_even_rate": float(np.mean(truth)),
        "mean_probability_even": float(np.mean(p)),
    }


def _per_row_logloss(y: np.ndarray, p_even: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p_even, dtype=float), 1e-15, 1.0 - 1e-15)
    truth = np.asarray(y, dtype=int)
    return -(truth * np.log(p) + (1 - truth) * np.log(1.0 - p))


def _cluster_bootstrap_delta(
    evaluation: pd.DataFrame,
    delta_per_row: np.ndarray,
    cluster_columns: list[str],
    resamples: int,
    seed: int,
) -> dict[str, float | int]:
    if len(delta_per_row) != len(evaluation):
        raise ResearchError("BOOTSTRAP_LENGTH_MISMATCH")
    grouped = evaluation.reset_index(drop=True).groupby(cluster_columns, sort=True).indices
    clusters = [np.asarray(grouped[key], dtype=int) for key in sorted(grouped)]
    if not clusters:
        raise ResearchError("NO_BOOTSTRAP_CLUSTERS")
    rng = np.random.default_rng(seed)
    draws = np.empty(resamples, dtype=float)
    n_clusters = len(clusters)
    for i in range(resamples):
        sampled = rng.integers(0, n_clusters, size=n_clusters)
        idx = np.concatenate([clusters[j] for j in sampled])
        draws[i] = float(np.mean(delta_per_row[idx]))
    return {
        "clusters": int(n_clusters),
        "resamples": int(resamples),
        "point": float(np.mean(delta_per_row)),
        "p05": float(np.quantile(draws, 0.05)),
        "median": float(np.quantile(draws, 0.50)),
        "p95": float(np.quantile(draws, 0.95)),
        "p_improve": float(np.mean(draws < 0.0)),
    }


def run(cfg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    oracle_cfg = load_json(ORACLE_R1_CONFIG)
    parent_cfg = load_json(DEFAULT_PARENT_CONFIG)
    parent = load_json(DEFAULT_PARENT_STATUS)
    cohort = pd.read_csv(DEFAULT_PARENT_COHORT)
    pcfg = oracle_cfg["parent_diagnostic"]

    if len(cohort) != int(pcfg["diagnostic_rows_to_exclude"]):
        raise ResearchError("PARENT_DIAGNOSTIC_ROW_COUNT_MISMATCH")
    if parent["cohort"]["identity_sha256"] != str(pcfg["diagnostic_1000_identity_sha256"]):
        raise ResearchError("PARENT_DIAGNOSTIC_IDENTITY_MISMATCH")
    excluded_ids = set(cohort["identity_key"].astype(str))

    frame, base_cfg, fcfg, _jcfg, fnames, jnames, common_ok, _seasons = build_frame()
    frame = _attach_exact_total(frame, fcfg)
    core = select_core_features(frame)

    fcontract = cfg["feature_contract"]
    if len(core) != int(fcontract["core47"]):
        raise ResearchError(f"CORE_FEATURE_COUNT_MISMATCH:{len(core)}")
    if len(fnames) != int(fcontract["r42f18"]):
        raise ResearchError(f"R42F_FEATURE_COUNT_MISMATCH:{len(fnames)}")
    if len(jnames) != int(fcontract["r42j18"]):
        raise ResearchError(f"R42J_FEATURE_COUNT_MISMATCH:{len(jnames)}")

    r40f, r40_audit = replay_r40f(parent_cfg)
    if not bool(r40_audit["frozen_summary_reproduced"]):
        raise ResearchError("R40F_FROZEN_SUMMARY_NOT_REPRODUCED")
    r40_keys = set(r40f["identity_key"].astype(str))
    target_all = frame[(frame["split"] == "target_pool") & common_ok].copy()
    target_all = target_all[target_all["identity_key"].astype(str).isin(r40_keys)].copy()
    evaluation = (
        target_all[~target_all["identity_key"].astype(str).isin(excluded_ids)]
        .sort_values("identity_key")
        .reset_index(drop=True)
    )
    if len(evaluation) != EXPECTED_ROWS:
        raise ResearchError(f"PRIMARY_EVALUATION_ROW_MISMATCH:{len(evaluation)}")
    identity_sha = _identity_sha(evaluation["identity_key"])
    if identity_sha != EXPECTED_ID_SHA:
        raise ResearchError(f"PRIMARY_EVALUATION_IDENTITY_MISMATCH:{identity_sha}")

    fit = frame[common_ok & frame["split"].isin(["train", "policy"])].copy()
    if len(fit) == 0:
        raise ResearchError("EMPTY_FIT_ROWS")
    if fit["even_total"].nunique() != 2 or evaluation["even_total"].nunique() != 2:
        raise ResearchError("PARITY_TARGET_NOT_BINARY_IN_FIT_OR_EVAL")

    family_columns = {
        "core47": core,
        "core_r42f65": core + fnames,
        "core_r42j65": core + jnames,
        "all83": core + fnames + jnames,
    }
    expected_counts = {"core47": 47, "core_r42f65": 65, "core_r42j65": 65, "all83": 83}
    for name, cols in family_columns.items():
        if len(cols) != expected_counts[name] or len(set(cols)) != len(cols):
            raise ResearchError(f"INVALID_FAMILY_COLUMNS:{name}:{len(cols)}:{len(set(cols))}")

    C = float(cfg["model_contract"]["fixed_C"])
    y_fit = fit["even_total"].to_numpy(int)
    y = evaluation["even_total"].to_numpy(int)
    probabilities: dict[str, np.ndarray] = {}
    family_metrics: dict[str, dict[str, float]] = {}
    losses: dict[str, np.ndarray] = {}
    for name, cols in family_columns.items():
        model = make_model(C, base_cfg)
        model.fit(fit[cols], y_fit)
        p = align_probability(model, evaluation[cols], BINARY_CLASSES)[:, 1]
        if not np.isfinite(p).all() or np.any((p < 0.0) | (p > 1.0)):
            raise ResearchError(f"INVALID_PROBABILITY:{name}")
        probabilities[name] = p
        family_metrics[name] = _metrics(y, p)
        losses[name] = _per_row_logloss(y, p)

    core_m = family_metrics["core47"]
    all_m = family_metrics["all83"]
    delta_all_minus_core = losses["all83"] - losses["core47"]
    report = cfg["reporting_contract"]
    bootstrap = _cluster_bootstrap_delta(
        evaluation,
        delta_all_minus_core,
        [str(x) for x in report["cluster_columns"]],
        int(report["bootstrap_resamples"]),
        int(report["bootstrap_seed"]),
    )
    comparison = {
        "binary_logloss_delta_all83_minus_core47": float(all_m["binary_logloss"] - core_m["binary_logloss"]),
        "binary_logloss_gain_core47_minus_all83": float(core_m["binary_logloss"] - all_m["binary_logloss"]),
        "brier_delta_all83_minus_core47": float(all_m["brier"] - core_m["brier"]),
        "auc_delta_all83_minus_core47": float(all_m["auc"] - core_m["auc"]),
        "accuracy_pp_all83_minus_core47": float(100.0 * (all_m["accuracy_at_0_5"] - core_m["accuracy_at_0_5"])),
        "cluster_bootstrap_logloss_delta_all83_minus_core47": bootstrap,
    }
    gate = report["development_signal_gate"]
    signal = (
        comparison["binary_logloss_gain_core47_minus_all83"]
        >= float(gate["minimum_logloss_gain_all83_vs_core47"])
        and bootstrap["p95"] < 0.0
        and comparison["auc_delta_all83_minus_core47"] >= float(gate["minimum_auc_gain_all83_vs_core47"])
        and all_m["brier"] <= core_m["brier"]
    )

    per_match = evaluation[
        [
            "identity_key",
            "competition_id",
            "season",
            "date_key",
            "home_team",
            "away_team",
            "total_goals_exact",
            "even_total",
        ]
    ].copy()
    for name in family_columns:
        per_match[f"p_even_{name}"] = probabilities[name]
        per_match[f"logloss_{name}"] = losses[name]
    out_dir.mkdir(parents=True, exist_ok=True)
    per_match.to_csv(out_dir / "per_match.csv", index=False)

    boundary = dict(cfg["boundary"])
    summary: dict[str, Any] = {
        "schema_version": cfg["schema_version"],
        "status": "POSTVIEW_R42FJ_TOTAL_PARITY_R8_COMPLETE_NO_PROMOTION",
        "source_contract": {
            **cfg["source_contract"],
            "observed_primary_rows": int(len(evaluation)),
            "observed_primary_identity_sha256": identity_sha,
            "fit_rows": int(len(fit)),
            "r40f_frozen_summary_reproduced": bool(r40_audit["frozen_summary_reproduced"]),
        },
        "feature_counts": {name: len(cols) for name, cols in family_columns.items()},
        "family_metrics": family_metrics,
        "primary_comparison": comparison,
        "development_signal_for_future_oos_design": bool(signal),
        "development_signal_is_scientific_pass": False,
        "development_signal_is_confirmation_pass": False,
        "boundary": boundary,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    cfg = load_json(CONFIG)
    summary = run(cfg, OUT_DIR)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
