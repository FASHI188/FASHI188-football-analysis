#!/usr/bin/env python3
"""All-domain V4.7 D|T candidate final-chain review.

This is a conservative, competition-isolated screening layer.  It reads each
competition's frozen challenger artifact and only replays domains whose D|T
training status is REVIEW_CANDIDATE.  Candidate deployment order is identical to
formal runtime policy:

    base unified matrix -> replay-safe season OOF temperature (if available)
      -> competition-specific D|T tilt -> final unified matrix

No weights are changed here.  USA_MLS 2026, which already owns an independent
promotion receipt, is reported but excluded from new promotion eligibility.
"""
from __future__ import annotations

import json
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
from platform_core import ROOT, MatchRow, derive_score_marginals, load_json, read_processed_matches
from review_mls_d_conditional_final_chain_v470 import (
    _actual_events,
    _bootstrap_ci,
    _calibration_summary,
    _event_probs,
    _multiclass_brier,
    _rps,
    _score_probability,
    _score_set_hit,
    _tail,
    _topk_hit,
)
from train_priority_challengers_v470 import rolling_records

COMPETITION_CONFIG = ROOT / "config" / "competition_independent_v470.json"
ARTIFACT_ROOT = ROOT / "models" / "challengers_v470"
CALIBRATOR_ROOT = ROOT / "models" / "formal_core_v460"
PROMOTION_ROOT = ROOT / "manifests" / "promotions"
OUT = ROOT / "manifests" / "d_conditional_all_domain_final_chain_review_v470_status.json"
EPS = 1e-15


def _review_competition(cid: str, artifact: dict[str, Any]) -> dict[str, Any]:
    conditional = artifact.get("conditional_allocation") or {}
    if conditional.get("status") != "REVIEW_CANDIDATE":
        return {
            "competition_id": cid,
            "status": "NOT_A_TRAINING_CANDIDATE_KEEP_FORMAL_WEIGHT_0",
            "target_live_season": artifact.get("target_live_season"),
            "formal_weight": 0,
            "training_status": conditional.get("status"),
        }

    calibrator_path = CALIBRATOR_ROOT / cid / "oof_matrix_calibrator.json"
    calibrator = load_json(calibrator_path) if calibrator_path.exists() else {}
    season_calibrators = calibrator.get("season_calibrators") if isinstance(calibrator, dict) else {}
    if not isinstance(season_calibrators, dict):
        season_calibrators = {}

    config = load_config()
    season_map: dict[str, list[MatchRow]] = defaultdict(list)
    for match in read_processed_matches(cid):
        season_map[str(match.season)].append(match)
    for matches in season_map.values():
        matches.sort(key=lambda row: (row.date, row.home_team, row.away_team))

    all_rows = []
    folds_out = []
    max_probability_residual = 0.0
    max_total_residual = 0.0
    point_in_time_safe = True

    for fold in artifact.get("folds") or []:
        season = str(fold.get("outer_season"))
        if season not in season_map:
            continue
        records = rolling_records(season_map[season], fold["base_parameters"], config, "eval")
        season_cal = season_calibrators.get(season)
        if isinstance(season_cal, dict):
            temperature = float(season_cal.get("temperature", 1.0))
            mode = str(season_cal.get("mode") or "temperature")
            training_max_raw = season_cal.get("training_max_date")
            training_max = date.fromisoformat(str(training_max_raw)) if training_max_raw else None
        else:
            temperature = 1.0
            mode = "runtime_calibration_unavailable_identity_fallback"
            training_max = None

        fold_rows = []
        for record in records:
            record_date = date.fromisoformat(str(record["date"]))
            if training_max is not None and training_max >= record_date:
                point_in_time_safe = False
            current_final = temperature_scale_matrix(record["matrix"], temperature) if temperature != 1.0 else record["matrix"]
            candidate_final, d_audit = apply_conditional_exponential_tilt(current_final, fold["conditional_parameters"])

            current_marg = derive_score_marginals(current_final)
            candidate_marg = derive_score_marginals(candidate_final)
            current_one = [current_marg["1x2"][k] for k in ("home", "draw", "away")]
            candidate_one = [candidate_marg["1x2"][k] for k in ("home", "draw", "away")]
            current_total = [current_marg["total_goals"][k] for k in ("0", "1", "2", "3", "4", "5", "6", "7+")]
            candidate_total = [candidate_marg["total_goals"][k] for k in ("0", "1", "2", "3", "4", "5", "6", "7+")]

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
                "actual_total": actual_total,
                "actual_outcome_index": actual_outcome_index,
                "actual_events": actual_events,
                "joint_log_diff": -__import__("math").log(max(EPS, _score_probability(candidate_final, h, a))) + __import__("math").log(max(EPS, _score_probability(current_final, h, a))),
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
            }
            all_rows.append(row)
            fold_rows.append(row)

        if fold_rows:
            folds_out.append({
                "outer_season": season,
                "predictions": len(fold_rows),
                "oof_calibration_mode": mode,
                "oof_temperature": temperature,
                "oof_training_max_date": training_max.isoformat() if training_max else None,
                "conditional_parameters": fold["conditional_parameters"],
                "mean_joint_log_diff": mean(r["joint_log_diff"] for r in fold_rows),
            })

    if not all_rows:
        return {
            "competition_id": cid,
            "status": "REVIEW_FAILED_NO_ELIGIBLE_OUTER_ROWS",
            "target_live_season": artifact.get("target_live_season"),
            "formal_weight": 0,
        }

    ci = {
        "joint_log": _bootstrap_ci(all_rows, "joint_log_diff", 4741),
        "one_x_two_brier": _bootstrap_ci(all_rows, "one_x_two_brier_diff", 4742),
        "one_x_two_rps": _bootstrap_ci(all_rows, "one_x_two_rps_diff", 4743),
        "total_rps": _bootstrap_ci(all_rows, "total_rps_diff", 4744),
    }
    current_cal = _calibration_summary(all_rows, "current")
    candidate_cal = _calibration_summary(all_rows, "candidate")
    structural_nonworse = {
        name: candidate_cal["structural"][name]["absolute_error"] <= current_cal["structural"][name]["absolute_error"]
        for name in ("btts", "home_zero", "away_zero", "margin2plus")
    }
    topk_nonworse = {
        "top1": candidate_cal["top1_hit_rate"] >= current_cal["top1_hit_rate"],
        "top3": candidate_cal["top3_hit_rate"] >= current_cal["top3_hit_rate"],
        "top5": candidate_cal["top5_hit_rate"] >= current_cal["top5_hit_rate"],
    }
    primary_pass = (
        ci["joint_log"]["ci95_upper"] < 0.0
        and ci["one_x_two_brier"]["ci95_upper"] <= 0.002
        and ci["one_x_two_rps"]["ci95_upper"] <= 0.002
        and abs(ci["total_rps"]["mean_difference"]) <= 1e-12
    )
    calibration_pass = (
        sum(structural_nonworse.values()) >= 3
        and candidate_cal["one_x_two_max_ece"] <= current_cal["one_x_two_max_ece"] + 0.01
        and 0.76 <= candidate_cal["score_set_80_coverage"] <= 0.84
        and 0.86 <= candidate_cal["score_set_90_coverage"] <= 0.94
        and candidate_cal["tail"]["tail4plus"]["absolute_error"] <= 0.04
        and candidate_cal["tail"]["tail5plus"]["absolute_error"] <= 0.04
    )
    conservation_pass = max_probability_residual <= 1e-10 and max_total_residual <= 1e-10
    topk_pass = all(topk_nonworse.values())
    final_ready = primary_pass and calibration_pass and conservation_pass and point_in_time_safe and topk_pass

    promoted_receipt = PROMOTION_ROOT / f"{cid}_d_conditional_v470.json"
    already_promoted = promoted_receipt.exists() and (load_json(promoted_receipt).get("promotion_status") == "PROMOTED")
    status = (
        "ALREADY_PROMOTED"
        if already_promoted
        else "FORMAL_PROMOTION_REVIEW_READY"
        if final_ready
        else "KEEP_FORMAL_WEIGHT_0"
    )
    return {
        "competition_id": cid,
        "status": status,
        "target_live_season": artifact.get("target_live_season"),
        "formal_weight": 1.0 if already_promoted else 0,
        "outer_predictions": len(all_rows),
        "outer_folds": len(folds_out),
        "confidence_intervals": ci,
        "current_final_chain_calibration": current_cal,
        "candidate_final_chain_calibration": candidate_cal,
        "structural_absolute_error_nonworse": structural_nonworse,
        "topk_nonworse": topk_nonworse,
        "folds": folds_out,
        "audits": {
            "primary_ci_pass": primary_pass,
            "calibration_pass": calibration_pass,
            "topk_pass": topk_pass,
            "probability_conservation_pass": conservation_pass,
            "point_in_time_calibration_safe": point_in_time_safe,
            "max_probability_sum_residual": max_probability_residual,
            "max_final_total_marginal_residual": max_total_residual,
        },
    }


