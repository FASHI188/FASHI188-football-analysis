#!/usr/bin/env python3
"""Strict USA_MLS V4.7 D|T final-deployment-chain promotion audit.

Exact candidate deployment order under review:
    current formal raw unified matrix
      -> replay-safe season-routed OOF temperature calibration when available
      -> USA_MLS competition-specific conditional P(H,A|T) exponential tilt
      -> final unified matrix

Placing D|T after OOF calibration guarantees that the already-calibrated total-goal
marginal P(T) is preserved exactly.  This script replays only frozen outer folds,
uses the fold-specific D|T parameters learned from prior seasons, and never changes
formal runtime weights.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
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

from conditional_allocation_challenger_v470 import apply_conditional_exponential_tilt
from football_v460_engine import load_config
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import ROOT, MatchRow, derive_score_marginals, read_processed_matches
from train_priority_challengers_v470 import rolling_records

CID = "USA_MLS"
PRIORITY_ARTIFACT = ROOT / "models" / "challengers_v470" / CID / "priority_v470.json"
CALIBRATOR_ARTIFACT = ROOT / "models" / "formal_core_v460" / CID / "oof_matrix_calibrator.json"
OUT = ROOT / "manifests" / "mls_d_conditional_final_chain_v470_status.json"
EPS = 1e-15


def _rps(probs: list[float], actual_index: int) -> float:
    cp = co = score = 0.0
    for index in range(len(probs) - 1):
        cp += probs[index]
        co += 1.0 if index == actual_index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(probs) - 1)


def _multiclass_brier(probs: list[float], actual_index: int) -> float:
    return sum((p - (1.0 if i == actual_index else 0.0)) ** 2 for i, p in enumerate(probs)) / len(probs)


def _score_probability(matrix: list[dict[str, Any]], home: int, away: int) -> float:
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


def _topk_hit(matrix: list[dict[str, Any]], home: int, away: int, k: int) -> float:
    ranked = sorted(matrix, key=lambda c: (-float(c["probability"]), int(c["home_goals"]), int(c["away_goals"])))[:k]
    return 1.0 if any(int(c["home_goals"]) == home and int(c["away_goals"]) == away for c in ranked) else 0.0


def _score_set_hit(matrix: list[dict[str, Any]], target: float, home: int, away: int) -> float:
    ranked = sorted(matrix, key=lambda c: (-float(c["probability"]), int(c["home_goals"]), int(c["away_goals"])))
    cumulative = 0.0
    hit = False
    for cell in ranked:
        cumulative += float(cell["probability"])
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            hit = True
        if cumulative + 1e-12 >= target:
            break
    return 1.0 if hit else 0.0


def _bootstrap_ci(rows: list[dict[str, Any]], field: str, seed: int, resamples: int = 500) -> dict[str, Any]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        blocks[str(row["block_id"])].append(float(row[field]))
    values = list(blocks.values())
    observed = mean(v for block in values for v in block)
    rng = random.Random(seed)
    samples = []
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
    grouped: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for p, y in zip(probabilities, outcomes):
        grouped[min(bins - 1, max(0, int(p * bins)))].append((p, y))
    total = len(probabilities)
    return sum(
        len(bucket) / total * abs(mean(p for p, _ in bucket) - mean(y for _, y in bucket))
        for bucket in grouped if bucket
    )


def _calibration_summary(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    ece = {}
    for idx, name in enumerate(("home", "draw", "away")):
        ece[name] = _ece(
            [row[f"{prefix}_one"][idx] for row in rows],
            [1.0 if row["actual_outcome_index"] == idx else 0.0 for row in rows],
        )
    structural = {}
    for name in ("btts", "home_zero", "away_zero", "margin2plus"):
        pred = mean(row[f"{prefix}_events"][name] for row in rows)
        actual = mean(row["actual_events"][name] for row in rows)
        structural[name] = {"predicted": pred, "actual": actual, "absolute_error": abs(pred - actual)}
    tails = {}
    for threshold, key in ((4, "tail4plus"), (5, "tail5plus"), (7, "tail7plus")):
        pred = mean(row[f"{prefix}_{key}"] for row in rows)
        actual = mean(1.0 if row["actual_total"] >= threshold else 0.0 for row in rows)
        tails[key] = {"predicted": pred, "actual": actual, "absolute_error": abs(pred - actual)}
    return {
        "one_x_two_ece": ece,
        "one_x_two_max_ece": max(ece.values()),
        "structural": structural,
        "tail": tails,
        "top1_hit_rate": mean(row[f"{prefix}_top1"] for row in rows),
        "top3_hit_rate": mean(row[f"{prefix}_top3"] for row in rows),
        "top5_hit_rate": mean(row[f"{prefix}_top5"] for row in rows),
        "score_set_80_coverage": mean(row[f"{prefix}_cover80"] for row in rows),
        "score_set_90_coverage": mean(row[f"{prefix}_cover90"] for row in rows),
    }


def _tail(total_vec: list[float], threshold: int) -> float:
    return sum(total_vec[threshold:]) if threshold < 7 else total_vec[7]


def main() -> int:
    priority = json.loads(PRIORITY_ARTIFACT.read_text(encoding="utf-8"))
    calibrator = json.loads(CALIBRATOR_ARTIFACT.read_text(encoding="utf-8"))
    if priority.get("competition_id") != CID or priority.get("formal_weight") != 0:
        raise RuntimeError("invalid priority challenger artifact")
    if calibrator.get("competition_id") != CID or calibrator.get("operational_status") != "OOF_MATRIX_CALIBRATOR_AVAILABLE":
        raise RuntimeError("invalid OOF calibrator artifact")

    config = load_config()
    season_map: dict[str, list[MatchRow]] = defaultdict(list)
    for match in read_processed_matches(CID):
        season_map[str(match.season)].append(match)
    for matches in season_map.values():
        matches.sort(key=lambda row: (row.date, row.home_team, row.away_team))

    season_calibrators = calibrator.get("season_calibrators") or {}
    all_rows = []
    fold_reports = []
    max_probability_residual = 0.0
    max_total_residual = 0.0
    point_in_time_calibration_safe = True

    for fold in priority.get("folds") or []:
        season = str(fold["outer_season"])
        if season not in season_map:
            continue
        records = rolling_records(season_map[season], fold["base_parameters"], config, "eval")
        season_cal = season_calibrators.get(season)
        if isinstance(season_cal, dict):
            temperature = float(season_cal.get("temperature", 1.0))
            mode = str(season_cal.get("mode") or "temperature")
            training_max_date_raw = season_cal.get("training_max_date")
            training_max_date = date.fromisoformat(str(training_max_date_raw)) if training_max_date_raw else None
        else:
            # Exact runtime fallback when no point-in-time calibrator exists: calibration
            # is unavailable and the raw unified matrix is left unchanged.
            temperature = 1.0
            mode = "runtime_calibration_unavailable_identity_fallback"
            training_max_date = None

        fold_rows = []
        for record in records:
            record_date = date.fromisoformat(str(record["date"]))
            if training_max_date is not None and training_max_date >= record_date:
                point_in_time_calibration_safe = False
            raw_matrix = record["matrix"]
            current_final = temperature_scale_matrix(raw_matrix, temperature) if temperature != 1.0 else raw_matrix
            candidate_final, d_audit = apply_conditional_exponential_tilt(current_final, fold["conditional_parameters"])

            current_marg = derive_score_marginals(current_final)
            candidate_marg = derive_score_marginals(candidate_final)
            current_one = [current_marg["1x2"][key] for key in ("home", "draw", "away")]
            candidate_one = [candidate_marg["1x2"][key] for key in ("home", "draw", "away")]
            current_total = [current_marg["total_goals"][key] for key in ("0", "1", "2", "3", "4", "5", "6", "7+")]
            candidate_total = [candidate_marg["total_goals"][key] for key in ("0", "1", "2", "3", "4", "5", "6", "7+")]
            h = int(record["actual_home"])
            a = int(record["actual_away"])
            actual_total = int(record["actual_total"])
            actual_total_index = min(actual_total, 7)
            actual_outcome_index = 0 if h > a else 1 if h == a else 2
            current_events = _event_probs(current_final)
            candidate_events = _event_probs(candidate_final)
            actual_events = _actual_events(h, a)

            max_probability_residual = max(max_probability_residual, abs(candidate_marg["probability_sum"] - 1.0))
            max_total_residual = max(max_total_residual, max(abs(x - y) for x, y in zip(current_total, candidate_total)))

            row = {
                "block_id": str(record["block_id"]),
                "season": season,
                "actual_total": actual_total,
                "actual_outcome_index": actual_outcome_index,
                "actual_events": actual_events,
                "joint_log_diff": -math.log(max(EPS, _score_probability(candidate_final, h, a))) + math.log(max(EPS, _score_probability(current_final, h, a))),
                "one_x_two_brier_diff": _multiclass_brier(candidate_one, actual_outcome_index) - _multiclass_brier(current_one, actual_outcome_index),
                "one_x_two_rps_diff": _rps(candidate_one, actual_outcome_index) - _rps(current_one, actual_outcome_index),
                "total_rps_diff": _rps(candidate_total, actual_total_index) - _rps(current_total, actual_total_index),
                "current_one": current_one,
                "candidate_one": candidate_one,
                "current_events": current_events,
                "candidate_events": candidate_events,
                "current_tail4plus": _tail(current_total, 4),
                "current_tail5plus": _tail(current_total, 5),
                "current_tail7plus": _tail(current_total, 7),
                "candidate_tail4plus": _tail(candidate_total, 4),
                "candidate_tail5plus": _tail(candidate_total, 5),
                "candidate_tail7plus": _tail(candidate_total, 7),
                "current_top1": _topk_hit(current_final, h, a, 1),
                "current_top3": _topk_hit(current_final, h, a, 3),
                "current_top5": _topk_hit(current_final, h, a, 5),
                "candidate_top1": _topk_hit(candidate_final, h, a, 1),
                "candidate_top3": _topk_hit(candidate_final, h, a, 3),
                "candidate_top5": _topk_hit(candidate_final, h, a, 5),
                "current_cover80": _score_set_hit(current_final, 0.80, h, a),
                "current_cover90": _score_set_hit(current_final, 0.90, h, a),
                "candidate_cover80": _score_set_hit(candidate_final, 0.80, h, a),
                "candidate_cover90": _score_set_hit(candidate_final, 0.90, h, a),
                "d_total_residual": float(d_audit["max_total_marginal_residual"]),
            }
            all_rows.append(row)
            fold_rows.append(row)
        if fold_rows:
            fold_reports.append({
                "outer_season": season,
                "predictions": len(fold_rows),
                "oof_calibration_mode": mode,
                "oof_temperature": temperature,
                "oof_training_max_date": training_max_date.isoformat() if training_max_date else None,
                "conditional_parameters": fold["conditional_parameters"],
                "mean_joint_log_diff": mean(row["joint_log_diff"] for row in fold_rows),
                "mean_one_x_two_brier_diff": mean(row["one_x_two_brier_diff"] for row in fold_rows),
                "mean_one_x_two_rps_diff": mean(row["one_x_two_rps_diff"] for row in fold_rows),
                "mean_total_rps_diff": mean(row["total_rps_diff"] for row in fold_rows),
            })

    if not all_rows:
        raise RuntimeError("no eligible final-chain outer OOF rows")

    ci = {
        "joint_log": _bootstrap_ci(all_rows, "joint_log_diff", 4731),
        "one_x_two_brier": _bootstrap_ci(all_rows, "one_x_two_brier_diff", 4732),
        "one_x_two_rps": _bootstrap_ci(all_rows, "one_x_two_rps_diff", 4733),
        "total_rps": _bootstrap_ci(all_rows, "total_rps_diff", 4734),
    }
    current_cal = _calibration_summary(all_rows, "current")
    candidate_cal = _calibration_summary(all_rows, "candidate")

    primary_ci_pass = (
        ci["joint_log"]["ci95_upper"] < 0.0
        and ci["one_x_two_brier"]["ci95_upper"] <= 0.002
        and ci["one_x_two_rps"]["ci95_upper"] <= 0.002
        and abs(ci["total_rps"]["mean_difference"]) <= 1e-12
        and abs(ci["total_rps"]["ci95_lower"]) <= 1e-12
        and abs(ci["total_rps"]["ci95_upper"]) <= 1e-12
    )
    structural_improvements = {
        name: candidate_cal["structural"][name]["absolute_error"] <= current_cal["structural"][name]["absolute_error"]
        for name in ("btts", "home_zero", "away_zero", "margin2plus")
    }
    calibration_pass = (
        sum(structural_improvements.values()) >= 3
        and candidate_cal["one_x_two_max_ece"] <= current_cal["one_x_two_max_ece"] + 0.01
        and 0.76 <= candidate_cal["score_set_80_coverage"] <= 0.84
        and 0.86 <= candidate_cal["score_set_90_coverage"] <= 0.94
        and candidate_cal["tail"]["tail4plus"]["absolute_error"] <= 0.04
        and candidate_cal["tail"]["tail5plus"]["absolute_error"] <= 0.04
    )
    conservation_pass = max_probability_residual <= 1e-10 and max_total_residual <= 1e-10
    deployment_ready = primary_ci_pass and calibration_pass and conservation_pass and point_in_time_calibration_safe

    report = {
        "schema_version": "V4.7.0-mls-d-conditional-final-chain-review-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "competition_id": CID,
        "status": "FORMAL_PROMOTION_REVIEW_READY" if deployment_ready else "KEEP_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "automatic_promotion": False,
        "deployment_order_under_test": "base_unified_matrix -> replay_safe_oof_temperature_if_available -> USA_MLS_D_given_T_tilt -> final_unified_matrix",
        "outer_predictions": len(all_rows),
        "outer_folds": len(fold_reports),
        "confidence_intervals": ci,
        "current_final_chain_calibration": current_cal,
        "candidate_final_chain_calibration": candidate_cal,
        "structural_absolute_error_nonworse": structural_improvements,
        "folds": fold_reports,
        "audits": {
            "primary_ci_pass": primary_ci_pass,
            "calibration_pass": calibration_pass,
            "probability_conservation_pass": conservation_pass,
            "point_in_time_calibration_safe": point_in_time_calibration_safe,
            "max_probability_sum_residual": max_probability_residual,
            "max_final_total_marginal_residual": max_total_residual,
        },
        "promotion_policy": "D|T-only promotion review. Total-tail challenger is intentionally excluded because it is not a promoted V4.7 formal module. No automatic promotion or weight change occurs here.",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
