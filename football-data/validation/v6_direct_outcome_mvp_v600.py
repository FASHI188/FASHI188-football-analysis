#!/usr/bin/env python3
"""V6.0.0 direct-outcome MVP challenge.

This experiment is intentionally independent of the formal score engine's decision rule.
It uses only information available strictly before each match:
- formal frozen 1X2 probabilities as a strong baseline signal;
- dynamic Elo difference;
- exponentially updated team form, goal-difference and draw propensity;
- competition draw rate, expected total goals and rest-day difference.

Two binary experts are trained:
1) draw versus decisive result;
2) home versus away conditional on a decisive result.
Their probabilities are recombined into one 1X2 distribution and optionally log-pooled
with the formal baseline. Hyperparameters are selected on the penultimate completed
outer season only, then evaluated on the last completed season. The experiment is a
research challenge only and never mutates CURRENT, formal weights or runtime outputs.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    _actual_result,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    atomic_write_json,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    read_processed_matches,
    score_matrix_rows,
)

OUT = ROOT / "manifests" / "v6_direct_outcome_mvp_v600_status.json"
CLASSES = ("home", "draw", "away")
EPS = 1e-12
L2_GRID = (0.1, 1.0, 10.0, 50.0, 200.0)
POOL_GRID = (0.25, 0.5, 0.75, 1.0)
FORM_ALPHA = 0.18
ELO_K = 22.0


@dataclass
class TeamState:
    elo: float = 1500.0
    ppg: float = 1.35
    gd: float = 0.0
    gf: float = 1.30
    ga: float = 1.30
    draw_rate: float = 0.26
    matches: int = 0
    last_date: datetime | None = None


@dataclass
class CompetitionState:
    matches: int = 0
    draws: int = 0
    total_goals: int = 0


def _team_key(name: str) -> str:
    return normalize_team_token(name)


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _logit(probability: float) -> float:
    p = _clip(float(probability), 1e-8, 1.0 - 1e-8)
    return math.log(p / (1.0 - p))


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        z = math.exp(-min(700.0, value))
        return 1.0 / (1.0 + z)
    z = math.exp(max(-700.0, value))
    return z / (1.0 + z)


def _expected_total(matrix: list[dict[str, Any]]) -> float:
    return sum((home + away) * probability for home, away, probability in score_matrix_rows(matrix))


def _rest_days(state: TeamState, date: datetime) -> float:
    if state.last_date is None:
        return 7.0
    return _clip((date - state.last_date).total_seconds() / 86400.0, 2.0, 21.0)


def _features(
    formal: dict[str, float],
    matrix: list[dict[str, Any]],
    home: TeamState,
    away: TeamState,
    competition: CompetitionState,
    date: datetime,
) -> tuple[list[float], list[float]]:
    league_draw = (competition.draws + 26.0) / (competition.matches + 100.0)
    league_total = (competition.total_goals + 260.0) / (competition.matches + 100.0)
    expected_total = _expected_total(matrix)
    elo_diff = (home.elo - away.elo) / 400.0
    ppg_diff = home.ppg - away.ppg
    gd_diff = home.gd - away.gd
    draw_mean = 0.5 * (home.draw_rate + away.draw_rate)
    rest_diff = (_rest_days(home, date) - _rest_days(away, date)) / 14.0
    side_gap = abs(math.log(max(EPS, formal["home"])) - math.log(max(EPS, formal["away"])))

    draw_x = [
        1.0,
        _logit(formal["draw"]),
        side_gap,
        abs(elo_diff),
        abs(ppg_diff),
        abs(gd_diff),
        draw_mean - 0.26,
        league_draw - 0.26,
        expected_total - league_total,
        expected_total - 2.6,
    ]

    side_x = [
        1.0,
        math.log(max(EPS, formal["home"])) - math.log(max(EPS, formal["away"])),
        elo_diff,
        ppg_diff,
        gd_diff,
        (home.gf - away.gf),
        (away.ga - home.ga),
        rest_diff,
    ]
    return draw_x, side_x


def _update_state(home: TeamState, away: TeamState, competition: CompetitionState, match) -> None:
    hg = int(match.home_goals)
    ag = int(match.away_goals)
    if hg > ag:
        hp, ap = 3.0, 0.0
        result_score = 1.0
    elif hg < ag:
        hp, ap = 0.0, 3.0
        result_score = 0.0
    else:
        hp = ap = 1.0
        result_score = 0.5

    expected_home = 1.0 / (1.0 + 10.0 ** (-(home.elo + 55.0 - away.elo) / 400.0))
    margin = math.log1p(abs(hg - ag)) if hg != ag else 1.0
    delta = ELO_K * margin * (result_score - expected_home)
    home.elo += delta
    away.elo -= delta

    alpha = FORM_ALPHA
    home.ppg = (1.0 - alpha) * home.ppg + alpha * hp
    away.ppg = (1.0 - alpha) * away.ppg + alpha * ap
    home.gd = (1.0 - alpha) * home.gd + alpha * (hg - ag)
    away.gd = (1.0 - alpha) * away.gd + alpha * (ag - hg)
    home.gf = (1.0 - alpha) * home.gf + alpha * hg
    home.ga = (1.0 - alpha) * home.ga + alpha * ag
    away.gf = (1.0 - alpha) * away.gf + alpha * ag
    away.ga = (1.0 - alpha) * away.ga + alpha * hg
    draw = 1.0 if hg == ag else 0.0
    home.draw_rate = (1.0 - alpha) * home.draw_rate + alpha * draw
    away.draw_rate = (1.0 - alpha) * away.draw_rate + alpha * draw
    home.matches += 1
    away.matches += 1
    home.last_date = match.date
    away.last_date = match.date
    competition.matches += 1
    competition.draws += int(hg == ag)
    competition.total_goals += hg + ag


def _build_domain_rows(cid: str, seasons: list[str]) -> dict[str, list[dict[str, Any]]]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    all_matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    selected = set(seasons)
    folds = {season: _fold_for_season(report, season) for season in seasons}
    temperatures = {season: _target_season_temperature(cid, season)[0] for season in seasons}
    teams: dict[str, TeamState] = defaultdict(TeamState)
    competition = CompetitionState()
    rows = {season: [] for season in seasons}

    by_date: dict[datetime, list[Any]] = defaultdict(list)
    for match in all_matches:
        by_date[match.date].append(match)

    for date in sorted(by_date):
        day_matches = sorted(by_date[date], key=lambda m: (m.home_team, m.away_team))
        for match in day_matches:
            season = str(match.season)
            if season not in selected:
                continue
            params = folds[season].get("selected_parameters")
            if not isinstance(params, dict):
                raise PlatformError(f"invalid formal parameters for {cid} {season}")
            try:
                matrix = _predict_from_loaded_matches(
                    all_matches, match.home_team, match.away_team, match.date, season, params
                )
            except PlatformError:
                continue
            temperature = float(temperatures[season])
            if abs(temperature - 1.0) > 1e-15:
                matrix = temperature_scale_matrix(matrix, temperature)
            margins = derive_score_marginals(matrix)
            formal = {key: float(margins["1x2"][key]) for key in CLASSES}
            home_state = teams[_team_key(match.home_team)]
            away_state = teams[_team_key(match.away_team)]
            draw_x, side_x = _features(formal, matrix, home_state, away_state, competition, match.date)
            actual = _actual_result(int(match.home_goals), int(match.away_goals))
            rows[season].append({
                "competition_id": cid,
                "season": season,
                "formal": formal,
                "draw_x": draw_x,
                "side_x": side_x,
                "actual_result": actual,
                "draw_y": 1 if actual == "draw" else 0,
                "side_y": 1 if actual == "home" else 0,
                "is_decisive": actual != "draw",
            })
        for match in day_matches:
            _update_state(
                teams[_team_key(match.home_team)],
                teams[_team_key(match.away_team)],
                competition,
                match,
            )
    return rows


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    augmented = [list(matrix[i]) + [float(vector[i])] for i in range(n)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= 1e-12:
            raise PlatformError("binary logistic Hessian is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        for j in range(column, n + 1):
            augmented[column][j] /= divisor
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            for j in range(column, n + 1):
                augmented[row][j] -= factor * augmented[column][j]
    return [augmented[i][n] for i in range(n)]


def _fit_binary(rows: list[dict[str, Any]], x_key: str, y_key: str, l2: float) -> dict[str, Any]:
    if not rows:
        raise PlatformError(f"no rows for {x_key}")
    dimension = len(rows[0][x_key])
    means = [0.0] * dimension
    scales = [1.0] * dimension
    for j in range(1, dimension):
        means[j] = sum(float(row[x_key][j]) for row in rows) / len(rows)
        variance = sum((float(row[x_key][j]) - means[j]) ** 2 for row in rows) / len(rows)
        scales[j] = max(1e-6, math.sqrt(variance))

    def standardized(row: dict[str, Any]) -> list[float]:
        raw = row[x_key]
        return [1.0] + [(float(raw[j]) - means[j]) / scales[j] for j in range(1, dimension)]

    theta = [0.0] * dimension
    prevalence = sum(int(row[y_key]) for row in rows) / len(rows)
    theta[0] = _logit(_clip(prevalence, 1e-4, 1.0 - 1e-4))
    objective = float("inf")
    converged = False
    gradient_norm = None
    for iteration in range(1, 61):
        gradient = [0.0] * dimension
        hessian = [[0.0] * dimension for _ in range(dimension)]
        current_objective = 0.5 * l2 * sum(value * value for value in theta[1:])
        for row in rows:
            x = standardized(row)
            y = float(row[y_key])
            eta = sum(theta[j] * x[j] for j in range(dimension))
            p = _sigmoid(eta)
            current_objective -= y * math.log(max(EPS, p)) + (1.0 - y) * math.log(max(EPS, 1.0 - p))
            for j in range(dimension):
                gradient[j] += (p - y) * x[j]
                for k in range(dimension):
                    hessian[j][k] += p * (1.0 - p) * x[j] * x[k]
        for j in range(1, dimension):
            gradient[j] += l2 * theta[j]
            hessian[j][j] += l2
        hessian[0][0] += 1e-8
        gradient_norm = max(abs(v) for v in gradient)
        if gradient_norm < 1e-7:
            converged = True
            objective = current_objective
            break
        step = _solve(hessian, gradient)
        scale = 1.0
        accepted = False
        for _ in range(25):
            candidate = [theta[j] - scale * step[j] for j in range(dimension)]
            candidate_objective = 0.5 * l2 * sum(value * value for value in candidate[1:])
            for row in rows:
                x = standardized(row)
                y = float(row[y_key])
                p = _sigmoid(sum(candidate[j] * x[j] for j in range(dimension)))
                candidate_objective -= y * math.log(max(EPS, p)) + (1.0 - y) * math.log(max(EPS, 1.0 - p))
            if math.isfinite(candidate_objective) and candidate_objective <= current_objective + 1e-10:
                theta = candidate
                objective = candidate_objective
                accepted = True
                break
            scale *= 0.5
        if not accepted:
            raise PlatformError(f"binary logistic line search failed for {x_key}")
        if max(abs(scale * value) for value in step) < 1e-8:
            converged = True
            break
    if not converged:
        raise PlatformError(f"binary logistic did not converge for {x_key}")
    return {
        "theta": theta,
        "means": means,
        "scales": scales,
        "l2": l2,
        "iterations": iteration,
        "objective": objective,
        "max_abs_gradient": gradient_norm,
        "training_count": len(rows),
    }


def _predict_binary(model: dict[str, Any], raw: list[float]) -> float:
    theta = [float(v) for v in model["theta"]]
    means = [float(v) for v in model["means"]]
    scales = [float(v) for v in model["scales"]]
    x = [1.0] + [(float(raw[j]) - means[j]) / scales[j] for j in range(1, len(raw))]
    return _sigmoid(sum(theta[j] * x[j] for j in range(len(theta))))


def _fit_models(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    draw_model = _fit_binary(rows, "draw_x", "draw_y", l2)
    decisive = [row for row in rows if row["is_decisive"]]
    side_model = _fit_binary(decisive, "side_x", "side_y", l2)
    return {"draw_model": draw_model, "side_model": side_model, "l2": l2}


def _direct_probability(row: dict[str, Any], models: dict[str, Any]) -> dict[str, float]:
    p_draw = _clip(_predict_binary(models["draw_model"], row["draw_x"]), 1e-6, 1.0 - 1e-6)
    p_home_decisive = _clip(_predict_binary(models["side_model"], row["side_x"]), 1e-6, 1.0 - 1e-6)
    remaining = 1.0 - p_draw
    return {
        "home": remaining * p_home_decisive,
        "draw": p_draw,
        "away": remaining * (1.0 - p_home_decisive),
    }


def _log_pool(formal: dict[str, float], direct: dict[str, float], weight: float) -> dict[str, float]:
    logits = {
        key: (1.0 - weight) * math.log(max(EPS, formal[key])) + weight * math.log(max(EPS, direct[key]))
        for key in CLASSES
    }
    maximum = max(logits.values())
    values = {key: math.exp(value - maximum) for key, value in logits.items()}
    total = sum(values.values())
    return {key: value / total for key, value in values.items()}


def _metrics(rows: list[dict[str, Any]], models: dict[str, Any] | None, pool_weight: float = 0.0) -> dict[str, Any]:
    count = hits = 0
    brier = rps = logloss = 0.0
    predicted = Counter()
    actual = Counter()
    agreement_count = agreement_hits = 0
    detail: list[dict[str, Any]] = []
    by_competition: dict[str, Counter] = {}
    for row in rows:
        formal = row["formal"]
        direct = formal if models is None else _direct_probability(row, models)
        q = formal if models is None else _log_pool(formal, direct, pool_weight)
        pick = max(CLASSES, key=lambda key: float(q[key]))
        formal_pick = max(CLASSES, key=lambda key: float(formal[key]))
        truth = str(row["actual_result"])
        hit = int(pick == truth)
        ordered = sorted((float(q[key]), key) for key in CLASSES)
        confidence = ordered[-1][0] - ordered[-2][0]
        agreement = int(pick == formal_pick)
        count += 1
        hits += hit
        predicted[pick] += 1
        actual[truth] += 1
        agreement_count += agreement
        agreement_hits += agreement * hit
        brier += sum((float(q[key]) - (1.0 if truth == key else 0.0)) ** 2 for key in CLASSES)
        truth_vec = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}[truth]
        c1 = q["home"] - truth_vec[0]
        c2 = q["home"] + q["draw"] - truth_vec[0] - truth_vec[1]
        rps += (c1 * c1 + c2 * c2) / 2.0
        logloss -= math.log(max(EPS, q[truth]))
        detail.append({"hit": hit, "confidence": confidence, "agreement": agreement})
        bucket = by_competition.setdefault(row["competition_id"], Counter())
        bucket["count"] += 1
        bucket["hits"] += hit
        bucket[f"predicted_{pick}"] += 1
        bucket[f"actual_{truth}"] += 1
    return {
        "count": count,
        "hit_count": hits,
        "accuracy": hits / count if count else None,
        "mean_brier": brier / count if count else None,
        "mean_rps": rps / count if count else None,
        "mean_log_loss": logloss / count if count else None,
        "predicted_direction_counts": dict(predicted),
        "actual_direction_counts": dict(actual),
        "draw_prediction_count": int(predicted["draw"]),
        "agreement_count": agreement_count,
        "agreement_accuracy": agreement_hits / agreement_count if agreement_count else None,
        "detail": detail,
        "by_competition": {
            cid: {
                "count": int(bucket["count"]),
                "hits": int(bucket["hits"]),
                "accuracy": bucket["hits"] / bucket["count"] if bucket["count"] else None,
                "predicted_home": int(bucket["predicted_home"]),
                "predicted_draw": int(bucket["predicted_draw"]),
                "predicted_away": int(bucket["predicted_away"]),
                "actual_draw": int(bucket["actual_draw"]),
            }
            for cid, bucket in sorted(by_competition.items())
        },
    }


def _selection_thresholds(validation_metrics: dict[str, Any]) -> dict[str, float]:
    details = sorted(validation_metrics["detail"], key=lambda item: float(item["confidence"]), reverse=True)
    thresholds: dict[str, float] = {}
    for coverage in (0.20, 0.10, 0.05):
        n = max(1, int(round(len(details) * coverage)))
        thresholds[f"top_{int(coverage * 100)}pct"] = float(details[n - 1]["confidence"])
    return thresholds


def _selective(metrics: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name, threshold in thresholds.items():
        chosen = [item for item in metrics["detail"] if float(item["confidence"]) >= threshold and int(item["agreement"]) == 1]
        output[name] = {
            "threshold": threshold,
            "count": len(chosen),
            "coverage": len(chosen) / len(metrics["detail"]) if metrics["detail"] else 0.0,
            "accuracy": sum(int(item["hit"]) for item in chosen) / len(chosen) if chosen else None,
            "requires_formal_direct_agreement": True,
        }
    return output


def _strip_detail(metrics: dict[str, Any]) -> dict[str, Any]:
    copy = dict(metrics)
    copy.pop("detail", None)
    return copy


def main() -> int:
    formal_status = load_json(FORMAL_STATUS)
    domains = sorted((formal_status.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 formal domains, found {len(domains)}")

    rows_by_domain_season: dict[str, dict[str, list[dict[str, Any]]]] = {}
    season_roles: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid in domains:
        try:
            report = load_json(REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons_last_complete_only(report)
            if len(seasons) < 4:
                raise PlatformError(f"need at least four completed outer seasons for {cid}")
            selected = seasons[-4:]
            rows_by_domain_season[cid] = _build_domain_rows(cid, selected)
            season_roles[cid] = {
                "fit_seasons": selected[:2],
                "selection_validation_season": selected[2],
                "development_holdout_season": selected[3],
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        payload = {
            "schema_version": "V6.0.0-direct-outcome-mvp-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL_DATA_BUILD",
            "failures": failures,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fit_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    for cid in domains:
        seasons = sorted(rows_by_domain_season[cid], key=_season_key)
        for season in seasons[:2]:
            fit_rows.extend(rows_by_domain_season[cid][season])
        validation_rows.extend(rows_by_domain_season[cid][seasons[2]])
        holdout_rows.extend(rows_by_domain_season[cid][seasons[3]])

    baseline_validation = _metrics(validation_rows, None)
    baseline_holdout = _metrics(holdout_rows, None)
    candidates: list[dict[str, Any]] = []
    for l2 in L2_GRID:
        try:
            models = _fit_models(fit_rows, l2)
            for pool_weight in POOL_GRID:
                metrics = _metrics(validation_rows, models, pool_weight)
                proper_nonworse = (
                    float(metrics["mean_log_loss"]) <= float(baseline_validation["mean_log_loss"]) + 1e-12
                    and float(metrics["mean_brier"]) <= float(baseline_validation["mean_brier"]) + 1e-12
                    and float(metrics["mean_rps"]) <= float(baseline_validation["mean_rps"]) + 1e-12
                )
                candidates.append({
                    "l2": l2,
                    "pool_weight": pool_weight,
                    "validation": _strip_detail(metrics),
                    "proper_scores_nonworse": proper_nonworse,
                })
        except Exception as exc:
            candidates.append({
                "l2": l2,
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "proper_scores_nonworse": False,
            })

    eligible = [item for item in candidates if item.get("proper_scores_nonworse") and item.get("validation")]
    eligible.sort(key=lambda item: (
        -float(item["validation"]["accuracy"]),
        float(item["validation"]["mean_log_loss"]),
        float(item["validation"]["mean_brier"]),
        float(item["l2"]),
        float(item["pool_weight"]),
    ))

    result: dict[str, Any]
    if not eligible:
        result = {
            "status": "NO_PROPER_SCORE_SAFE_CANDIDATE",
            "selected_candidate": None,
            "challenge_gate_passed": False,
            "challenge_gate_fail_reasons": ["no validation candidate preserved all three proper scores"],
        }
    else:
        selected = eligible[0]
        refit_rows = fit_rows + validation_rows
        models = _fit_models(refit_rows, float(selected["l2"]))
        validation_selected_full = _metrics(validation_rows, _fit_models(fit_rows, float(selected["l2"])), float(selected["pool_weight"]))
        thresholds = _selection_thresholds(validation_selected_full)
        holdout_metrics = _metrics(holdout_rows, models, float(selected["pool_weight"]))
        selective = _selective(holdout_metrics, thresholds)
        accuracy_gain_pp = 100.0 * (float(holdout_metrics["accuracy"]) - float(baseline_holdout["accuracy"]))
        fail_reasons: list[str] = []
        if accuracy_gain_pp < 1.0:
            fail_reasons.append("development holdout 1X2 accuracy gain below 1 percentage point")
        if float(holdout_metrics["mean_log_loss"]) >= float(baseline_holdout["mean_log_loss"]):
            fail_reasons.append("development holdout log loss did not improve")
        if float(holdout_metrics["mean_brier"]) >= float(baseline_holdout["mean_brier"]):
            fail_reasons.append("development holdout Brier score did not improve")
        if float(holdout_metrics["mean_rps"]) >= float(baseline_holdout["mean_rps"]):
            fail_reasons.append("development holdout RPS did not improve")
        if int(holdout_metrics["draw_prediction_count"]) < 100:
            fail_reasons.append("draw prediction count below 100")
        result = {
            "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
            "selected_candidate": {
                "l2": selected["l2"],
                "pool_weight": selected["pool_weight"],
                "selection_validation": selected["validation"],
            },
            "refit_audit": models,
            "holdout": _strip_detail(holdout_metrics),
            "selective_holdout": selective,
            "accuracy_gain_pp": accuracy_gain_pp,
            "challenge_gate_passed": not fail_reasons,
            "challenge_gate_fail_reasons": fail_reasons,
        }

    payload = {
        "schema_version": "V6.0.0-direct-outcome-mvp-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "season_roles": season_roles,
        "method": {
            "formal_baseline_signal": True,
            "historical_market_odds_used": False,
            "xg_used": False,
            "lineups_used": False,
            "direct_outcome_structure": "draw-vs-decisive expert plus home-vs-away conditional expert",
            "dynamic_features": [
                "Elo difference", "EWMA points", "EWMA goal difference", "EWMA goals for/against",
                "team draw propensity", "competition draw rate", "expected total goals", "rest-day difference",
            ],
            "same_date_leakage_control": "all matches on a date predicted before any same-date result update",
            "hyperparameter_selection": "earliest two outer seasons fit; penultimate season selects l2 and pool weight",
            "holdout_note": "last-complete-season is a development holdout already viewed by earlier project experiments; not a pristine final test",
        },
        "row_counts": {
            "fit": len(fit_rows),
            "selection_validation": len(validation_rows),
            "development_holdout": len(holdout_rows),
        },
        "baseline": {
            "selection_validation": _strip_detail(baseline_validation),
            "development_holdout": _strip_detail(baseline_holdout),
        },
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "candidate_summary": candidates,
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "promotion_requires_new_pristine_forward_test": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload["status"],
        "fit_count": len(fit_rows),
        "validation_count": len(validation_rows),
        "holdout_count": len(holdout_rows),
        "baseline_accuracy": baseline_holdout["accuracy"],
        "candidate_accuracy": ((result.get("holdout") or {}).get("accuracy")),
        "accuracy_gain_pp": result.get("accuracy_gain_pp"),
        "draw_predictions": ((result.get("holdout") or {}).get("draw_prediction_count")),
        "challenge_gate_passed": result.get("challenge_gate_passed"),
        "fail_reasons": result.get("challenge_gate_fail_reasons"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