def main() -> int:
    competition_config = load_json(COMPETITION_CONFIG)
    competition_ids = list(competition_config.get("competitions") or [])
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid in competition_ids:
        artifact_path = ARTIFACT_ROOT / cid / "priority_v470.json"
        if not artifact_path.exists():
            reports[cid] = {
                "competition_id": cid,
                "status": "TRAINING_ARTIFACT_NOT_AVAILABLE",
                "formal_weight": 0,
            }
            continue
        try:
            reports[cid] = _review_competition(cid, load_json(artifact_path))
        except Exception as exc:
            failures[cid] = str(exc)
            reports[cid] = {
                "competition_id": cid,
                "status": "REVIEW_FAILED_KEEP_FORMAL_WEIGHT_0",
                "reason": str(exc),
                "formal_weight": 0,
            }

    ready = [cid for cid, rep in reports.items() if rep.get("status") == "FORMAL_PROMOTION_REVIEW_READY"]
    promoted = [cid for cid, rep in reports.items() if rep.get("status") == "ALREADY_PROMOTED"]
    payload = {
        "schema_version": "V4.7.0-all-domain-D-conditional-final-chain-review-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "competition_count": len(competition_ids),
        "promotion_review_ready": ready,
        "already_promoted": promoted,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "deployment_order_under_test": "base_unified_matrix -> replay_safe_oof_temperature_if_available -> competition_specific_D_given_T_tilt -> final_unified_matrix",
        "reports": reports,
        "failures": failures,
        "policy": "Screening only. Every new promotion still requires a separate competition/season-specific independent promotion receipt and runtime activation receipt.",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "ready": ready,
        "already_promoted": promoted,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
