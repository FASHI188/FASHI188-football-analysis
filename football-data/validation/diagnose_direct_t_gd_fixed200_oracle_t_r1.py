#!/usr/bin/env python3
"""Post-result diagnostic: replace predicted T with realized T on the SAME fixed200.

This is explicitly exploratory diagnosis, not a new confirmation experiment. Its only
purpose is to isolate whether the zero natural Draw calls come mainly from Direct-T
uncertainty or from P(GD|T,X) itself. No new sample is consumed.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_direct_t_gd_joint_fixed200_r1 import (
    KEYS,
    LABELS,
    conditional_probabilities,
    hda_metrics,
    load_config,
    row_identity,
    sample_fixed200,
    score_metrics,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "direct_t_gd_fixed200_oracle_t_diagnostic_r1.json"
ROWS_OUT = ROOT / "manifests" / "direct_t_gd_fixed200_oracle_t_diagnostic_r1_rows.csv"


def run() -> dict:
    config = load_config()
    ledger = ROOT / str(config["input_ledger"])
    raw = pd.read_csv(ledger)
    audit_data_identity(raw, config)
    features = build_features(raw)
    feature_names = select_core_features(features)
    seasons, _ = complete_seasons(raw, config)
    test_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    fold = features.copy()
    fold["split"] = assign_fold(fold, seasons, test_position)
    fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
    sample, sample_hash = sample_fixed200(fold[fold.split == "test"].copy())

    actual = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals", "goal_difference"]].copy()
    actual["season"] = actual["season"].astype(str)
    sample["season"] = sample["season"].astype(str)
    sample = sample.merge(actual, on=KEYS, how="left", validate="one_to_one", suffixes=("", "_actual"))
    if sample[["home_goals_90", "away_goals_90"]].isna().any().any():
        raise ResearchError("final-score join failure")

    cond_model, cond_base, _ = conditional_probabilities(fold, sample, feature_names, config)

    rows = sample[KEYS + ["home_goals_90", "away_goals_90", "total_goals", "goal_difference"]].copy()
    rows["match_identity"] = rows.apply(row_identity, axis=1)
    rows["actual_total"] = rows["total_goals"].astype(int)
    rows["actual_gd"] = rows["goal_difference"].astype(int)
    rows["actual_score"] = rows["home_goals_90"].astype(int).astype(str) + ":" + rows["away_goals_90"].astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))

    def oracle_frame(cond_source: dict[int, tuple[list[int], np.ndarray]], prefix: str) -> None:
        ph, pd_, pa = [], [], []
        pred_result, pred_score, top3 = [], [], []
        gd0_prob, gd0_is_argmax = [], []
        for i, row in rows.iterrows():
            actual_t = int(row.actual_total)
            bucket = min(actual_t, 7)
            classes, probs_all = cond_source[bucket]
            probs = probs_all[i]
            h = float(sum(p for g, p in zip(classes, probs) if g > 0))
            d = float(sum(p for g, p in zip(classes, probs) if g == 0))
            a = float(sum(p for g, p in zip(classes, probs) if g < 0))
            total_mass = h + d + a
            if total_mass <= 0:
                raise ResearchError("oracle-T HDA mass failure")
            h, d, a = h / total_mass, d / total_mass, a / total_mass
            ph.append(h); pd_.append(d); pa.append(a)
            pred_result.append(LABELS[int(np.argmax([h, d, a]))])
            if 0 in classes:
                zero_pos = classes.index(0)
                gd0_prob.append(float(probs[zero_pos]))
                gd0_is_argmax.append(int(zero_pos == int(np.argmax(probs))))
            else:
                gd0_prob.append(0.0); gd0_is_argmax.append(0)

            if actual_t <= 6:
                ranked = sorted(zip(classes, probs), key=lambda kv: (-float(kv[1]), int(kv[0])))
                legal_scores = []
                for gd, p in ranked[:3]:
                    hg = (actual_t + int(gd)) // 2
                    ag = (actual_t - int(gd)) // 2
                    legal_scores.append((f"{hg}:{ag}", float(p)))
                pred_score.append(legal_scores[0][0])
                top3.append(";".join(x[0] for x in legal_scores))
            else:
                pred_score.append("TAIL7+")
                top3.append("TAIL7+")

        rows[f"{prefix}_p_home"] = ph
        rows[f"{prefix}_p_draw"] = pd_
        rows[f"{prefix}_p_away"] = pa
        rows[f"{prefix}_pred_result"] = pred_result
        rows[f"{prefix}_pred_score"] = pred_score
        rows[f"{prefix}_pred_score_top3"] = top3
        rows[f"{prefix}_gd0_probability_given_actual_T"] = gd0_prob
        rows[f"{prefix}_gd0_argmax_given_actual_T"] = gd0_is_argmax

    oracle_frame(cond_model, "oracle_model")
    oracle_frame(cond_base, "oracle_baseline")

    model_hda = hda_metrics(rows, "oracle_model")
    base_hda = hda_metrics(rows, "oracle_baseline")
    model_score = score_metrics(rows, "oracle_model")
    base_score = score_metrics(rows, "oracle_baseline")

    draws = rows[rows.actual_result == "D"].copy()
    non_draws = rows[rows.actual_result != "D"].copy()
    result = {
        "schema_version": "DIRECT_T_GD_FIXED200_ORACLE_T_DIAGNOSTIC_R1",
        "classification": "POST_RESULT_SAME_SAMPLE_DIAGNOSTIC_ONLY",
        "sample_n": 200,
        "sample_identity_sha256": sample_hash,
        "new_sample_consumed": False,
        "question": "If T were known exactly, would the existing conditional GD component naturally recover Draw Top-1?",
        "oracle_hda": {
            "model": model_hda,
            "baseline": base_hda,
            "accuracy_delta_pp": (model_hda["accuracy"] - base_hda["accuracy"]) * 100.0,
            "log_loss_delta": model_hda["log_loss"] - base_hda["log_loss"],
            "draw_f1_delta_pp": (model_hda["draw_f1"] - base_hda["draw_f1"]) * 100.0,
        },
        "oracle_exact_score": {
            "model": model_score,
            "baseline": base_score,
            "top1_delta_pp_all200": (model_score["top1_accuracy_all200"] - base_score["top1_accuracy_all200"]) * 100.0,
            "top3_delta_pp_all200": (model_score["top3_accuracy_all200"] - base_score["top3_accuracy_all200"]) * 100.0,
        },
        "draw_decomposition": {
            "actual_draws": int(len(draws)),
            "oracle_model_draw_calls": int((rows.oracle_model_pred_result == "D").sum()),
            "oracle_model_draw_hits": int(((rows.oracle_model_pred_result == "D") & (rows.actual_result == "D")).sum()),
            "actual_draws_with_gd0_argmax_given_actual_T": int(draws.oracle_model_gd0_argmax_given_actual_T.sum()),
            "non_draws_with_gd0_argmax_given_actual_T": int(non_draws.oracle_model_gd0_argmax_given_actual_T.sum()),
            "mean_gd0_probability_actual_draws": float(draws.oracle_model_gd0_probability_given_actual_T.mean()),
            "mean_gd0_probability_non_draws": float(non_draws.oracle_model_gd0_probability_given_actual_T.mean()),
            "draw_score_breakdown": {str(k): int(v) for k, v in draws.actual_score.value_counts().sort_index().to_dict().items()},
        },
        "interpretation_rule": {
            "if_oracle_draw_calls_positive": "Direct-T uncertainty/parity dilution is a material bottleneck; improve T-even/level coupling before changing GD head.",
            "if_oracle_draw_calls_zero": "Conditional GD itself still cannot make GD=0 Top-1; a new joint/diagonal architecture is required.",
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "formal_asset_changes": 0,
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows.to_csv(ROWS_OUT, index=False, encoding="utf-8")
    return result


def main() -> None:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
