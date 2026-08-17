#!/usr/bin/env python3
"""Strict-PIT test of timestamped Hugging Face O/U 2.5 information on a fixed 500.

The test identities are selected deterministically without labels by the R2 matchability audit.
The primary test uses the latest valid O/U quote at or before T-90m, no older than 24h.
T-5m is a same-source sensitivity test, not an independent confirmation cohort.

Direct-T fitting and regularization selection use only historical train/policy folds. Historical
O/U used for fitting comes from repository-local processed columns and lacks original quote
timestamps, so this run can establish a strict-PIT *test* replication but cannot by itself
establish a fully strict-PIT training-to-test promotion result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from diagnose_fixed500_existing_market_pack_t_r1 import add_identity_key, fit_total, logit, materialize_market
from evaluate_direct_t_gd_joint_fixed200_r1 import load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import attach_exact_total, paired_bootstrap
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
OUT = ROOT / "manifests" / "hf_odds_fixed500_direct_t_r1.json"
ROWS_OUT = ROOT / "manifests" / "hf_odds_fixed500_direct_t_r1_rows.csv"
T90 = ROOT / "manifests" / "hf_odds_pit_fixed500_t90_r2.csv"
T5 = ROOT / "manifests" / "hf_odds_pit_fixed500_t5_r2.csv"
TOTAL_CLASSES = list(range(8))
OU_FEATURE = "mkt_ou_over_logit"
TOP5 = {"ENG_PremierLeague", "ESP_LaLiga", "ITA_SerieA", "GER_Bundesliga", "FRA_Ligue1"}
IDENTITY_COLS = ["competition_id", "season", "date_key", "home_team", "away_team"]
TARGET_SEASON = "2023/24"


def digest_ids(frame: pd.DataFrame) -> str:
    ids = frame[IDENTITY_COLS].astype(str).agg("|".join, axis=1).sort_values().tolist()
    return hashlib.sha256(("\n".join(ids) + "\n").encode("utf-8")).hexdigest()


def load_fixed(path: Path, freeze_minutes: int) -> pd.DataFrame:
    # Intentionally do not load score/result columns from the fixed-sample artifact.
    use = [
        "league_id", "kickoff_utc", "home_team", "away_team", "freeze_minutes",
        "quote_utc", "quote_age_to_cutoff_min", "fair_over_2.5", "fair_under_2.5",
    ]
    x = pd.read_csv(path, usecols=use)
    if len(x) != 500:
        raise ResearchError(f"{path.name}: expected fixed500, got {len(x)}")
    if set(x.freeze_minutes.astype(int)) != {freeze_minutes}:
        raise ResearchError(f"{path.name}: freeze mismatch")
    if (x.quote_age_to_cutoff_min.astype(float) < -1e-9).any() or (x.quote_age_to_cutoff_min.astype(float) > 1440.0001).any():
        raise ResearchError(f"{path.name}: quote-age gate violation")
    x = x.rename(columns={"league_id": "competition_id"})
    x["season"] = TARGET_SEASON
    # football-data.co.uk Date/Time and the HF source are matched on the Europe/London clock.
    # date_key in the repository ledger is ISO YYYY-MM-DD, so derive it from the frozen kickoff.
    x["date_key"] = (
        pd.to_datetime(x["kickoff_utc"], utc=True)
        .dt.tz_convert("Europe/London")
        .dt.strftime("%Y-%m-%d")
    )
    x[OU_FEATURE] = x["fair_over_2.5"].map(logit)
    if x[IDENTITY_COLS].duplicated().any():
        raise ResearchError(f"{path.name}: duplicate fixture identity")
    return x


def high_t_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    truth = (np.asarray(y, dtype=int) >= 4).astype(int)
    prob = np.asarray(p, dtype=float)[:, 4:].sum(axis=1)
    pred = (prob >= 0.5).astype(int)
    auc = float(roc_auc_score(truth, prob)) if len(np.unique(truth)) == 2 else float("nan")
    return {
        "actual_high_t_n": int(truth.sum()),
        "actual_high_t_rate": float(truth.mean()),
        "mean_predicted_high_t_probability": float(prob.mean()),
        "auc": auc,
        "threshold_0_5_calls": int(pred.sum()),
        "threshold_0_5_recall": float(((pred == 1) & (truth == 1)).sum() / max(1, truth.sum())),
    }


def evaluate_one(
    fold: pd.DataFrame,
    fit: pd.DataFrame,
    train: pd.DataFrame,
    policy: pd.DataFrame,
    core: list[str],
    fixed: pd.DataFrame,
    config: dict[str, Any],
    freeze_minutes: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    test_cols = IDENTITY_COLS + ["total_class", "split"] + core
    right = fold[
        fold.competition_id.isin(TOP5)
        & fold.season.astype(str).eq(TARGET_SEASON)
        & fold.split.eq("test")
    ][test_cols].copy()
    if right[IDENTITY_COLS].duplicated().any():
        examples = right[right[IDENTITY_COLS].duplicated(False)][IDENTITY_COLS].head(20).to_dict("records")
        raise ResearchError(f"T-{freeze_minutes}: exact ledger identities duplicated: {examples}")

    sample = fixed.merge(right, on=IDENTITY_COLS, how="left", validate="one_to_one")
    if len(sample) != 500 or sample.total_class.isna().any():
        missing = sample[sample.total_class.isna()][IDENTITY_COLS].head(20).to_dict("records")
        raise ResearchError(f"T-{freeze_minutes}: fixed500 reconstruction failure; missing={missing}")
    if set(sample.split.astype(str)) != {"test"}:
        counts = sample.split.value_counts(dropna=False).to_dict()
        raise ResearchError(f"T-{freeze_minutes}: sample not wholly in test fold: {counts}")

    p_core, rec_core = fit_total(fit, train, policy, sample, core, config)
    p_ou, rec_ou = fit_total(fit, train, policy, sample, core + [OU_FEATURE], config)
    y = sample.total_class.to_numpy(int)
    comp_core = metric_components(y, p_core, TOTAL_CLASSES)
    comp_ou = metric_components(y, p_ou, TOTAL_CLASSES)
    met_core = metric_summary(comp_core)
    met_ou = metric_summary(comp_ou)
    delta = {k: float(met_ou[k] - met_core[k]) for k in met_core}
    boot = {
        m: paired_bootstrap(
            comp_ou[m].to_numpy(float) - comp_core[m].to_numpy(float),
            5000,
            861000 + freeze_minutes * 10 + i,
        )
        for i, m in enumerate(("logloss", "brier", "rps"))
    }
    out = {
        "freeze_minutes": freeze_minutes,
        "sample_n": 500,
        "identity_sha256": digest_ids(sample),
        "quote_age_minutes": {
            "mean": float(sample.quote_age_to_cutoff_min.mean()),
            "median": float(sample.quote_age_to_cutoff_min.median()),
            "p90": float(sample.quote_age_to_cutoff_min.quantile(.9)),
            "max": float(sample.quote_age_to_cutoff_min.max()),
            "within_60m": int((sample.quote_age_to_cutoff_min <= 60).sum()),
            "within_360m": int((sample.quote_age_to_cutoff_min <= 360).sum()),
        },
        "core": met_core,
        "core_plus_timestamped_ou": met_ou,
        "delta_ou_minus_core": delta,
        "paired_bootstrap_delta": boot,
        "high_t": {
            "core": high_t_metrics(y, p_core),
            "core_plus_timestamped_ou": high_t_metrics(y, p_ou),
        },
        "fit_receipt": {"core": rec_core, "core_plus_timestamped_ou": rec_ou},
    }

    rows = sample[IDENTITY_COLS + ["kickoff_utc", "quote_utc", "quote_age_to_cutoff_min", "fair_over_2.5", "total_class"]].copy()
    rows["freeze_minutes"] = freeze_minutes
    rows["core_pred_T"] = np.argmax(p_core, axis=1)
    rows["ou_pred_T"] = np.argmax(p_ou, axis=1)
    rows["core_p_T_ge4"] = p_core[:, 4:].sum(axis=1)
    rows["ou_p_T_ge4"] = p_ou[:, 4:].sum(axis=1)
    for j in TOTAL_CLASSES:
        rows[f"core_p_T{j}"] = p_core[:, j]
        rows[f"ou_p_T{j}"] = p_ou[:, j]
    return out, rows


def run() -> dict[str, Any]:
    config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = add_identity_key(build_features(raw))
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)

    test_position = 2
    if test_position not in [int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"]]:
        raise ResearchError("test position 2 not allowed by rolling contract")
    base["split"] = assign_fold(base, seasons, test_position)
    fold = attach_exact_total(base, raw)

    market = materialize_market(raw)
    fold = fold.merge(market[["identity_key", OU_FEATURE]], on="identity_key", how="left", validate="one_to_one")
    history = fold[
        fold.competition_id.isin(TOP5)
        & fold.split.isin(["train", "policy"])
        & fold[OU_FEATURE].notna()
    ].copy()
    train = history[history.split == "train"].copy()
    policy = history[history.split == "policy"].copy()
    if min(len(train), len(policy)) < 500:
        raise ResearchError(f"historical OU train/policy too small: train={len(train)} policy={len(policy)}")

    fixed90 = load_fixed(T90, 90)
    fixed5 = load_fixed(T5, 5)
    e90, r90 = evaluate_one(fold, history, train, policy, core, fixed90, config, 90)
    e5, r5 = evaluate_one(fold, history, train, policy, core, fixed5, config, 5)

    ids90 = set(map(tuple, fixed90[IDENTITY_COLS].astype(str).to_numpy()))
    ids5 = set(map(tuple, fixed5[IDENTITY_COLS].astype(str).to_numpy()))
    overlap = len(ids90 & ids5)

    primary = e90["paired_bootstrap_delta"]["logloss"]
    replicated = (
        e90["delta_ou_minus_core"]["logloss"] < 0
        and float(primary["p95"]) < 0
        and e90["delta_ou_minus_core"]["brier"] <= 0
    )
    verdict = "STRICT_PIT_TEST_SIGNAL_REPLICATED_RESEARCH_ONLY" if replicated else "STRICT_PIT_TEST_SIGNAL_NOT_REPLICATED"

    result = {
        "schema_version": "HF_ODDS_FIXED500_DIRECT_T_R1",
        "classification": "RESEARCH_ONLY_STRICT_PIT_TEST_WITH_RETROSPECTIVE_HISTORICAL_OU_FIT",
        "scientific_verdict": verdict,
        "scientific_question": "Does a timestamped pre-match O/U 2.5 snapshot add Direct-T information beyond the existing historical core on a new fixed500?",
        "sample": {
            "season": TARGET_SEASON,
            "competitions": sorted(TOP5),
            "primary_t90_n": 500,
            "sensitivity_t5_n": 500,
            "t90_t5_identity_overlap_n": overlap,
            "t5_is_independent_confirmation": False,
            "selection_used_result_labels": False,
        },
        "historical_fit": {
            "train_rows": int(len(train)),
            "policy_rows": int(len(policy)),
            "fit_rows": int(len(history)),
            "fit_competitions": sorted(TOP5),
            "market_training_timestamp_provenance": "UNAVAILABLE_REPOSITORY_RETROSPECTIVE_REFERENCE",
            "test_market_timestamp_provenance": "HF_TIMESTAMPED_PREMATCH",
        },
        "primary_t90": e90,
        "sensitivity_t5": e5,
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "interpretation_guard": {
            "strict_pit_test_quotes": True,
            "all_test_quotes_at_or_before_freeze": True,
            "test_result_labels_not_used_for_selection_or_tuning": True,
            "fully_strict_pit_training_to_test_claim": False,
            "can_authorize_formal_promotion": False,
            "t5_same_source_sensitivity_not_independent_replication": True,
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.concat([r90, r5], ignore_index=True).to_csv(ROWS_OUT, index=False)
    return result


def main() -> None:
    x = run()
    print(json.dumps({
        "scientific_verdict": x["scientific_verdict"],
        "sample": x["sample"],
        "historical_fit": x["historical_fit"],
        "primary_t90": x["primary_t90"],
        "sensitivity_t5": x["sensitivity_t5"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
