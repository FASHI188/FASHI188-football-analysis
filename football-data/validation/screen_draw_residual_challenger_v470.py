#!/usr/bin/env python3
"""Research-only draw residual challenger for all 17 competition domains.

The challenger is NOT registered for formal promotion under CURRENT V4.7.0 and
therefore always has formal weight 0. It tests whether PIT venue-specific team draw
tendencies add discrimination missing from the formal unified matrix.

Training uses only completed prior outer seasons. Target evaluation is the previous
complete season (2025 or 2025/26). The transform operates within each fixed total T:
diagonal score cells receive a learned exponential tilt and every T-specific vector
is renormalized, so the direct total-goals marginal P(T) is preserved exactly.
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from football_v460_engine import current_season_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    read_processed_matches,
    score_matrix_rows,
    top_scores,
)

FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
OUT = ROOT / "manifests" / "draw_residual_challenger_screen_v470_status.json"
EPS = 1e-12


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    p = min(1.0 - 1e-9, max(1e-9, p))
    return math.log(p / (1.0 - p))


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    matrix = [list(a[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(matrix[row][col]))
        if abs(matrix[pivot][col]) < 1e-12:
            raise PlatformError("singular logistic system")
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        scale = matrix[col][col]
        matrix[col] = [value / scale for value in matrix[col]]
        for row in range(n):
            if row == col:
                continue
            factor = matrix[row][col]
            if abs(factor) <= 1e-18:
                continue
            matrix[row] = [matrix[row][j] - factor * matrix[col][j] for j in range(n + 1)]
    return [matrix[i][-1] for i in range(n)]


def _fit_logistic(features: list[list[float]], labels: list[int], l2: float = 2.0) -> dict[str, Any]:
    if len(features) < 100 or len(features) != len(labels):
        raise PlatformError("insufficient draw residual training rows")
    d = len(features[0])
    means = [mean(row[j] for row in features) for j in range(d)]
    stds = [max(1e-6, pstdev(row[j] for row in features)) for j in range(d)]
    x = [[1.0] + [(row[j] - means[j]) / stds[j] for j in range(d)] for row in features]
    beta = [0.0] * (d + 1)
    converged = False
    for iteration in range(60):
        gradient = [0.0] * (d + 1)
        hessian = [[0.0] * (d + 1) for _ in range(d + 1)]
        for row, y in zip(x, labels):
            eta = sum(beta[j] * row[j] for j in range(d + 1))
            p = _sigmoid(eta)
            residual = y - p
            weight = max(1e-9, p * (1.0 - p))
            for j in range(d + 1):
                gradient[j] += row[j] * residual
                for k in range(d + 1):
                    hessian[j][k] += weight * row[j] * row[k]
        for j in range(1, d + 1):
            gradient[j] -= l2 * beta[j]
            hessian[j][j] += l2
        hessian[0][0] += 1e-8
        delta = _solve_linear(hessian, gradient)
        beta = [beta[j] + delta[j] for j in range(d + 1)]
        if max(abs(value) for value in delta) < 1e-7:
            converged = True
            break
    return {
        "beta": beta,
        "feature_means": means,
        "feature_stds": stds,
        "l2": l2,
        "iterations": iteration + 1,
        "converged": converged,
        "training_rows": len(features),
        "training_draw_rate": mean(labels),
    }


def _predict_logistic(model: dict[str, Any], features: list[float]) -> float:
    x = [1.0] + [
        (features[j] - model["feature_means"][j]) / model["feature_stds"][j]
        for j in range(len(features))
    ]
    return _sigmoid(sum(model["beta"][j] * x[j] for j in range(len(x))))


def _venue_draw_features(history, home_team: str, away_team: str, one: dict[str, float], matrix, prior_matches: float) -> list[float]:
    league_draw_rate = sum(1 for match in history if match.home_goals == match.away_goals) / max(1, len(history))
    home_key = normalize_team_token(home_team)
    away_key = normalize_team_token(away_team)
    home_venue = [match for match in history if normalize_team_token(match.home_team) == home_key]
    away_venue = [match for match in history if normalize_team_token(match.away_team) == away_key]
    home_draws = sum(1 for match in home_venue if match.home_goals == match.away_goals)
    away_draws = sum(1 for match in away_venue if match.home_goals == match.away_goals)
    home_rate = (home_draws + league_draw_rate * prior_matches) / (len(home_venue) + prior_matches)
    away_rate = (away_draws + league_draw_rate * prior_matches) / (len(away_venue) + prior_matches)
    pair_draw_residual = 0.5 * (home_rate + away_rate) - league_draw_rate
    balance = 1.0 - abs(float(one["home"]) - float(one["away"]))
    expected_total = sum((h + a) * p for h, a, p in score_matrix_rows(matrix))
    return [
        _logit(float(one["draw"])),
        pair_draw_residual,
        balance,
        expected_total,
        pair_draw_residual * balance,
    ]


def _draw_probability_after_lambda(matrix, lam: float) -> float:
    exp_lam = math.exp(max(-40.0, min(40.0, lam)))
    grouped: dict[int, list[tuple[int, int, float]]] = {}
    for h, a, p in score_matrix_rows(matrix):
        grouped.setdefault(h + a, []).append((h, a, p))
    draw = 0.0
    for items in grouped.values():
        total_mass = sum(p for _, _, p in items)
        if total_mass <= 0.0:
            continue
        weights = [(h, a, p * (exp_lam if h == a else 1.0)) for h, a, p in items]
        denominator = sum(w for _, _, w in weights)
        if denominator <= 0.0:
            continue
        for h, a, w in weights:
            if h == a:
                draw += total_mass * w / denominator
    return draw


def _tilt_diagonal_to_target(matrix, target_draw: float) -> tuple[list[dict[str, Any]], float, float]:
    even_mass = sum(p for h, a, p in score_matrix_rows(matrix) if (h + a) % 2 == 0)
    target = min(even_mass - 1e-9, max(1e-9, target_draw))
    low, high = -20.0, 20.0
    for _ in range(80):
        mid = 0.5 * (low + high)
        value = _draw_probability_after_lambda(matrix, mid)
        if value < target:
            low = mid
        else:
            high = mid
    lam = 0.5 * (low + high)
    exp_lam = math.exp(lam)
    grouped: dict[int, list[tuple[int, int, float]]] = {}
    for h, a, p in score_matrix_rows(matrix):
        grouped.setdefault(h + a, []).append((h, a, p))
    output = []
    max_total_residual = 0.0
    for total, items in grouped.items():
        total_mass = sum(p for _, _, p in items)
        weights = [(h, a, p * (exp_lam if h == a else 1.0)) for h, a, p in items]
        denominator = sum(w for _, _, w in weights)
        transformed = [(h, a, total_mass * w / denominator) for h, a, w in weights]
        max_total_residual = max(max_total_residual, abs(sum(p for _, _, p in transformed) - total_mass))
        output.extend({"home_goals": h, "away_goals": a, "probability": p} for h, a, p in transformed)
    return output, lam, max_total_residual


def _auc(scores: list[float], labels: list[int]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    pairs = sorted(zip(scores, labels), key=lambda item: item[0])
    rank_sum = 0.0
    i = 0
    rank = 1
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and abs(pairs[j][0] - pairs[i][0]) <= 1e-15:
            j += 1
        avg_rank = (rank + rank + j - i - 1) / 2.0
        rank_sum += avg_rank * sum(label for _, label in pairs[i:j])
        rank += j - i
        i = j
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _brier(one: dict[str, float], actual: str) -> float:
    return sum((float(one[key]) - (1.0 if key == actual else 0.0)) ** 2 for key in ("home", "draw", "away"))


def _rps(one: dict[str, float], actual: str) -> float:
    target = {"home": (1, 0, 0), "draw": (0, 1, 0), "away": (0, 0, 1)}[actual]
    c1 = float(one["home"]) - target[0]
    c2 = float(one["home"]) + float(one["draw"]) - target[0] - target[1]
    return 0.5 * (c1 * c1 + c2 * c2)


def _joint_log(matrix, hg: int, ag: int) -> float | None:
    p = sum(prob for h, a, prob in score_matrix_rows(matrix) if h == hg and a == ag)
    return -math.log(max(1e-15, p)) if p > 0 else None


def _binary_probability(matrix, predicate) -> float:
    return sum(p for h, a, p in score_matrix_rows(matrix) if predicate(h, a))


def _season_training_rows(cid: str, report: dict[str, Any], all_matches, target_season: str) -> tuple[list[list[float]], list[int], list[str]]:
    features: list[list[float]] = []
    labels: list[int] = []
    seasons_used: list[str] = []
    folds = report.get("folds") or []
    for fold in folds:
        season = str(fold.get("outer_season") or "")
        if not season or season == target_season:
            continue
        params = fold.get("selected_parameters")
        if not isinstance(params, dict):
            continue
        matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
        if not matches:
            continue
        temperature, _ = _target_season_temperature(cid, season)
        season_count = 0
        for match in matches:
            try:
                _, history = current_season_history(all_matches, match.date, season)
                matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
            except PlatformError:
                continue
            if abs(temperature - 1.0) > 1e-15:
                matrix = temperature_scale_matrix(matrix, temperature)
            one = derive_score_marginals(matrix)["1x2"]
            prior = float(params.get("team_prior_matches", 8.0))
            features.append(_venue_draw_features(history, match.home_team, match.away_team, one, matrix, prior))
            labels.append(1 if match.home_goals == match.away_goals else 0)
            season_count += 1
        if season_count:
            seasons_used.append(season)
    return features, labels, seasons_used


def screen(cid: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    target_params = target_fold.get("selected_parameters")
    if not isinstance(target_params, dict):
        raise PlatformError("target selected parameters missing")
    all_matches = read_processed_matches(cid)
    train_x, train_y, training_seasons = _season_training_rows(cid, report, all_matches, target_season)
    model = _fit_logistic(train_x, train_y)
    target_matches = sorted([m for m in all_matches if str(m.season) == target_season], key=lambda m: (m.date, m.home_team, m.away_team))
    temperature, calibration_mode = _target_season_temperature(cid, target_season)
    rows = []
    for match in target_matches:
        try:
            _, history = current_season_history(all_matches, match.date, target_season)
            baseline = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, target_season, target_params)
        except PlatformError:
            continue
        if abs(temperature - 1.0) > 1e-15:
            baseline = temperature_scale_matrix(baseline, temperature)
        base_m = derive_score_marginals(baseline)
        features = _venue_draw_features(history, match.home_team, match.away_team, base_m["1x2"], baseline, float(target_params.get("team_prior_matches", 8.0)))
        target_draw = _predict_logistic(model, features)
        candidate, lam, total_residual = _tilt_diagonal_to_target(baseline, target_draw)
        cand_m = derive_score_marginals(candidate)
        actual = "home" if match.home_goals > match.away_goals else "away" if match.home_goals < match.away_goals else "draw"
        actual_draw = 1 if actual == "draw" else 0
        base_log = _joint_log(baseline, int(match.home_goals), int(match.away_goals))
        cand_log = _joint_log(candidate, int(match.home_goals), int(match.away_goals))
        base_rank = top_scores(baseline, 3)
        cand_rank = top_scores(candidate, 3)
        actual_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
        structural_actual = {
            "btts": 1 if match.home_goals > 0 and match.away_goals > 0 else 0,
            "home_zero": 1 if match.home_goals == 0 else 0,
            "away_zero": 1 if match.away_goals == 0 else 0,
            "margin2plus": 1 if abs(match.home_goals - match.away_goals) >= 2 else 0,
        }
        rows.append({
            "actual": actual,
            "actual_draw": actual_draw,
            "base_draw": float(base_m["1x2"]["draw"]),
            "cand_draw": float(cand_m["1x2"]["draw"]),
            "base_brier": _brier(base_m["1x2"], actual),
            "cand_brier": _brier(cand_m["1x2"], actual),
            "base_rps": _rps(base_m["1x2"], actual),
            "cand_rps": _rps(cand_m["1x2"], actual),
            "base_log": base_log,
            "cand_log": cand_log,
            "base_hit": 1 if max(base_m["1x2"], key=base_m["1x2"].get) == actual else 0,
            "cand_hit": 1 if max(cand_m["1x2"], key=cand_m["1x2"].get) == actual else 0,
            "base_score_top1": 1 if base_rank and base_rank[0]["score"] == actual_score else 0,
            "cand_score_top1": 1 if cand_rank and cand_rank[0]["score"] == actual_score else 0,
            "base_score_top3": 1 if any(item["score"] == actual_score for item in base_rank) else 0,
            "cand_score_top3": 1 if any(item["score"] == actual_score for item in cand_rank) else 0,
            "lambda": lam,
            "total_residual": total_residual,
            "structural": {
                key: {
                    "actual": value,
                    "base": _binary_probability(baseline, predicate),
                    "cand": _binary_probability(candidate, predicate),
                }
                for key, value, predicate in (
                    ("btts", structural_actual["btts"], lambda h, a: h > 0 and a > 0),
                    ("home_zero", structural_actual["home_zero"], lambda h, a: h == 0),
                    ("away_zero", structural_actual["away_zero"], lambda h, a: a == 0),
                    ("margin2plus", structural_actual["margin2plus"], lambda h, a: abs(h - a) >= 2),
                )
            },
        })
    if not rows:
        raise PlatformError("no target evaluation rows")

    labels = [row["actual_draw"] for row in rows]
    base_draw = [row["base_draw"] for row in rows]
    cand_draw = [row["cand_draw"] for row in rows]
    structural = {}
    for key in ("btts", "home_zero", "away_zero", "margin2plus"):
        structural[key] = {
            "baseline_brier": mean((row["structural"][key]["base"] - row["structural"][key]["actual"]) ** 2 for row in rows),
            "candidate_brier": mean((row["structural"][key]["cand"] - row["structural"][key]["actual"]) ** 2 for row in rows),
        }
        structural[key]["candidate_minus_baseline"] = structural[key]["candidate_brier"] - structural[key]["baseline_brier"]

    baseline_log_rows = [row["base_log"] for row in rows if row["base_log"] is not None]
    candidate_log_rows = [row["cand_log"] for row in rows if row["cand_log"] is not None]
    metrics = {
        "count": len(rows),
        "one_x_two_accuracy": {"baseline": mean(row["base_hit"] for row in rows), "candidate": mean(row["cand_hit"] for row in rows)},
        "one_x_two_brier": {"baseline": mean(row["base_brier"] for row in rows), "candidate": mean(row["cand_brier"] for row in rows)},
        "one_x_two_rps": {"baseline": mean(row["base_rps"] for row in rows), "candidate": mean(row["cand_rps"] for row in rows)},
        "draw_brier": {
            "baseline": mean((row["base_draw"] - row["actual_draw"]) ** 2 for row in rows),
            "candidate": mean((row["cand_draw"] - row["actual_draw"]) ** 2 for row in rows),
        },
        "draw_auc": {"baseline": _auc(base_draw, labels), "candidate": _auc(cand_draw, labels)},
        "joint_log": {"baseline": mean(baseline_log_rows), "candidate": mean(candidate_log_rows)},
        "score_top1_accuracy": {"baseline": mean(row["base_score_top1"] for row in rows), "candidate": mean(row["cand_score_top1"] for row in rows)},
        "score_top3_accuracy": {"baseline": mean(row["base_score_top3"] for row in rows), "candidate": mean(row["cand_score_top3"] for row in rows)},
        "mean_lambda": mean(row["lambda"] for row in rows),
        "max_total_marginal_residual": max(row["total_residual"] for row in rows),
    }
    for key in ("one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "draw_brier", "draw_auc", "joint_log", "score_top1_accuracy", "score_top3_accuracy"):
        metrics[key]["candidate_minus_baseline"] = metrics[key]["candidate"] - metrics[key]["baseline"]

    checks = {
        "draw_brier_improves": metrics["draw_brier"]["candidate_minus_baseline"] < 0.0,
        "draw_auc_nonworse": metrics["draw_auc"]["candidate_minus_baseline"] >= -1e-9,
        "one_x_two_brier_nonworse": metrics["one_x_two_brier"]["candidate_minus_baseline"] <= 0.001,
        "one_x_two_rps_nonworse": metrics["one_x_two_rps"]["candidate_minus_baseline"] <= 0.001,
        "joint_log_nonworse": metrics["joint_log"]["candidate_minus_baseline"] <= 0.005,
        "total_marginal_preserved": metrics["max_total_marginal_residual"] <= 1e-10,
    }
    status = "RESEARCH_SIGNAL" if all(checks.values()) else "KEEP_FORMAL_WEIGHT_0"
    return {
        "competition_id": cid,
        "target_season": target_season,
        "training_seasons": training_seasons,
        "training_model": model,
        "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
        "metrics": metrics,
        "structural_brier": structural,
        "checks": checks,
        "status": status,
        "formal_weight": 0,
        "automatic_promotion": False,
        "probability_change": False,
        "governance_reason": "Research-only unregistered draw residual challenger. CURRENT V4.7.0 does not authorize formal promotion of this transform.",
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    for cid in competitions:
        try:
            reports[cid] = screen(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    signals = [cid for cid, report in reports.items() if report["status"] == "RESEARCH_SIGNAL"]
    payload = {
        "schema_version": "V4.7.0-draw-residual-challenger-screen-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "research_signal_competitions": signals,
        "reports": reports,
        "failures": failures,
        "governance": {
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
            "registered_in_current": False,
            "formal_use_requires_complete_current_upgrade": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "research_signal_competitions": signals, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
