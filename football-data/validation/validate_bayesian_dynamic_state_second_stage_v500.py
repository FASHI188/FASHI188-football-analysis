#!/usr/bin/env python3
"""Second-stage fixed-profile validation for strong V5 dynamic-state shadows.

The profile is frozen from the first-stage adjudication before this script evaluates
chronological quarter-windows. The script is research/shadow only and cannot change
formal probabilities or weights. Handicap evidence remains a hard promotion blocker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from bayesian_dynamic_state_oof_v500 import (
    REPORT_ROOT,
    SEED,
    _bootstrap,
    _completed_outer_seasons,
    _paired_summary,
    _simulate_season,
)
from platform_core import PlatformError, atomic_write_json, load_json, read_processed_matches, sha256_file

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v500_status.json"
FIRST_STAGE_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500"
OUT = ROOT / "manifests" / "bayesian_dynamic_state_second_stage_v500_status.json"
REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_second_stage_v500"
WINDOWS_PER_SEASON = 4
MIN_WINDOW_ROWS = 50
MAX_WINDOWS_WORSE_THAN_MINUS_5PP = 2
WORST_WINDOW_FLOOR = -0.10


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _paired_rows(simulation: dict[str, Any], profile_id: str) -> list[dict[str, Any]]:
    base_map = {row["match_key"]: row for row in simulation["baseline"]}
    candidate_map = {row["match_key"]: row for row in simulation["profiles"][profile_id]}
    rows = []
    metrics = (
        "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
        "score_top1", "score_top3", "total_top1", "total_top2", "total_rps",
    )
    for key in sorted(set(base_map) & set(candidate_map)):
        base = base_map[key]
        candidate = candidate_map[key]
        item = {
            "match_key": key,
            "season": base["season"],
            "date": base["date"],
            "selected_profile": profile_id,
        }
        for metric in metrics:
            item[f"baseline_{metric}"] = base[metric]
            item[f"candidate_{metric}"] = candidate[metric]
        rows.append(item)
    return sorted(rows, key=lambda row: (row["date"], row["match_key"]))


def _windows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not rows:
        return []
    return [
        rows[index * len(rows) // WINDOWS_PER_SEASON:(index + 1) * len(rows) // WINDOWS_PER_SEASON]
        for index in range(WINDOWS_PER_SEASON)
    ]


def validate_domain(competition_id: str, adjudication: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(adjudication.get("frozen_shadow_profile") or "")
    if not profile_id:
        raise PlatformError(f"frozen profile missing for {competition_id}")
    first_stage = load_json(FIRST_STAGE_DIR / f"{competition_id}.json")
    formal_report = load_json(REPORT_ROOT / f"{competition_id}.json")
    all_matches = read_processed_matches(competition_id)

    target_seasons = [
        str(fold["target_season"])
        for fold in (first_stage.get("folds") or [])
        if fold.get("status") == "EVALUATED_FORWARD_FROZEN_PROFILE"
    ]
    if len(target_seasons) < 2:
        raise PlatformError(f"need two evaluated outer seasons for {competition_id}")

    completed = set(_completed_outer_seasons(competition_id, formal_report))
    if any(season not in completed for season in target_seasons):
        raise PlatformError(f"target season outside completed outer folds for {competition_id}")

    all_rows: list[dict[str, Any]] = []
    season_reports = []
    window_reports = []
    max_probability_residual = 0.0
    max_total_residual = 0.0

    for season in target_seasons:
        simulation = _simulate_season(competition_id, season, all_matches, formal_report)
        rows = _paired_rows(simulation, profile_id)
        if not rows:
            raise PlatformError(f"no paired rows for {competition_id} {season}")
        all_rows.extend(rows)
        season_summary = _paired_summary(rows)
        season_reports.append({
            "season": season,
            "prediction_count": len(rows),
            "metrics": season_summary,
        })
        max_probability_residual = max(max_probability_residual, float(simulation["max_probability_sum_residual"]))
        max_total_residual = max(max_total_residual, float(simulation["max_total_marginal_residual"]))

        for index, window in enumerate(_windows(rows), start=1):
            if not window:
                continue
            summary = _paired_summary(window)
            window_reports.append({
                "window_id": f"{season}:Q{index}",
                "season": season,
                "window_index": index,
                "prediction_count": len(window),
                "start_date": window[0]["date"],
                "end_date": window[-1]["date"],
                "metrics": summary,
            })

    pooled = _paired_summary(all_rows)
    ci = {
        "one_x_two_brier": _bootstrap(all_rows, "candidate_one_x_two_brier", "baseline_one_x_two_brier", SEED + 101),
        "one_x_two_rps": _bootstrap(all_rows, "candidate_one_x_two_rps", "baseline_one_x_two_rps", SEED + 102),
        "joint_log": _bootstrap(all_rows, "candidate_joint_log", "baseline_joint_log", SEED + 103),
        "total_rps": _bootstrap(all_rows, "candidate_total_rps", "baseline_total_rps", SEED + 104),
    }

    season_accuracy_diffs = [
        float(item["metrics"]["one_x_two_accuracy"]["candidate_minus_baseline"])
        for item in season_reports
    ]
    window_accuracy_diffs = [
        float(item["metrics"]["one_x_two_accuracy"]["candidate_minus_baseline"])
        for item in window_reports
    ]
    bad_windows = sum(diff < -0.05 for diff in window_accuracy_diffs)
    minimum_window_rows = min(item["prediction_count"] for item in window_reports) if window_reports else 0

    checks = {
        "profile_frozen_before_second_stage": True,
        "profile_matches_first_stage_stable_selection": adjudication.get("profile_stable") is True,
        "two_outer_seasons": len(season_reports) >= 2,
        "minimum_pooled_predictions_500": len(all_rows) >= 500,
        "eight_chronological_windows": len(window_reports) >= 8,
        "minimum_window_rows_50": minimum_window_rows >= MIN_WINDOW_ROWS,
        "all_seasons_one_x_two_accuracy_nonworse": min(season_accuracy_diffs) >= -1e-12,
        "bad_windows_below_minus_5pp_at_most_2": bad_windows <= MAX_WINDOWS_WORSE_THAN_MINUS_5PP,
        "worst_window_one_x_two_accuracy_above_minus_10pp": min(window_accuracy_diffs) >= WORST_WINDOW_FLOOR,
        "one_x_two_brier_ci_improves": ci["one_x_two_brier"]["ci95_upper"] < 0.0,
        "one_x_two_rps_ci_improves": ci["one_x_two_rps"]["ci95_upper"] < 0.0,
        "joint_log_ci_noninferior": ci["joint_log"]["ci95_upper"] <= 0.002,
        "total_rps_ci_noninferior": ci["total_rps"]["ci95_upper"] <= 0.0005,
        "score_top1_nonworse": pooled["score_top1"]["candidate"] + 1e-12 >= pooled["score_top1"]["baseline"],
        "score_top3_nonworse": pooled["score_top3"]["candidate"] + 1e-12 >= pooled["score_top3"]["baseline"],
        "total_top1_nonworse": pooled["total_top1"]["candidate"] + 1e-12 >= pooled["total_top1"]["baseline"],
        "total_top2_nonworse": pooled["total_top2"]["candidate"] + 1e-12 >= pooled["total_top2"]["baseline"],
        "probability_conservation": max_probability_residual <= 1e-10,
        "total_projection_conservation": max_total_residual <= 1e-10,
        "handicap_fourth_target_available": False,
    }
    probability_checks = {key: value for key, value in checks.items() if key != "handicap_fourth_target_available"}
    probability_pass = all(probability_checks.values())
    status = "SECOND_STAGE_SHADOW_PASS_AH_BLOCKED" if probability_pass else "SECOND_STAGE_REJECT_KEEP_FORMAL_WEIGHT_0"

    return {
        "schema_version": "V5.0.0-bayesian-dynamic-state-second-stage-domain-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "target_season": adjudication.get("target_season"),
        "status": status,
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "frozen_profile": profile_id,
        "first_stage_report": str((FIRST_STAGE_DIR / f"{competition_id}.json").relative_to(ROOT)),
        "first_stage_report_sha256": sha256_file(FIRST_STAGE_DIR / f"{competition_id}.json"),
        "outer_prediction_count": len(all_rows),
        "season_reports": season_reports,
        "window_reports": window_reports,
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "minimum_season_one_x_two_accuracy_difference": min(season_accuracy_diffs),
        "minimum_window_one_x_two_accuracy_difference": min(window_accuracy_diffs),
        "windows_worse_than_minus_5pp": bad_windows,
        "minimum_window_prediction_count": minimum_window_rows,
        "max_probability_sum_residual": max_probability_residual,
        "max_total_marginal_residual": max_total_residual,
        "checks": checks,
        "handicap_target_status": "UNAVAILABLE_NO_COMPLETE_POINT_IN_TIME_FROZEN_HANDICAP_LINES",
        "policy": "Second-stage shadow validation only. Formal promotion remains prohibited without fourth-target handicap evidence and a future hash-bound promotion receipt.",
    }


def main() -> int:
    adjudication = load_json(ADJUDICATION)
    strong = adjudication.get("strong_shadow_candidates_ah_blocked") or []
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    passed: list[str] = []
    for competition_id in strong:
        try:
            item = validate_domain(competition_id, adjudication["adjudications"][competition_id])
            reports[competition_id] = item
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(REPORT_DIR / f"{competition_id}.json", item)
            if item["status"] == "SECOND_STAGE_SHADOW_PASS_AH_BLOCKED":
                passed.append(competition_id)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "V5.0.0-bayesian-dynamic-state-second-stage-aggregate-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if len(reports) == len(strong) and not failures else "PARTIAL",
        "requested_domains": strong,
        "completed_domains": sorted(reports),
        "second_stage_shadow_pass_ah_blocked": passed,
        "second_stage_rejected": sorted(set(reports) - set(passed)),
        "failures": failures,
        "reports": {
            cid: {
                "status": item["status"],
                "frozen_profile": item["frozen_profile"],
                "outer_prediction_count": item["outer_prediction_count"],
                "pooled_metrics": item["pooled_metrics"],
                "minimum_season_one_x_two_accuracy_difference": item["minimum_season_one_x_two_accuracy_difference"],
                "minimum_window_one_x_two_accuracy_difference": item["minimum_window_one_x_two_accuracy_difference"],
                "windows_worse_than_minus_5pp": item["windows_worse_than_minus_5pp"],
                "checks": item["checks"],
            }
            for cid, item in reports.items()
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "No second-stage result can change formal weights. Handicap evidence and a CURRENT-compliant promotion receipt remain mandatory.",
    }
    atomic_write_json(OUT, payload)
    print({"status": payload["status"], "passed": passed, "failures": failures})
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
