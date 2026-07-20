#!/usr/bin/env python3
"""Second-stage V4.8 direct-total review with candidate-specific recalibration.

Only competitions that passed the first-stage direct categorical total screen are
fully evaluated.  This stage uses two disjoint rolling test windows per outer
season, yielding the policy-required time-fold structure where data permits.
Candidate selection may use only records strictly before each test window.
Candidate temperature calibration is trained only on complete earlier seasons.

No formal weights change. Passing means only that evidence is strong enough to
justify drafting a future complete CURRENT upgrade; V4.7 authority is unchanged.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
VALIDATION_DIR = ROOT_DIR / "validation"
for item in (str(ENGINE_DIR), str(VALIDATION_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

import direct_total_distribution_challenger_v480 as stage1
from football_v460_engine import load_config, predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import ROOT, MatchRow, PlatformError, derive_score_marginals, load_json, read_processed_matches
from total_goals_joint_integration_v466 import _replace_total_marginal

TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
EPS = 1e-15
WINDOWS_PER_OUTER_SEASON = 2
BOOTSTRAP_RESAMPLES = 700
TEMPERATURE_GRID = (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15)
POLICY_PATH = ROOT / "validation" / "promotion_policy.json"
FORMAL_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
CALIBRATOR_ROOT = ROOT / "models" / "formal_core_v460"
STAGE1_ROOT = ROOT / "manifests" / "direct_total_distribution_v480"
OUT_ROOT = ROOT / "manifests" / "direct_total_distribution_v480_recalibration"
MODEL_ROOT = ROOT / "models" / "challengers_v480"


def _proper_rps(values: list[float], actual_index: int) -> float:
    cp = 0.0
    co = 0.0
    score = 0.0
    for index in range(len(values) - 1):
        cp += float(values[index])
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(values) - 1)


stage1._rps = _proper_rps


def _formal_parameters_by_season(cid: str) -> dict[str, dict[str, Any]]:
    path = FORMAL_REPORT_ROOT / f"{cid}.json"
    if not path.exists():
        raise PlatformError(f"formal report missing: {cid}")
    report = load_json(path)
    output = {}
    for fold in report.get("folds") or []:
        if fold.get("outer_season") is not None and isinstance(fold.get("selected_parameters"), dict):
            output[str(fold["outer_season"])] = dict(fold["selected_parameters"])
    return output


def _season_calibrators(cid: str) -> dict[str, dict[str, Any]]:
    path = CALIBRATOR_ROOT / cid / "oof_matrix_calibrator.json"
    if not path.exists():
        return {}
    artifact = load_json(path)
    mapping = artifact.get("season_calibrators")
    return mapping if isinstance(mapping, dict) else {}


def _team_counts(history: list[MatchRow]) -> tuple[Counter[str], Counter[str]]:
    home = Counter()
    away = Counter()
    for match in history:
        home[match.home_team] += 1
        away[match.away_team] += 1
    return home, away


def _one_x_two(matrix: list[dict[str, Any]]) -> list[float]:
    one = derive_score_marginals(matrix)["1x2"]
    return [float(one[key]) for key in ("home", "draw", "away")]


def _multiclass_brier(values: list[float], actual_index: int) -> float:
    return sum((float(value) - (1.0 if i == actual_index else 0.0)) ** 2 for i, value in enumerate(values))


def _score_probability(matrix: list[dict[str, Any]], home: int, away: int) -> float:
    for cell in matrix:
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            return float(cell["probability"])
    return EPS


def _topk_hit(matrix: list[dict[str, Any]], home: int, away: int, k: int) -> float:
    ranked = sorted(matrix, key=lambda cell: (-float(cell["probability"]), int(cell["home_goals"]), int(cell["away_goals"])))[:k]
    return 1.0 if any(int(cell["home_goals"]) == home and int(cell["away_goals"]) == away for cell in ranked) else 0.0


def _score_set_hit(matrix: list[dict[str, Any]], target: float, home: int, away: int) -> float:
    ranked = sorted(matrix, key=lambda cell: (-float(cell["probability"]), int(cell["home_goals"]), int(cell["away_goals"])))
    cumulative = 0.0
    hit = False
    for cell in ranked:
        cumulative += float(cell["probability"])
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            hit = True
        if cumulative + 1e-12 >= target:
            return 1.0 if hit else 0.0
    return 1.0 if hit else 0.0


def _tail(values: list[float], threshold: int) -> float:
    return sum(values[threshold:]) if threshold < 7 else values[7]


def _peak(values: list[float]) -> tuple[int, float]:
    ranked = sorted(enumerate(values), key=lambda item: (-item[1], item[0]))
    return ranked[0][0], ranked[0][1] - ranked[1][1]


def _date_windows(records: list[dict[str, Any]], count: int) -> list[set[str]]:
    dates = sorted({str(row["date"]) for row in records})
    if not dates:
        return []
    count = min(max(1, count), len(dates))
    output = []
    for index in range(count):
        start = index * len(dates) // count
        end = (index + 1) * len(dates) // count
        selected = set(dates[start:end])
        if selected:
            output.append(selected)
    return output


def _raw_season_records(
    cid: str,
    season_matches: list[MatchRow],
    formal_params: dict[str, Any],
    current_calibrator: dict[str, Any] | None,
    candidate: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for match in season_matches:
        by_date[match.date].append(match)
    history: list[MatchRow] = []
    output = []
    sequence = 0
    warmup_comp = int(config["validation"]["warmup_competition_matches"])
    warmup_team = int(config["validation"]["warmup_team_matches"])
    current_temperature = float((current_calibrator or {}).get("temperature", 1.0))
    current_training_max_raw = (current_calibrator or {}).get("training_max_date")
    current_training_max = date.fromisoformat(str(current_training_max_raw)) if current_training_max_raw else None

    for match_date in sorted(by_date):
        home_counts, away_counts = _team_counts(history)
        for match in sorted(by_date[match_date], key=lambda row: (row.home_team, row.away_team)):
            if len(history) < warmup_comp or home_counts[match.home_team] < warmup_team or away_counts[match.away_team] < warmup_team:
                continue
            try:
                base = predict_from_history(
                    history, cid, str(match.season), match.home_team, match.away_team,
                    match.date, selected_parameters=formal_params, use_team_effects=True,
                )
            except (PlatformError, KeyError, ValueError):
                continue
            base_matrix = base["probabilities"]["score_matrix"]
            base_marg = derive_score_marginals(base_matrix)
            champion_total = [float(base_marg["total_goals"][key]) for key in TOTAL_KEYS]
            candidate_total = stage1.categorical_total_distribution(history, match, champion_total, candidate)
            candidate_matrix = _replace_total_marginal(
                base_matrix, {key: candidate_total[i] for i, key in enumerate(TOTAL_KEYS)}
            )
            current_final = temperature_scale_matrix(base_matrix, current_temperature) if current_temperature != 1.0 else base_matrix
            selection_final = temperature_scale_matrix(candidate_matrix, current_temperature) if current_temperature != 1.0 else candidate_matrix
            output.append({
                "match_key": f"{match.season}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}",
                "season": str(match.season),
                "date": match.date.date().isoformat(),
                "block_id": f"{match.season}:{sequence // 20}",
                "actual_home": int(match.home_goals),
                "actual_away": int(match.away_goals),
                "base_matrix": base_matrix,
                "candidate_raw_matrix": candidate_matrix,
                "current_final_matrix": current_final,
                "selection_candidate_matrix": selection_final,
                "current_calibration_safe": current_training_max is None or current_training_max < match.date.date(),
            })
            sequence += 1
        history.extend(by_date[match_date])
        history.sort(key=lambda row: (row.date, row.home_team, row.away_team))
    return output


def _matrix_objective(matrix: list[dict[str, Any]], home: int, away: int) -> float:
    marg = derive_score_marginals(matrix)
    total = [float(marg["total_goals"][key]) for key in TOTAL_KEYS]
    one = [float(marg["1x2"][key]) for key in ("home", "draw", "away")]
    actual_total = min(home + away, 7)
    actual_outcome = 0 if home > away else 1 if home == away else 2
    return (
        -math.log(max(EPS, _score_probability(matrix, home, away)))
        + 0.50 * _proper_rps(total, actual_total)
        + 0.25 * _proper_rps(one, actual_outcome)
    )


def _fit_temperature(records: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    if not records:
        return 1.0, {"mode": "identity_no_prior_training", "training_predictions": 0}
    scored = []
    for temperature in TEMPERATURE_GRID:
        losses = []
        for row in records:
            matrix = row["candidate_raw_matrix"]
            calibrated = temperature_scale_matrix(matrix, temperature) if temperature != 1.0 else matrix
            losses.append(_matrix_objective(calibrated, int(row["actual_home"]), int(row["actual_away"])))
        scored.append((mean(losses), abs(temperature - 1.0), temperature))
    scored.sort()
    return float(scored[0][2]), {
        "mode": "candidate_specific_temperature_grid",
        "temperature": float(scored[0][2]),
        "training_predictions": len(records),
        "training_max_date": max(str(row["date"]) for row in records),
        "grid": list(TEMPERATURE_GRID),
    }


def _selection_loss(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return float("inf")
    losses = []
    for row in rows:
        matrix = row["selection_candidate_matrix"]
        current = row["current_final_matrix"]
        h = int(row["actual_home"])
        a = int(row["actual_away"])
        cand_m = derive_score_marginals(matrix)
        curr_m = derive_score_marginals(current)
        cand_total = [float(cand_m["total_goals"][key]) for key in TOTAL_KEYS]
        curr_total = [float(curr_m["total_goals"][key]) for key in TOTAL_KEYS]
        actual_total = min(h + a, 7)
        total_diff = _proper_rps(cand_total, actual_total) - _proper_rps(curr_total, actual_total)
        joint_diff = -math.log(max(EPS, _score_probability(matrix, h, a))) + math.log(max(EPS, _score_probability(current, h, a)))
        losses.append(total_diff + 0.10 * max(0.0, joint_diff))
    return mean(losses)


def _evaluate_pair(current: list[dict[str, Any]], candidate: list[dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    h = int(row["actual_home"])
    a = int(row["actual_away"])
    actual_total_raw = h + a
    actual_total = min(actual_total_raw, 7)
    actual_outcome = 0 if h > a else 1 if h == a else 2
    current_m = derive_score_marginals(current)
    candidate_m = derive_score_marginals(candidate)
    current_total = [float(current_m["total_goals"][key]) for key in TOTAL_KEYS]
    candidate_total = [float(candidate_m["total_goals"][key]) for key in TOTAL_KEYS]
    current_one = [float(current_m["1x2"][key]) for key in ("home", "draw", "away")]
    candidate_one = [float(candidate_m["1x2"][key]) for key in ("home", "draw", "away")]
    current_peak, current_gap = _peak(current_total)
    candidate_peak, candidate_gap = _peak(candidate_total)
    return {
        "block_id": str(row["block_id"]),
        "date": str(row["date"]),
        "total_rps_diff": _proper_rps(candidate_total, actual_total) - _proper_rps(current_total, actual_total),
        "joint_log_diff": -math.log(max(EPS, _score_probability(candidate, h, a))) + math.log(max(EPS, _score_probability(current, h, a))),
        "one_x_two_brier_diff": _multiclass_brier(candidate_one, actual_outcome) - _multiclass_brier(current_one, actual_outcome),
        "one_x_two_rps_diff": _proper_rps(candidate_one, actual_outcome) - _proper_rps(current_one, actual_outcome),
        "tail4_brier_diff": (_tail(candidate_total, 4) - (1.0 if actual_total_raw >= 4 else 0.0)) ** 2 - (_tail(current_total, 4) - (1.0 if actual_total_raw >= 4 else 0.0)) ** 2,
        "tail5_brier_diff": (_tail(candidate_total, 5) - (1.0 if actual_total_raw >= 5 else 0.0)) ** 2 - (_tail(current_total, 5) - (1.0 if actual_total_raw >= 5 else 0.0)) ** 2,
        "tail7_brier_diff": (_tail(candidate_total, 7) - (1.0 if actual_total_raw >= 7 else 0.0)) ** 2 - (_tail(current_total, 7) - (1.0 if actual_total_raw >= 7 else 0.0)) ** 2,
        "current_top1": _topk_hit(current, h, a, 1),
        "current_top3": _topk_hit(current, h, a, 3),
        "current_top5": _topk_hit(current, h, a, 5),
        "candidate_top1": _topk_hit(candidate, h, a, 1),
        "candidate_top3": _topk_hit(candidate, h, a, 3),
        "candidate_top5": _topk_hit(candidate, h, a, 5),
        "current_cover80": _score_set_hit(current, 0.80, h, a),
        "current_cover90": _score_set_hit(current, 0.90, h, a),
        "candidate_cover80": _score_set_hit(candidate, 0.80, h, a),
        "candidate_cover90": _score_set_hit(candidate, 0.90, h, a),
        "current_peak_bucket": current_peak,
        "candidate_peak_bucket": candidate_peak,
        "actual_total_bucket": actual_total,
        "current_weak_peak": 1.0 if current_gap < 0.02 else 0.0,
        "candidate_weak_peak": 1.0 if candidate_gap < 0.02 else 0.0,
        "probability_residual": abs(float(candidate_m["probability_sum"]) - 1.0),
    }


def _bootstrap_ci(rows: list[dict[str, Any]], field: str, seed: int) -> dict[str, Any]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        blocks[str(row["block_id"])].append(float(row[field]))
    values = list(blocks.values())
    if not values:
        return {"count": 0, "blocks": 0, "mean_difference": None, "ci95_lower": None, "ci95_upper": None}
    observed = mean(value for block in values for value in block)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        chosen = [rng.choice(values) for _ in values]
        samples.append(mean(value for block in chosen for value in block))
    samples.sort()
    return {
        "count": sum(len(block) for block in values),
        "blocks": len(values),
        "mean_difference": observed,
        "ci95_lower": samples[max(0, int(0.025 * len(samples)) - 1)],
        "ci95_upper": samples[min(len(samples) - 1, int(0.975 * len(samples)))],
    }


def validate_competition(cid: str) -> dict[str, Any]:
    stage1_path = STAGE1_ROOT / f"{cid}.json"
    if not stage1_path.exists():
        raise PlatformError("stage1 direct-total research receipt missing")
    stage1_receipt = load_json(stage1_path)
    if stage1_receipt.get("status") != "RECALIBRATION_REVIEW_CANDIDATE":
        return {
            "schema_version": "V4.8.0-direct-total-recalibration-review-r1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "competition_id": cid,
            "status": "NOT_STAGE1_CANDIDATE_KEEP_RESEARCH_WEIGHT_0",
            "formal_weight": 0,
            "automatic_promotion": False,
            "stage1_status": stage1_receipt.get("status"),
        }

    matches = read_processed_matches(cid)
    season_map: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        season_map[str(match.season)].append(match)
    seasons = sorted(season_map, key=lambda season: min(row.date for row in season_map[season]))
    season_order = {season: i for i, season in enumerate(seasons)}
    for rows in season_map.values():
        rows.sort(key=lambda row: (row.date, row.home_team, row.away_team))
    formal_params = _formal_parameters_by_season(cid)
    current_calibrators = _season_calibrators(cid)
    config = load_config()

    cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for season in seasons:
        params = formal_params.get(season)
        if params is None:
            continue
        for candidate in stage1.CANDIDATES:
            cache[(season, str(candidate["id"]))] = _raw_season_records(
                cid, season_map[season], params, current_calibrators.get(season), candidate, config
            )

    all_rows = []
    folds = []
    seen: set[str] = set()
    for outer_season in seasons[1:]:
        if outer_season not in formal_params:
            continue
        identity_rows = cache.get((outer_season, "D0_identity"), [])
        for window_index, test_dates in enumerate(_date_windows(identity_rows, WINDOWS_PER_OUTER_SEASON), start=1):
            test_start = min(test_dates)
            scored = []
            for candidate in stage1.CANDIDATES:
                candidate_id = str(candidate["id"])
                prior_rows = []
                for season in seasons:
                    if season not in formal_params:
                        continue
                    rows = cache.get((season, candidate_id), [])
                    if season_order[season] < season_order[outer_season]:
                        prior_rows.extend(rows)
                    elif season == outer_season:
                        prior_rows.extend(row for row in rows if str(row["date"]) < test_start)
                if prior_rows:
                    scored.append((_selection_loss(prior_rows), candidate_id, candidate, prior_rows))
            if not scored:
                continue
            scored.sort(key=lambda item: (item[0], item[1]))
            selection_loss, selected_id, selected, selection_rows = scored[0]

            calibration_rows = []
            for season in seasons:
                if season in formal_params and season_order[season] < season_order[outer_season]:
                    calibration_rows.extend(cache.get((season, selected_id), []))
            candidate_temperature, calibration_audit = _fit_temperature(calibration_rows)
            calibration_end = calibration_audit.get("training_max_date")
            calibration_safe = calibration_end is None or str(calibration_end) < test_start
            selection_end = max(str(row["date"]) for row in selection_rows)

            test_rows = [row for row in cache.get((outer_season, selected_id), []) if str(row["date"]) in test_dates]
            fold_evaluated = []
            for row in test_rows:
                if row["match_key"] in seen:
                    raise PlatformError(f"overlapping recalibration test row: {row['match_key']}")
                seen.add(row["match_key"])
                current_final = row["current_final_matrix"]
                raw_candidate = row["candidate_raw_matrix"]
                candidate_final = temperature_scale_matrix(raw_candidate, candidate_temperature) if candidate_temperature != 1.0 else raw_candidate
                evaluated = _evaluate_pair(current_final, candidate_final, row)
                evaluated["outer_fold_id"] = f"{outer_season}:RW{window_index}"
                all_rows.append(evaluated)
                fold_evaluated.append(evaluated)
            if fold_evaluated:
                folds.append({
                    "outer_fold_id": f"{outer_season}:RW{window_index}",
                    "outer_season": outer_season,
                    "test_start_date": test_start,
                    "test_end_date": max(test_dates),
                    "selection_information_end": selection_end,
                    "selected_candidate": selected,
                    "selection_loss": selection_loss,
                    "selection_predictions": len(selection_rows),
                    "candidate_calibration": calibration_audit,
                    "candidate_calibration_safe": calibration_safe,
                    "outer_predictions": len(fold_evaluated),
                    "mean_total_rps_diff": mean(row["total_rps_diff"] for row in fold_evaluated),
                    "mean_joint_log_diff": mean(row["joint_log_diff"] for row in fold_evaluated),
                })

    if not all_rows:
        raise PlatformError("no eligible second-stage outer rows")

    ci = {
        "total_rps": _bootstrap_ci(all_rows, "total_rps_diff", 4811),
        "joint_log": _bootstrap_ci(all_rows, "joint_log_diff", 4812),
        "one_x_two_brier": _bootstrap_ci(all_rows, "one_x_two_brier_diff", 4813),
        "one_x_two_rps": _bootstrap_ci(all_rows, "one_x_two_rps_diff", 4814),
    }
    tail = {
        "tail4plus_brier_diff": mean(row["tail4_brier_diff"] for row in all_rows),
        "tail5plus_brier_diff": mean(row["tail5_brier_diff"] for row in all_rows),
        "tail7plus_brier_diff": mean(row["tail7_brier_diff"] for row in all_rows),
    }
    coverage = {
        "current_top1": mean(row["current_top1"] for row in all_rows),
        "candidate_top1": mean(row["candidate_top1"] for row in all_rows),
        "current_top3": mean(row["current_top3"] for row in all_rows),
        "candidate_top3": mean(row["candidate_top3"] for row in all_rows),
        "current_top5": mean(row["current_top5"] for row in all_rows),
        "candidate_top5": mean(row["candidate_top5"] for row in all_rows),
        "current_score80": mean(row["current_cover80"] for row in all_rows),
        "candidate_score80": mean(row["candidate_cover80"] for row in all_rows),
        "current_score90": mean(row["current_cover90"] for row in all_rows),
        "candidate_score90": mean(row["candidate_cover90"] for row in all_rows),
    }
    current_modes = Counter(int(row["current_peak_bucket"]) for row in all_rows)
    candidate_modes = Counter(int(row["candidate_peak_bucket"]) for row in all_rows)
    peak_diagnostics = {
        "current_mode3_rate": current_modes.get(3, 0) / len(all_rows),
        "candidate_mode3_rate": candidate_modes.get(3, 0) / len(all_rows),
        "current_weak_peak_rate": mean(row["current_weak_peak"] for row in all_rows),
        "candidate_weak_peak_rate": mean(row["candidate_weak_peak"] for row in all_rows),
        "current_total_top1_accuracy": mean(1.0 if row["current_peak_bucket"] == row["actual_total_bucket"] else 0.0 for row in all_rows),
        "candidate_total_top1_accuracy": mean(1.0 if row["candidate_peak_bucket"] == row["actual_total_bucket"] else 0.0 for row in all_rows),
        "current_unique_peak_buckets": sorted(current_modes),
        "candidate_unique_peak_buckets": sorted(candidate_modes),
        "diagnostic_only": True,
    }
    thresholds = load_json(POLICY_PATH)["a_grade_thresholds"]
    checks = {
        "minimum_outer_predictions": len(all_rows) >= int(thresholds["minimum_outer_predictions"]),
        "minimum_outer_time_folds": len(folds) >= int(thresholds["minimum_outer_time_folds"]),
        "disjoint_test_windows": len(seen) == len(all_rows),
        "strictly_prior_selection": all(str(fold["selection_information_end"]) < str(fold["test_start_date"]) for fold in folds),
        "candidate_calibration_strictly_prior": all(bool(fold["candidate_calibration_safe"]) for fold in folds),
        "total_rps_ci_improves": float(ci["total_rps"]["ci95_upper"]) <= float(thresholds["total_goals_rps_difference_ci_upper_lte"]),
        "joint_log_ci_improves": float(ci["joint_log"]["ci95_upper"]) < float(thresholds["joint_log_score_difference_ci_upper_lt"]),
        "one_x_two_brier_noninferior": float(ci["one_x_two_brier"]["ci95_upper"]) <= float(thresholds["one_x_two_brier_rps_difference_ci_upper_lte"]),
        "one_x_two_rps_noninferior": float(ci["one_x_two_rps"]["ci95_upper"]) <= float(thresholds["one_x_two_brier_rps_difference_ci_upper_lte"]),
        "tail4_nonworse": tail["tail4plus_brier_diff"] <= 0.0,
        "tail5_nonworse": tail["tail5plus_brier_diff"] <= 0.0,
        "top1_nonworse": coverage["candidate_top1"] >= coverage["current_top1"],
        "top3_nonworse": coverage["candidate_top3"] >= coverage["current_top3"],
        "top5_nonworse": coverage["candidate_top5"] >= coverage["current_top5"],
        "score80_calibrated": float(thresholds["score_set_80_coverage_min"]) <= coverage["candidate_score80"] <= float(thresholds["score_set_80_coverage_max"]),
        "score90_calibrated": float(thresholds["score_set_90_coverage_min"]) <= coverage["candidate_score90"] <= float(thresholds["score_set_90_coverage_max"]),
        "probability_conservation": max(float(row["probability_residual"]) for row in all_rows) <= 1e-10,
    }
    ready = all(checks.values())
    payload = {
        "schema_version": "V4.8.0-direct-total-recalibration-review-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "competition_id": cid,
        "status": "V480_CURRENT_UPGRADE_EVIDENCE_READY" if ready else "KEEP_RESEARCH_WEIGHT_0",
        "formal_weight": 0,
        "automatic_promotion": False,
        "outer_predictions": len(all_rows),
        "outer_folds": len(folds),
        "confidence_intervals": ci,
        "tail_brier_differences": tail,
        "score_coverage": coverage,
        "peak_diagnostics": peak_diagnostics,
        "checks": checks,
        "folds": folds,
        "policy": (
            "Even a full pass cannot alter V4.7 formal probabilities. It only permits a future complete V4.8 CURRENT draft, "
            "fresh candidate-specific calibration artifact construction, independent replay, and explicit activation." 
        ),
    }
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / f"{cid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target = MODEL_ROOT / cid
    target.mkdir(parents=True, exist_ok=True)
    (target / "direct_total_distribution_recalibration_review.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    args = parser.parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        result = validate_competition(args.competition)
    except Exception as exc:
        result = {
            "schema_version": "V4.8.0-direct-total-recalibration-review-r1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "competition_id": args.competition,
            "status": "FAILED",
            "formal_weight": 0,
            "automatic_promotion": False,
            "reason": str(exc),
        }
        (OUT_ROOT / f"{args.competition}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "competition_id": result["competition_id"],
        "status": result["status"],
        "outer_predictions": result.get("outer_predictions"),
        "outer_folds": result.get("outer_folds"),
        "confidence_intervals": result.get("confidence_intervals"),
        "peak_diagnostics": result.get("peak_diagnostics"),
        "checks": result.get("checks"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
