#!/usr/bin/env python3
"""Priority competition-specific V4.7 challenger training.

Independent nested chronological OOF training for:
KOR K League 1, Eliteserien, Allsvenskan and MLS.

Research challengers:
1) conditional P(H,A|T) exponential tilt;
2) direct total-goal tail (4+/5+/7+) exponential tilt.

Formal weights remain 0. No automatic promotion.
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


def _rps(probs: list[float], actual_index: int) -> float:
    cp = co = score = 0.0
    for index in range(len(probs) - 1):
        cp += probs[index]
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(probs) - 1)


def _team_counts(history: list[MatchRow]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for match in history:
        counts[match.home_team] += 1
        counts[match.away_team] += 1
    return counts


def _score_probability(matrix: list[dict[str, Any]], home: int, away: int) -> float:
    for cell in matrix:
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            return float(cell["probability"])
    return 0.0


def _event_probs(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {"btts": 0.0, "home_zero": 0.0, "away_zero": 0.0, "margin2plus": 0.0}
    for cell in matrix:
        h, a, p = int(cell["home_goals"]), int(cell["away_goals"]), float(cell["probability"])
        out["btts"] += p if h > 0 and a > 0 else 0.0
        out["home_zero"] += p if h == 0 else 0.0
        out["away_zero"] += p if a == 0 else 0.0
        out["margin2plus"] += p if abs(h - a) >= 2 else 0.0
    return out


def _actual_features(home: int, away: int) -> dict[str, float]:
    return {
        "btts": 1.0 if home > 0 and away > 0 else 0.0,
        "home_zero": 1.0 if home == 0 else 0.0,
        "away_zero": 1.0 if away == 0 else 0.0,
        "margin2plus": 1.0 if abs(home - away) >= 2 else 0.0,
    }


def _conditional_features(home: int, away: int) -> tuple[float, float, float, float]:
    return (
        1.0 if home > 0 and away > 0 else 0.0,
        1.0 if home == 0 else 0.0,
        1.0 if away == 0 else 0.0,
        1.0 if abs(home - away) >= 2 else 0.0,
    )


def _make_record(match: MatchRow, prediction: dict[str, Any], sequence_index: int, level: str) -> dict[str, Any]:
    matrix = prediction["probabilities"]["score_matrix"]
    marginals = derive_score_marginals(matrix)
    total_vector = [marginals["total_goals"][key] for key in ("0", "1", "2", "3", "4", "5", "6", "7+")]
    actual_total = int(match.home_goals + match.away_goals)
    actual_index = min(actual_total, 7)
    p_score = _score_probability(matrix, match.home_goals, match.away_goals)
    one = marginals["1x2"]
    actual_outcome = "home" if match.home_goals > match.away_goals else "draw" if match.home_goals == match.away_goals else "away"
    one_rps = _rps([one["home"], one["draw"], one["away"]], ("home", "draw", "away").index(actual_outcome))
    base = {
        "season": str(match.season),
        "date": match.date.date().isoformat(),
        "sequence_index": sequence_index,
        "block_id": f"{match.season}:{sequence_index // 20}",
        "base_joint_log": -math.log(max(EPS, p_score)),
        "base_total_rps": _rps(total_vector, actual_index),
        "base_one_x_two_rps": one_rps,
    }
    if level == "metrics":
        return base

    p_total_exact = sum(
        float(cell["probability"])
        for cell in matrix
        if int(cell["home_goals"]) + int(cell["away_goals"]) == actual_total
    )
    conditional_slice = []
    if p_total_exact > 0:
        for cell in matrix:
            h, a = int(cell["home_goals"]), int(cell["away_goals"])
            if h + a != actual_total:
                continue
            conditional_slice.append({
                "home": h,
                "away": a,
                "base_conditional": float(cell["probability"]) / p_total_exact,
                "features": _conditional_features(h, a),
            })
    base.update({
        "actual_home": int(match.home_goals),
        "actual_away": int(match.away_goals),
        "actual_total": actual_total,
        "actual_total_index": actual_index,
        "p_total_exact": p_total_exact,
        "conditional_slice": conditional_slice,
        "total_vector": total_vector,
        "base_events": _event_probs(matrix),
    })
    if level == "eval":
        base["matrix"] = matrix
    return base


def rolling_records(season_matches: list[MatchRow], params: dict[str, Any], config: dict[str, Any], level: str) -> list[dict[str, Any]]:
    warmup_comp = int(config["validation"]["warmup_competition_matches"])
    warmup_team = int(config["validation"]["warmup_team_matches"])
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for match in season_matches:
        by_date[match.date].append(match)
    history: list[MatchRow] = []
    records = []
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
                    history, match.competition_id, str(match.season),
                    match.home_team, match.away_team, match.date, params,
                    use_team_effects=True,
                )
            except (PlatformError, KeyError, ValueError):
                continue
            records.append(_make_record(match, prediction, sequence_index, level))
            sequence_index += 1
        history.extend(by_date[date])
        history.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    return records


def _base_objective(records: list[dict[str, Any]]) -> float:
    if not records:
        return float("inf")
    return mean(row["base_joint_log"] + 0.50 * row["base_total_rps"] + 0.25 * row["base_one_x_two_rps"] for row in records)


def _season_is_partial(season_map: dict[str, list[MatchRow]], latest: str) -> bool:
    counts = [len(rows) for season, rows in season_map.items() if season != latest and rows]
    return bool(counts) and len(season_map[latest]) < 0.85 * median(counts)


def _conditional_log_probability(row: dict[str, Any], values: tuple[float, ...]) -> float:
    normalizer = actual_weight = 0.0
    for cell in row["conditional_slice"]:
        log_tilt = sum(values[i] * cell["features"][i] for i in range(4))
        weight = cell["base_conditional"] * math.exp(log_tilt)
        normalizer += weight
        if cell["home"] == row["actual_home"] and cell["away"] == row["actual_away"]:
            actual_weight = weight
    if normalizer <= 0 or actual_weight <= 0 or row["p_total_exact"] <= 0:
        return math.log(EPS)
    return math.log(max(EPS, row["p_total_exact"] * actual_weight / normalizer))


def train_conditional_params(records: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {name: 0.0 for name in D_FEATURES}
    best_key = None
    best_values = None
    for values in itertools.product(D_GRID_VALUES, repeat=4):
        loss = -mean(_conditional_log_probability(row, values) for row in records)
        loss += 0.01 * sum(value * value for value in values)
        key = (loss, sum(abs(value) for value in values), values)
        if best_key is None or key < best_key:
            best_key, best_values = key, values
    return dict(zip(D_FEATURES, best_values))


def train_tail_params(records: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {name: 0.0 for name in TAIL_FEATURES}
    best_key = None
    best_values = None
    for values in itertools.product(TAIL_GRID_VALUES, repeat=3):
        params = dict(zip(TAIL_FEATURES, values))
        loss = mean(_rps(tilt_total_vector(row["total_vector"], params), row["actual_total_index"]) for row in records)
        loss += 0.005 * sum(value * value for value in values)
        key = (loss, sum(abs(value) for value in values), values)
        if best_key is None or key < best_key:
            best_key, best_values = key, values
    return dict(zip(TAIL_FEATURES, best_values))


def evaluate(records: list[dict[str, Any]], d_params: dict[str, float], tail_params: dict[str, float]) -> list[dict[str, Any]]:
    output = []
    for row in records:
        matrix = row["matrix"]
        d_matrix, d_audit = apply_conditional_exponential_tilt(matrix, d_params)
        tail_matrix, tail_audit = apply_total_tail_tilt(matrix, tail_params)
        d_events = _event_probs(d_matrix)
        actual_events = _actual_features(row["actual_home"], row["actual_away"])
        tail_vec = total_vector_from_matrix(tail_matrix)
        base_vec = row["total_vector"]
        result = {
            "block_id": row["block_id"],
            "d_joint_log_diff": -math.log(max(EPS, _score_probability(d_matrix, row["actual_home"], row["actual_away"]))) - row["base_joint_log"],
            "tail_rps_diff": _rps(tail_vec, row["actual_total_index"]) - row["base_total_rps"],
            "d_max_total_residual": float(d_audit["max_total_marginal_residual"]),
            "tail_max_vector_residual": float(tail_audit["max_total_vector_residual"]),
            "tail_max_conditional_residual": float(tail_audit["max_conditional_score_residual"]),
        }
        for name in D_FEATURES:
            result[f"{name}_brier_diff"] = (d_events[name] - actual_events[name]) ** 2 - (row["base_events"][name] - actual_events[name]) ** 2
        for threshold, field in ((4, "tail4_brier_diff"), (5, "tail5_brier_diff"), (7, "tail7_brier_diff")):
            base_p = sum(base_vec[threshold:]) if threshold < 7 else base_vec[7]
            new_p = sum(tail_vec[threshold:]) if threshold < 7 else tail_vec[7]
            y = 1.0 if row["actual_total"] >= threshold else 0.0
            result[field] = (new_p - y) ** 2 - (base_p - y) ** 2
        output.append(result)
    return output


def _bootstrap_ci(rows: list[dict[str, Any]], field: str, seed: int, resamples: int = 300) -> dict[str, Any]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        blocks[row["block_id"]].append(float(row[field]))
    if not blocks:
        return {"count": 0, "mean_difference": None, "ci95_lower": None, "ci95_upper": None}
    block_values = list(blocks.values())
    observed = mean(value for block in block_values for value in block)
    rng = random.Random(seed)
    samples = []
    for _ in range(resamples):
        chosen = [rng.choice(block_values) for _ in block_values]
        samples.append(mean(value for block in chosen for value in block))
    samples.sort()
    return {
        "count": sum(len(block) for block in block_values),
        "blocks": len(block_values),
        "mean_difference": observed,
        "ci95_lower": samples[max(0, int(0.025 * len(samples)) - 1)],
        "ci95_upper": samples[min(len(samples) - 1, int(0.975 * len(samples)))],
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

    metrics_cache: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for index, candidate in enumerate(candidates):
        for season in seasons:
            metrics_cache[(index, season)] = rolling_records(season_map[season], candidate, config, "metrics")

    train_cache: dict[tuple[int, str], list[dict[str, Any]]] = {}
    def training_rows(index: int, season: str) -> list[dict[str, Any]]:
        key = (index, season)
        if key not in train_cache:
            train_cache[key] = rolling_records(season_map[season], candidates[index], config, "train")
        return train_cache[key]

    outer_rows = []
    folds = []
    for outer_index in range(1, len(seasons)):
        outer_season = seasons[outer_index]
        prior_seasons = seasons[:outer_index]
        scored = []
        for index, candidate in enumerate(candidates):
            prior_metrics = [row for season in prior_seasons for row in metrics_cache[(index, season)]]
            scored.append((_base_objective(prior_metrics), index, candidate, len(prior_metrics)))
        scored.sort(key=lambda item: (item[0], item[1]))
        _, selected_index, selected_base, selection_count = scored[0]
        train_records = [row for season in prior_seasons for row in training_rows(selected_index, season)]
        test_records = rolling_records(season_map[outer_season], selected_base, config, "eval")
        if not train_records or not test_records:
            continue
        d_params = train_conditional_params(train_records)
        tail_params = train_tail_params(train_records)
        evaluated = evaluate(test_records, d_params, tail_params)
        outer_rows.extend(evaluated)
        folds.append({
            "outer_season": outer_season,
            "prior_seasons": prior_seasons,
            "base_candidate_index": selected_index,
            "base_parameters": selected_base,
            "base_selection_predictions": selection_count,
            "conditional_parameters": d_params,
            "tail_parameters": tail_params,
            "outer_predictions": len(evaluated),
            "mean_d_joint_log_diff": mean(row["d_joint_log_diff"] for row in evaluated),
            "mean_tail_rps_diff": mean(row["tail_rps_diff"] for row in evaluated),
        })

    if not outer_rows:
        raise RuntimeError("no eligible outer OOF rows")

    latest = seasons[-1]
    tuning_seasons = seasons[:-1] if _season_is_partial(season_map, latest) else seasons
    live_scores = []
    for index, candidate in enumerate(candidates):
        rows = [row for season in tuning_seasons for row in metrics_cache[(index, season)]]
        live_scores.append((_base_objective(rows), index, candidate, len(rows)))
    live_scores.sort(key=lambda item: (item[0], item[1]))
    _, live_index, live_base, live_count = live_scores[0]
    live_training = [row for season in tuning_seasons for row in training_rows(live_index, season)]
    live_d = train_conditional_params(live_training)
    live_tail = train_tail_params(live_training)

    d_ci = _bootstrap_ci(outer_rows, "d_joint_log_diff", 4701)
    tail_ci = _bootstrap_ci(outer_rows, "tail_rps_diff", 4702)
    structural = {name: mean(row[f"{name}_brier_diff"] for row in outer_rows) for name in D_FEATURES}
    tail_brier = {
        "tail4plus": mean(row["tail4_brier_diff"] for row in outer_rows),
        "tail5plus": mean(row["tail5_brier_diff"] for row in outer_rows),
        "tail7plus": mean(row["tail7_brier_diff"] for row in outer_rows),
    }
    d_status = "REVIEW_CANDIDATE" if d_ci["ci95_upper"] < 0 and sum(v <= 0 for v in structural.values()) >= 3 else "KEEP_FORMAL_WEIGHT_0"
    tail_status = "REVIEW_CANDIDATE" if tail_ci["ci95_upper"] < 0 and tail_brier["tail4plus"] <= 0 and tail_brier["tail5plus"] <= 0 else "KEEP_FORMAL_WEIGHT_0"

    artifact = {
        "schema_version": "V4.7.0-priority-challenger-training-r2",
        "competition_id": competition_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_weight": 0,
        "automatic_promotion": False,
        "target_live_season": latest,
        "tuning_seasons": tuning_seasons,
        "live_base_candidate_index": live_index,
        "live_base_parameters": live_base,
        "live_base_selection_predictions": live_count,
        "conditional_allocation": {
            "parameters": live_d,
            "outer_primary_ci": d_ci,
            "structural_brier_differences": structural,
            "status": d_status,
            "max_total_marginal_residual": max(row["d_max_total_residual"] for row in outer_rows),
        },
        "total_tail": {
            "parameters": live_tail,
            "outer_primary_ci": tail_ci,
            "tail_brier_differences": tail_brier,
            "status": tail_status,
            "max_total_vector_residual": max(row["tail_max_vector_residual"] for row in outer_rows),
            "max_conditional_score_residual": max(row["tail_max_conditional_residual"] for row in outer_rows),
        },
        "outer_predictions": len(outer_rows),
        "outer_folds": len(folds),
        "folds": folds,
        "promotion_policy": "No automatic promotion; CURRENT-compliant competition-specific review required.",
    }
    target = ARTIFACT_ROOT / competition_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "priority_v470.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
        "schema_version": "V4.7.0-priority-challenger-training-r2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "priority_competitions": list(PRIORITY_COMPETITIONS),
        "formal_weight_change": False,
        "automatic_promotion": False,
        "method": "competition-independent nested chronological OOF; no cross-competition training rows, calibrators or weights",
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
