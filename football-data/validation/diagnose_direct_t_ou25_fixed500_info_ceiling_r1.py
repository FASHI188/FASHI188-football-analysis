#!/usr/bin/env python3
"""Same-fixed500 retrospective OU2.5 information-ceiling diagnosis.

This diagnosis reuses the already-viewed fixed500 from PR #197. It does NOT consume a
new sample and does NOT change the frozen fixed500 verdict. Historical closing/reference
OU2.5 prices have no original quote timestamps, so they are retrospective evidence only.
They cannot establish formal PIT validity or authorize promotion.

Frozen question:
  On the subset of the fixed500 with a valid de-vigged closing/reference OU2.5 quote,
  does one scalar logit(P(Over 2.5)) add information beyond the 47 historical features
  for Direct-T, parity, and the downstream P(T)*P(GD|T,X) score matrix?

No fixed500 label is used for feature, regularization, threshold, or blend selection.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, LABELS, assemble_joint, conditional_probabilities, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import (
    attach_exact_total,
    hda_metrics,
    load_experiment,
    paired_bootstrap,
    sample_fixed_n,
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
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "direct_t_ou25_fixed500_info_ceiling_r1.json"
ROWS_OUT = ROOT / "manifests" / "direct_t_ou25_fixed500_info_ceiling_r1_rows.csv"
TOTAL_CLASSES = list(range(8))
OU_PAIRS = {
    "PS": ("PC>2.5", "PC<2.5"),
    "B365": ("B365C>2.5", "B365C<2.5"),
    "AVG": ("AvgC>2.5", "AvgC<2.5"),
    "MAX": ("MaxC>2.5", "MaxC<2.5"),
}
OU_PREFERENCE = ("PS", "B365", "AVG", "MAX")


def add_identity_key(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["identity_key"] = out[KEYS].astype(str).agg("|".join, axis=1)
    return out


def de_vig_over(over: float, under: float) -> float:
    inv = np.asarray([1.0 / over, 1.0 / under], dtype=float)
    return float(inv[0] / inv.sum())


def materialize_ou25(ledger: pd.DataFrame) -> pd.DataFrame:
    keyed = add_identity_key(ledger)
    wanted: dict[str, dict[int, str]] = {}
    for row in keyed[["source_file", "row_number", "identity_key"]].itertuples(index=False):
        wanted.setdefault(str(row.source_file), {})[int(row.row_number)] = str(row.identity_key)

    records: list[dict[str, Any]] = []
    for source_file in sorted(wanted):
        path = ROOT / source_file
        if not path.is_file():
            continue
        row_map = wanted[source_file]
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            resolved = {
                source: tuple(field_name(headers, [alias]) for alias in OU_PAIRS[source])
                for source in OU_PREFERENCE
            }
            for row_number, row in enumerate(reader, start=2):
                identity = row_map.get(row_number)
                if identity is None:
                    continue
                chosen_prob = None
                chosen_source = None
                for source in OU_PREFERENCE:
                    over_field, under_field = resolved[source]
                    if over_field is None or under_field is None:
                        continue
                    over = float_value(row, over_field)
                    under = float_value(row, under_field)
                    if valid_price(over) and valid_price(under):
                        chosen_prob = de_vig_over(float(over), float(under))
                        chosen_source = source
                        break
                if chosen_prob is not None:
                    records.append({
                        "identity_key": identity,
                        "ou_over_prob": float(chosen_prob),
                        "ou_source": str(chosen_source),
                        "source_file_market": source_file,
                        "row_number_market": int(row_number),
                    })
    if not records:
        raise ResearchError("no OU2.5 reference records materialized")
    frame = pd.DataFrame(records)
    if frame.identity_key.duplicated().any():
        frame = frame.sort_values(["identity_key", "source_file_market", "row_number_market"]).drop_duplicates("identity_key")
    return frame.reset_index(drop=True)


def clean_hda(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metrics.items() if not str(k).startswith("_")}


def parity_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    truth = np.asarray(y, dtype=int)
    pred = np.argmax(p, axis=1)
    idx = truth
    ll = -np.log(np.clip(p[np.arange(len(p)), idx], 1e-15, 1.0))
    onehot = np.zeros_like(p)
    onehot[np.arange(len(p)), idx] = 1.0
    return {
        "accuracy": float(np.mean(pred == truth)),
        "log_loss": float(ll.mean()),
        "brier": float(np.mean(np.sum((p - onehot) ** 2, axis=1))),
        "odd_auc": float(roc_auc_score(truth, p[:, 1])),
        "actual_even_rate": float(np.mean(truth == 0)),
        "mean_p_even": float(np.mean(p[:, 0])),
    }


def run() -> dict[str, Any]:
    exp = load_experiment()
    config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = add_identity_key(build_features(raw))
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    test_position = int(exp["test_position_zero_based"])
    latest_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if test_position >= latest_position:
        raise ResearchError("diagnostic must not open latest confirmation position")

    base["split"] = assign_fold(base, seasons, test_position)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))

    fold = attach_exact_total(base, raw)
    market = materialize_ou25(raw)
    fold = fold.merge(market, on="identity_key", how="left", validate="one_to_one")
    eps = 1e-6
    valid = fold.ou_over_prob.notna() & np.isfinite(fold.ou_over_prob.astype(float))
    valid &= (fold.ou_over_prob.astype(float) > 0.0) & (fold.ou_over_prob.astype(float) < 1.0)
    fold["ou25_logit_over"] = np.nan
    fold.loc[valid, "ou25_logit_over"] = np.log(
        np.clip(fold.loc[valid, "ou_over_prob"].astype(float), eps, 1 - eps)
        / np.clip(1 - fold.loc[valid, "ou_over_prob"].astype(float), eps, 1 - eps)
    )

    sample = fold.merge(
        sample_base[KEYS + ["match_identity", "identity_hash"]],
        on=KEYS,
        how="inner",
        validate="one_to_one",
    )
    raw_scores = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy()
    raw_scores["season"] = raw_scores["season"].astype(str)
    sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=KEYS, how="left", validate="one_to_one")
    if len(sample) != int(exp["sample_n"]):
        raise ResearchError("fixed500 reconstruction mismatch")
    covered = sample[sample.ou_over_prob.notna() & np.isfinite(sample.ou_over_prob.astype(float))].copy()
    if len(covered) < 50:
        raise ResearchError(f"fixed500 OU2.5 coverage too small for diagnosis: {len(covered)}")

    fit_mask = fold.split.isin(["train", "policy"]) & valid
    fit = fold[fit_mask].copy()
    train = fit[fit.split == "train"].copy()
    policy = fit[fit.split == "policy"].copy()
    if min(len(train), len(policy)) == 0:
        raise ResearchError("empty historical OU2.5 fit split")

    feature = "ou25_logit_over"
    challenger_features = core + [feature]
    selected_C, policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, config)
    baseline = make_model(selected_C, config)
    challenger = make_model(selected_C, config)
    baseline.fit(fit[core], fit.total_class)
    challenger.fit(fit[challenger_features], fit.total_class)
    p_base = align_probability(baseline, covered[core], TOTAL_CLASSES)
    p_ch = align_probability(challenger, covered[challenger_features], TOTAL_CLASSES)

    yT = covered.total_class.to_numpy(int)
    base_components = metric_components(yT, p_base, TOTAL_CLASSES)
    ch_components = metric_components(yT, p_ch, TOTAL_CLASSES)
    base_T = metric_summary(base_components)
    ch_T = metric_summary(ch_components)
    delta_T = {k: float(ch_T[k] - base_T[k]) for k in base_T}
    bootstrap = {
        metric: paired_bootstrap(
            ch_components[metric].to_numpy(float) - base_components[metric].to_numpy(float),
            5000,
            197250 + i,
        )
        for i, metric in enumerate(("logloss", "brier", "rps"))
    }

    parity_classes = [0, 1]
    parity_C, parity_grid = select_C(train, policy, core, "exact_parity", parity_classes, config)
    parity_base = make_model(parity_C, config)
    parity_ch = make_model(parity_C, config)
    parity_base.fit(fit[core], fit.exact_parity)
    parity_ch.fit(fit[challenger_features], fit.exact_parity)
    pp_base = align_probability(parity_base, covered[core], parity_classes)
    pp_ch = align_probability(parity_ch, covered[challenger_features], parity_classes)
    py = covered.exact_parity.to_numpy(int)
    parity_base_metrics = parity_metrics(py, pp_base)
    parity_ch_metrics = parity_metrics(py, pp_ch)

    cond_model, _, cond_receipt = conditional_probabilities(fold, covered, core, config)
    joint_base = assemble_joint(p_base, cond_model)
    joint_ch = assemble_joint(p_ch, cond_model)

    rows = covered[KEYS + [
        "match_identity", "identity_hash", "home_goals_90", "away_goals_90", "total_goals",
        "goal_difference", "ou_over_prob", "ou25_logit_over", "ou_source", "source_file_market",
    ]].copy()
    rows = rows.rename(columns={"total_goals": "actual_total", "goal_difference": "actual_gd"})
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int), 7)
    rows["actual_parity"] = rows.actual_total.astype(int) % 2
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str) + ":" + rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))
    rows["baseline_pred_total_class"] = np.argmax(p_base, axis=1)
    rows["ou25_pred_total_class"] = np.argmax(p_ch, axis=1)
    rows["baseline_p_even"] = pp_base[:, 0]
    rows["ou25_p_even"] = pp_ch[:, 0]
    for prefix, joint in (("baseline", joint_base), ("ou25", joint_ch)):
        jf = pd.DataFrame(joint)
        for column in jf.columns:
            rows[f"{prefix}_{column}"] = jf[column].to_numpy()

    base_hda_raw = hda_metrics(rows, "baseline")
    ch_hda_raw = hda_metrics(rows, "ou25")
    base_hda = clean_hda(base_hda_raw)
    ch_hda = clean_hda(ch_hda_raw)
    base_score = score_metrics(rows, "baseline")
    ch_score = score_metrics(rows, "ou25")

    actual_draw = rows.actual_result.to_numpy() == "D"
    result = {
        "schema_version": "DIRECT_T_OU25_FIXED500_INFO_CEILING_R1",
        "classification": "POST_RESULT_SAME_FIXED500_RETROSPECTIVE_MARKET_DIAGNOSTIC_ONLY",
        "scientific_question": "Does one retrospective de-vigged closing/reference P(Over2.5) scalar contain missing T information beyond the 47-feature historical core?",
        "sample": {
            "parent_fixed500_n": int(exp["sample_n"]),
            "parent_fixed500_identity_sha256": sample_hash,
            "covered_n": int(len(rows)),
            "coverage_rate": float(len(rows) / int(exp["sample_n"])),
            "covered_actual_draws": int(actual_draw.sum()),
            "covered_competitions": int(rows.competition_id.nunique()),
            "ou_source_counts": {str(k): int(v) for k, v in rows.ou_source.value_counts().sort_index().items()},
            "labels_used_for_coverage_selection": False,
            "new_sample_consumed": False,
            "latest_position4_confirmation_opened": False,
        },
        "market_boundary": {
            "totals_line": 2.5,
            "reference_preference": list(OU_PREFERENCE),
            "de_vig_method": "reciprocal odds normalized within Over/Under pair",
            "original_quote_timestamps_available": False,
            "formal_PIT_claim": False,
            "retrospective_reference_only": True,
        },
        "fit_contract": {
            "historical_fit_rows_with_ou25": int(len(fit)),
            "historical_train_rows_with_ou25": int(len(train)),
            "historical_policy_rows_with_ou25": int(len(policy)),
            "baseline_feature_count": int(len(core)),
            "challenger_feature_count": int(len(challenger_features)),
            "added_feature": feature,
            "direct_t_selected_C_from_historical_policy_only": float(selected_C),
            "direct_t_policy_grid": policy_grid,
            "same_C_used_for_baseline_and_challenger": True,
            "parity_selected_C_from_historical_policy_only": float(parity_C),
            "parity_policy_grid": parity_grid,
            "fixed500_parameter_selection": False,
            "fixed500_feature_selection": False,
            "fixed500_threshold_selection": False,
            "manual_blend": False,
            "post_result_parameter_search": False,
        },
        "metrics": {
            "direct_t": {
                "baseline_same_market_fit": base_T,
                "plus_ou25": ch_T,
                "delta_plus_ou25_minus_baseline": delta_T,
                "paired_bootstrap_delta": bootstrap,
            },
            "parity": {
                "baseline_same_market_fit": parity_base_metrics,
                "plus_ou25": parity_ch_metrics,
                "delta": {k: float(parity_ch_metrics[k] - parity_base_metrics[k]) for k in parity_base_metrics},
            },
            "hda_from_same_conditional_gd": {
                "baseline_same_market_fit": base_hda,
                "plus_ou25": ch_hda,
                "delta_log_loss": float(ch_hda["log_loss"] - base_hda["log_loss"]),
                "delta_accuracy_pp": float((ch_hda["accuracy"] - base_hda["accuracy"]) * 100.0),
                "delta_draw_f1_pp": float((ch_hda["draw_f1"] - base_hda["draw_f1"]) * 100.0),
            },
            "exact_score_from_same_conditional_gd": {
                "baseline_same_market_fit": base_score,
                "plus_ou25": ch_score,
                "delta_top1_pp": float((ch_score["top1_accuracy"] - base_score["top1_accuracy"]) * 100.0),
                "delta_top3_pp": float((ch_score["top3_accuracy"] - base_score["top3_accuracy"]) * 100.0),
            },
        },
        "draw_diagnostics": {
            "actual_draws": int(actual_draw.sum()),
            "baseline_draw_calls": int(np.sum(rows.baseline_pred_result == "D")),
            "ou25_draw_calls": int(np.sum(rows.ou25_pred_result == "D")),
            "baseline_draw_hits": int(np.sum((rows.baseline_pred_result == "D") & (rows.actual_result == "D"))),
            "ou25_draw_hits": int(np.sum((rows.ou25_pred_result == "D") & (rows.actual_result == "D"))),
        },
        "conditional_gd_receipt": cond_receipt,
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "interpretation_guard": {
            "can_establish_information_ceiling": True,
            "can_establish_live_pre_match_value": False,
            "can_authorize_promotion": False,
            "frozen_PR197_verdict_changed": False,
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "latest_position4_confirmation_opened": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows.to_csv(ROWS_OUT, index=False)
    return result


def main() -> None:
    x = run()
    print(json.dumps({
        "classification": x["classification"],
        "sample": x["sample"],
        "metrics": x["metrics"],
        "draw_diagnostics": x["draw_diagnostics"],
        "interpretation_guard": x["interpretation_guard"],
        "governance": x["governance"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
