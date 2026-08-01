#!/usr/bin/env python3
from __future__ import annotations

import csv
import itertools
import math
import pathlib
import time
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from draw_auto_research_math_r1 import (
    canonical_json_sha256,
    fit_offset_logistic,
    hda_from_draw_and_elo,
    logit,
    metric_delta,
    metrics,
    predict_draw,
)

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

BASE_FEATURES = {
    "strength": ["elo_signed", "elo_abs", "elo_closeness"],
    "form": ["ppg_gap", "ppg_sum", "gf_gap", "ga_gap", "home_net", "away_net"],
    "volume": ["history_min", "history_gap", "last5_min", "cold_start", "stage_unverified"],
    "low_goal": ["low_goal_proxy", "goal_environment", "defence_tightness"],
}
PROFILE_FEATURES = {
    "strength": BASE_FEATURES["strength"],
    "form": BASE_FEATURES["form"],
    "volume": BASE_FEATURES["volume"],
    "low_goal": BASE_FEATURES["low_goal"],
    "strength_form": BASE_FEATURES["strength"] + BASE_FEATURES["form"],
    "strength_low_goal": BASE_FEATURES["strength"] + BASE_FEATURES["low_goal"],
    "form_low_goal": BASE_FEATURES["form"] + BASE_FEATURES["low_goal"],
    "full_core": BASE_FEATURES["strength"] + BASE_FEATURES["form"] + BASE_FEATURES["volume"] + BASE_FEATURES["low_goal"],
    "full_interactions": BASE_FEATURES["strength"] + BASE_FEATURES["form"] + BASE_FEATURES["volume"] + BASE_FEATURES["low_goal"] + ["closeness_x_low_goal", "closeness_x_ppg_gap", "form_x_low_goal"],
    "robust_full": BASE_FEATURES["strength"] + BASE_FEATURES["form"] + BASE_FEATURES["volume"] + BASE_FEATURES["low_goal"] + ["closeness_x_low_goal", "closeness_x_ppg_gap", "form_x_low_goal", "elo_abs_sq", "ppg_gap_sq"],
}
PROFILE_L2 = {
    "strength": [0.25, 1.0, 4.0],
    "form": [0.25, 1.0, 4.0],
    "volume": [1.0, 4.0, 16.0],
    "low_goal": [0.25, 1.0, 4.0],
    "strength_form": [0.5, 2.0, 8.0],
    "strength_low_goal": [0.5, 2.0, 8.0],
    "form_low_goal": [0.5, 2.0, 8.0],
    "full_core": [1.0, 4.0, 16.0],
    "full_interactions": [2.0, 8.0, 32.0],
    "robust_full": [4.0, 16.0, 64.0],
}

NUMERIC_SOURCE_FIELDS = (
    "home_history_matches", "away_history_matches", "home_last5_matches", "away_last5_matches",
    "home_last5_gf", "away_last5_gf", "home_last5_ga", "away_last5_ga",
    "home_last5_ppg", "away_last5_ppg", "home_elo_pre_match", "away_elo_pre_match",
    "elo_difference_with_home_advantage", "cold_start_flag", "stage_unverified_flag",
)


def _number(value: str | None) -> float:
    if value is None or value == "":
        return math.nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _season_sort_key(season: str) -> tuple[int, str]:
    digits = "".join(ch for ch in season[:4] if ch.isdigit())
    return (int(digits) if len(digits) == 4 else 9999, season)


@dataclass(frozen=True)
class MatchRow:
    competition: str
    season: str
    date: str
    home_team: str
    away_team: str
    label: str
    values: dict[str, float]

    @property
    def key(self) -> str:
        return f"{self.competition}|{self.season}|{self.date}|{self.home_team}|{self.away_team}"


