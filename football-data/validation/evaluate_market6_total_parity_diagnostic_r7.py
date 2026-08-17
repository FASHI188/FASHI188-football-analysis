#!/usr/bin/env python3
"""R7 rolling-OOS market6 -> exact total parity diagnostic.

Research-only, retrospective and post-view. The target is even_total=1 iff the exact
90-minute total is even. This component diagnostic does not modify Direct-T, conditional
GD, HDA, score probabilities, thresholds, or any unopened package labels.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from evaluate_even_t_gd0_market_diagnostic_r5 import KEYS, MARKET6, build_market6
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "market6_total_parity_diagnostic_r7.json"
BASE_CONFIG = ROOT / "config" / "v510_historical_label_structure_rolling_r1.json"
OUT_DIR = ROOT / "manifests" / "market6_total_parity_diagnostic_r7"
FAMILIES = [
    "core47_binary_full",
    "core47_binary_marketmatched",
    "market6_binary",
    "core47_market6_binary",
]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError(f"R7 JSON root must be object: {path}")
    return value


def _safe_auc(y: np.ndarray, score: np.ndarray) -> float | None:
    y = np.asarray(y, dtype=int)
    score = np.asarray(score, dtype=float)
    if len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, score))


def _binary_probability(
    fit: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    base_cfg: dict[str, Any],
    C: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    y = fit.even_total.to_numpy(int)
    counts = {"0": int(np.sum(y == 0)), "1": int(np.sum(y == 1))}
    if min(counts.values()) <= 0:
        raise ResearchError(f"R7 binary fit lacks both classes: {counts}")
    model = make_model(C, base_cfg)
    model.fit(fit[features], y)
    probability = align_probability(model, test[features], [0, 1])[:, 1]
    return probability, {
        "fit_rows": int(len(fit)),
        "class_counts": counts,
        "fixed_C": float(C),
        "feature_count": int(len(features)),
        "probability_min": float(np.min(probability)) if len(probability) else None,
        "probability_max": float(np.max(probability)) if len(probability) else None,
    }


def _row_logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1.0 - 1e-15)
    return -(y * np.log(p) + (1 - y) * np.log(1.0 - p))


def _binary_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1.0 - 1e-15)
    loss = _row_logloss(y, p)
    return {
        "rows": int(len(y)),
        "positives_even_total": int(y.sum()),
        "observed_even_rate": float(y.mean()) if len(y) else None,
        "mean_probability_even": float(p.mean()) if len(p) else None,
        "calibration_residual_pred_minus_observed": float(p.mean() - y.mean()) if len(y) else None,
        "binary_logloss": float(loss.mean()) if len(loss) else None,
        "brier": float(np.mean((p - y) ** 2)) if len(y) else None,
        "auc": _safe_auc(y, p),
        "accuracy_at_0_5": float(np.mean((p >= 0.5).astype(int) == y)) if len(y) else None,
    }


def _cluster_bootstrap_delta(
    rows: pd.DataFrame,
    family_a: str,
    family_b: str,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    y = rows.even_total.to_numpy(int)
    delta = _row_logloss(y, rows[f"p_{family_a}"].to_numpy(float)) - _row_logloss(
        y, rows[f"p_{family_b}"].to_numpy(float)
    )
    keys = rows[["fold", "competition_id", "season"]].astype(str).agg("|".join, axis=1)
    key_array = keys.to_numpy()
    groups = sorted(keys.unique())
    sums = np.asarray([float(delta[key_array == group].sum()) for group in groups], dtype=float)
    counts = np.asarray([int(np.sum(key_array == group)) for group in groups], dtype=float)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(groups), size=(samples, len(groups)))
    values = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return {
        "comparison": f"{family_a}_minus_{family_b}",
        "clusters": int(len(groups)),
        "rows": int(len(rows)),
        "observed_mean_delta": float(delta.mean()),
        "bootstrap_mean": float(values.mean()),
        "p05": float(np.quantile(values, 0.05)),
        "median": float(np.quantile(values, 0.50)),
        "p95": float(np.quantile(values, 0.95)),
        "probability_family_a_better": float(np.mean(values < 0.0)),
    }


def _metrics_by_group(rows: pd.DataFrame, group: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for value, part in rows.groupby(group, sort=True):
        y = part.even_total.to_numpy(int)
        output[str(value)] = {
            family: _binary_metrics(y, part[f"p_{family}"].to_numpy(float))
            for family in FAMILIES
        }
    return output


def _raw_signal_auc(rows: pd.DataFrame) -> dict[str, Any]:
    y = rows.even_total.to_numpy(int)
    signals = {
        "market_p_draw_devig": rows.market_p_draw_devig.to_numpy(float),
        "negative_market_hda_home_away_abs_gap": -rows.market_hda_home_away_abs_gap.to_numpy(float),
        "negative_market_abs_ah_line": -rows.market_abs_ah_line.to_numpy(float),
        "negative_market_ah_home_abs_from_half": -rows.market_ah_home_abs_from_half.to_numpy(float),
        "market_ou_line": rows.market_ou_line.to_numpy(float),
        "market_p_over_devig": rows.market_p_over_devig.to_numpy(float),
    }
    return {name: {"auc": _safe_auc(y, score)} for name, score in signals.items()}


def _write(result: dict[str, Any], rows: pd.DataFrame) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows.to_csv(OUT_DIR / "rows.csv", index=False)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (OUT_DIR / "summary.json").write_text(text, encoding="utf-8")
    (OUT_DIR / "summary.sha256").write_text(
        hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="ascii"
    )


def run() -> dict[str, Any]:
    cfg = load_json(CONFIG)
    base_cfg = load_json(BASE_CONFIG)
    ledger = ROOT / str(base_cfg["input_ledger"])
    if not ledger.is_file():
        raise ResearchError(f"R7 runtime ledger missing: {ledger}")

    raw = pd.read_csv(ledger)
    raw["season"] = raw["season"].astype(str)
    data_identity = audit_data_identity(raw, base_cfg)
    source = cfg["source_contract"]
    if int(data_identity["rows"]) != int(source["expected_historical_rows"]):
        raise ResearchError("R7 historical row count mismatch")
    if int(data_identity["competitions"]) != int(source["expected_competitions"]):
        raise ResearchError("R7 competition count mismatch")

    features = build_features(raw)
    features["season"] = features["season"].astype(str)
    core = select_core_features(features)
    if len(core) != int(cfg["model_contract"]["core_feature_count"]):
        raise ResearchError(f"R7 core feature count mismatch: {len(core)}")

    exact = raw[KEYS + ["total_goals"]].copy()
    exact["season"] = exact["season"].astype(str)
    if exact[KEYS].duplicated().any():
        raise ResearchError("R7 duplicate exact-total identity keys")
    exact["even_total"] = ((exact.total_goals.astype(int) % 2) == 0).astype(int)
    frame = features.merge(exact[KEYS + ["even_total"]], on=KEYS, how="left", validate="one_to_one")
    if frame.even_total.isna().any():
        raise ResearchError("R7 exact parity label join failure")
    frame["even_total"] = frame.even_total.astype(int)

    market, market_audit = build_market6(raw, cfg["market_contract"])
    frame = frame.merge(market, on=KEYS, how="left", validate="one_to_one")
    if len(frame) != len(features) or frame.market6_complete.isna().any():
        raise ResearchError("R7 market merge mismatch")
    frame["market6_complete"] = frame.market6_complete.astype(bool)

    seasons, excluded = complete_seasons(raw, base_cfg)
    positions = [int(x) for x in source["rolling_test_positions_zero_based"]]
    fixed_C = float(cfg["model_contract"]["fixed_C"])
    rows_out: list[pd.DataFrame] = []
    receipts: dict[str, Any] = {}
    coverage_detail: list[dict[str, Any]] = []

    for position in positions:
        fold = frame.copy()
        fold["split"] = assign_fold(fold, seasons, position)
        fold_name = f"position_{position}"
        fit_all = fold[fold.split.isin(["train", "policy"])].copy()
        fit_market = fit_all[fit_all.market6_complete].copy()
        test_all = fold[fold.split == "test"].copy()
        test = test_all[test_all.market6_complete].copy()
        if len(test) == 0:
            raise ResearchError(f"R7 no market-complete test rows: {fold_name}")

        core_full_p, r_core_full = _binary_probability(fit_all, test, core, base_cfg, fixed_C)
        core_match_p, r_core_match = _binary_probability(fit_market, test, core, base_cfg, fixed_C)
        market_p, r_market = _binary_probability(fit_market, test, MARKET6, base_cfg, fixed_C)
        combined_p, r_combined = _binary_probability(fit_market, test, core + MARKET6, base_cfg, fixed_C)

        part = test[KEYS + ["even_total"] + MARKET6].copy()
        part["fold"] = fold_name
        part["p_core47_binary_full"] = core_full_p
        part["p_core47_binary_marketmatched"] = core_match_p
        part["p_market6_binary"] = market_p
        part["p_core47_market6_binary"] = combined_p
        rows_out.append(part)

        coverage_detail.append({
            "fold": fold_name,
            "fit_all_rows": int(len(fit_all)),
            "fit_market_complete_rows": int(len(fit_market)),
            "fit_market_complete_rate": float(len(fit_market) / len(fit_all)) if len(fit_all) else 0.0,
            "test_all_rows": int(len(test_all)),
            "test_market_complete_rows": int(len(test)),
            "test_market_complete_rate": float(len(test) / len(test_all)) if len(test_all) else 0.0,
            "test_even_rows": int(test.even_total.sum()),
        })
        receipts[fold_name] = {
            "core47_binary_full": r_core_full,
            "core47_binary_marketmatched": r_core_match,
            "market6_binary": r_market,
            "core47_market6_binary": r_combined,
        }

    rows = pd.concat(rows_out, ignore_index=True)
    if rows[KEYS + ["fold"]].duplicated().any():
        raise ResearchError("R7 duplicate evaluation rows across folds")

    y = rows.even_total.to_numpy(int)
    pooled = {
        family: _binary_metrics(y, rows[f"p_{family}"].to_numpy(float))
        for family in FAMILIES
    }
    by_fold = _metrics_by_group(rows, "fold")

    reporting = cfg["reporting_contract"]
    boot_n = int(reporting["bootstrap_resamples"])
    seed = int(reporting["bootstrap_seed"])
    bootstrap = {
        "market6_minus_core_marketmatched": _cluster_bootstrap_delta(
            rows, "market6_binary", "core47_binary_marketmatched", boot_n, seed
        ),
        "combined_minus_core_marketmatched": _cluster_bootstrap_delta(
            rows, "core47_market6_binary", "core47_binary_marketmatched", boot_n, seed + 1
        ),
        "market6_minus_core_full": _cluster_bootstrap_delta(
            rows, "market6_binary", "core47_binary_full", boot_n, seed + 2
        ),
    }

    coverage_cfg = cfg["coverage_gate"]
    detail = pd.DataFrame(coverage_detail)
    coverage_checks = {
        "pooled_market_complete_test_rows": bool(len(rows) >= int(coverage_cfg["minimum_pooled_market_complete_test_rows"])),
        "each_fold_market_complete_test_rows": bool((detail.test_market_complete_rows >= int(coverage_cfg["minimum_market_complete_test_rows_each_fold"])).all()),
        "each_fold_market_complete_fit_rows": bool((detail.fit_market_complete_rows >= int(coverage_cfg["minimum_market_complete_fit_rows_each_fold"])).all()),
    }
    coverage_pass = bool(all(coverage_checks.values()))

    core_match = pooled["core47_binary_marketmatched"]
    market_only = pooled["market6_binary"]
    ll_gain = float(core_match["binary_logloss"] - market_only["binary_logloss"])
    auc_gain = (
        float(market_only["auc"] - core_match["auc"])
        if market_only["auc"] is not None and core_match["auc"] is not None else None
    )
    fold_wins = 0
    for fold_name in sorted(by_fold):
        if by_fold[fold_name]["market6_binary"]["binary_logloss"] < by_fold[fold_name]["core47_binary_marketmatched"]["binary_logloss"]:
            fold_wins += 1

    gate = reporting["development_signal_gate"]
    primary_boot = bootstrap["market6_minus_core_marketmatched"]
    development_checks = {
        "coverage_gate_passed": coverage_pass,
        "minimum_logloss_gain_market6_vs_core_marketmatched": bool(ll_gain >= float(gate["minimum_logloss_gain_market6_vs_core_marketmatched"])),
        "cluster_bootstrap_logloss_p95_lt_zero": bool(primary_boot["p95"] < 0.0),
        "minimum_auc_gain_market6_vs_core_marketmatched": bool(auc_gain is not None and auc_gain >= float(gate["minimum_auc_gain_market6_vs_core_marketmatched"])),
        "brier_nonworse": bool(market_only["brier"] <= core_match["brier"]),
        "minimum_fold_logloss_wins": bool(fold_wins >= int(gate["minimum_fold_logloss_wins"])),
    }
    development_pass = bool(all(development_checks.values()))

    result = {
        "schema_version": cfg["schema_version"],
        "status": "POSTVIEW_MARKET6_TOTAL_PARITY_DIAGNOSTIC_R7_COMPLETE_NO_PROMOTION",
        "scientific_verdict": (
            "POSTVIEW_MARKET6_TOTAL_PARITY_SIGNAL_DESCRIPTIVE_ONLY"
            if development_pass else "POSTVIEW_MARKET6_TOTAL_PARITY_SIGNAL_NOT_ESTABLISHED"
        ),
        "question": cfg["purpose"],
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "target": {
            "name": "even_total",
            "definition": source["target_definition"],
            "draw_requires_even_total": True,
        },
        "market_evidence": {
            "evidence_class": cfg["market_contract"]["evidence_class"],
            "original_quote_timestamp_available": False,
            "formal_pre_match_snapshot": False,
            "market6_audit": market_audit,
        },
        "coverage": {
            "pooled_market_complete_test_rows": int(len(rows)),
            "checks": coverage_checks,
            "passed": coverage_pass,
            "detail": coverage_detail,
        },
        "model_contract": cfg["model_contract"],
        "fold_model_receipts": receipts,
        "metrics": {
            "pooled": pooled,
            "by_fold": by_fold,
            "raw_market_signal_auc": _raw_signal_auc(rows),
        },
        "bootstrap": bootstrap,
        "diagnosis": {
            "market6_logloss_gain_vs_core_marketmatched": ll_gain,
            "market6_auc_gain_vs_core_marketmatched": auc_gain,
            "market6_fold_logloss_wins": int(fold_wins),
            "combined_logloss_gain_vs_core_marketmatched": float(core_match["binary_logloss"] - pooled["core47_market6_binary"]["binary_logloss"]),
            "combined_auc_gain_vs_core_marketmatched": (
                float(pooled["core47_market6_binary"]["auc"] - core_match["auc"])
                if pooled["core47_market6_binary"]["auc"] is not None and core_match["auc"] is not None else None
            ),
        },
        "development_signal": {
            "passed": development_pass,
            "checks": development_checks,
            "scientific_pass": False,
            "future_oos_label_open_authorized": False,
        },
        "boundary": cfg["boundary"],
    }
    _write(result, rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
