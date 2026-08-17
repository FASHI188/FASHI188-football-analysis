#!/usr/bin/env python3
"""R5 rolling-OOS even-T conditional GD=0 market diagnostic.

This is deliberately component-level only. It conditions retrospectively on realized
T in {2,4,6} and asks whether a fixed current-match market-balance block discriminates
GD=0 beyond the historical conditional-GD state. It does not modify HDA or score
probabilities and cannot authorize B05 label opening.

Existing processed market columns have no original quote timestamp, so market features
are retrospective closing/reference evidence, not strict PIT inputs.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, select_C

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "even_t_gd0_market_diagnostic_r5.json"
BASE_CONFIG = ROOT / "config" / "v510_historical_label_structure_rolling_r1.json"
OUT_DIR = ROOT / "manifests" / "even_t_gd0_market_diagnostic_r5"
KEYS = ["competition_id", "season", "date_key", "home_team", "away_team"]
MARKET6 = [
    "market_p_draw_devig",
    "market_hda_home_away_abs_gap",
    "market_abs_ah_line",
    "market_ah_home_abs_from_half",
    "market_ou_line",
    "market_p_over_devig",
]
FAMILIES = [
    "parent_multinomial_p0",
    "core47_binary_full",
    "core47_binary_marketmatched",
    "market6_binary",
    "core47_market6_binary",
]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError(f"JSON root must be object: {path}")
    return value


def _devig(prices: list[float]) -> tuple[np.ndarray, float]:
    inv = np.asarray([1.0 / float(x) for x in prices], dtype=float)
    total = float(inv.sum())
    if not np.isfinite(inv).all() or total <= 0.0:
        raise ResearchError("R5 invalid inverse odds")
    return inv / total, total


def _first_prices(
    row: dict[str, Any], headers: list[str], candidates: list[list[str]]
) -> tuple[list[float], list[str]] | None:
    for aliases in candidates:
        fields = [field_name(headers, [str(alias)]) for alias in aliases]
        if not all(fields):
            continue
        values = [float_value(row, str(field)) for field in fields]
        if all(valid_price(value) for value in values):
            return [float(value) for value in values], [str(field) for field in fields]
    return None


def _first_ou(
    row: dict[str, Any], headers: list[str], cfg: dict[str, Any]
) -> tuple[list[float], float, list[str], str] | None:
    for candidate in cfg["ou_price_alias_priority"]:
        over_alias, under_alias, implicit_line = candidate
        over_field = field_name(headers, [str(over_alias)])
        under_field = field_name(headers, [str(under_alias)])
        if not over_field or not under_field:
            continue
        over_value = float_value(row, str(over_field))
        under_value = float_value(row, str(under_field))
        if not (valid_price(over_value) and valid_price(under_value)):
            continue
        line_field = "implicit_2.5"
        line = implicit_line
        if line is None:
            resolved = field_name(headers, [str(x) for x in cfg["ou_line_alias_priority"]])
            if not resolved:
                continue
            parsed = float_value(row, str(resolved))
            if parsed is None or not np.isfinite(float(parsed)):
                continue
            line = float(parsed)
            line_field = str(resolved)
        return [float(over_value), float(under_value)], float(line), [str(over_field), str(under_field)], line_field
    return None


def _market6_vector(
    row: dict[str, Any], headers: list[str], cfg: dict[str, Any]
) -> tuple[np.ndarray | None, str | None, dict[str, Any]]:
    one = _first_prices(row, headers, cfg["one_x_two_alias_priority"])
    if one is None:
        return None, "one_x_two_missing", {}
    one_values, one_fields = one
    p1, _ = _devig(one_values)

    ah_line_field = field_name(headers, [str(x) for x in cfg["asian_line_alias_priority"]])
    ah_line = float_value(row, str(ah_line_field)) if ah_line_field else None
    if ah_line is None or not np.isfinite(float(ah_line)):
        return None, "asian_line_missing", {"one_x_two_fields": one_fields}
    ah = _first_prices(row, headers, cfg["asian_price_alias_priority"])
    if ah is None:
        return None, "asian_prices_missing", {
            "one_x_two_fields": one_fields,
            "asian_line_field": str(ah_line_field),
        }
    ah_values, ah_fields = ah
    pah, _ = _devig(ah_values)

    ou = _first_ou(row, headers, cfg)
    if ou is None:
        return None, "ou_missing", {
            "one_x_two_fields": one_fields,
            "asian_line_field": str(ah_line_field),
            "asian_fields": ah_fields,
        }
    ou_values, ou_line, ou_fields, ou_line_field = ou
    pou, _ = _devig(ou_values)

    vector = np.asarray(
        [
            float(p1[1]),
            float(abs(p1[0] - p1[2])),
            float(abs(float(ah_line))),
            float(abs(pah[0] - 0.5)),
            float(ou_line),
            float(pou[0]),
        ],
        dtype=float,
    )
    if vector.shape != (6,) or not np.isfinite(vector).all():
        raise ResearchError("R5 invalid market6 vector")
    provenance = {
        "one_x_two_fields": one_fields,
        "asian_line_field": str(ah_line_field),
        "asian_fields": ah_fields,
        "ou_line_field": ou_line_field,
        "ou_fields": ou_fields,
    }
    return vector, None, provenance


def build_market6(raw: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = set(KEYS + ["source_file", "row_number"])
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ResearchError(f"R5 market provenance fields missing: {missing}")

    matrix = np.full((len(raw), 6), np.nan, dtype=float)
    reasons: Counter[str] = Counter()
    field_paths: Counter[str] = Counter()
    by_source: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for pos, row in enumerate(raw.itertuples(index=False)):
        by_source[str(row.source_file)][int(row.row_number)].append(pos)

    source_missing = 0
    source_row_missing = 0
    for source_file, wanted in sorted(by_source.items()):
        path = ROOT / source_file
        if not path.is_file():
            count = sum(len(v) for v in wanted.values())
            source_missing += count
            reasons["source_file_missing"] += count
            continue
        seen: set[int] = set()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            for row_number, record in enumerate(reader, start=2):
                if row_number not in wanted:
                    continue
                seen.add(row_number)
                vector, reason, provenance = _market6_vector(record, headers, cfg)
                if vector is None:
                    reasons[str(reason)] += len(wanted[row_number])
                    continue
                signature = json.dumps(provenance, ensure_ascii=True, sort_keys=True)
                field_paths[signature] += len(wanted[row_number])
                for pos in wanted[row_number]:
                    matrix[pos] = vector
        for row_number, positions in wanted.items():
            if row_number not in seen:
                source_row_missing += len(positions)
                reasons["source_row_missing"] += len(positions)

    market = raw[KEYS].copy()
    market["season"] = market["season"].astype(str)
    for i, name in enumerate(MARKET6):
        market[name] = matrix[:, i]
    market["market6_complete"] = np.isfinite(matrix).all(axis=1)
    if market[KEYS].duplicated().any():
        raise ResearchError("R5 duplicate market identity keys")
    audit = {
        "rows": int(len(raw)),
        "complete_rows": int(market.market6_complete.sum()),
        "complete_rate": float(market.market6_complete.mean()),
        "source_file_missing_rows": int(source_missing),
        "source_row_missing_rows": int(source_row_missing),
        "incomplete_reason_counts": dict(sorted(reasons.items())),
        "field_path_counts": dict(field_paths.most_common()),
    }
    return market, audit


def _binary_probability(
    fit: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    base_cfg: dict[str, Any],
    C: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    y = fit.gd0.to_numpy(int)
    counts = {"0": int(np.sum(y == 0)), "1": int(np.sum(y == 1))}
    if min(counts.values()) <= 0:
        raise ResearchError(f"R5 binary fit lacks both classes: {counts}")
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


def _parent_multinomial_p0(
    fold: pd.DataFrame,
    test: pd.DataFrame,
    core: list[str],
    base_cfg: dict[str, Any],
    total: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    train = fold[(fold.split == "train") & (fold.total_class == total)]
    policy = fold[(fold.split == "policy") & (fold.total_class == total)]
    fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_class == total)]
    classes = list(range(-total, total + 1, 2))
    selected_C, policy_grid = select_C(train, policy, core, "goal_difference", classes, base_cfg)
    model = make_model(selected_C, base_cfg)
    model.fit(fit[core], fit.goal_difference)
    probability = align_probability(model, test[core], classes)[:, classes.index(0)]
    return probability, {
        "fit_rows": int(len(fit)),
        "selected_C": float(selected_C),
        "policy_grid": policy_grid,
        "support": classes,
    }


def _safe_auc(y: np.ndarray, score: np.ndarray) -> float | None:
    y = np.asarray(y, dtype=int)
    score = np.asarray(score, dtype=float)
    if len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, score))


def _binary_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1.0 - 1e-15)
    loss = -(y * np.log(p) + (1 - y) * np.log(1.0 - p))
    return {
        "rows": int(len(y)),
        "positives": int(y.sum()),
        "observed_rate": float(y.mean()) if len(y) else None,
        "mean_probability": float(p.mean()) if len(p) else None,
        "calibration_residual_pred_minus_observed": float(p.mean() - y.mean()) if len(y) else None,
        "binary_logloss": float(loss.mean()) if len(loss) else None,
        "brier": float(np.mean((p - y) ** 2)) if len(y) else None,
        "auc": _safe_auc(y, p),
    }


def _row_logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1.0 - 1e-15)
    y = np.asarray(y, dtype=int)
    return -(y * np.log(p) + (1 - y) * np.log(1.0 - p))


def _cluster_bootstrap_delta(
    rows: pd.DataFrame,
    family_a: str,
    family_b: str,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    if len(rows) == 0:
        raise ResearchError("R5 bootstrap has zero rows")
    delta = _row_logloss(rows.gd0.to_numpy(int), rows[f"p_{family_a}"].to_numpy(float)) - _row_logloss(
        rows.gd0.to_numpy(int), rows[f"p_{family_b}"].to_numpy(float)
    )
    keys = rows[["fold", "competition_id", "season"]].astype(str).agg("|".join, axis=1)
    groups = sorted(keys.unique())
    sums = np.asarray([float(delta[keys.to_numpy() == group].sum()) for group in groups], dtype=float)
    counts = np.asarray([int(np.sum(keys.to_numpy() == group)) for group in groups], dtype=float)
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


def _metrics_for_groups(rows: pd.DataFrame, group: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for value, part in rows.groupby(group, sort=True):
        y = part.gd0.to_numpy(int)
        output[str(value)] = {
            family: _binary_metrics(y, part[f"p_{family}"].to_numpy(float))
            for family in FAMILIES
        }
    return output


def _raw_signal_metrics(rows: pd.DataFrame) -> dict[str, Any]:
    signals = {
        "market_p_draw_devig": rows.market_p_draw_devig.to_numpy(float),
        "negative_market_hda_home_away_abs_gap": -rows.market_hda_home_away_abs_gap.to_numpy(float),
        "negative_market_abs_ah_line": -rows.market_abs_ah_line.to_numpy(float),
        "negative_market_ah_home_abs_from_half": -rows.market_ah_home_abs_from_half.to_numpy(float),
        "market_ou_line": rows.market_ou_line.to_numpy(float),
        "market_p_over_devig": rows.market_p_over_devig.to_numpy(float),
    }
    y = rows.gd0.to_numpy(int)
    pooled = {name: {"auc": _safe_auc(y, score)} for name, score in signals.items()}
    by_total: dict[str, Any] = {}
    for total, part in rows.groupby("total_class", sort=True):
        yy = part.gd0.to_numpy(int)
        by_total[str(int(total))] = {
            "market_p_draw_devig": {"auc": _safe_auc(yy, part.market_p_draw_devig.to_numpy(float))},
            "negative_market_hda_home_away_abs_gap": {"auc": _safe_auc(yy, -part.market_hda_home_away_abs_gap.to_numpy(float))},
            "negative_market_abs_ah_line": {"auc": _safe_auc(yy, -part.market_abs_ah_line.to_numpy(float))},
            "negative_market_ah_home_abs_from_half": {"auc": _safe_auc(yy, -part.market_ah_home_abs_from_half.to_numpy(float))},
            "market_ou_line": {"auc": _safe_auc(yy, part.market_ou_line.to_numpy(float))},
            "market_p_over_devig": {"auc": _safe_auc(yy, part.market_p_over_devig.to_numpy(float))},
        }
    return {"pooled": pooled, "by_total": by_total}


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
        raise ResearchError(f"R5 runtime ledger missing: {ledger}")
    raw = pd.read_csv(ledger)
    data_identity = audit_data_identity(raw, base_cfg)
    if data_identity["rows"] != int(cfg["source_contract"]["expected_historical_rows"]):
        raise ResearchError("R5 historical row count mismatch")

    features = build_features(raw)
    core = select_core_features(features)
    if len(core) != int(cfg["model_contract"]["core_feature_count"]):
        raise ResearchError(f"R5 core feature count mismatch: {len(core)}")
    features["season"] = features["season"].astype(str)
    features["gd0"] = (features.goal_difference.astype(int) == 0).astype(int)

    market, market_audit = build_market6(raw, cfg["market_contract"])
    frame = features.merge(market, on=KEYS, how="left", validate="one_to_one")
    if len(frame) != len(features) or frame.market6_complete.isna().any():
        raise ResearchError("R5 market merge mismatch")
    frame["market6_complete"] = frame.market6_complete.astype(bool)

    seasons, excluded = complete_seasons(raw, base_cfg)
    positions = [int(x) for x in cfg["source_contract"]["rolling_test_positions_zero_based"]]
    totals = [int(x) for x in cfg["source_contract"]["conditioned_total_classes"]]
    fixed_C = float(cfg["model_contract"]["fixed_C"])

    rows_out: list[pd.DataFrame] = []
    fold_receipts: dict[str, Any] = {}
    coverage_rows: list[dict[str, Any]] = []

    for position in positions:
        fold = frame.copy()
        fold["split"] = assign_fold(fold, seasons, position)
        fold_name = f"position_{position}"
        fold_receipts[fold_name] = {}

        for total in totals:
            fit_all = fold[(fold.split.isin(["train", "policy"])) & (fold.total_class == total)].copy()
            fit_market = fit_all[fit_all.market6_complete].copy()
            test_all = fold[(fold.split == "test") & (fold.total_class == total)].copy()
            test = test_all[test_all.market6_complete].copy()
            if len(test) == 0:
                raise ResearchError(f"R5 no market-complete test rows {fold_name} T={total}")

            coverage_rows.append({
                "fold": fold_name,
                "total": int(total),
                "fit_all_rows": int(len(fit_all)),
                "fit_market_complete_rows": int(len(fit_market)),
                "fit_market_complete_rate": float(len(fit_market) / len(fit_all)) if len(fit_all) else 0.0,
                "test_all_rows": int(len(test_all)),
                "test_market_complete_rows": int(len(test)),
                "test_market_complete_rate": float(len(test) / len(test_all)) if len(test_all) else 0.0,
                "test_gd0_rows": int(test.gd0.sum()),
            })

            parent_p, parent_receipt = _parent_multinomial_p0(fold, test, core, base_cfg, total)
            core_full_p, core_full_receipt = _binary_probability(
                fit_all, test, core, base_cfg, fixed_C
            )
            core_match_p, core_match_receipt = _binary_probability(
                fit_market, test, core, base_cfg, fixed_C
            )
            market_p, market_receipt = _binary_probability(
                fit_market, test, MARKET6, base_cfg, fixed_C
            )
            combined_features = core + MARKET6
            combined_p, combined_receipt = _binary_probability(
                fit_market, test, combined_features, base_cfg, fixed_C
            )

            part = test[KEYS + ["total_class", "goal_difference", "gd0"] + MARKET6].copy()
            part["fold"] = fold_name
            part["p_parent_multinomial_p0"] = parent_p
            part["p_core47_binary_full"] = core_full_p
            part["p_core47_binary_marketmatched"] = core_match_p
            part["p_market6_binary"] = market_p
            part["p_core47_market6_binary"] = combined_p
            rows_out.append(part)

            fold_receipts[fold_name][str(total)] = {
                "parent_multinomial_p0": parent_receipt,
                "core47_binary_full": core_full_receipt,
                "core47_binary_marketmatched": core_match_receipt,
                "market6_binary": market_receipt,
                "core47_market6_binary": combined_receipt,
            }

    rows = pd.concat(rows_out, ignore_index=True)
    if rows[KEYS + ["fold"]].duplicated().any():
        raise ResearchError("R5 duplicate evaluation rows across folds")
    coverage = pd.DataFrame(coverage_rows)

    pooled_y = rows.gd0.to_numpy(int)
    pooled_metrics = {
        family: _binary_metrics(pooled_y, rows[f"p_{family}"].to_numpy(float))
        for family in FAMILIES
    }
    by_total = _metrics_for_groups(rows, "total_class")
    by_fold = _metrics_for_groups(rows, "fold")
    raw_signals = _raw_signal_metrics(rows)

    report_cfg = cfg["reporting_contract"]
    boot_n = int(report_cfg["bootstrap_resamples"])
    seed = int(report_cfg["bootstrap_seed"])
    bootstrap = {
        "combined_minus_core_marketmatched": _cluster_bootstrap_delta(
            rows, "core47_market6_binary", "core47_binary_marketmatched", boot_n, seed
        ),
        "core_full_minus_parent": _cluster_bootstrap_delta(
            rows, "core47_binary_full", "parent_multinomial_p0", boot_n, seed + 1
        ),
        "market6_minus_parent": _cluster_bootstrap_delta(
            rows, "market6_binary", "parent_multinomial_p0", boot_n, seed + 2
        ),
        "combined_minus_parent": _cluster_bootstrap_delta(
            rows, "core47_market6_binary", "parent_multinomial_p0", boot_n, seed + 3
        ),
    }

    gate_cfg = cfg["coverage_gate"]
    pooled_complete = int(len(rows))
    per_total_complete = rows.groupby("total_class").size().to_dict()
    fit_min = int(coverage.fit_market_complete_rows.min())
    coverage_checks = {
        "pooled_test_rows": bool(pooled_complete >= int(gate_cfg["minimum_pooled_market_complete_even_t_test_rows"])),
        "each_total_test_rows": bool(all(int(per_total_complete.get(total, 0)) >= int(gate_cfg["minimum_market_complete_test_rows_each_total"]) for total in totals)),
        "each_fold_total_fit_rows": bool(fit_min >= int(gate_cfg["minimum_market_complete_fit_rows_each_fold_total"])),
    }
    coverage_pass = bool(all(coverage_checks.values()))

    core_match = pooled_metrics["core47_binary_marketmatched"]
    combined = pooled_metrics["core47_market6_binary"]
    gain_ll = float(core_match["binary_logloss"] - combined["binary_logloss"])
    auc_gain = (
        float(combined["auc"] - core_match["auc"])
        if combined["auc"] is not None and core_match["auc"] is not None else None
    )
    per_total_wins = 0
    for total in totals:
        c = by_total[str(total)]["core47_binary_marketmatched"]["binary_logloss"]
        m = by_total[str(total)]["core47_market6_binary"]["binary_logloss"]
        per_total_wins += int(m < c)

    dev_cfg = report_cfg["development_signal_gate"]
    primary_boot = bootstrap["combined_minus_core_marketmatched"]
    development_checks = {
        "coverage_gate_passed": coverage_pass,
        "minimum_logloss_gain_combined_vs_core_marketmatched": bool(gain_ll >= float(dev_cfg["minimum_logloss_gain_combined_vs_core_marketmatched"])),
        "cluster_bootstrap_logloss_p95_lt_zero": bool(primary_boot["p95"] < 0.0),
        "minimum_auc_gain_combined_vs_core_marketmatched": bool(auc_gain is not None and auc_gain >= float(dev_cfg["minimum_auc_gain_combined_vs_core_marketmatched"])),
        "brier_nonworse_combined_vs_core_marketmatched": bool(combined["brier"] <= core_match["brier"]),
        "minimum_per_total_logloss_wins": bool(per_total_wins >= int(dev_cfg["minimum_per_total_logloss_wins"])),
    }
    development_signal = bool(all(development_checks.values()))

    result = {
        "schema_version": cfg["schema_version"],
        "status": "POSTVIEW_EVEN_T_GD0_MARKET_DIAGNOSTIC_R5_COMPLETE_NO_PROMOTION",
        "scientific_verdict": (
            "POSTVIEW_EVEN_T_MARKET_PARITY_SIGNAL_DESCRIPTIVE_ONLY"
            if development_signal else "POSTVIEW_EVEN_T_MARKET_PARITY_SIGNAL_NOT_ESTABLISHED"
        ),
        "question": cfg["purpose"],
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "market_evidence": {
            "evidence_class": cfg["market_contract"]["evidence_class"],
            "original_quote_timestamp_available": False,
            "formal_pre_match_snapshot": False,
            "market6_audit": market_audit,
        },
        "coverage": {
            "pooled_market_complete_even_t_test_rows": pooled_complete,
            "per_total_market_complete_test_rows": {str(k): int(v) for k, v in per_total_complete.items()},
            "minimum_fold_total_market_complete_fit_rows": fit_min,
            "checks": coverage_checks,
            "passed": coverage_pass,
            "detail": coverage.to_dict(orient="records"),
        },
        "model_contract": cfg["model_contract"],
        "fold_model_receipts": fold_receipts,
        "metrics": {
            "pooled": pooled_metrics,
            "by_total": by_total,
            "by_fold": by_fold,
            "raw_market_signal_auc": raw_signals,
        },
        "bootstrap": bootstrap,
        "diagnosis": {
            "core_binary_full_logloss_gain_vs_parent": float(
                pooled_metrics["parent_multinomial_p0"]["binary_logloss"] - pooled_metrics["core47_binary_full"]["binary_logloss"]
            ),
            "core_binary_full_auc_gain_vs_parent": (
                float(pooled_metrics["core47_binary_full"]["auc"] - pooled_metrics["parent_multinomial_p0"]["auc"])
                if pooled_metrics["core47_binary_full"]["auc"] is not None and pooled_metrics["parent_multinomial_p0"]["auc"] is not None else None
            ),
            "market_increment_logloss_gain_combined_vs_core_marketmatched": gain_ll,
            "market_increment_auc_gain_combined_vs_core_marketmatched": auc_gain,
            "market_increment_per_total_logloss_wins": int(per_total_wins),
        },
        "development_signal": {
            "passed": development_signal,
            "checks": development_checks,
            "scientific_pass": False,
            "future_oos_label_open_authorized": False,
        },
        "boundary": {
            "retrospective_viewed_historical_labels": True,
            "realized_total_conditioning_is_not_deployable_input": True,
            "market_evidence_not_strict_pit": True,
            "hda_probability_mutation": False,
            "score_probability_mutation": False,
            "forced_draw": False,
            "threshold_search": False,
            "new_target_label_access": 0,
            "b05_b07_labels_opened": False,
            "future_oos_label_open_authorized": False,
            "formal_weight": 0,
            "provider_requests": 0,
            "paid_api_requests": 0,
            "new_data_collection": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }
    _write(result, rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