@dataclass(frozen=True)
class OuterFold:
    fold_id: str
    competition: str
    target_season: str
    prior_seasons: tuple[str, ...]
    inner_train_seasons: tuple[str, ...]
    inner_validation_season: str
    train_rows: tuple[MatchRow, ...]
    inner_train_rows: tuple[MatchRow, ...]
    inner_validation_rows: tuple[MatchRow, ...]
    evaluation_rows: tuple[MatchRow, ...]


def feature_values(row: MatchRow) -> dict[str, float]:
    v = row.values
    elo_signed = v["elo_difference_with_home_advantage"]
    elo_abs = abs(elo_signed) if math.isfinite(elo_signed) else math.nan
    elo_closeness = math.exp(-elo_abs / 200.0) if math.isfinite(elo_abs) else math.nan
    ppg_gap = abs(v["home_last5_ppg"] - v["away_last5_ppg"])
    ppg_sum = v["home_last5_ppg"] + v["away_last5_ppg"]
    gf_gap = abs(v["home_last5_gf"] - v["away_last5_gf"])
    ga_gap = abs(v["home_last5_ga"] - v["away_last5_ga"])
    home_net = v["home_last5_gf"] - v["home_last5_ga"]
    away_net = v["away_last5_gf"] - v["away_last5_ga"]
    history_min = min(v["home_history_matches"], v["away_history_matches"])
    history_gap = abs(v["home_history_matches"] - v["away_history_matches"])
    last5_min = min(v["home_last5_matches"], v["away_last5_matches"])
    cold_start = v["cold_start_flag"]
    stage_unverified = v["stage_unverified_flag"]
    goal_environment = (v["home_last5_gf"] + v["away_last5_gf"] + v["home_last5_ga"] + v["away_last5_ga"]) / 2.0
    low_goal_proxy = 2.5 - goal_environment
    defence_tightness = -(v["home_last5_ga"] + v["away_last5_ga"])
    output = {
        "elo_signed": elo_signed, "elo_abs": elo_abs, "elo_closeness": elo_closeness,
        "ppg_gap": ppg_gap, "ppg_sum": ppg_sum, "gf_gap": gf_gap, "ga_gap": ga_gap,
        "home_net": home_net, "away_net": away_net,
        "history_min": history_min, "history_gap": history_gap, "last5_min": last5_min,
        "cold_start": cold_start, "stage_unverified": stage_unverified,
        "low_goal_proxy": low_goal_proxy, "goal_environment": goal_environment,
        "defence_tightness": defence_tightness,
    }
    output.update({
        "closeness_x_low_goal": elo_closeness * low_goal_proxy,
        "closeness_x_ppg_gap": elo_closeness * ppg_gap,
        "form_x_low_goal": (home_net + away_net) * low_goal_proxy,
        "elo_abs_sq": elo_abs * elo_abs,
        "ppg_gap_sq": ppg_gap * ppg_gap,
    })
    return output


