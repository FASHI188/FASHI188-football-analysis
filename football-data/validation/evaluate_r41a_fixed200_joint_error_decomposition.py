#!/usr/bin/env python3
"""R41A: fixed-200 decomposition of joint score-structure error.

Research only. The 200-match sample is selected by identity hash without using labels.
It is retrospective/viewed evidence, not a blind confirmation. No fixed-200 label is
used for model/regularization/threshold selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

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
    select_C,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r41a_fixed200_joint_error_decomposition.json"
DEFAULT_OUT = ROOT / "manifests" / "r41a_fixed200_joint_error_decomposition_status.json"
IDENTITY_FIELDS = ["competition_id", "season", "date_key", "home_team", "away_team"]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError(f"config root must be object: {path}")
    return value


def identity_key(row: Any) -> str:
    return "|".join(str(getattr(row, field)) for field in IDENTITY_FIELDS)


def add_identity_key(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["identity_key"] = out[IDENTITY_FIELDS].astype(str).agg("|".join, axis=1)
    return out


def select_fixed_identities(pool: pd.DataFrame, size: int, seed: int) -> tuple[list[str], str]:
    identities = sorted(set(pool["identity_key"].astype(str)))
    if len(identities) < size:
        raise ResearchError(f"target pool too small: {len(identities)} < {size}")
    ranked = sorted(
        identities,
        key=lambda value: (hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest(), value),
    )
    selected = sorted(ranked[:size])
    digest = hashlib.sha256(("\n".join(selected) + "\n").encode("utf-8")).hexdigest()
    return selected, digest


def split_for_latest_complete(features: pd.DataFrame, seasons: dict[str, list[str]], cfg: dict[str, Any]) -> pd.Series:
    fit_cfg = cfg["fit_contract"]
    train_positions = {int(x) for x in fit_cfg["train_season_positions_zero_based"]}
    policy_position = int(fit_cfg["policy_season_position_zero_based"])
    test_position = int(fit_cfg["test_season_position_zero_based"])
    values: list[str] = []
    for row in features[["competition_id", "season"]].itertuples(index=False):
        sequence = seasons[str(row.competition_id)]
        season = str(row.season)
        split = "excluded"
        if season in {sequence[pos] for pos in train_positions}:
            split = "train"
        elif season == sequence[policy_position]:
            split = "policy"
        elif season == sequence[test_position]:
            split = "target_pool"
        values.append(split)
    return pd.Series(values, index=features.index)


def true_probability(y: np.ndarray, probabilities: np.ndarray, classes: list[int]) -> np.ndarray:
    positions = {int(value): idx for idx, value in enumerate(classes)}
    idx = np.asarray([positions[int(value)] for value in y], dtype=int)
    return probabilities[np.arange(len(y)), idx]


def direct_predictions(
    fold: pd.DataFrame,
    test: pd.DataFrame,
    feature_names: list[str],
    base_cfg: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train = fold[fold.split == "train"]
    policy = fold[fold.split == "policy"]
    fit = fold[fold.split.isin(["train", "policy"])]
    classes = [int(x) for x in base_cfg["model_contract"]["direct_total_classes"]]
    selected_C, grid = select_C(train, policy, feature_names, "total_class", classes, base_cfg)
    model = make_model(selected_C, base_cfg)
    model.fit(fit[feature_names], fit.total_class)
    model_p = align_probability(model, test[feature_names], classes)
    baseline_p = empirical_probability(
        fit,
        test,
        "total_class",
        classes,
        float(base_cfg["model_contract"]["competition_empirical_alpha"]),
    )
    y = test.total_class.to_numpy(int)
    return (
        true_probability(y, model_p, classes),
        true_probability(y, baseline_p, classes),
        {
            "selected_C": float(selected_C),
            "policy_grid": grid,
            "probability_sum_max_residual": float(np.max(np.abs(model_p.sum(axis=1) - 1.0))),
        },
    )


def conditional_predictions(
    fold: pd.DataFrame,
    test: pd.DataFrame,
    feature_names: list[str],
    base_cfg: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    model_true = pd.Series(index=test.index, dtype=float)
    baseline_true = pd.Series(index=test.index, dtype=float)
    model_draw_given_true_t = pd.Series(index=test.index, dtype=float)
    receipts: dict[str, Any] = {}
    contract = base_cfg["model_contract"]

    for total in TOTAL_CLASSES:
        train = fold[(fold.split == "train") & (fold.total_class == total)]
        policy = fold[(fold.split == "policy") & (fold.total_class == total)]
        fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_class == total)]
        target = test[test.total_class == total]
        classes = (
            list(range(-total, total + 1, 2))
            if total < 7
            else list(range(int(contract["conditional_tail_support_min"]), int(contract["conditional_tail_support_max"]) + 1))
        )
        if not len(target):
            receipts[str(total)] = {"test_rows": 0, "status": "NO_FIXED200_ROWS"}
            continue
        y = target.goal_difference.to_numpy(int)
        unseen = sorted(set(y.tolist()) - set(classes))
        if unseen:
            raise ResearchError(f"conditional support misses target D for T={total}: {unseen}")
        grid: list[dict[str, Any]] = []
        selected_C: float | None = None

        if len(classes) == 1:
            status = "DETERMINISTIC"
            model_p = np.ones((len(target), 1), dtype=float)
            baseline_p = model_p.copy()
        elif total == 7:
            status = "EMPIRICAL_TAIL_BUCKET_NO_EXACT_TOTAL"
            baseline_p = empirical_probability(
                fit,
                target,
                "goal_difference",
                classes,
                float(contract["tail_empirical_alpha"]),
            )
            model_p = baseline_p.copy()
        else:
            status = "LOGISTIC_CHALLENGER"
            selected_C, grid = select_C(train, policy, feature_names, "goal_difference", classes, base_cfg)
            model = make_model(selected_C, base_cfg)
            model.fit(fit[feature_names], fit.goal_difference)
            model_p = align_probability(model, target[feature_names], classes)
            baseline_p = empirical_probability(
                fit,
                target,
                "goal_difference",
                classes,
                float(contract["competition_empirical_alpha"]),
            )

        model_true.loc[target.index] = true_probability(y, model_p, classes)
        baseline_true.loc[target.index] = true_probability(y, baseline_p, classes)
        if 0 in classes:
            model_draw_given_true_t.loc[target.index] = model_p[:, classes.index(0)]
        else:
            model_draw_given_true_t.loc[target.index] = 0.0
        receipts[str(total)] = {
            "test_rows": int(len(target)),
            "status": status,
            "selected_C": selected_C,
            "policy_grid": grid,
            "probability_sum_max_residual": float(np.max(np.abs(model_p.sum(axis=1) - 1.0))),
        }

    if model_true.isna().any() or baseline_true.isna().any() or model_draw_given_true_t.isna().any():
        raise ResearchError("conditional probability assignment incomplete")
    return (
        model_true.loc[test.index].to_numpy(float),
        baseline_true.loc[test.index].to_numpy(float),
        model_draw_given_true_t.loc[test.index].to_numpy(float),
        receipts,
    )


def safe_nll(probability: np.ndarray) -> np.ndarray:
    return -np.log(np.clip(probability.astype(float), 1e-15, 1.0))


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if not len(frame):
        return {"rows": 0}
    total = float(frame.direct_total_nll.mean())
    conditional = float(frame.conditional_D_nll.mean())
    joint = total + conditional
    base_total = float(frame.baseline_direct_total_nll.mean())
    base_cond = float(frame.baseline_conditional_D_nll.mean())
    return {
        "rows": int(len(frame)),
        "direct_total_nll": total,
        "conditional_D_given_true_T_nll": conditional,
        "joint_factorized_nll": joint,
        "direct_total_share_of_joint_nll": (total / joint if joint > 0 else None),
        "rows_direct_total_nll_gt_conditional": int((frame.direct_total_nll > frame.conditional_D_nll).sum()),
        "rows_conditional_nll_gt_direct_total": int((frame.conditional_D_nll > frame.direct_total_nll).sum()),
        "empirical_baseline_direct_total_nll": base_total,
        "empirical_baseline_conditional_D_nll": base_cond,
        "direct_total_relative_skill": (1.0 - total / base_total if base_total > 0 else None),
        "conditional_D_relative_skill": (1.0 - conditional / base_cond if base_cond > 0 else None),
        "mean_nll_difference_total_minus_conditional": float((frame.direct_total_nll - frame.conditional_D_nll).mean()),
    }


def bootstrap_difference(values: np.ndarray, cfg: dict[str, Any], seed_offset: int = 0) -> dict[str, float]:
    n = len(values)
    if n == 0:
        return {"mean": float("nan"), "p05": float("nan"), "p95": float("nan")}
    bcfg = cfg["bootstrap"]
    rng = np.random.default_rng(int(bcfg["seed"]) + seed_offset)
    picks = rng.integers(0, n, size=(int(bcfg["samples"]), n))
    means = values[picks].mean(axis=1)
    q0, q1 = [float(x) for x in bcfg["interval"]]
    return {
        "mean": float(means.mean()),
        "p05": float(np.quantile(means, q0)),
        "p95": float(np.quantile(means, q1)),
        "probability_total_component_larger": float((means > 0).mean()),
    }


def score_breakdown(draws: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not len(draws):
        return rows
    for score, part in draws.groupby("score_label", sort=True):
        rows.append({
            "score": str(score),
            "rows": int(len(part)),
            "mean_P_true_total_bucket": float(part.p_true_total.mean()),
            "mean_P_D0_given_true_total_bucket": float(part.p_D0_given_true_T.mean()),
            "mean_joint_probability_of_realized_draw_cell_or_tail_bucket": float((part.p_true_total * part.p_D0_given_true_T).mean()),
            "mean_direct_total_nll": float(part.direct_total_nll.mean()),
            "mean_conditional_D_nll": float(part.conditional_D_nll.mean()),
        })
    return rows


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    ledger = ROOT / str(cfg["input_ledger"])
    raw = pd.read_csv(ledger)
    data_identity = audit_data_identity(raw, base_cfg)
    seasons, excluded = complete_seasons(raw, base_cfg)

    raw_keyed = add_identity_key(raw)
    target_pool_parts = []
    for competition, sequence in seasons.items():
        target_season = sequence[int(cfg["fit_contract"]["test_season_position_zero_based"])]
        target_pool_parts.append(
            raw_keyed[(raw_keyed.competition_id.astype(str) == str(competition)) & (raw_keyed.season.astype(str) == str(target_season))]
        )
    target_pool = pd.concat(target_pool_parts, ignore_index=True)
    selected_ids, sample_sha = select_fixed_identities(
        target_pool,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )

    features = add_identity_key(build_features(raw))
    feature_names = select_core_features(features)
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    test = features[features.identity_key.isin(set(selected_ids))].copy().sort_values("identity_key")
    if len(test) != int(cfg["sample_contract"]["sample_size"]):
        raise ResearchError(f"fixed200 reproduction failed: {len(test)}")
    if not (test.split == "target_pool").all():
        raise ResearchError("fixed200 contains row outside target season")

    pT, pT_base, direct_receipt = direct_predictions(features, test, feature_names, base_cfg)
    pD, pD_base, pD0, conditional_receipts = conditional_predictions(features, test, feature_names, base_cfg)

    truth_cols = IDENTITY_FIELDS + ["home_goals_90", "away_goals_90", "total_goals", "goal_difference"]
    truth = raw_keyed[truth_cols + ["identity_key"]].copy()
    if truth.identity_key.duplicated().any():
        duplicates = int(truth.identity_key.duplicated(keep=False).sum())
        raise ResearchError(f"non-unique match identity rows: {duplicates}")
    result_rows = test.merge(truth, on=IDENTITY_FIELDS + ["identity_key"], how="left", validate="one_to_one")
    if result_rows[["home_goals_90", "away_goals_90", "total_goals", "goal_difference_y"]].isna().any().any():
        raise ResearchError("truth merge incomplete")

    result_rows["p_true_total"] = pT
    result_rows["p_true_conditional_D"] = pD
    result_rows["p_D0_given_true_T"] = pD0
    result_rows["direct_total_nll"] = safe_nll(pT)
    result_rows["conditional_D_nll"] = safe_nll(pD)
    result_rows["baseline_direct_total_nll"] = safe_nll(pT_base)
    result_rows["baseline_conditional_D_nll"] = safe_nll(pD_base)
    result_rows["is_tail7"] = result_rows.total_goals.astype(int) >= 7
    result_rows["is_draw"] = result_rows.home_goals_90.astype(int) == result_rows.away_goals_90.astype(int)
    result_rows["score_label"] = result_rows.home_goals_90.astype(int).astype(str) + "-" + result_rows.away_goals_90.astype(int).astype(str)

    non_tail = result_rows[~result_rows.is_tail7].copy()
    draws = result_rows[result_rows.is_draw].copy()
    draws_non_tail = draws[~draws.is_tail7].copy()
    non_draws = result_rows[~result_rows.is_draw].copy()

    boot_all = bootstrap_difference((result_rows.direct_total_nll - result_rows.conditional_D_nll).to_numpy(float), cfg, 0)
    boot_core = bootstrap_difference((non_tail.direct_total_nll - non_tail.conditional_D_nll).to_numpy(float), cfg, 1)
    boot_draw = bootstrap_difference((draws_non_tail.direct_total_nll - draws_non_tail.conditional_D_nll).to_numpy(float), cfg, 2)

    if boot_core["p05"] > 0:
        component_verdict = "DIRECT_TOTAL_COMPONENT_LARGER"
    elif boot_core["p95"] < 0:
        component_verdict = "CONDITIONAL_D_COMPONENT_LARGER"
    else:
        component_verdict = "MIXED_OR_UNCERTAIN"

    core_summary = summarize(non_tail)
    direct_skill = core_summary.get("direct_total_relative_skill")
    conditional_skill = core_summary.get("conditional_D_relative_skill")
    if (
        component_verdict == "DIRECT_TOTAL_COMPONENT_LARGER"
        and direct_skill is not None
        and conditional_skill is not None
        and direct_skill < conditional_skill
    ):
        bottleneck = "TOTAL_GOAL_STATE_PRIMARY_BOTTLENECK_SUPPORTED_ON_FIXED200"
    elif (
        component_verdict == "CONDITIONAL_D_COMPONENT_LARGER"
        and direct_skill is not None
        and conditional_skill is not None
        and conditional_skill < direct_skill
    ):
        bottleneck = "CONDITIONAL_MARGIN_PRIMARY_BOTTLENECK_SUPPORTED_ON_FIXED200"
    else:
        bottleneck = "MIXED_OR_ENTROPY_DOMINANCE_ONLY_NO_SINGLE_BOTTLENECK"

    competition_counts = test.groupby("competition_id").size().sort_index()
    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R41A_FIXED200_DIAGNOSTIC_COMPLETE",
        "scientific_verdict": bottleneck,
        "data_identity": data_identity,
        "sample": {
            "rows": int(len(test)),
            "target_pool_rows": int(len(target_pool)),
            "identity_sha256": sample_sha,
            "seed": int(cfg["sample_contract"]["seed"]),
            "competitions_represented": int(test.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in competition_counts.items()},
            "date_min": str(test.date_key.min()),
            "date_max": str(test.date_key.max()),
            "retrospective_viewed_sample": True,
            "blind_claim": False,
            "labels_used_for_identity_selection": False,
        },
        "chronology": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "train_positions": cfg["fit_contract"]["train_season_positions_zero_based"],
            "policy_position": cfg["fit_contract"]["policy_season_position_zero_based"],
            "target_position": cfg["fit_contract"]["test_season_position_zero_based"],
            "same_day_feature_freeze_before_update": True,
            "fixed200_used_for_C_selection": False,
        },
        "model_freeze": {
            "feature_count": int(len(feature_names)),
            "direct_total": direct_receipt,
            "conditional_D_by_true_total_bucket": conditional_receipts,
            "manual_probability_input": False,
            "fixed200_threshold_selection": False,
        },
        "decomposition": {
            "all_200": summarize(result_rows),
            "non_tail_T_lt_7": core_summary,
            "actual_draws": summarize(draws),
            "actual_draws_non_tail": summarize(draws_non_tail),
            "actual_non_draws": summarize(non_draws),
            "tail7_rows": int(result_rows.is_tail7.sum()),
            "tail7_draws": int((result_rows.is_tail7 & result_rows.is_draw).sum()),
            "bootstrap_total_minus_conditional_all_90": boot_all,
            "bootstrap_total_minus_conditional_non_tail_90": boot_core,
            "bootstrap_total_minus_conditional_draws_non_tail_90": boot_draw,
            "component_verdict": component_verdict,
            "relative_skill_interpretation": bottleneck,
        },
        "draw_diagnostics": {
            "actual_draws": int(draws.shape[0]),
            "zero_zero": int(((draws.home_goals_90 == 0) & (draws.away_goals_90 == 0)).sum()),
            "score_breakdown": score_breakdown(draws),
        },
        "interpretation_limits": [
            "Direct-total NLL and conditional-D NLL are additive components of the factorized joint log score, but they have different intrinsic class entropies.",
            "The primary bottleneck ruling therefore also requires the larger component to show weaker relative skill versus its own empirical baseline.",
            "T=7+ remains a grouped tail bucket; the primary verdict uses only T<7 rows.",
            "This is retrospective viewed historical evidence, not an untouched blind confirmation and not a formal current-match model.",
        ],
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    tiny = pd.DataFrame([
        {"competition_id": "A", "season": "1", "date_key": "2024-01-01", "home_team": "H1", "away_team": "A1"},
        {"competition_id": "A", "season": "1", "date_key": "2024-01-02", "home_team": "H2", "away_team": "A2"},
        {"competition_id": "A", "season": "1", "date_key": "2024-01-03", "home_team": "H3", "away_team": "A3"},
    ])
    tiny = add_identity_key(tiny)
    a, h1 = select_fixed_identities(tiny, 2, 41101)
    b, h2 = select_fixed_identities(tiny, 2, 41101)
    assert a == b and h1 == h2 and len(a) == 2
    cfg = {"bootstrap": {"samples": 100, "seed": 1, "interval": [0.05, 0.95]}}
    boot = bootstrap_difference(np.asarray([1.0, 2.0, 3.0]), cfg)
    assert boot["p05"] > 0 and boot["probability_total_component_larger"] == 1.0
    assert np.allclose(safe_nll(np.asarray([1.0])), 0.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = run(load_json(args.config), args.out)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "decomposition": result["decomposition"],
        "draw_diagnostics": result["draw_diagnostics"],
        "governance": result["governance"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
