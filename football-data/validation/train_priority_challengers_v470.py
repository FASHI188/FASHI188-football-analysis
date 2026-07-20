#!/usr/bin/env python3
"""Priority competition-specific V4.7 challenger training.

Trains and evaluates, independently for KOR K League 1, Eliteserien,
Allsvenskan and MLS:
1) conditional P(H,A|T) exponential-tilt parameters;
2) direct total-goal tail (4+/5+/7+) exponential-tilt parameters.

No formal weights are changed. Promotion remains fail-closed and requires
competition-specific chronological OOF evidence plus CURRENT-compliant approval.
"""
from __future__ import annotations

import itertools
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from conditional_allocation_challenger_v470 import apply_conditional_exponential_tilt
from football_v460_engine import load_config, predict_from_history
from platform_core import ROOT, MatchRow, PlatformError, derive_score_marginals, read_processed_matches
from total_tail_challenger_v470 import apply_total_tail_tilt, tilt_total_vector, total_vector_from_matrix

PRIORITY_COMPETITIONS = (
    "KOR_KLeague1",
    "NOR_Eliteserien",
    "SWE_Allsvenskan",
    "USA_MLS",
)
OUT = ROOT / "manifests" / "priority_challenger_training_v470_status.json"
ARTIFACT_ROOT = ROOT / "models" / "challengers_v470"
EPS = 1e-15

D_GRID_VALUES = (-0.6, -0.3, 0.0, 0.3, 0.6)
TAIL_GRID_VALUES = (-0.5, -0.25, 0.0, 0.25, 0.5)
D_FEATURES = ("btts", "home_zero", "away_zero", "margin2plus")
TAIL_FEATURES = ("tail4plus", "tail5plus", "tail7plus")


def _actual_outcome(home: int, away: int) -> str:
    return "home" if home > away else "draw" if home == away else "away"


def _rps(probs: list[float], actual_index: int) -> float:
    cp = 0.0
    co = 0.0
    score = 0.0
    for index in range(len(probs) - 1):
        cp += probs[index]
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(probs) - 1)


def _brier(p: float, y: float) -> float:
    return (p - y) ** 2


