#!/usr/bin/env python3
"""Research-only E3a: matrix-compatible conditional draw gate for the Big Five.

The experiment preserves the Champion direct total-goal marginal and all
non-central score ratios. For even totals, a time-ordered regularized logistic
gate estimates P(D=0 | T=t, X) and replaces only the central score mass.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from big5_high_completeness_b100 import (  # noqa: E402
    BIG5,
    TARGET_PER_LEAGUE,
    deterministic_rank,
    raw_rows,
)
from football_v460_engine import load_config, predict_from_history  # noqa: E402
from nested_backtest_v460 import _objective, evaluate_season  # noqa: E402
from platform_core import MatchRow, PlatformError, ROOT, read_processed_matches  # noqa: E402

OUT = ROOT.parent / "artifacts/research/matrix_draw_gate_e3a"
OUTCOMES = ("home", "draw", "away")
MODEL_TOTALS = (2, 4, 6)
EPS = 1e-15
MIN_GATE_ROWS = 100
MIN_GATE_CLASS = 20
L2 = 1.0
ITERATIONS = 1200


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def season_year(season: str) -> int:
    token = str(season)
    for index in range(max(0, len(token) - 3)):
        part = token[index:index + 4]
        if part.isdigit() and part.startswith("20"):
            return int(part)
    raise ValueError(f"season has no four-digit start year: {season!r}")


def team_counts(history: list[MatchRow]) -> Counter[str]:
    result: Counter[str] = Counter()
    for match in history:
        result[match.home_team] += 1
        result[match.away_team] += 1
    return result


def score_parts(prediction: dict[str, Any]) -> tuple[dict[int, float], dict[int, float]]:
    totals: Counter[int] = Counter()
    diagonal: Counter[int] = Counter()
    for cell in prediction["probabilities"]["score_matrix"]:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        probability = float(cell["probability"])
        totals[home + away] += probability
        if home == away:
            diagonal[home + away] += probability
    return dict(totals), dict(diagonal)


def base_record(match: MatchRow, prediction: dict[str, Any], sequence_index: int) -> dict[str, Any]:
    totals, diagonal = score_parts(prediction)
    one = prediction["probabilities"]["one_x_two"]
    sample = dict(prediction.get("team_sample", {}))
    share = float(sample.get("allocation_home_share", 0.5))
    base_q = {
        str(total): diagonal.get(total, 0.0) / max(EPS, probability)
        for total, probability in totals.items()
        if total % 2 == 0
    }
    return {
        "match_key": f"{match.season}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}",
        "competition_id": match.competition_id,
        "season": match.season,
        "date": match.date.date().isoformat(),
        "sequence_index": sequence_index,
        "actual_score": f"{match.home_goals}-{match.away_goals}",
        "actual_total": match.home_goals + match.away_goals,
        "actual_outcome": match.result,
        "actual_draw": match.home_goals == match.away_goals,
        "champion_probs": {label: float(one[label]) for label in OUTCOMES},
        "matrix": [
            {
                "home_goals": int(cell["home_goals"]),
                "away_goals": int(cell["away_goals"]),
                "probability": float(cell["probability"]),
            }
            for cell in prediction["probabilities"]["score_matrix"]
        ],
        "p_total_exact": {str(key): float(value) for key, value in totals.items()},
        "base_q": {key: float(value) for key, value in base_q.items()},
        "strength_gap": abs(float(one["home"]) - float(one["away"])),
        "allocation_gap": abs(share - 0.5),
        "team_sample": sample,
    }


def evaluate_structure(
    competition_id: str,
    matches: list[MatchRow],
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    validation = load_config()["validation"]
    warmup_competition = int(validation["warmup_competition_matches"])
    warmup_team = int(validation["warmup_team_matches"])
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for match in matches:
        by_date[match.date].append(match)

    history: list[MatchRow] = []
    records: list[dict[str, Any]] = []
    sequence_index = 0
    for date in sorted(by_date):
        counts = team_counts(history)
        for match in sorted(by_date[date], key=lambda item: (item.home_team, item.away_team)):
            if (
                len(history) >= warmup_competition
                and counts[match.home_team] >= warmup_team
                and counts[match.away_team] >= warmup_team
            ):
                try:
                    prediction = predict_from_history(
                        history,
                        competition_id,
                        match.season,
                        match.home_team,
                        match.away_team,
                        match.date,
                        parameters,
                        use_team_effects=True,
                    )
                except PlatformError:
                    continue
                records.append(base_record(match, prediction, sequence_index))
                sequence_index += 1
        history.extend(by_date[date])
        history.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    return records


def nested_competition(competition_id: str) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    matches = read_processed_matches(competition_id)
    grouped: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        grouped[match.season].append(match)
    seasons = sorted(grouped, key=lambda season: min(item.date for item in grouped[season]))

    config = load_config()
    candidates = config["candidate_parameters"]
    cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for candidate_index, candidate in enumerate(candidates):
        for season in seasons:
            ordered = sorted(grouped[season], key=lambda item: (item.date, item.home_team, item.away_team))
            cache[candidate_index][season] = evaluate_season(
                competition_id, ordered, candidate, use_team_effects=True
            )

    by_outer_season: dict[str, list[dict[str, Any]]] = {}
    folds = []
    for outer_index in range(1, len(seasons)):
        season = seasons[outer_index]
        prior = seasons[:outer_index]
        ranked = []
        for candidate_index, candidate in enumerate(candidates):
            prior_records = [
                record
                for prior_season in prior
                for record in cache[candidate_index][prior_season]
            ]
            ranked.append(
                (_objective(prior_records), candidate_index, candidate, len(prior_records))
            )
        objective, selected_index, selected_parameters, selection_count = sorted(
            ranked, key=lambda item: (item[0], item[1])
        )[0]
        ordered = sorted(grouped[season], key=lambda item: (item.date, item.home_team, item.away_team))
        records = evaluate_structure(competition_id, ordered, selected_parameters)
        expected_keys = {record["match_key"] for record in cache[selected_index][season]}
        actual_keys = {record["match_key"] for record in records}
        if actual_keys != expected_keys:
            raise RuntimeError(f"base OOS identity mismatch: {competition_id} {season}")
        by_outer_season[season] = records
        folds.append({
            "outer_season": season,
            "prior_seasons": prior,
            "selected_candidate_index": selected_index,
            "selected_parameters": selected_parameters,
            "selection_objective": objective,
            "selection_prediction_count": selection_count,
            "oos_records": len(records),
            "record_identity_check": "PASS",
        })
    return by_outer_season, folds


def clip_probability(value: float) -> float:
    return min(1.0 - 1e-8, max(1e-8, float(value)))


def logit(value: float) -> float:
    probability = clip_probability(value)
    return math.log(probability / (1.0 - probability))


def sigmoid(value: float) -> float:
    if value >= 0:
        e = math.exp(-min(value, 50.0))
        return 1.0 / (1.0 + e)
    e = math.exp(max(value, -50.0))
    return e / (1.0 + e)


def feature_vector(record: dict[str, Any], total: int) -> list[float]:
    sample = record["team_sample"]
    home_signal = float(sample.get("home_score_signal", 0.0))
    away_signal = float(sample.get("away_score_signal", 0.0))
    home_total_rate = float(sample.get("home_direct_total_rate", 0.0))
    away_total_rate = float(sample.get("away_direct_total_rate", 0.0))
    score_signal_gap = abs(home_signal - away_signal) / max(EPS, home_signal + away_signal)
    total_rate_gap = abs(home_total_rate - away_total_rate) / max(
        EPS, home_total_rate + away_total_rate
    )
    return [
        logit(float(record["base_q"].get(str(total), 0.5))),
        float(record["strength_gap"]),
        float(record["allocation_gap"]),
        float(record["p_total_exact"].get(str(total), 0.0)),
        float(sample.get("mu_total", 0.0)),
        math.log1p(max(0.0, float(sample.get("ess", 0.0)))),
        score_signal_gap,
        total_rate_gap,
        float(record["champion_probs"]["draw"]),
    ]


def logistic_loss(
    x_rows: list[list[float]],
    y_rows: list[float],
    weights: list[float],
    l2: float,
) -> float:
    total = 0.0
    for row, target in zip(x_rows, y_rows):
        probability = clip_probability(sigmoid(sum(w * x for w, x in zip(weights, row))))
        total += -(target * math.log(probability) + (1.0 - target) * math.log(1.0 - probability))
    penalty = 0.5 * l2 * sum(value * value for value in weights[1:])
    return (total + penalty) / max(1, len(x_rows))


def fit_gate(records: list[dict[str, Any]], total: int) -> dict[str, Any]:
    subset = [record for record in records if int(record["actual_total"]) == total]
    positives = sum(bool(record["actual_draw"]) for record in subset)
    negatives = len(subset) - positives
    if len(subset) < MIN_GATE_ROWS or positives < MIN_GATE_CLASS or negatives < MIN_GATE_CLASS:
        return {
            "status": "FALLBACK_BASELINE",
            "total": total,
            "training_rows": len(subset),
            "positives": positives,
            "negatives": negatives,
        }

    raw = [feature_vector(record, total) for record in subset]
    dimensions = len(raw[0])
    means = [mean(row[index] for row in raw) for index in range(dimensions)]
    scales = []
    for index in range(dimensions):
        variance = mean((row[index] - means[index]) ** 2 for row in raw)
        scales.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
    x_rows = [
        [1.0] + [(row[index] - means[index]) / scales[index] for index in range(dimensions)]
        for row in raw
    ]
    y_rows = [1.0 if record["actual_draw"] else 0.0 for record in subset]
    base_rate = clip_probability(mean(y_rows))
    weights = [logit(base_rate)] + [0.0] * dimensions
    current_loss = logistic_loss(x_rows, y_rows, weights, L2)

    for iteration in range(ITERATIONS):
        probabilities = [
            sigmoid(sum(weight * value for weight, value in zip(weights, row)))
            for row in x_rows
        ]
        gradient = [0.0] * len(weights)
        n = len(x_rows)
        for row, target, probability in zip(x_rows, y_rows, probabilities):
            error = probability - target
            for index, value in enumerate(row):
                gradient[index] += error * value / n
        for index in range(1, len(weights)):
            gradient[index] += L2 * weights[index] / n
        norm = math.sqrt(sum(value * value for value in gradient))
        if norm < 1e-7:
            break

        step = 0.5 / math.sqrt(1.0 + iteration / 100.0)
        accepted = False
        for _ in range(12):
            candidate = [
                weight - step * grad for weight, grad in zip(weights, gradient)
            ]
            candidate_loss = logistic_loss(x_rows, y_rows, candidate, L2)
            if candidate_loss <= current_loss + 1e-12:
                weights = candidate
                current_loss = candidate_loss
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break

    return {
        "status": "TRAINED",
        "total": total,
        "training_rows": len(subset),
        "positives": positives,
        "negatives": negatives,
        "feature_means": means,
        "feature_scales": scales,
        "weights": weights,
        "l2": L2,
        "iterations_limit": ITERATIONS,
        "training_logloss": current_loss,
    }


def predict_gate(model: dict[str, Any], record: dict[str, Any], total: int) -> float:
    if model.get("status") != "TRAINED":
        return float(record["base_q"].get(str(total), 0.0))
    raw = feature_vector(record, total)
    standardized = [1.0] + [
        (raw[index] - model["feature_means"][index]) / model["feature_scales"][index]
        for index in range(len(raw))
    ]
    return clip_probability(
        sigmoid(sum(weight * value for weight, value in zip(model["weights"], standardized)))
    )


def adjust_matrix(
    record: dict[str, Any],
    models: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    matrix = [dict(cell) for cell in record["matrix"]]
    grouped: dict[int, list[int]] = defaultdict(list)
    for index, cell in enumerate(matrix):
        grouped[int(cell["home_goals"]) + int(cell["away_goals"])].append(index)

    e3a_q: dict[str, float] = {}
    maximum_total_residual = 0.0
    for total, indices in grouped.items():
        before_total = sum(float(matrix[index]["probability"]) for index in indices)
        if total == 0:
            e3a_q[str(total)] = 1.0
            continue
        if total not in models or total % 2 != 0:
            if total % 2 == 0:
                e3a_q[str(total)] = float(record["base_q"].get(str(total), 0.0))
            continue

        center_indices = [
            index
            for index in indices
            if int(matrix[index]["home_goals"]) == int(matrix[index]["away_goals"])
        ]
        if len(center_indices) != 1:
            raise RuntimeError(f"central cell count invalid for total={total}")
        center_index = center_indices[0]
        noncentral = [index for index in indices if index != center_index]
        noncentral_sum = sum(float(matrix[index]["probability"]) for index in noncentral)
        target_q = predict_gate(models[total], record, total)
        e3a_q[str(total)] = target_q
        matrix[center_index]["probability"] = before_total * target_q
        if noncentral_sum <= 0:
            raise RuntimeError(f"noncentral mass missing for total={total}")
        scale = before_total * (1.0 - target_q) / noncentral_sum
        for index in noncentral:
            matrix[index]["probability"] = float(matrix[index]["probability"]) * scale
        after_total = sum(float(matrix[index]["probability"]) for index in indices)
        maximum_total_residual = max(maximum_total_residual, abs(after_total - before_total))

    probability_sum = sum(float(cell["probability"]) for cell in matrix)
    if abs(probability_sum - 1.0) > 1e-10:
        for cell in matrix:
            cell["probability"] = float(cell["probability"]) / probability_sum
    probabilities = {label: 0.0 for label in OUTCOMES}
    total_after: Counter[int] = Counter()
    for cell in matrix:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        probability = float(cell["probability"])
        total_after[home + away] += probability
        label = "home" if home > away else "draw" if home == away else "away"
        probabilities[label] += probability

    maximum_total_residual = max(
        maximum_total_residual,
        max(
            abs(float(record["p_total_exact"].get(str(total), 0.0)) - probability)
            for total, probability in total_after.items()
        ),
    )
    return {
        **record,
        "e3a_probs": probabilities,
        "e3a_matrix": matrix,
        "e3a_q": e3a_q,
        "matrix_probability_residual": abs(sum(probabilities.values()) - 1.0),
        "maximum_total_marginal_residual": maximum_total_residual,
    }


def classification_metrics(records: list[dict[str, Any]], probability_field: str) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    confusion = {actual: {predicted: 0 for predicted in OUTCOMES} for actual in OUTCOMES}
    logloss_values = []
    brier_values = []
    rps_values = []
    for record in records:
        actual = record["actual_outcome"]
        probabilities = {
            label: float(record[probability_field][label]) for label in OUTCOMES
        }
        predicted = max(
            OUTCOMES,
            key=lambda label: (probabilities[label], -OUTCOMES.index(label)),
        )
        confusion[actual][predicted] += 1
        logloss_values.append(-math.log(max(EPS, probabilities[actual])))
        brier_values.append(
            sum(
                (probabilities[label] - (1.0 if label == actual else 0.0)) ** 2
                for label in OUTCOMES
            )
        )
        cumulative_p = 0.0
        cumulative_o = 0.0
        rps = 0.0
        actual_index = OUTCOMES.index(actual)
        for index in range(len(OUTCOMES) - 1):
            cumulative_p += probabilities[OUTCOMES[index]]
            cumulative_o += 1.0 if actual_index == index else 0.0
            rps += (cumulative_p - cumulative_o) ** 2
        rps_values.append(rps / 2.0)

    per_class = {}
    for label in OUTCOMES:
        true_positive = confusion[label][label]
        actual_count = sum(confusion[label].values())
        predicted_count = sum(confusion[actual][label] for actual in OUTCOMES)
        precision = true_positive / predicted_count if predicted_count else 0.0
        recall = true_positive / actual_count if actual_count else 0.0
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "actual_count": actual_count,
            "predicted_count": predicted_count,
        }
    correct = sum(confusion[label][label] for label in OUTCOMES)
    return {
        "count": len(records),
        "accuracy": correct / len(records),
        "balanced_accuracy": mean(per_class[label]["recall"] for label in OUTCOMES),
        "macro_f1": mean(per_class[label]["f1"] for label in OUTCOMES),
        "logloss": mean(logloss_values),
        "brier": mean(brier_values),
        "rps": mean(rps_values),
        "draw_precision": per_class["draw"]["precision"],
        "draw_recall": per_class["draw"]["recall"],
        "draw_f1": per_class["draw"]["f1"],
        "per_class": per_class,
        "confusion_matrix_actual_rows": confusion,
    }


def conditional_central_mae(
    records: list[dict[str, Any]],
    q_field: str,
) -> dict[str, Any]:
    rows = {}
    weighted_error = 0.0
    total_rows = 0
    for total in (0, 2, 4, 6):
        subset = [record for record in records if int(record["actual_total"]) == total]
        if not subset:
            continue
        if q_field == "base_q":
            predicted = mean(float(record["base_q"].get(str(total), 0.0)) for record in subset)
        else:
            predicted = mean(float(record["e3a_q"].get(str(total), record["base_q"].get(str(total), 0.0))) for record in subset)
        observed = mean(float(record["actual_draw"]) for record in subset)
        residual = predicted - observed
        rows[str(total)] = {
            "count": len(subset),
            "predicted": predicted,
            "observed": observed,
            "residual": residual,
            "absolute_error": abs(residual),
        }
        weighted_error += len(subset) * abs(residual)
        total_rows += len(subset)
    return {
        "by_total": rows,
        "weighted_mean_absolute_error": weighted_error / total_rows if total_rows else None,
        "rows": total_rows,
    }


def deltas(champion: dict[str, Any], e3a: dict[str, Any]) -> dict[str, float]:
    keys = (
        "accuracy",
        "balanced_accuracy",
        "macro_f1",
        "logloss",
        "brier",
        "rps",
        "draw_precision",
        "draw_recall",
        "draw_f1",
    )
    return {key: float(e3a[key]) - float(champion[key]) for key in keys}


def fixed_b100(records_by_competition: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_all = []
    metadata = {}
    for competition_id in BIG5:
        raw = raw_rows(competition_id)
        oos = {record["match_key"]: record for record in records_by_competition[competition_id]}
        seasons = sorted(
            {record["season"] for record in oos.values()},
            key=season_year,
            reverse=True,
        )
        selected_season = None
        candidates = []
        for season in seasons:
            eligible = [
                oos[key]
                for key, meta in raw.items()
                if meta["season"] == season
                and meta["quality"]["passed"]
                and key in oos
            ]
            if len(eligible) >= TARGET_PER_LEAGUE:
                selected_season = season
                candidates = eligible
                break
        ordered = sorted(
            candidates,
            key=lambda record: deterministic_rank(competition_id, record["match_key"]),
        )
        selected = ordered[:TARGET_PER_LEAGUE]
        selected_all.extend(selected)
        metadata[competition_id] = {
            "selected_season": selected_season,
            "strict_complete_oos_candidates": len(candidates),
            "selected_count": len(selected),
            "selected_match_keys": [record["match_key"] for record in selected],
        }
    return selected_all, metadata


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# E3a Matrix-Compatible Conditional Draw Gate",
        "",
        "Research-only; no formal mutation or automatic promotion.",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Full Big Five time-ordered OOS records: {report['full_oos']['count']}",
        f"- Fixed B100 records: {report['b100']['count']}",
        f"- Maximum total-marginal residual: {report['audit']['maximum_total_marginal_residual']:.3e}",
        f"- Maximum matrix probability residual: {report['audit']['maximum_matrix_probability_residual']:.3e}",
        "",
        "## Full Big Five rolling OOS",
        "",
    ]
    for label in ("champion", "e3a"):
        metric = report["full_oos"][label]
        lines += [
            f"### {label}",
            f"- Accuracy: {metric['accuracy']:.4%}",
            f"- Balanced accuracy: {metric['balanced_accuracy']:.4%}",
            f"- Macro-F1: {metric['macro_f1']:.4%}",
            f"- Draw precision / recall / F1: {metric['draw_precision']:.4%} / {metric['draw_recall']:.4%} / {metric['draw_f1']:.4%}",
            f"- LogLoss / Brier / RPS: {metric['logloss']:.6f} / {metric['brier']:.6f} / {metric['rps']:.6f}",
            "",
        ]
    lines += ["## Fixed high-completeness B100", ""]
    for label in ("champion", "e3a"):
        metric = report["b100"][label]
        lines += [
            f"### {label}",
            f"- Accuracy: {metric['accuracy']:.4%}",
            f"- Balanced accuracy: {metric['balanced_accuracy']:.4%}",
            f"- Macro-F1: {metric['macro_f1']:.4%}",
            f"- Draw precision / recall / F1: {metric['draw_precision']:.4%} / {metric['draw_recall']:.4%} / {metric['draw_f1']:.4%}",
            f"- LogLoss / Brier / RPS: {metric['logloss']:.6f} / {metric['brier']:.6f} / {metric['rps']:.6f}",
            "",
        ]
    lines += [
        f"- Full-OOS conditional central MAE: Champion={report['full_oos']['central_calibration']['champion']['weighted_mean_absolute_error']}, E3a={report['full_oos']['central_calibration']['e3a']['weighted_mean_absolute_error']}",
        f"- B100 conditional central MAE: Champion={report['b100']['central_calibration']['champion']['weighted_mean_absolute_error']}, E3a={report['b100']['central_calibration']['e3a']['weighted_mean_absolute_error']}",
        "",
        "The direct total-goal marginal is preserved by construction. This report is a challenge-layer result only.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    by_competition_season = {}
    folds = {}
    failures = []
    for competition_id in BIG5:
        try:
            season_records, competition_folds = nested_competition(competition_id)
            by_competition_season[competition_id] = season_records
            folds[competition_id] = competition_folds
        except Exception as exc:
            failures.append({
                "competition_id": competition_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    if failures:
        report = {
            "schema_version": "1.0",
            "research_status": "FAIL",
            "repository_head": repository_head(),
            "failures": failures,
            "formal_mutation": {"model": 0, "data": 0, "config": 0, "current": 0},
        }
        (output_dir / "matrix_draw_gate_e3a.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "matrix_draw_gate_e3a.md").write_text(
            "# E3a Matrix-Compatible Conditional Draw Gate\n\nExecution failed.\n",
            encoding="utf-8",
        )
        return 1

    records_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for competition_id, season_map in by_competition_season.items():
        for season, records in season_map.items():
            year = season_year(season)
            records_by_year[year].extend(records)

    adjusted_all = []
    gate_folds = []
    years = sorted(records_by_year)
    for year in years:
        training = [
            record
            for prior_year in years
            if prior_year < year
            for record in records_by_year[prior_year]
        ]
        models = {total: fit_gate(training, total) for total in MODEL_TOTALS}
        current = records_by_year[year]
        adjusted = [adjust_matrix(record, models) for record in current]
        adjusted_all.extend(adjusted)
        gate_folds.append({
            "target_season_start_year": year,
            "training_records": len(training),
            "target_records": len(current),
            "models": models,
        })

    adjusted_by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in adjusted_all:
        adjusted_by_competition[record["competition_id"]].append(record)

    b100_records, b100_meta = fixed_b100(adjusted_by_competition)
    champion_full = classification_metrics(adjusted_all, "champion_probs")
    e3a_full = classification_metrics(adjusted_all, "e3a_probs")
    champion_b100 = classification_metrics(b100_records, "champion_probs")
    e3a_b100 = classification_metrics(b100_records, "e3a_probs")

    maximum_total_residual = max(
        (float(record["maximum_total_marginal_residual"]) for record in adjusted_all),
        default=0.0,
    )
    maximum_matrix_residual = max(
        (float(record["matrix_probability_residual"]) for record in adjusted_all),
        default=0.0,
    )
    b100_count = len(b100_records)
    audit_pass = maximum_total_residual <= 1e-10 and maximum_matrix_residual <= 1e-10
    status = (
        "PASS"
        if not failures
        and b100_count == TARGET_PER_LEAGUE * len(BIG5)
        and audit_pass
        else "FAIL"
    )

    report = {
        "schema_version": "1.0",
        "research_status": status,
        "repository_head": repository_head(),
        "scope": "90_minutes_including_stoppage",
        "experiment": "E3A_MATRIX_COMPATIBLE_CONDITIONAL_DRAW_GATE",
        "architecture": {
            "preserved": [
                "direct_total_goal_marginal",
                "noncentral_conditional_score_ratios",
                "unified_score_matrix",
            ],
            "replaced": "central conditional mass for T=2,4,6",
            "model": "fixed L2-regularized logistic gate, pooled Big Five, prior seasons only",
            "model_totals": list(MODEL_TOTALS),
            "minimum_rows": MIN_GATE_ROWS,
            "minimum_class_rows": MIN_GATE_CLASS,
            "l2": L2,
            "iterations_limit": ITERATIONS,
        },
        "full_oos": {
            "count": len(adjusted_all),
            "champion": champion_full,
            "e3a": e3a_full,
            "delta_e3a_minus_champion": deltas(champion_full, e3a_full),
            "central_calibration": {
                "champion": conditional_central_mae(adjusted_all, "base_q"),
                "e3a": conditional_central_mae(adjusted_all, "e3a_q"),
            },
        },
        "b100": {
            "count": b100_count,
            "selection": b100_meta,
            "champion": champion_b100,
            "e3a": e3a_b100,
            "delta_e3a_minus_champion": deltas(champion_b100, e3a_b100),
            "central_calibration": {
                "champion": conditional_central_mae(b100_records, "base_q"),
                "e3a": conditional_central_mae(b100_records, "e3a_q"),
            },
            "records": [
                {
                    "match_key": record["match_key"],
                    "competition_id": record["competition_id"],
                    "actual_score": record["actual_score"],
                    "actual_outcome": record["actual_outcome"],
                    "champion_probs": record["champion_probs"],
                    "e3a_probs": record["e3a_probs"],
                    "base_q": record["base_q"],
                    "e3a_q": record["e3a_q"],
                }
                for record in b100_records
            ],
        },
        "gate_folds": gate_folds,
        "base_parameter_folds": folds,
        "audit": {
            "maximum_total_marginal_residual": maximum_total_residual,
            "maximum_matrix_probability_residual": maximum_matrix_residual,
            "total_marginal_preservation": "PASS" if maximum_total_residual <= 1e-10 else "FAIL",
            "probability_conservation": "PASS" if maximum_matrix_residual <= 1e-10 else "FAIL",
        },
        "promotion": {
            "automatic_promotion": False,
            "formal_weight": 0,
            "status": "CHALLENGE_LAYER_ONLY_PENDING_CODEX_AND_USER_REVIEW",
        },
        "failures": failures,
        "formal_mutation": {"model": 0, "data": 0, "config": 0, "current": 0},
    }

    (output_dir / "matrix_draw_gate_e3a.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "matrix_draw_gate_e3a.md").write_text(
        markdown(report), encoding="utf-8"
    )
    if args.print_summary:
        print(json.dumps({
            "status": status,
            "full_oos": {
                "count": len(adjusted_all),
                "champion": champion_full,
                "e3a": e3a_full,
                "delta": report["full_oos"]["delta_e3a_minus_champion"],
            },
            "b100": {
                "count": b100_count,
                "champion": champion_b100,
                "e3a": e3a_b100,
                "delta": report["b100"]["delta_e3a_minus_champion"],
            },
            "audit": report["audit"],
            "failures": failures,
        }, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