def load_rows(spec: dict[str, Any], root: pathlib.Path = ROOT) -> list[MatchRow]:
    rows: list[MatchRow] = []
    for competition in sorted(spec["dataset_sha256"]):
        path = root / "football-data" / "training_datasets" / competition / "point_in_time.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"competition_id", "season", "date", "home_team", "away_team", "label_result", *NUMERIC_SOURCE_FIELDS}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"dataset header missing {competition}: {sorted(missing)}")
            for raw in reader:
                label = str(raw["label_result"])
                if label not in {"H", "D", "A"}:
                    raise ValueError(f"invalid label {competition}: {label}")
                values = {field: _number(raw.get(field)) for field in NUMERIC_SOURCE_FIELDS}
                rows.append(MatchRow(
                    competition=competition,
                    season=str(raw["season"]),
                    date=str(raw["date"]),
                    home_team=str(raw["home_team"]),
                    away_team=str(raw["away_team"]),
                    label=label,
                    values=values,
                ))
    keys = [row.key for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate match key")
    return rows


def build_outer_folds(rows: Sequence[MatchRow]) -> list[OuterFold]:
    by_comp: dict[str, list[MatchRow]] = {}
    for row in rows:
        by_comp.setdefault(row.competition, []).append(row)
    folds: list[OuterFold] = []
    for competition, comp_rows in sorted(by_comp.items()):
        seasons = sorted({row.season for row in comp_rows}, key=_season_sort_key)
        if len(seasons) < 5:
            raise ValueError(f"fewer than five complete seasons: {competition}")
        targets = seasons[2:5]
        for target in targets:
            index = seasons.index(target)
            prior = seasons[:index]
            inner_validation = prior[-1]
            inner_train = prior[:-1]
            if not inner_train:
                raise ValueError(f"inner training empty: {competition} {target}")
            folds.append(OuterFold(
                fold_id=f"{competition}|{target}",
                competition=competition,
                target_season=target,
                prior_seasons=tuple(prior),
                inner_train_seasons=tuple(inner_train),
                inner_validation_season=inner_validation,
                train_rows=tuple(row for row in comp_rows if row.season in prior),
                inner_train_rows=tuple(row for row in comp_rows if row.season in inner_train),
                inner_validation_rows=tuple(row for row in comp_rows if row.season == inner_validation),
                evaluation_rows=tuple(row for row in comp_rows if row.season == target),
            ))
    if len(folds) != 51:
        raise ValueError(f"expected 51 outer folds, got {len(folds)}")
    return folds


def candidate_catalog() -> list[dict[str, Any]]:
    profiles = list(PROFILE_FEATURES)
    weights = [1.0, 1.1, 1.2, 1.3, 1.4]
    offsets = [-0.15, -0.05, 0.05, 0.15]
    candidates: list[dict[str, Any]] = []
    for index, (profile, weight, offset) in enumerate(itertools.product(profiles, weights, offsets), start=1):
        candidate = {
            "candidate_id": f"C{index:03d}",
            "profile": profile,
            "features": PROFILE_FEATURES[profile],
            "positive_class_weight": weight,
            "draw_logit_offset": offset,
            "l2_grid": PROFILE_L2[profile],
            "generation_index": index,
        }
        candidate["candidate_sha256"] = canonical_json_sha256(candidate)
        candidates.append(candidate)
    if len(candidates) != 200 or len({item["candidate_sha256"] for item in candidates}) != 200:
        raise ValueError("candidate catalog integrity failure")
    return candidates


@dataclass
class Preprocessor:
    original_features: list[str]
    kept_features: list[str]
    medians: list[float]
    means: list[float]
    scales: list[float]
    missing_indicator_features: list[str]
    dropped: dict[str, str]
    fit_row_keys_sha256: str
    evaluation_rows_used_for_decisions: int = 0

    @classmethod
    def fit(cls, rows: Sequence[MatchRow], features: Sequence[str]) -> "Preprocessor":
        if not rows:
            raise ValueError("empty preprocessing training rows")
        feature_rows = [feature_values(row) for row in rows]
        raw = np.asarray([[values[name] for name in features] for values in feature_rows], dtype=float)
        medians: list[float] = []
        missing_indicators: list[str] = []
        imputed = raw.copy()
        for index, name in enumerate(features):
            column = raw[:, index]
            finite = column[np.isfinite(column)]
            median = float(np.median(finite)) if len(finite) else 0.0
            medians.append(median)
            if np.any(~np.isfinite(column)):
                missing_indicators.append(name)
            imputed[:, index] = np.where(np.isfinite(column), column, median)
        means = imputed.mean(axis=0)
        scales = imputed.std(axis=0)
        standardized = np.zeros_like(imputed)
        dropped: dict[str, str] = {}
        preliminary: list[int] = []
        for index, name in enumerate(features):
            if not math.isfinite(float(scales[index])) or float(scales[index]) < 1e-12:
                dropped[name] = "near_zero_variance_training_only"
                continue
            standardized[:, index] = (imputed[:, index] - means[index]) / scales[index]
            preliminary.append(index)
        kept_indices: list[int] = []
        for index in preliminary:
            duplicate_of: str | None = None
            for earlier in kept_indices:
                left, right = standardized[:, earlier], standardized[:, index]
                if float(np.max(np.abs(left - right))) <= 1e-12:
                    duplicate_of = features[earlier]
                    break
                correlation = float(np.corrcoef(left, right)[0, 1])
                if math.isfinite(correlation) and abs(correlation) >= 0.999999:
                    duplicate_of = features[earlier]
                    break
            if duplicate_of:
                dropped[features[index]] = f"training_only_duplicate_or_correlation:{duplicate_of}"
            else:
                kept_indices.append(index)
        keys_hash = canonical_json_sha256(sorted(row.key for row in rows))
        return cls(
            original_features=list(features),
            kept_features=[features[index] for index in kept_indices],
            medians=medians, means=[float(value) for value in means], scales=[float(value) for value in scales],
            missing_indicator_features=missing_indicators, dropped=dropped,
            fit_row_keys_sha256=keys_hash,
        )

    def transform(self, rows: Sequence[MatchRow]) -> np.ndarray:
        feature_rows = [feature_values(row) for row in rows]
        index_by_name = {name: index for index, name in enumerate(self.original_features)}
        columns: list[np.ndarray] = []
        for name in self.kept_features:
            index = index_by_name[name]
            raw = np.asarray([values[name] for values in feature_rows], dtype=float)
            missing = ~np.isfinite(raw)
            imputed = np.where(np.isfinite(raw), raw, self.medians[index])
            columns.append((imputed - self.means[index]) / self.scales[index])
            if name in self.missing_indicator_features:
                columns.append(missing.astype(float))
        if not columns:
            return np.zeros((len(rows), 0), dtype=float)
        matrix = np.column_stack(columns)
        if not np.all(np.isfinite(matrix)):
            raise ValueError("nonfinite transformed matrix")
        return matrix

    def receipt(self) -> dict[str, Any]:
        return {
            "original_features": self.original_features,
            "kept_features": self.kept_features,
            "missing_indicator_features": self.missing_indicator_features,
            "dropped": self.dropped,
            "fit_row_keys_sha256": self.fit_row_keys_sha256,
            "evaluation_rows_used_for_decisions": self.evaluation_rows_used_for_decisions,
        }


def _base_probabilities(train_rows: Sequence[MatchRow], rows: Sequence[MatchRow], draw_logit_offset: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    draws = sum(row.label == "D" for row in train_rows)
    draw_rate = (draws + 1.0) / (len(train_rows) + 2.0)
    draw = np.full(len(rows), 1.0 / (1.0 + math.exp(-(logit(draw_rate) + draw_logit_offset))), dtype=float)
    elo = np.asarray([row.values["elo_difference_with_home_advantage"] for row in rows], dtype=float)
    elo = np.where(np.isfinite(elo), elo, 60.0)
    return hda_from_draw_and_elo(draw, elo), np.full(len(rows), logit(draw_rate) + draw_logit_offset)


def _labels(rows: Sequence[MatchRow]) -> tuple[list[str], np.ndarray]:
    labels = [row.label for row in rows]
    binary = np.asarray([1.0 if label == "D" else 0.0 for label in labels], dtype=float)
    return labels, binary


def _fit_for_l2(train_rows: Sequence[MatchRow], validation_rows: Sequence[MatchRow], candidate: dict[str, Any], l2: float) -> tuple[dict[str, Any], float]:
    processor = Preprocessor.fit(train_rows, candidate["features"])
    x_train = processor.transform(train_rows)
    x_validation = processor.transform(validation_rows)
    _, train_offset = _base_probabilities(train_rows, train_rows, candidate["draw_logit_offset"])
    _, validation_offset = _base_probabilities(train_rows, validation_rows, candidate["draw_logit_offset"])
    _, y_train = _labels(train_rows)
    labels_validation, _ = _labels(validation_rows)
    fit = fit_offset_logistic(x_train, y_train, train_offset, l2=float(l2), positive_weight=float(candidate["positive_class_weight"]))
    if not fit.converged or not fit.probability_gate_pass:
        return {"l2": l2, "fit": fit.as_dict(), "preprocessing": processor.receipt()}, math.inf
    draw = predict_draw(fit, x_validation, validation_offset)
    elo = np.asarray([row.values["elo_difference_with_home_advantage"] for row in validation_rows], dtype=float)
    elo = np.where(np.isfinite(elo), elo, 60.0)
    prediction = hda_from_draw_and_elo(draw, elo)
    scored = metrics(prediction, labels_validation)
    objective = float(scored["Log Loss"] + scored["RPS"] - 0.05 * scored["Draw F1"])
    return {"l2": l2, "fit": fit.as_dict(), "preprocessing": processor.receipt(), "inner_metrics": scored, "objective": objective}, objective


def evaluate_candidate(candidate: dict[str, Any], folds: Sequence[OuterFold]) -> dict[str, Any]:
    started = time.monotonic()
    fold_results: list[dict[str, Any]] = []
    all_candidate_predictions: list[np.ndarray] = []
    all_baseline_predictions: list[np.ndarray] = []
    all_labels: list[str] = []
    all_fit_receipts: list[dict[str, Any]] = []
    all_preprocessing_receipts: list[dict[str, Any]] = []
    for fold in folds:
        inner_trials: list[dict[str, Any]] = []
        for l2 in candidate["l2_grid"]:
            trial, _ = _fit_for_l2(fold.inner_train_rows, fold.inner_validation_rows, candidate, float(l2))
            inner_trials.append(trial)
        valid_trials = [trial for trial in inner_trials if math.isfinite(float(trial.get("objective", math.inf)))]
        if not valid_trials:
            raise ValueError(f"all inner trials failed: {fold.fold_id}")
        selected = min(valid_trials, key=lambda item: (float(item["objective"]), float(item["l2"])))
        processor = Preprocessor.fit(fold.train_rows, candidate["features"])
        x_train = processor.transform(fold.train_rows)
        x_eval = processor.transform(fold.evaluation_rows)
        _, train_offset = _base_probabilities(fold.train_rows, fold.train_rows, candidate["draw_logit_offset"])
        baseline_target, eval_offset = _base_probabilities(fold.train_rows, fold.evaluation_rows, candidate["draw_logit_offset"])
        _, y_train = _labels(fold.train_rows)
        labels_eval, _ = _labels(fold.evaluation_rows)
        fit = fit_offset_logistic(x_train, y_train, train_offset, l2=float(selected["l2"]), positive_weight=float(candidate["positive_class_weight"]))
        if not fit.converged or not fit.probability_gate_pass:
            raise ValueError(f"outer fit failed: {fold.fold_id}: {fit.error}")
        draw = predict_draw(fit, x_eval, eval_offset)
        elo = np.asarray([row.values["elo_difference_with_home_advantage"] for row in fold.evaluation_rows], dtype=float)
        elo = np.where(np.isfinite(elo), elo, 60.0)
        candidate_prediction = hda_from_draw_and_elo(draw, elo)
        candidate_metrics = metrics(candidate_prediction, labels_eval)
        baseline_metrics = metrics(baseline_target, labels_eval)
        fit_receipt = fit.as_dict() | {"fold_id": fold.fold_id, "selected_l2": selected["l2"]}
        prep_receipt = processor.receipt() | {"fold_id": fold.fold_id}
        all_fit_receipts.append(fit_receipt)
        all_preprocessing_receipts.append(prep_receipt)
        fold_results.append({
            "fold_id": fold.fold_id,
            "competition": fold.competition,
            "target_season": fold.target_season,
            "prior_seasons": list(fold.prior_seasons),
            "inner_train_seasons": list(fold.inner_train_seasons),
            "inner_validation_season": fold.inner_validation_season,
            "selected_l2": selected["l2"],
            "inner_trials": inner_trials,
            "candidate_metrics": candidate_metrics,
            "baseline_metrics": baseline_metrics,
            "delta": metric_delta(candidate_metrics, baseline_metrics),
            "fit_receipt": fit_receipt,
            "preprocessing_receipt": prep_receipt,
            "row_count": len(labels_eval),
        })
        all_candidate_predictions.append(candidate_prediction)
        all_baseline_predictions.append(baseline_target)
        all_labels.extend(labels_eval)
    candidate_all = np.vstack(all_candidate_predictions)
    baseline_all = np.vstack(all_baseline_predictions)
    pooled_candidate = metrics(candidate_all, all_labels)
    pooled_baseline = metrics(baseline_all, all_labels)
    league_results: dict[str, dict[str, Any]] = {}
    for competition in sorted({fold.competition for fold in folds}):
        indices = [index for index, fold in enumerate(folds) if fold.competition == competition]
        predictions = np.vstack([all_candidate_predictions[index] for index in indices])
        baseline = np.vstack([all_baseline_predictions[index] for index in indices])
        labels: list[str] = []
        for index in indices:
            labels.extend([row.label for row in folds[index].evaluation_rows])
        scored = metrics(predictions, labels)
        base_scored = metrics(baseline, labels)
        league_results[competition] = {"candidate_metrics": scored, "baseline_metrics": base_scored, "delta": metric_delta(scored, base_scored)}
    delta = metric_delta(pooled_candidate, pooled_baseline)
    ranking_score = float(delta["Draw F1"] + 0.20 * delta["Macro-F1"] + 0.10 * delta["Accuracy"] - 0.20 * max(0.0, delta["Log Loss"]) - 0.15 * max(0.0, delta["RPS"]) - 0.10 * max(0.0, delta["Draw ECE"]))
    return {
        "schema_version": "DRAW-AUTO-CANDIDATE-RESULT-R1.0",
        "data_status": "VIEWED_DEVELOPMENT_DATA",
        "candidate": candidate,
        "status": "COMPLETED",
        "fold_count": len(fold_results),
        "pooled_candidate_metrics": pooled_candidate,
        "pooled_baseline_metrics": pooled_baseline,
        "pooled_delta": delta,
        "ranking_score": ranking_score,
        "fold_results": fold_results,
        "league_results": league_results,
        "fit_receipts": all_fit_receipts,
        "preprocessing_receipts": all_preprocessing_receipts,
        "safety_gates": {
            "all_51_folds_present": len(fold_results) == 51,
            "all_fits_converged": all(item["converged"] for item in all_fit_receipts),
            "probability_gates_pass": all(item["probability_gate_pass"] for item in all_fit_receipts),
            "evaluation_rows_used_for_preprocessing_decisions": sum(item["evaluation_rows_used_for_decisions"] for item in all_preprocessing_receipts),
            "random_split_used": False
        },
        "runtime_seconds": time.monotonic() - started,
    }


def validate_candidate_result(result: dict[str, Any]) -> None:
    if result.get("status") != "COMPLETED":
        raise ValueError("candidate result status incomplete")
    if result.get("fold_count") != 51 or len(result.get("fold_results") or []) != 51:
        raise ValueError("candidate fold completeness failure")
    if not result.get("league_results"):
        raise ValueError("candidate league results missing")
    gates = result.get("safety_gates") or {}
    if not gates.get("all_51_folds_present") or not gates.get("all_fits_converged") or not gates.get("probability_gates_pass"):
        raise ValueError("candidate numerical safety gate failed")
    if gates.get("evaluation_rows_used_for_preprocessing_decisions") != 0:
        raise ValueError("evaluation leakage gate failed")
    required_metrics = {"Accuracy", "Macro-F1", "Draw Precision", "Draw Recall", "Draw F1", "Log Loss", "Brier", "RPS", "Draw ECE", "Top-label ECE"}
    if not required_metrics.issubset(result.get("pooled_candidate_metrics") or {}):
        raise ValueError("candidate metrics incomplete")
