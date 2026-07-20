#!/usr/bin/env python3
"""Strict combined V4.7 priority-challenger promotion audit.

This review is deliberately fail-closed.  It replays the frozen outer folds from
``priority_v470.json`` and applies BOTH staged challengers to the same base matrix:

    base unified matrix
      -> competition-specific conditional P(H,A|T) tilt
      -> competition-specific direct total-tail tilt
      -> one audited candidate matrix

The review reports the CURRENT-required probability-quality surfaces for the
TARGET competition only: joint Log Score, 1X2 Brier/RPS, total-goals RPS,
BTTS/clean sheets/|D|>=2, 4+/5+/7+ tail calibration, 80%/90% score-set
coverage, 1X2 ECE, probability conservation and fold stability.

It NEVER changes formal weights and NEVER writes a promotion receipt.  A later
CURRENT-compliant review may promote a module only when this audit passes.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
VALIDATION_DIR = ROOT_DIR / "validation"
for item in (str(ENGINE_DIR), str(VALIDATION_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

from conditional_allocation_challenger_v470 import apply_conditional_exponential_tilt
from football_v460_engine import load_config
from platform_core import ROOT, MatchRow, derive_score_marginals, read_processed_matches
from total_tail_challenger_v470 import apply_total_tail_tilt, total_vector_from_matrix
from train_priority_challengers_v470 import rolling_records

TARGET_COMPETITIONS = ("USA_MLS",)
ARTIFACT_ROOT = ROOT / "models" / "challengers_v470"
OUT = ROOT / "manifests" / "priority_challenger_promotion_review_v470_status.json"
EPS = 1e-15


def _score_probability(matrix: Iterable[dict[str, Any]], home: int, away: int) -> float:
    for cell in matrix:
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            return float(cell["probability"])
    return 0.0


def _rps(probs: list[float], actual_index: int) -> float:
    cp = co = score = 0.0
    for index in range(len(probs) - 1):
        cp += probs[index]
        co += 1.0 if index == actual_index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(probs) - 1)


def _multiclass_brier(probs: list[float], actual_index: int) -> float:
    return sum((p - (1.0 if i == actual_index else 0.0)) ** 2 for i, p in enumerate(probs)) / len(probs)


def _event_probs(matrix: Iterable[dict[str, Any]]) -> dict[str, float]:
    out = {"btts": 0.0, "home_zero": 0.0, "away_zero": 0.0, "margin2plus": 0.0}
    for cell in matrix:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = float(cell["probability"])
        out["btts"] += p if h > 0 and a > 0 else 0.0
        out["home_zero"] += p if h == 0 else 0.0
        out["away_zero"] += p if a == 0 else 0.0
        out["margin2plus"] += p if abs(h - a) >= 2 else 0.0
    return out


def _actual_events(home: int, away: int) -> dict[str, float]:
    return {
        "btts": 1.0 if home > 0 and away > 0 else 0.0,
        "home_zero": 1.0 if home == 0 else 0.0,
        "away_zero": 1.0 if away == 0 else 0.0,
        "margin2plus": 1.0 if abs(home - away) >= 2 else 0.0,
    }


def _minimum_score_set_contains(matrix: list[dict[str, Any]], target: float, home: int, away: int) -> bool:
    ranking = sorted(matrix, key=lambda cell: (-float(cell["probability"]), int(cell["home_goals"]), int(cell["away_goals"])))
    cumulative = 0.0
    selected: set[tuple[int, int]] = set()
    for cell in ranking:
        selected.add((int(cell["home_goals"]), int(cell["away_goals"])))
        cumulative += float(cell["probability"])
        if cumulative + 1e-12 >= target:
            break
    return (home, away) in selected


def _bootstrap_ci(rows: list[dict[str, Any]], field: str, seed: int, resamples: int = 500) -> dict[str, Any]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        blocks[str(row["block_id"])].append(float(row[field]))
    if not blocks:
        return {"count": 0, "blocks": 0, "mean_difference": None, "ci95_lower": None, "ci95_upper": None}
    values = list(blocks.values())
    observed = mean(v for block in values for v in block)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        chosen = [rng.choice(values) for _ in values]
        samples.append(mean(v for block in chosen for v in block))
    samples.sort()
    return {
        "count": sum(len(block) for block in values),
        "blocks": len(values),
        "mean_difference": observed,
        "ci95_lower": samples[max(0, int(0.025 * len(samples)) - 1)],
        "ci95_upper": samples[min(len(samples) - 1, int(0.975 * len(samples)))],
    }


def _ece(probabilities: list[float], outcomes: list[float], bins: int = 10) -> float:
    if not probabilities:
        return float("nan")
    bucketed: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for p, y in zip(probabilities, outcomes):
        index = min(bins - 1, max(0, int(float(p) * bins)))
        bucketed[index].append((float(p), float(y)))
    total = len(probabilities)
    return sum(
        (len(bucket) / total) * abs(mean(p for p, _ in bucket) - mean(y for _, y in bucket))
        for bucket in bucketed if bucket
    )


def _aggregate_calibration(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    outcome_names = ("home", "draw", "away")
    ece = {}
    for index, name in enumerate(outcome_names):
        probabilities = [row[f"{prefix}_one"][index] for row in rows]
        outcomes = [1.0 if row["actual_outcome_index"] == index else 0.0 for row in rows]
        ece[name] = _ece(probabilities, outcomes)

    structural = {}
    for name in ("btts", "home_zero", "away_zero", "margin2plus"):
        predicted = mean(row[f"{prefix}_events"][name] for row in rows)
        actual = mean(row["actual_events"][name] for row in rows)
        structural[name] = {"predicted": predicted, "actual": actual, "absolute_error": abs(predicted - actual)}

    tail = {}
    for threshold, key in ((4, "tail4plus"), (5, "tail5plus"), (7, "tail7plus")):
        predicted = mean(row[f"{prefix}_{key}_p"] for row in rows)
        actual = mean(1.0 if row["actual_total"] >= threshold else 0.0 for row in rows)
        tail[key] = {"predicted": predicted, "actual": actual, "absolute_error": abs(predicted - actual)}

    return {
        "one_x_two_ece": ece,
        "one_x_two_max_ece": max(ece.values()),
        "structural": structural,
        "tail": tail,
        "score_set_80_coverage": mean(row[f"{prefix}_cover80"] for row in rows),
        "score_set_90_coverage": mean(row[f"{prefix}_cover90"] for row in rows),
    }


def _row_metrics(record: dict[str, Any], candidate_matrix: list[dict[str, Any]], block_id: str, season: str) -> dict[str, Any]:
    base_matrix = record["matrix"]
    actual_home = int(record["actual_home"])
    actual_away = int(record["actual_away"])
    actual_total = int(record["actual_total"])
    actual_total_index = min(actual_total, 7)
    actual_outcome_index = 0 if actual_home > actual_away else 1 if actual_home == actual_away else 2

    base_marginals = derive_score_marginals(base_matrix)
    cand_marginals = derive_score_marginals(candidate_matrix)
    base_one = [base_marginals["1x2"][name] for name in ("home", "draw", "away")]
    cand_one = [cand_marginals["1x2"][name] for name in ("home", "draw", "away")]
    base_total = [base_marginals["total_goals"][key] for key in ("0", "1", "2", "3", "4", "5", "6", "7+")]
    cand_total = total_vector_from_matrix(candidate_matrix)
    base_events = _event_probs(base_matrix)
    cand_events = _event_probs(candidate_matrix)
    actual_events = _actual_events(actual_home, actual_away)

    base_p_score = _score_probability(base_matrix, actual_home, actual_away)
    cand_p_score = _score_probability(candidate_matrix, actual_home, actual_away)

    def tail_p(vector: list[float], threshold: int) -> float:
        return sum(vector[threshold:]) if threshold < 7 else vector[7]

    return {
        "block_id": block_id,
        "season": season,
        "actual_total": actual_total,
        "actual_outcome_index": actual_outcome_index,
        "actual_events": actual_events,
        "joint_log_diff": -math.log(max(EPS, cand_p_score)) + math.log(max(EPS, base_p_score)),
        "one_x_two_brier_diff": _multiclass_brier(cand_one, actual_outcome_index) - _multiclass_brier(base_one, actual_outcome_index),
        "one_x_two_rps_diff": _rps(cand_one, actual_outcome_index) - _rps(base_one, actual_outcome_index),
        "total_rps_diff": _rps(cand_total, actual_total_index) - _rps(base_total, actual_total_index),
        "base_one": base_one,
        "candidate_one": cand_one,
        "base_events": base_events,
        "candidate_events": cand_events,
        "base_tail4plus_p": tail_p(base_total, 4),
        "base_tail5plus_p": tail_p(base_total, 5),
        "base_tail7plus_p": tail_p(base_total, 7),
        "candidate_tail4plus_p": tail_p(cand_total, 4),
        "candidate_tail5plus_p": tail_p(cand_total, 5),
        "candidate_tail7plus_p": tail_p(cand_total, 7),
        "base_cover80": 1.0 if _minimum_score_set_contains(base_matrix, 0.80, actual_home, actual_away) else 0.0,
        "base_cover90": 1.0 if _minimum_score_set_contains(base_matrix, 0.90, actual_home, actual_away) else 0.0,
        "candidate_cover80": 1.0 if _minimum_score_set_contains(candidate_matrix, 0.80, actual_home, actual_away) else 0.0,
        "candidate_cover90": 1.0 if _minimum_score_set_contains(candidate_matrix, 0.90, actual_home, actual_away) else 0.0,
        "candidate_probability_sum": sum(float(cell["probability"]) for cell in candidate_matrix),
    }


def review_competition(competition_id: str) -> dict[str, Any]:
    artifact_path = ARTIFACT_ROOT / competition_id / "priority_v470.json"
    if not artifact_path.exists():
        raise RuntimeError(f"missing priority challenger artifact: {artifact_path}")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("competition_id") != competition_id:
        raise RuntimeError("artifact competition mismatch")
    if artifact.get("formal_weight") != 0:
        raise RuntimeError("review requires challenger artifact formal_weight=0")

    config = load_config()
    matches = read_processed_matches(competition_id)
    season_map: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        season_map[str(match.season)].append(match)
    for rows in season_map.values():
        rows.sort(key=lambda row: (row.date, row.home_team, row.away_team))

    review_rows: list[dict[str, Any]] = []
    fold_reports = []
    max_total_residual = 0.0
    max_probability_residual = 0.0

    for fold in artifact.get("folds", []):
        season = str(fold["outer_season"])
        if season not in season_map:
            continue
        records = rolling_records(season_map[season], fold["base_parameters"], config, "eval")
        fold_rows = []
        for record in records:
            conditional_matrix, conditional_audit = apply_conditional_exponential_tilt(record["matrix"], fold["conditional_parameters"])
            candidate_matrix, tail_audit = apply_total_tail_tilt(conditional_matrix, fold["tail_parameters"])
            max_total_residual = max(
                max_total_residual,
                float(conditional_audit["max_total_marginal_residual"]),
                float(tail_audit["max_total_vector_residual"]),
                float(tail_audit["max_conditional_score_residual"]),
            )
            probability_sum = sum(float(cell["probability"]) for cell in candidate_matrix)
            max_probability_residual = max(max_probability_residual, abs(probability_sum - 1.0))
            row = _row_metrics(record, candidate_matrix, str(record["block_id"]), season)
            fold_rows.append(row)
            review_rows.append(row)
        if fold_rows:
            fold_reports.append({
                "outer_season": season,
                "predictions": len(fold_rows),
                "mean_joint_log_diff": mean(row["joint_log_diff"] for row in fold_rows),
                "mean_one_x_two_brier_diff": mean(row["one_x_two_brier_diff"] for row in fold_rows),
                "mean_one_x_two_rps_diff": mean(row["one_x_two_rps_diff"] for row in fold_rows),
                "mean_total_rps_diff": mean(row["total_rps_diff"] for row in fold_rows),
            })

    if not review_rows:
        raise RuntimeError("no eligible combined outer OOF rows")

    ci = {
        "joint_log": _bootstrap_ci(review_rows, "joint_log_diff", 4711),
        "one_x_two_brier": _bootstrap_ci(review_rows, "one_x_two_brier_diff", 4712),
        "one_x_two_rps": _bootstrap_ci(review_rows, "one_x_two_rps_diff", 4713),
        "total_rps": _bootstrap_ci(review_rows, "total_rps_diff", 4714),
    }
    base_calibration = _aggregate_calibration(review_rows, "base")
    candidate_calibration = _aggregate_calibration(review_rows, "candidate")

    # CURRENT requires both statistically credible OOF improvement and calibration
    # review.  This script intentionally does not encode a new numerical promotion
    # threshold that is absent from CURRENT.  It only identifies whether the
    # candidate is ready for a formal, competition-specific governance decision.
    primary_ci_pass = (
        ci["joint_log"]["ci95_upper"] is not None and ci["joint_log"]["ci95_upper"] < 0.0
        and ci["one_x_two_brier"]["ci95_upper"] is not None and ci["one_x_two_brier"]["ci95_upper"] <= 0.002
        and ci["one_x_two_rps"]["ci95_upper"] is not None and ci["one_x_two_rps"]["ci95_upper"] <= 0.002
        and ci["total_rps"]["ci95_upper"] is not None and ci["total_rps"]["ci95_upper"] <= 0.0
    )
    calibration_non_degradation = (
        candidate_calibration["one_x_two_max_ece"] <= base_calibration["one_x_two_max_ece"] + 0.01
        and candidate_calibration["structural"]["btts"]["absolute_error"] <= base_calibration["structural"]["btts"]["absolute_error"] + 0.01
        and candidate_calibration["structural"]["margin2plus"]["absolute_error"] <= base_calibration["structural"]["margin2plus"]["absolute_error"] + 0.01
        and candidate_calibration["tail"]["tail4plus"]["absolute_error"] <= base_calibration["tail"]["tail4plus"]["absolute_error"] + 0.01
        and candidate_calibration["tail"]["tail5plus"]["absolute_error"] <= base_calibration["tail"]["tail5plus"]["absolute_error"] + 0.01
    )
    coverage_auditable = (
        0.0 <= candidate_calibration["score_set_80_coverage"] <= 1.0
        and 0.0 <= candidate_calibration["score_set_90_coverage"] <= 1.0
        and candidate_calibration["score_set_80_coverage"] <= candidate_calibration["score_set_90_coverage"]
    )
    conservation_pass = max_total_residual <= 1e-10 and max_probability_residual <= 1e-10
    fold_joint_improved = sum(1 for fold in fold_reports if fold["mean_joint_log_diff"] < 0.0)
    fold_total_nonworse = sum(1 for fold in fold_reports if fold["mean_total_rps_diff"] <= 0.0)
    fold_stability = {
        "joint_log_improved_folds": fold_joint_improved,
        "total_rps_nonworse_folds": fold_total_nonworse,
        "fold_count": len(fold_reports),
    }

    review_ready = primary_ci_pass and calibration_non_degradation and coverage_auditable and conservation_pass
    status = "FORMAL_PROMOTION_REVIEW_READY" if review_ready else "KEEP_FORMAL_WEIGHT_0"

    return {
        "competition_id": competition_id,
        "status": status,
        "formal_weight": 0,
        "automatic_promotion": False,
        "combined_chain": "base_matrix -> conditional_allocation_v470 -> total_tail_v470",
        "outer_predictions": len(review_rows),
        "outer_folds": len(fold_reports),
        "confidence_intervals": ci,
        "base_calibration": base_calibration,
        "candidate_calibration": candidate_calibration,
        "fold_stability": fold_stability,
        "folds": fold_reports,
        "audits": {
            "max_structural_or_total_residual": max_total_residual,
            "max_probability_sum_residual": max_probability_residual,
            "primary_ci_pass": primary_ci_pass,
            "calibration_non_degradation": calibration_non_degradation,
            "coverage_auditable": coverage_auditable,
            "probability_conservation_pass": conservation_pass,
        },
        "promotion_policy": "No automatic promotion. CURRENT-compliant competition-specific governance decision is still required; formal_weight remains 0 until that decision is recorded.",
    }


def main() -> int:
    reports = {}
    failures = {}
    for competition_id in TARGET_COMPETITIONS:
        try:
            reports[competition_id] = review_competition(competition_id)
        except Exception as exc:
            failures[competition_id] = str(exc)
            reports[competition_id] = {"competition_id": competition_id, "status": "失败", "formal_weight": 0, "reason": str(exc)}

    status = "PASS" if not failures else "FAIL"
    report = {
        "schema_version": "V4.7.0-priority-challenger-promotion-review-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "target_competitions": list(TARGET_COMPETITIONS),
        "method": "frozen competition-specific outer-fold replay; combined challenger matrix; block-bootstrap CIs; calibration/coverage/conservation audit",
        "reports": reports,
        "failures": failures,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
