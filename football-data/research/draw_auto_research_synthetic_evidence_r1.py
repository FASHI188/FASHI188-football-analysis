#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
from typing import Any

import numpy as np

from draw_auto_research_baseline_r1 import baseline_identity, baseline_predictions
from draw_auto_research_engine_r1 import BASIS_VARIANTS, MatchRow, candidate_catalog, fit_predict_split


def row(index: int, *, target: bool = False) -> MatchRow:
    phase = index / 11.0
    elo = 220.0 * math.sin(phase) + 35.0 * math.cos(index / 5.0)
    home_ppg = 1.4 + 0.75 * math.sin(index / 7.0)
    away_ppg = 1.3 + 0.65 * math.cos(index / 9.0)
    home_gf = 1.25 + 0.55 * math.sin(index / 6.0)
    away_gf = 1.15 + 0.45 * math.cos(index / 8.0)
    home_ga = 1.05 + 0.35 * math.cos(index / 10.0)
    away_ga = 1.20 + 0.40 * math.sin(index / 12.0)
    closeness = math.exp(-abs(elo) / 200.0)
    nonlinear = closeness + 0.55 * ((home_ppg - away_ppg) ** 2) - 0.35 * ((home_gf + away_gf + home_ga + away_ga) / 2.0 - 2.4) ** 2
    label = "D" if nonlinear > 0.75 else ("H" if elo >= 0 else "A")
    return MatchRow(
        competition="SYN", season="2025" if target else "2024", date=f"2024-01-{(index % 28) + 1:02d}",
        home_team=f"H{index}", away_team=f"A{index}", label=label,
        values={
            "home_history_matches": float(20 + index % 30), "away_history_matches": float(18 + (index * 3) % 30),
            "home_last5_matches": 5.0, "away_last5_matches": 5.0,
            "home_last5_gf": home_gf, "away_last5_gf": away_gf,
            "home_last5_ga": home_ga, "away_last5_ga": away_ga,
            "home_last5_ppg": home_ppg, "away_last5_ppg": away_ppg,
            "home_elo_pre_match": 1500.0 + elo / 2.0, "away_elo_pre_match": 1500.0 - elo / 2.0,
            "elo_difference_with_home_advantage": elo,
            "cold_start_flag": 0.0, "stage_unverified_flag": 0.0,
        },
    )


def fingerprint(predictions: np.ndarray) -> str:
    return hashlib.sha256(np.round(predictions, 12).tobytes(order="C")).hexdigest()


def generate() -> dict[str, Any]:
    train = [row(index) for index in range(180)]
    target = [row(index + 300, target=True) for index in range(80)]
    candidates = [item for item in candidate_catalog() if item["profile"] == "full_core" and item["positive_class_weight"] == 1.1]
    by_basis = {item["basis_variant"]: item for item in candidates}
    predictions: dict[str, np.ndarray] = {}
    for basis in BASIS_VARIANTS:
        prediction, _, _ = fit_predict_split(train, target, by_basis[basis], 4.0)
        predictions[basis] = prediction
    pairwise: dict[str, float] = {}
    for left_index, left in enumerate(BASIS_VARIANTS):
        for right in BASIS_VARIANTS[left_index + 1:]:
            pairwise[f"{left}__{right}"] = round(float(np.max(np.abs(predictions[left] - predictions[right]))), 12)
    baseline_a, _, receipt_a = baseline_predictions(train, target)
    baseline_b, _, receipt_b = baseline_predictions(train, target)
    result = {
        "schema_version": "DRAW-AUTO-SYNTHETIC-EVIDENCE-R1.4",
        "basis_prediction_fingerprints": {basis: fingerprint(predictions[basis]) for basis in BASIS_VARIANTS},
        "pairwise_max_abs_prediction_difference": pairwise,
        "minimum_pairwise_difference": round(min(pairwise.values()), 12),
        "all_basis_predictions_distinct": len({fingerprint(value) for value in predictions.values()}) == len(BASIS_VARIANTS),
        "baseline_identity": baseline_identity(),
        "baseline_fingerprint_a": fingerprint(baseline_a),
        "baseline_fingerprint_b": fingerprint(baseline_b),
        "baseline_candidate_independent": fingerprint(baseline_a) == fingerprint(baseline_b) and receipt_a["candidate_parameters_used"] == [] and receipt_b["candidate_parameters_used"] == [],
        "synthetic_rows_only": True,
        "real_labels_read": 0,
        "real_training_runs": 0,
    }
    if not result["all_basis_predictions_distinct"] or result["minimum_pairwise_difference"] <= 1e-6:
        raise ValueError(f"basis variants are not predictively distinct: {result}")
    if not result["baseline_candidate_independent"]:
        raise ValueError("baseline changed across candidate-independent calls")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = generate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
