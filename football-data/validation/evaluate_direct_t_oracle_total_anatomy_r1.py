#!/usr/bin/env python3
"""Post-view anatomy of PR #179 Direct-T oracle heterogeneity by realized total bucket.

This script does not fit a selector, tune thresholds, or alter any formal asset. It
reconstructs the exact disjoint 1,584-row PR #179 evaluation, refits the same three frozen
Direct-T experts on the same train+policy rows, then measures how much of the ex-post oracle
gap is organized by realized total class. Realized T is label information and is used only
for retrospective diagnosis; all routing ceilings here are explicitly non-deployable.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_direct_t_oracle_learnability_r1 import (
    DEFAULT_CONFIG,
    DEFAULT_PARENT_COHORT,
    DEFAULT_PARENT_CONFIG,
    DEFAULT_PARENT_STATUS,
    EXPERTS,
    ROOT,
    build_frame,
    fit_experts,
)
from evaluate_r41a_fixed200_joint_error_decomposition import load_json
from evaluate_viewed_common_cohort_oracle_r1 import _true_loss, replay_r40f
from v510_historical_structure_features_r1 import ResearchError, select_core_features

OUT_DIR = ROOT / "manifests" / "direct_t_oracle_total_anatomy_r1"
EXPECTED_ROWS = 1584
EXPECTED_ID_SHA = "0e7c57c5168280cd8b3264fe3c04d46d5caa51b6b6f4218aee84826bf7d7908c"
EXPECTED_STATIC_LL = 1.852486746078988
EXPECTED_ORACLE_LL = 1.817277840385054


def _identity_sha(s: pd.Series) -> str:
    vals = sorted(s.astype(str))
    return hashlib.sha256(("\n".join(vals) + "\n").encode("utf-8")).hexdigest()


def _router_summary(groups: pd.Series, losses: dict[str, np.ndarray], static_loss: np.ndarray) -> dict[str, Any]:
    group_values = list(dict.fromkeys(groups.tolist()))
    routed = np.empty(len(groups), dtype=float)
    rows: list[dict[str, Any]] = []
    loss_matrix = np.column_stack([losses[n] for n in EXPERTS])
    oracle_idx = np.argmin(loss_matrix, axis=1)
    oracle_loss = np.min(loss_matrix, axis=1)

    for g in group_values:
        mask = groups.to_numpy() == g
        means = {n: float(np.mean(losses[n][mask])) for n in EXPERTS}
        best = min(EXPERTS, key=lambda n: means[n])
        routed[mask] = losses[best][mask]
        winners = {n: int(np.sum(oracle_idx[mask] == i)) for i, n in enumerate(EXPERTS)}
        rows.append(
            {
                "group": str(g),
                "n": int(np.sum(mask)),
                "mean_loss": means,
                "best_group_expert": best,
                "best_group_mean_loss": float(means[best]),
                "row_oracle_winner_counts": winners,
                "row_oracle_gain_vs_static_mean": float(np.mean(static_loss[mask] - oracle_loss[mask])),
                "row_oracle_gap_contribution_to_all_rows": float(np.sum(static_loss[mask] - oracle_loss[mask]) / len(groups)),
            }
        )

    static_ll = float(np.mean(static_loss))
    routed_ll = float(np.mean(routed))
    oracle_ll = float(np.mean(oracle_loss))
    full_gap = static_ll - oracle_ll
    routed_gain = static_ll - routed_ll
    return {
        "groups": rows,
        "static_logloss": static_ll,
        "group_router_logloss": routed_ll,
        "group_router_gain_vs_static": routed_gain,
        "full_row_oracle_logloss": oracle_ll,
        "full_row_oracle_gap_vs_static": full_gap,
        "fraction_of_full_oracle_gap_captured_by_true_group": float(routed_gain / full_gap) if full_gap > 0 else None,
        "uses_realized_label_information": True,
        "deployable": False,
    }


def run() -> dict[str, Any]:
    cfg = load_json(DEFAULT_CONFIG)
    parent_cfg = load_json(DEFAULT_PARENT_CONFIG)
    parent = load_json(DEFAULT_PARENT_STATUS)
    cohort = pd.read_csv(DEFAULT_PARENT_COHORT)
    pcfg = cfg["parent_diagnostic"]

    if len(cohort) != int(pcfg["diagnostic_rows_to_exclude"]):
        raise ResearchError("PARENT_DIAGNOSTIC_ROW_COUNT_MISMATCH")
    if parent["cohort"]["identity_sha256"] != str(pcfg["diagnostic_1000_identity_sha256"]):
        raise ResearchError("PARENT_DIAGNOSTIC_IDENTITY_MISMATCH")
    excluded_ids = set(cohort.identity_key.astype(str))

    frame, base_cfg, _fcfg, _jcfg, fnames, jnames, common_ok, _seasons = build_frame()
    core = select_core_features(frame)
    C = float(cfg["expert_contract"]["fixed_C"])

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

    fit_target = frame[common_ok & frame.split.isin(["train", "policy"])].copy()
    p_eval = fit_experts(fit_target, evaluation, core, fnames, jnames, C, base_cfg)
    y = evaluation.total_class.to_numpy(int)
    losses = {n: _true_loss(y, p_eval[n]) for n in EXPERTS}
    static_name = "common_baseline"
    static_loss = losses[static_name]
    loss_matrix = np.column_stack([losses[n] for n in EXPERTS])
    oracle_idx = np.argmin(loss_matrix, axis=1)
    oracle_loss = np.min(loss_matrix, axis=1)

    static_ll = float(np.mean(static_loss))
    oracle_ll = float(np.mean(oracle_loss))
    if abs(static_ll - EXPECTED_STATIC_LL) > 1e-12:
        raise ResearchError(f"STATIC_LL_REPRODUCTION_MISMATCH:{static_ll}")
    if abs(oracle_ll - EXPECTED_ORACLE_LL) > 1e-12:
        raise ResearchError(f"ORACLE_LL_REPRODUCTION_MISMATCH:{oracle_ll}")

    per_match = evaluation[["identity_key", "competition_id", "season", "date_key", "home_team", "away_team", "total_class"]].copy()
    for n in EXPERTS:
        per_match[f"loss_{n}"] = losses[n]
        for c in range(8):
            per_match[f"{n}_pT_{c if c < 7 else '7plus'}"] = p_eval[n][:, c]
    per_match["oracle_expert"] = [EXPERTS[i] for i in oracle_idx]
    per_match["oracle_loss"] = oracle_loss
    per_match["oracle_gain_vs_common_baseline"] = static_loss - oracle_loss
    per_match["true_total_regime_0_3_vs_4plus"] = np.where(y <= 3, "0-3", "4+")

    t_labels = pd.Series([str(int(v)) if int(v) < 7 else "7+" for v in y], index=evaluation.index)
    coarse = pd.Series(np.where(y <= 3, "0-3", "4+"), index=evaluation.index)
    by_t = _router_summary(t_labels.reset_index(drop=True), losses, static_loss)
    by_coarse = _router_summary(coarse.reset_index(drop=True), losses, static_loss)

    result = {
        "schema_version": "DIRECT_T_ORACLE_TOTAL_ANATOMY_R1.0",
        "status": "POSTVIEW_DIRECT_T_ORACLE_TOTAL_ANATOMY_COMPLETE_NO_PROMOTION",
        "source_contract": {
            "parent_pr": 179,
            "parent_head": "7abd9a3ebb8ce49742549cbaefefc2aef6920672",
            "primary_rows": int(len(evaluation)),
            "primary_identity_sha256": identity_sha,
            "experts": list(EXPERTS),
            "static_comparator": static_name,
            "fixed_C": C,
        },
        "reproduction": {
            "static_logloss": static_ll,
            "full_row_oracle_logloss": oracle_ll,
            "full_row_oracle_gap": float(static_ll - oracle_ll),
            "r40f_frozen_summary_reproduced": True,
        },
        "true_total_class_router": by_t,
        "true_total_coarse_router": by_coarse,
        "boundary": {
            "retrospective_viewed_only": True,
            "realized_total_used_only_after_reconstruction": True,
            "selector_fit": False,
            "threshold_search": False,
            "new_data_collection": 0,
            "provider_requests": 0,
            "paid_api_requests": 0,
            "protected_sample_consumption": 0,
            "formal_weight": 0,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
            "promotion_allowed": False,
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    per_match.to_csv(OUT_DIR / "per_match.csv", index=False)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (OUT_DIR / "summary.json").write_text(text, encoding="utf-8")
    (OUT_DIR / "summary.sha256").write_text(hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="ascii")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
