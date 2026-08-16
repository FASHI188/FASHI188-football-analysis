#!/usr/bin/env python3
"""Exploratory fixed-200 joint score matrix from Direct-T and P(GD|T,X).

Research-only on already-viewed historical data. The 200 rows are selected only by
match identity hash from the latest rolling test fold; labels do not select rows.
No formal promotion, no current-match prediction, no provider request.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import (
    align_probability,
    empirical_probability,
    make_model,
    select_C,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v510_historical_label_structure_rolling_r1.json"
OUT = ROOT / "manifests" / "direct_t_gd_joint_fixed200_r1_status.json"
ROWS_OUT = ROOT / "manifests" / "direct_t_gd_joint_fixed200_r1_rows.csv"

KEYS = ["competition_id", "season", "date_key", "home_team", "away_team"]
LABELS = ("H", "D", "A")


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be object")
    return value


def row_identity(row: pd.Series) -> str:
    return "|".join(str(row[k]) for k in KEYS)


def identity_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def sample_fixed200(test: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    if len(test) < 200:
        raise ResearchError(f"latest test fold has only {len(test)} rows")
    sample = test.copy()
    sample["match_identity"] = sample.apply(row_identity, axis=1)
    sample["identity_hash"] = sample["match_identity"].map(identity_hash)
    sample = sample.sort_values(["identity_hash", "match_identity"]).head(200).copy()
    if sample["match_identity"].nunique() != 200:
        raise ResearchError("fixed200 identity uniqueness failure")
    digest = hashlib.sha256(("\n".join(sorted(sample["match_identity"])) + "\n").encode("utf-8")).hexdigest()
    return sample, digest


def direct_total_probabilities(
    fold: pd.DataFrame,
    sample: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train = fold[fold.split == "train"]
    policy = fold[fold.split == "policy"]
    fit = fold[fold.split.isin(["train", "policy"])]
    classes = [int(x) for x in config["model_contract"]["direct_total_classes"]]
    selected_C, policy_grid = select_C(train, policy, features, "total_class", classes, config)
    model = make_model(selected_C, config)
    model.fit(fit[features], fit.total_class)
    model_p = align_probability(model, sample[features], classes)
    baseline_p = empirical_probability(
        fit,
        sample,
        "total_class",
        classes,
        float(config["model_contract"]["competition_empirical_alpha"]),
    )
    return model_p, baseline_p, {
        "selected_C": selected_C,
        "policy_grid": policy_grid,
        "fit_rows": int(len(fit)),
        "probability_sum_max_residual": float(np.max(np.abs(model_p.sum(axis=1) - 1.0))),
    }


def conditional_probabilities(
    fold: pd.DataFrame,
    sample: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[dict[int, tuple[list[int], np.ndarray]], dict[int, tuple[list[int], np.ndarray]], dict[str, Any]]:
    model_out: dict[int, tuple[list[int], np.ndarray]] = {}
    baseline_out: dict[int, tuple[list[int], np.ndarray]] = {}
    receipt: dict[str, Any] = {}
    contract = config["model_contract"]

    for total in range(8):
        train = fold[(fold.split == "train") & (fold.total_class == total)]
        policy = fold[(fold.split == "policy") & (fold.total_class == total)]
        fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_class == total)]
        if total < 7:
            classes = list(range(-total, total + 1, 2))
        else:
            classes = list(range(int(contract["conditional_tail_support_min"]), int(contract["conditional_tail_support_max"]) + 1))

        if len(classes) == 1:
            model_p = np.ones((len(sample), 1), dtype=float)
            baseline_p = model_p.copy()
            status = "DETERMINISTIC"
            selected_C = None
            policy_grid: list[dict[str, Any]] = []
        elif total == 7:
            # T=7+ has no exact total. For H/D/A only, retain the frozen empirical GD reference.
            baseline_p = empirical_probability(
                fit,
                sample,
                "goal_difference",
                classes,
                float(contract["tail_empirical_alpha"]),
            )
            model_p = baseline_p.copy()
            status = "EMPIRICAL_TAIL_REFERENCE"
            selected_C = None
            policy_grid = []
        else:
            selected_C, policy_grid = select_C(train, policy, features, "goal_difference", classes, config)
            model = make_model(selected_C, config)
            model.fit(fit[features], fit.goal_difference)
            model_p = align_probability(model, sample[features], classes)
            baseline_p = empirical_probability(
                fit,
                sample,
                "goal_difference",
                classes,
                float(contract["competition_empirical_alpha"]),
            )
            status = "LOGISTIC_CHALLENGER"

        model_out[total] = (classes, model_p)
        baseline_out[total] = (classes, baseline_p)
        receipt[str(total)] = {
            "status": status,
            "fit_rows": int(len(fit)),
            "selected_C": selected_C,
            "policy_grid": policy_grid,
            "support": classes,
            "model_probability_sum_max_residual": float(np.max(np.abs(model_p.sum(axis=1) - 1.0))),
        }
    return model_out, baseline_out, receipt


def assemble_joint(
    p_total: np.ndarray,
    p_conditional: dict[int, tuple[list[int], np.ndarray]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(len(p_total)):
        score_prob: dict[tuple[int, int], float] = {}
        hda = np.zeros(3, dtype=float)
        for total in range(7):
            classes, cond = p_conditional[total]
            for j, gd in enumerate(classes):
                # Parity and |GD|<=T are guaranteed by the support for exact T<=6.
                home = (total + gd) // 2
                away = (total - gd) // 2
                prob = float(p_total[i, total] * cond[i, j])
                score_prob[(home, away)] = score_prob.get((home, away), 0.0) + prob
                hda[0 if gd > 0 else 1 if gd == 0 else 2] += prob

        tail_classes, tail_cond = p_conditional[7]
        for j, gd in enumerate(tail_classes):
            prob = float(p_total[i, 7] * tail_cond[i, j])
            hda[0 if gd > 0 else 1 if gd == 0 else 2] += prob

        if abs(float(hda.sum()) - 1.0) > 1e-10:
            raise ResearchError("HDA probability conservation failure")
        ranked = sorted(score_prob.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
        top_scores = ranked[:3]
        rows.append({
            "p_home": float(hda[0]),
            "p_draw": float(hda[1]),
            "p_away": float(hda[2]),
            "pred_result": LABELS[int(np.argmax(hda))],
            "pred_score": f"{top_scores[0][0][0]}:{top_scores[0][0][1]}",
            "pred_score_probability": float(top_scores[0][1]),
            "pred_score_top3": ";".join(f"{h}:{a}" for (h, a), _ in top_scores),
            "mapped_exact_score_mass_T0_6": float(1.0 - p_total[i, 7]),
            "tail7plus_mass": float(p_total[i, 7]),
        })
    return rows


def hda_metrics(frame: pd.DataFrame, prefix: str) -> dict[str, Any]:
    actual = frame["actual_result"].astype(str).to_numpy()
    pred = frame[f"{prefix}_pred_result"].astype(str).to_numpy()
    p = frame[[f"{prefix}_p_home", f"{prefix}_p_draw", f"{prefix}_p_away"]].to_numpy(float)
    idx = np.asarray([LABELS.index(x) for x in actual], dtype=int)
    ll = float(-np.log(np.clip(p[np.arange(len(p)), idx], 1e-15, 1.0)).mean())
    acc = float(np.mean(pred == actual))
    tp = int(np.sum((pred == "D") & (actual == "D")))
    fp = int(np.sum((pred == "D") & (actual != "D")))
    fn = int(np.sum((pred != "D") & (actual == "D")))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "accuracy": acc,
        "log_loss": ll,
        "predicted_counts": {label: int(np.sum(pred == label)) for label in LABELS},
        "actual_counts": {label: int(np.sum(actual == label)) for label in LABELS},
        "draw_precision": precision,
        "draw_recall": recall,
        "draw_f1": f1,
        "draw_hits": tp,
    }


def score_metrics(frame: pd.DataFrame, prefix: str) -> dict[str, Any]:
    actual = frame["actual_score"].astype(str)
    top1 = frame[f"{prefix}_pred_score"].astype(str)
    top3 = frame[f"{prefix}_pred_score_top3"].astype(str).str.split(";")
    eligible = frame["actual_total"].astype(int) <= 6
    return {
        "top1_accuracy_all200": float(np.mean(top1 == actual)),
        "top3_accuracy_all200": float(np.mean([a in xs for a, xs in zip(actual, top3)])),
        "actual_T0_6_rows": int(eligible.sum()),
        "top1_accuracy_actual_T0_6": float(np.mean((top1[eligible] == actual[eligible]).to_numpy())) if eligible.any() else None,
        "top3_accuracy_actual_T0_6": float(np.mean([a in xs for a, xs in zip(actual[eligible], top3[eligible])])) if eligible.any() else None,
    }


def run() -> dict[str, Any]:
    config = load_config()
    ledger = ROOT / str(config["input_ledger"])
    if not ledger.is_file():
        raise ResearchError(f"ledger missing: {ledger.relative_to(ROOT)}")
    raw = pd.read_csv(ledger)
    data_identity = audit_data_identity(raw, config)
    features = build_features(raw)
    feature_names = select_core_features(features)
    seasons, excluded = complete_seasons(raw, config)

    test_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    fold = features.copy()
    fold["split"] = assign_fold(fold, seasons, test_position)
    fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
    test = fold[fold.split == "test"].copy()
    sample, sample_hash = sample_fixed200(test)

    # Attach final scores only after the identity-only sample has been frozen.
    actual = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals", "goal_difference"]].copy()
    actual["season"] = actual["season"].astype(str)
    sample["season"] = sample["season"].astype(str)
    sample = sample.merge(actual, on=KEYS, how="left", validate="one_to_one", suffixes=("", "_actual"))
    if sample[["home_goals_90", "away_goals_90"]].isna().any().any():
        raise ResearchError("final-score join failure")

    pT_model, pT_base, direct_receipt = direct_total_probabilities(fold, sample, feature_names, config)
    cond_model, cond_base, cond_receipt = conditional_probabilities(fold, sample, feature_names, config)
    joint_model = assemble_joint(pT_model, cond_model)
    joint_base = assemble_joint(pT_base, cond_base)

    rows = sample[KEYS + ["match_identity", "identity_hash", "home_goals_90", "away_goals_90", "total_goals", "goal_difference"]].copy()
    rows = rows.rename(columns={"total_goals": "actual_total", "goal_difference": "actual_gd"})
    rows["actual_score"] = rows["home_goals_90"].astype(int).astype(str) + ":" + rows["away_goals_90"].astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int), 7)
    rows["model_pred_total_class"] = np.argmax(pT_model, axis=1)
    rows["baseline_pred_total_class"] = np.argmax(pT_base, axis=1)

    for prefix, joint in (("model", joint_model), ("baseline", joint_base)):
        jf = pd.DataFrame(joint)
        for column in jf.columns:
            rows[f"{prefix}_{column}"] = jf[column].to_numpy()

    total_model_acc = float(np.mean(rows.model_pred_total_class == rows.actual_total_class))
    total_base_acc = float(np.mean(rows.baseline_pred_total_class == rows.actual_total_class))
    model_hda = hda_metrics(rows, "model")
    base_hda = hda_metrics(rows, "baseline")
    model_score = score_metrics(rows, "model")
    base_score = score_metrics(rows, "baseline")

    # Diagnostics specifically aimed at the draw problem.
    actual_draws = rows[rows.actual_result == "D"].copy()
    draw_by_score = actual_draws.actual_score.value_counts().sort_index().to_dict()
    model_natural_draws = rows[rows.model_pred_result == "D"]

    result = {
        "schema_version": "DIRECT_T_GD_JOINT_FIXED200_R1",
        "classification": "VIEWED_HISTORICAL_EXPLORATORY_RESEARCH_ONLY",
        "formal_weight": 0,
        "sample": {
            "selection": "lowest SHA256 identity hashes from latest rolling test fold; labels excluded from selection",
            "n": 200,
            "sample_identity_sha256": sample_hash,
            "test_fold": f"window_{test_position - 1}_to_{test_position}",
            "test_pool_rows": int(len(test)),
            "actual_draws": int((rows.actual_result == "D").sum()),
            "actual_draw_score_counts": {str(k): int(v) for k, v in draw_by_score.items()},
            "actual_7plus_rows": int((rows.actual_total >= 7).sum()),
        },
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "feature_count": len(feature_names),
        "architecture": {
            "direct_total": "P(T=0..6,7+|X)",
            "conditional_goal_difference": "P(GD|T,X), exact legal parity support for T=0..6; empirical sign reference for T=7+",
            "joint": "P(T|X) * P(GD|T,X)",
            "score_mapping": "H=(T+GD)/2, A=(T-GD)/2 for exact T<=6",
            "forced_draw": False,
            "manual_threshold": False,
            "class_weight_override": False,
        },
        "direct_total_fit": direct_receipt,
        "conditional_fit": cond_receipt,
        "metrics": {
            "direct_total_top1": {
                "model": total_model_acc,
                "baseline": total_base_acc,
                "delta_pp": (total_model_acc - total_base_acc) * 100.0,
            },
            "hda": {
                "model": model_hda,
                "baseline": base_hda,
                "accuracy_delta_pp": (model_hda["accuracy"] - base_hda["accuracy"]) * 100.0,
                "draw_f1_delta_pp": (model_hda["draw_f1"] - base_hda["draw_f1"]) * 100.0,
                "log_loss_delta": model_hda["log_loss"] - base_hda["log_loss"],
            },
            "exact_score": {
                "model": model_score,
                "baseline": base_score,
                "top1_delta_pp_all200": (model_score["top1_accuracy_all200"] - base_score["top1_accuracy_all200"]) * 100.0,
                "top3_delta_pp_all200": (model_score["top3_accuracy_all200"] - base_score["top3_accuracy_all200"]) * 100.0,
            },
        },
        "draw_diagnostics": {
            "model_natural_top1_draw_calls": int(len(model_natural_draws)),
            "model_natural_top1_draw_hits": int(np.sum(model_natural_draws.actual_result == "D")),
            "model_draw_call_scores_top10": model_natural_draws.model_pred_score.value_counts().head(10).to_dict(),
            "no_forced_draw": True,
        },
        "limitations": [
            "The 200 rows are already-viewed historical research data, not confirmation or blind holdout.",
            "Exact score mapping is explicit only for T<=6; T=7+ contributes to H/D/A through an empirical GD sign reference.",
            "This run tests whether the unified architecture naturally produces useful score/HDA structure; it cannot authorize promotion.",
        ],
        "governance": {
            "provider_requests": 0,
            "new_data_collection": False,
            "formal_model_changes": 0,
            "formal_data_changes": 0,
            "config_changes": 0,
            "CURRENT_changes": 0,
            "main_changes": 0,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows.to_csv(ROWS_OUT, index=False, encoding="utf-8")
    return result


def self_test() -> None:
    pT = np.zeros((1, 8), dtype=float)
    pT[0, 4] = 1.0
    cond: dict[int, tuple[list[int], np.ndarray]] = {}
    for t in range(7):
        classes = list(range(-t, t + 1, 2))
        probs = np.zeros((1, len(classes)), dtype=float)
        probs[0, 0] = 1.0
        cond[t] = (classes, probs)
    tail_classes = list(range(-30, 31))
    tail_probs = np.zeros((1, len(tail_classes)), dtype=float)
    tail_probs[0, tail_classes.index(0)] = 1.0
    cond[7] = (tail_classes, tail_probs)
    # Override T=4 conditional to GD=+2 => 3:1.
    c4 = list(range(-4, 5, 2))
    q4 = np.zeros((1, len(c4)), dtype=float)
    q4[0, c4.index(2)] = 1.0
    cond[4] = (c4, q4)
    out = assemble_joint(pT, cond)[0]
    assert out["pred_score"] == "3:1"
    assert out["pred_result"] == "H"
    assert abs(out["p_home"] - 1.0) < 1e-12
    print(json.dumps({"status": "PASS", "self_test": True, "example": out}, ensure_ascii=False))


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run()
    print(json.dumps({
        "status": result["classification"],
        "sample": result["sample"],
        "metrics": result["metrics"],
        "draw_diagnostics": result["draw_diagnostics"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
