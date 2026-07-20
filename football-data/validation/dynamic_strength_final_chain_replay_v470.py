#!/usr/bin/env python3
"""Replay a V4.7 dynamic-strength candidate through the existing OOF calibration chain.

This is the final research gate before live-input/runtime review.  It verifies the
current validated full-matrix calibrator artifact, reconstructs the second-stage
frozen dynamic-strength candidate, applies the exact target-season temperature
when the formal runtime would apply it, and preserves the raw matrix when the
formal runtime has no target-season calibrator.  No formal weight is changed.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from dynamic_strength_oof_screen_v470 import (
    CANDIDATES,
    MODEL_ROOT,
    bootstrap_diff,
    build_season_indexes,
    challenger_matrix,
    date_windows,
    load_domain_data,
    score_metrics,
    team_features,
    to_match,
    utc_now,
    write_json,
)
from football_v460_engine import _merge_parameters, build_score_matrix, expected_goals, fit_current_season_state, load_config, low_score_factors
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, load_json, sha256_file, sha256_json

ROOT = Path(__file__).resolve().parents[1]
SECOND_STAGE_ROOT = ROOT / "manifests" / "dynamic_strength_second_stage_v470"
OUT_ROOT = ROOT / "manifests" / "dynamic_strength_final_chain_replay_v470"
ENGINE_PATH = ROOT / "engine" / "football_v460_engine.py"
CALIBRATION_CODE_PATH = ROOT / "engine" / "oof_matrix_calibration.py"
SOURCE_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
CALIBRATION_REPORT_ROOT = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461"


def verify_calibrator(competition_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = MODEL_ROOT / competition_id / "oof_matrix_calibrator.json"
    if not path.exists():
        raise PlatformError("OOF matrix calibrator artifact missing")
    artifact = load_json(path)
    source_path = SOURCE_REPORT_ROOT / f"{competition_id}.json"
    calibration_report_path = CALIBRATION_REPORT_ROOT / f"{competition_id}.json"
    checks = {
        "operational": artifact.get("operational_status") == "OOF_MATRIX_CALIBRATOR_AVAILABLE" and artifact.get("enabled") is True,
        "competition_match": artifact.get("competition_id") == competition_id,
        "engine_sha_match": artifact.get("engine_sha256") == sha256_file(ENGINE_PATH),
        "calibration_code_sha_match": artifact.get("calibration_code_sha256") == sha256_file(CALIBRATION_CODE_PATH),
        "source_report_exists": source_path.exists(),
        "calibration_report_exists": calibration_report_path.exists(),
    }
    if source_path.exists():
        checks["source_report_sha_match"] = artifact.get("source_nested_backtest_report_sha256") == sha256_file(source_path)
    else:
        checks["source_report_sha_match"] = False
    if calibration_report_path.exists():
        checks["calibration_report_sha_match"] = artifact.get("calibration_report_sha256") == sha256_json(load_json(calibration_report_path))
    else:
        checks["calibration_report_sha_match"] = False
    if not all(checks.values()):
        raise PlatformError(f"OOF calibrator integrity checks failed: {checks}")
    return artifact, {
        "status": "通过",
        "artifact_path": str(path.relative_to(ROOT)),
        "artifact_sha256": sha256_file(path),
        "checks": checks,
        "method": artifact.get("method"),
    }


def raw_matrices_for_season(
    competition_id: str,
    season: str,
    selected_params: dict[str, Any],
    selected_candidate: dict[str, Any],
    data: dict[str, Any],
    indexes: dict[str, Any],
) -> list[dict[str, Any]]:
    config = load_config()
    by_season = indexes["by_season"]
    games = by_season.get(season, [])
    previous = indexes["previous"].get(season)
    if not games or not previous or previous not in by_season:
        return []
    params = _merge_parameters(config, selected_params)
    prior_rows = [to_match(g, competition_id) for g in by_season[previous]]
    prior_cutoff = max(g["date"] for g in by_season[previous]) + timedelta(days=1)
    try:
        prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, config)
    except PlatformError:
        prior_state = None
    output = []
    for target in games:
        history = [to_match(g, competition_id) for g in games if g["date"] < target["date"]]
        try:
            current_state = fit_current_season_state(history, target["date"], params, config)
            base_means = expected_goals(current_state, f"club_{target['home_id']}", f"club_{target['away_id']}", params, config)
            base_matrix = build_score_matrix(
                float(base_means["mu_home"]), float(base_means["mu_away"]), current_state["nb_dispersion_k"],
                params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params),
            )
        except PlatformError:
            continue
        home_feat = team_features(target["home_id"], season, target["date"], indexes, data["transfers"])
        away_feat = team_features(target["away_id"], season, target["date"], indexes, data["transfers"])
        if not home_feat.get("feature_complete") or not away_feat.get("feature_complete"):
            continue
        try:
            challenger, challenger_audit = challenger_matrix(
                current_state, prior_state, target["home_id"], target["away_id"], home_feat, away_feat,
                selected_candidate, params, config,
            )
        except PlatformError:
            continue
        output.append({
            "match_key": f"{competition_id}:{season}:{target['game_id']}",
            "date": target["date"].date().isoformat(),
            "season": season,
            "block_id": f"{season}:{target['date'].year}-{target['date'].month:02d}",
            "home_goals": int(target["home_goals"]),
            "away_goals": int(target["away_goals"]),
            "base_matrix": base_matrix,
            "candidate_matrix": challenger,
            "challenger_audit": challenger_audit,
        })
    return output


def apply_runtime_equivalent_calibration(matrix: list[dict[str, Any]], season: str, artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    season_map = artifact.get("season_calibrators") if isinstance(artifact.get("season_calibrators"), dict) else {}
    calibrator = season_map.get(season)
    if not isinstance(calibrator, dict):
        return matrix, {
            "status": "不可用",
            "mode": "runtime_preserves_raw_when_target_season_calibrator_missing",
            "temperature": 1.0,
            "training_max_date": None,
        }
    temperature = float(calibrator.get("temperature", 1.0))
    return temperature_scale_matrix(matrix, temperature), {
        "status": "通过",
        "mode": calibrator.get("mode"),
        "temperature": temperature,
        "training_max_date": calibrator.get("training_max_date"),
        "guardrail_passed": calibrator.get("guardrail_passed"),
    }


def validate(competition_id: str, cache: Path) -> dict[str, Any]:
    second_path = SECOND_STAGE_ROOT / f"{competition_id}.json"
    if not second_path.exists():
        raise PlatformError("second-stage receipt missing")
    second = load_json(second_path)
    if second.get("status") != "SECOND_STAGE_FINAL_CHAIN_REVIEW_CANDIDATE":
        raise PlatformError("final-chain replay requires a passing second-stage receipt")
    artifact, calibrator_integrity = verify_calibrator(competition_id)
    model = load_json(MODEL_ROOT / competition_id / "model.json")
    parameter_map = model.get("point_in_time_parameters") or {}
    candidate_by_id = {item["id"]: item for item in CANDIDATES}
    frozen_selections = {item["target_season"]: item for item in second.get("season_candidate_selections", [])}
    data = load_domain_data(competition_id, cache)
    indexes = build_season_indexes(data)

    model_rows: list[dict[str, Any]] = []
    base_rows: list[dict[str, Any]] = []
    folds = []
    season_calibration_audit = {}
    for season, selection in frozen_selections.items():
        if season not in parameter_map:
            continue
        candidate_id = str(selection.get("selected_candidate") or "")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            raise PlatformError(f"unknown frozen second-stage candidate: {candidate_id}")
        raw_rows = raw_matrices_for_season(competition_id, season, parameter_map[season], candidate, data, indexes)
        if not raw_rows:
            continue
        calibrated_records = []
        for row in raw_rows:
            base_matrix, base_cal = apply_runtime_equivalent_calibration(row["base_matrix"], season, artifact)
            candidate_matrix, candidate_cal = apply_runtime_equivalent_calibration(row["candidate_matrix"], season, artifact)
            if base_cal != candidate_cal:
                raise PlatformError("baseline and candidate calibration paths diverged")
            base_metric = score_metrics(base_matrix, row["home_goals"], row["away_goals"])
            candidate_metric = score_metrics(candidate_matrix, row["home_goals"], row["away_goals"])
            base_records = {"match_key": row["match_key"], "date": row["date"], "season": season, "block_id": row["block_id"], **base_metric}
            candidate_records = {"match_key": row["match_key"], "date": row["date"], "season": season, "block_id": row["block_id"], **candidate_metric, **row["challenger_audit"]}
            calibrated_records.append((candidate_records, base_records))
            season_calibration_audit[season] = base_cal
        date_records = [pair[1] for pair in calibrated_records]
        for wi, dates in enumerate(date_windows(date_records, 2), start=1):
            pairs = [pair for pair in calibrated_records if pair[1]["date"] in dates]
            if not pairs:
                continue
            model_rows.extend(pair[0] for pair in pairs)
            base_rows.extend(pair[1] for pair in pairs)
            folds.append({
                "fold_id": f"{season}:FINAL{wi}",
                "target_season": season,
                "frozen_candidate": candidate_id,
                "calibration_status": season_calibration_audit[season]["status"],
                "calibration_mode": season_calibration_audit[season]["mode"],
                "temperature": season_calibration_audit[season]["temperature"],
                "test_start": min(dates),
                "test_end": max(dates),
                "outer_predictions": len(pairs),
            })

    pairs = list(zip(model_rows, base_rows))
    if not pairs:
        raise PlatformError("no paired final-chain predictions")
    cis = {metric: bootstrap_diff(pairs, metric) for metric in ("joint_log", "one_x_two_brier", "one_x_two_rps", "total_goals_rps")}
    def avg(rows: list[dict[str, Any]], key: str) -> float:
        return mean(row[key] for row in rows)
    coverage = {key: {"current": avg(base_rows, key), "candidate": avg(model_rows, key)} for key in ("top1", "top3", "top5", "score80", "score90")}
    calibration_modes = {season: audit["mode"] for season, audit in season_calibration_audit.items()}
    checks = {
        "calibrator_artifact_integrity": calibrator_integrity["status"] == "通过",
        "minimum_outer_predictions": len(pairs) >= 200,
        "minimum_independent_forward_time_folds": len(folds) >= 8,
        "runtime_equivalent_missing_calibrator_behavior": all(audit["status"] in {"通过", "不可用"} for audit in season_calibration_audit.values()),
        "one_x_two_rps_ci_improves": cis["one_x_two_rps"]["ci95_upper"] < 0.0,
        "joint_log_ci_improves": cis["joint_log"]["ci95_upper"] < 0.0,
        "one_x_two_brier_ci_nonworse": cis["one_x_two_brier"]["ci95_upper"] <= 0.0,
        "total_goals_rps_ci_nonworse": cis["total_goals_rps"]["ci95_upper"] <= 0.0,
        "top1_nonworse": coverage["top1"]["candidate"] + 1e-12 >= coverage["top1"]["current"],
        "top3_nonworse": coverage["top3"]["candidate"] + 1e-12 >= coverage["top3"]["current"],
        "top5_nonworse": coverage["top5"]["candidate"] + 1e-12 >= coverage["top5"]["current"],
        "score80_calibrated": 0.76 <= coverage["score80"]["candidate"] <= 0.84,
        "score90_calibrated": 0.86 <= coverage["score90"]["candidate"] <= 0.94,
        "probability_conservation": max(row["probability_sum_error"] for row in model_rows) <= 1e-8,
    }
    status = "FINAL_CHAIN_LIVE_INPUT_REVIEW_REQUIRED" if all(checks.values()) else "FINAL_CHAIN_NOT_PROMOTED"
    report = {
        "schema_version": "V4.7.0-dynamic-strength-final-chain-replay-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": status,
        "formal_weight": 0,
        "automatic_promotion": False,
        "probability_change": False,
        "outer_predictions": len(pairs),
        "independent_forward_time_folds": len(folds),
        "calibrator_integrity": calibrator_integrity,
        "season_calibration_audit": season_calibration_audit,
        "calibration_modes": calibration_modes,
        "confidence_intervals": cis,
        "coverage": coverage,
        "checks": checks,
        "folds": folds,
        "live_input_gate": {
            "status": "REQUIRED_NOT_YET_PASSED" if status == "FINAL_CHAIN_LIVE_INPUT_REVIEW_REQUIRED" else "NOT_APPLICABLE",
            "requirements": [
                "question-time point-in-time roster continuity evidence",
                "question-time current manager evidence",
                "dated transfers strictly before freeze",
                "competition and target-season freshness audit",
                "runtime implementation with fail-closed hash-bound receipt",
            ],
        },
        "policy": "Final calibration-chain research replay only. A pass never activates formal weight. Live executable PIT inputs and a competition-specific hash-bound promotion remain mandatory under V4.7 CURRENT.",
    }
    write_json(OUT_ROOT / f"{competition_id}.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", default="ESP_LaLiga")
    parser.add_argument("--cache-dir", default="/tmp/football-dynamic-strength-final-chain-cache")
    args = parser.parse_args()
    try:
        report = validate(args.competition, Path(args.cache_dir))
    except Exception as exc:
        report = {
            "schema_version": "V4.7.0-dynamic-strength-final-chain-replay-r1",
            "generated_at_utc": utc_now(),
            "competition_id": args.competition,
            "status": "FAILED",
            "formal_weight": 0,
            "automatic_promotion": False,
            "probability_change": False,
            "reason": str(exc),
        }
        write_json(OUT_ROOT / f"{args.competition}.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"competition_id": args.competition, "status": report["status"], "outer_predictions": report["outer_predictions"], "folds": report["independent_forward_time_folds"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