def _team_counts(history: list[MatchRow]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for match in history:
        counts[match.home_team] += 1
        counts[match.away_team] += 1
    return counts


def _matrix_score_probability(matrix: list[dict[str, Any]], home: int, away: int) -> float:
    for cell in matrix:
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            return float(cell["probability"])
    return 0.0


def _event_probs(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {"btts": 0.0, "home_zero": 0.0, "away_zero": 0.0, "margin2plus": 0.0}
    for cell in matrix:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = float(cell["probability"])
        if h > 0 and a > 0:
            out["btts"] += p
        if h == 0:
            out["home_zero"] += p
        if a == 0:
            out["away_zero"] += p
        if abs(h - a) >= 2:
            out["margin2plus"] += p
    return out


def _one_x_two_rps(one: dict[str, float], actual: str) -> float:
    values = [one["home"], one["draw"], one["away"]]
    return _rps(values, ("home", "draw", "away").index(actual))


def _record_from_prediction(match: MatchRow, prediction: dict[str, Any], sequence_index: int) -> dict[str, Any]:
    matrix = prediction["probabilities"]["score_matrix"]
    marginals = derive_score_marginals(matrix)
    total_vec = [marginals["total_goals"][key] for key in ("0", "1", "2", "3", "4", "5", "6", "7+")]
    actual_total = int(match.home_goals + match.away_goals)
    actual_index = min(actual_total, 7)
    actual_outcome = _actual_outcome(match.home_goals, match.away_goals)
    p_score = _matrix_score_probability(matrix, match.home_goals, match.away_goals)
    events = _event_probs(matrix)
    return {
        "season": str(match.season),
        "date": match.date.date().isoformat(),
        "sequence_index": sequence_index,
        "block_id": f"{match.season}:{sequence_index // 20}",
        "actual_home": int(match.home_goals),
        "actual_away": int(match.away_goals),
        "actual_total": actual_total,
        "actual_total_index": actual_index,
        "actual_outcome": actual_outcome,
        "matrix": matrix,
        "total_vector": total_vec,
        "base_joint_log": -math.log(max(EPS, p_score)),
        "base_total_rps": _rps(total_vec, actual_index),
        "base_one_x_two_rps": _one_x_two_rps(marginals["1x2"], actual_outcome),
        "base_events": events,
    }


def rolling_records(season_matches: list[MatchRow], params: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    warmup_comp = int(config["validation"]["warmup_competition_matches"])
    warmup_team = int(config["validation"]["warmup_team_matches"])
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for match in season_matches:
        by_date[match.date].append(match)
    history: list[MatchRow] = []
    records: list[dict[str, Any]] = []
    sequence_index = 0
    for date in sorted(by_date):
        counts = _team_counts(history)
        for match in sorted(by_date[date], key=lambda item: (item.home_team, item.away_team)):
            if len(history) < warmup_comp:
                continue
            if counts[match.home_team] < warmup_team or counts[match.away_team] < warmup_team:
                continue
            try:
                prediction = predict_from_history(
                    history,
                    match.competition_id,
                    str(match.season),
                    match.home_team,
                    match.away_team,
                    match.date,
                    params,
                    use_team_effects=True,
                )
            except (PlatformError, KeyError, ValueError):
                continue
            records.append(_record_from_prediction(match, prediction, sequence_index))
            sequence_index += 1
        history.extend(by_date[date])
        history.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    return records


def _base_objective(records: list[dict[str, Any]]) -> float:
    if not records:
        return float("inf")
    return mean(
        row["base_joint_log"] + 0.50 * row["base_total_rps"] + 0.25 * row["base_one_x_two_rps"]
        for row in records
    )


def _season_is_partial(season_map: dict[str, list[MatchRow]], latest: str) -> bool:
    earlier = [len(rows) for season, rows in season_map.items() if season != latest and rows]
    if not earlier:
        return False
    return len(season_map[latest]) < 0.85 * median(earlier)


def _conditional_feature(home: int, away: int) -> dict[str, float]:
    return {
        "btts": 1.0 if home > 0 and away > 0 else 0.0,
        "home_zero": 1.0 if home == 0 else 0.0,
        "away_zero": 1.0 if away == 0 else 0.0,
        "margin2plus": 1.0 if abs(home - away) >= 2 else 0.0,
    }


def _conditional_tilt_log_probability(row: dict[str, Any], params: dict[str, float]) -> float:
    total = row["actual_total"]
    cells = [
        cell for cell in row["matrix"]
        if int(cell["home_goals"]) + int(cell["away_goals"]) == total
    ]
    p_total = sum(float(cell["probability"]) for cell in cells)
    if p_total <= 0:
        return math.log(EPS)
    normalizer = 0.0
    actual_weight = 0.0
    for cell in cells:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        base_cond = float(cell["probability"]) / p_total
        feats = _conditional_feature(h, a)
        weight = base_cond * math.exp(sum(params[name] * feats[name] for name in D_FEATURES))
        normalizer += weight
        if h == row["actual_home"] and a == row["actual_away"]:
            actual_weight = weight
    if normalizer <= 0 or actual_weight <= 0:
        return math.log(EPS)
    return math.log(max(EPS, p_total * actual_weight / normalizer))


def train_conditional_params(records: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {name: 0.0 for name in D_FEATURES}
    best = None
    for values in itertools.product(D_GRID_VALUES, repeat=len(D_FEATURES)):
        params = dict(zip(D_FEATURES, values))
        loss = -mean(_conditional_tilt_log_probability(row, params) for row in records)
        loss += 0.01 * sum(value * value for value in values)
        key = (loss, sum(abs(value) for value in values), values)
        if best is None or key < best[0]:
            best = (key, params)
    return best[1]


def train_tail_params(records: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {name: 0.0 for name in TAIL_FEATURES}
    best = None
    for values in itertools.product(TAIL_GRID_VALUES, repeat=len(TAIL_FEATURES)):
        params = dict(zip(TAIL_FEATURES, values))
        losses = []
        for row in records:
            tilted = tilt_total_vector(row["total_vector"], params)
            losses.append(_rps(tilted, row["actual_total_index"]))
        loss = mean(losses) + 0.005 * sum(value * value for value in values)
        key = (loss, sum(abs(value) for value in values), values)
        if best is None or key < best[0]:
            best = (key, params)
    return best[1]


def evaluate_challengers(records: list[dict[str, Any]], d_params: dict[str, float], tail_params: dict[str, float]) -> list[dict[str, Any]]:
    output = []
    for row in records:
        d_matrix, d_audit = apply_conditional_exponential_tilt(row["matrix"], d_params)
        tail_matrix, tail_audit = apply_total_tail_tilt(row["matrix"], tail_params)
        d_score_p = _matrix_score_probability(d_matrix, row["actual_home"], row["actual_away"])
        d_events = _event_probs(d_matrix)
        tail_vec = total_vector_from_matrix(tail_matrix)
        actual = {
            "btts": 1.0 if row["actual_home"] > 0 and row["actual_away"] > 0 else 0.0,
            "home_zero": 1.0 if row["actual_home"] == 0 else 0.0,
            "away_zero": 1.0 if row["actual_away"] == 0 else 0.0,
            "margin2plus": 1.0 if abs(row["actual_home"] - row["actual_away"]) >= 2 else 0.0,
        }
        base_vec = row["total_vector"]
        base_tail4 = sum(base_vec[4:])
        base_tail5 = sum(base_vec[5:])
        base_tail7 = base_vec[7]
        new_tail4 = sum(tail_vec[4:])
        new_tail5 = sum(tail_vec[5:])
        new_tail7 = tail_vec[7]
        y4 = 1.0 if row["actual_total"] >= 4 else 0.0
        y5 = 1.0 if row["actual_total"] >= 5 else 0.0
        y7 = 1.0 if row["actual_total"] >= 7 else 0.0
        result = {
            "block_id": row["block_id"],
            "season": row["season"],
            "date": row["date"],
            "d_joint_log_diff": -math.log(max(EPS, d_score_p)) - row["base_joint_log"],
            "tail_rps_diff": _rps(tail_vec, row["actual_total_index"]) - row["base_total_rps"],
            "tail4_brier_diff": _brier(new_tail4, y4) - _brier(base_tail4, y4),
            "tail5_brier_diff": _brier(new_tail5, y5) - _brier(base_tail5, y5),
            "tail7_brier_diff": _brier(new_tail7, y7) - _brier(base_tail7, y7),
            "d_max_total_residual": float(d_audit["max_total_marginal_residual"]),
            "tail_max_vector_residual": float(tail_audit["max_total_vector_residual"]),
            "tail_max_conditional_residual": float(tail_audit["max_conditional_score_residual"]),
        }
        for name in D_FEATURES:
            result[f"{name}_brier_diff"] = _brier(d_events[name], actual[name]) - _brier(row["base_events"][name], actual[name])
        output.append(result)
    return output


def _bootstrap_ci(rows: list[dict[str, Any]], field: str, seed: int, resamples: int = 300) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "mean_difference": None, "ci95_lower": None, "ci95_upper": None}
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        blocks[row["block_id"]].append(float(row[field]))
    block_values = list(blocks.values())
    observed = mean(float(row[field]) for row in rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(resamples):
        selected = [rng.choice(block_values) for _ in block_values]
        flattened = [value for block in selected for value in block]
        samples.append(mean(flattened))
    samples.sort()
    low = samples[max(0, int(0.025 * len(samples)) - 1)]
    high = samples[min(len(samples) - 1, int(0.975 * len(samples)))]
    return {
        "count": len(rows),
        "blocks": len(block_values),
        "mean_difference": observed,
        "ci95_lower": low,
        "ci95_upper": high,
    }


def train_competition(competition_id: str, config: dict[str, Any]) -> dict[str, Any]:
    matches = read_processed_matches(competition_id)
    season_map: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        season_map[str(match.season)].append(match)
    seasons = sorted(season_map, key=lambda season: min(row.date for row in season_map[season]))
    for rows in season_map.values():
        rows.sort(key=lambda row: (row.date, row.home_team, row.away_team))
    candidates = list(config["candidate_parameters"])

    lightweight_cache: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for index, candidate in enumerate(candidates):
        for season in seasons:
            lightweight_cache[(index, season)] = rolling_records(season_map[season], candidate, config)

    outer_rows = []
    fold_reports = []
    for outer_index in range(1, len(seasons)):
        outer_season = seasons[outer_index]
        prior_seasons = seasons[:outer_index]
        candidate_scores = []
        for index, candidate in enumerate(candidates):
            prior_records = [row for season in prior_seasons for row in lightweight_cache[(index, season)]]
            candidate_scores.append((_base_objective(prior_records), index, candidate, len(prior_records)))
        candidate_scores.sort(key=lambda item: (item[0], item[1]))
        _, selected_index, selected_base_params, selection_count = candidate_scores[0]
        train_records = [row for season in prior_seasons for row in lightweight_cache[(selected_index, season)]]
        test_records = lightweight_cache[(selected_index, outer_season)]
        if not train_records or not test_records:
            continue
        d_params = train_conditional_params(train_records)
        tail_params = train_tail_params(train_records)
        evaluated = evaluate_challengers(test_records, d_params, tail_params)
        outer_rows.extend(evaluated)
        fold_reports.append({
            "outer_season": outer_season,
            "prior_seasons": prior_seasons,
            "base_candidate_index": selected_index,
            "base_parameters": selected_base_params,
            "base_selection_predictions": selection_count,
            "conditional_parameters": d_params,
            "tail_parameters": tail_params,
            "outer_predictions": len(evaluated),
            "mean_d_joint_log_diff": mean(row["d_joint_log_diff"] for row in evaluated),
            "mean_tail_rps_diff": mean(row["tail_rps_diff"] for row in evaluated),
        })

    if not outer_rows:
        return {"competition_id": competition_id, "status": "不可用", "reason": "no eligible outer OOF rows"}

    latest = seasons[-1]
    tuning_seasons = seasons[:-1] if _season_is_partial(season_map, latest) else seasons
    live_scores = []
    for index, candidate in enumerate(candidates):
        rows = [row for season in tuning_seasons for row in lightweight_cache[(index, season)]]
        live_scores.append((_base_objective(rows), index, candidate, len(rows)))
    live_scores.sort(key=lambda item: (item[0], item[1]))
    _, live_index, live_base_params, live_count = live_scores[0]
    live_training_rows = [row for season in tuning_seasons for row in lightweight_cache[(live_index, season)]]
    live_d_params = train_conditional_params(live_training_rows)
    live_tail_params = train_tail_params(live_training_rows)

    d_ci = _bootstrap_ci(outer_rows, "d_joint_log_diff", 4701)
    tail_ci = _bootstrap_ci(outer_rows, "tail_rps_diff", 4702)
    structural = {
        name: mean(row[f"{name}_brier_diff"] for row in outer_rows)
        for name in D_FEATURES
    }
    tail_briers = {
        "tail4plus": mean(row["tail4_brier_diff"] for row in outer_rows),
        "tail5plus": mean(row["tail5_brier_diff"] for row in outer_rows),
        "tail7plus": mean(row["tail7_brier_diff"] for row in outer_rows),
    }
    d_nonworse_count = sum(1 for value in structural.values() if value <= 0.0)
    d_status = (
        "REVIEW_CANDIDATE"
        if d_ci["ci95_upper"] is not None and d_ci["ci95_upper"] < 0.0 and d_nonworse_count >= 3
        else "KEEP_FORMAL_WEIGHT_0"
    )
    tail_status = (
        "REVIEW_CANDIDATE"
        if tail_ci["ci95_upper"] is not None
        and tail_ci["ci95_upper"] < 0.0
        and tail_briers["tail4plus"] <= 0.0
        and tail_briers["tail5plus"] <= 0.0
        else "KEEP_FORMAL_WEIGHT_0"
    )

    max_d_residual = max(row["d_max_total_residual"] for row in outer_rows)
    max_tail_vector_residual = max(row["tail_max_vector_residual"] for row in outer_rows)
    max_tail_conditional_residual = max(row["tail_max_conditional_residual"] for row in outer_rows)

    artifact = {
        "schema_version": "V4.7.0-priority-challenger-training",
        "competition_id": competition_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_weight": 0,
        "automatic_promotion": False,
        "target_live_season": latest,
        "tuning_seasons": tuning_seasons,
        "live_base_candidate_index": live_index,
        "live_base_parameters": live_base_params,
        "live_base_selection_predictions": live_count,
        "conditional_allocation": {
            "parameters": live_d_params,
            "outer_primary_ci": d_ci,
            "structural_brier_differences": structural,
            "status": d_status,
            "max_total_marginal_residual": max_d_residual,
        },
        "total_tail": {
            "parameters": live_tail_params,
            "outer_primary_ci": tail_ci,
            "tail_brier_differences": tail_briers,
            "status": tail_status,
            "max_total_vector_residual": max_tail_vector_residual,
            "max_conditional_score_residual": max_tail_conditional_residual,
        },
        "outer_predictions": len(outer_rows),
        "outer_folds": len(fold_reports),
        "folds": fold_reports,
        "promotion_policy": "No automatic promotion. Competition-specific CURRENT-compliant review required even when status is REVIEW_CANDIDATE.",
    }
    artifact_dir = ARTIFACT_ROOT / competition_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "priority_v470.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def main() -> int:
    config = load_config()
    reports = {}
    failures = {}
    for competition_id in PRIORITY_COMPETITIONS:
        try:
            reports[competition_id] = train_competition(competition_id, config)
        except Exception as exc:
            failures[competition_id] = str(exc)
            reports[competition_id] = {"competition_id": competition_id, "status": "失败", "reason": str(exc)}
    status = "PASS" if not failures else "PARTIAL"
    report = {
        "schema_version": "V4.7.0-priority-challenger-training",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "priority_competitions": list(PRIORITY_COMPETITIONS),
        "formal_weight_change": False,
        "automatic_promotion": False,
        "method": "competition-independent nested chronological OOF; prior seasons train challenger parameters, unseen outer season evaluates; no cross-competition rows or calibrators",
        "reports": reports,
        "failures": failures,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "failures": failures,
        "summary": {
            cid: {
                "conditional": rep.get("conditional_allocation", {}).get("status"),
                "conditional_ci": rep.get("conditional_allocation", {}).get("outer_primary_ci"),
                "tail": rep.get("total_tail", {}).get("status"),
                "tail_ci": rep.get("total_tail", {}).get("outer_primary_ci"),
            }
            for cid, rep in reports.items()
        },
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
