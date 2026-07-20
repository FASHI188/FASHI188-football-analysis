#!/usr/bin/env python3
"""Strict rolling OOF validation for draw-residual research signals.

Only the three domains that survived the first completed-season research screen are
re-evaluated here: FRA_Ligue1, NED_Eredivisie and SWE_Allsvenskan.

For each target outer season, the draw residual logistic model is trained only on
eligible PIT predictions from strictly earlier seasons. The candidate then tilts
only diagonal score cells within each fixed total T after the target season's
replay-safe OOF matrix calibration. Every T-specific vector is renormalized, so
P(T) remains unchanged. This remains an unregistered research challenger with
formal weight 0; no promotion or production mutation is created.
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import screen_draw_residual_challenger_v470_strict as strict_entry
from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from football_v460_engine import current_season_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows, top_scores

base = strict_entry.base
TARGETS = ("FRA_Ligue1", "NED_Eredivisie", "SWE_Allsvenskan")
OUT = ROOT / "manifests" / "draw_residual_rolling_oof_v470_status.json"
RANDOM_SEED = 4702026
BLOCK_SIZE = 20
BOOTSTRAP_DRAWS = 4000


def _season_year(season: str) -> int:
    match = re.match(r"^(20\d{2})", str(season).strip())
    if not match:
        raise PlatformError(f"cannot resolve season year: {season!r}")
    return int(match.group(1))


def _actual_result(match) -> str:
    if match.home_goals > match.away_goals:
        return "home"
    if match.home_goals < match.away_goals:
        return "away"
    return "draw"


def _metric_row(baseline, candidate, match) -> dict[str, Any]:
    base_m = derive_score_marginals(baseline)
    cand_m = derive_score_marginals(candidate)
    actual = _actual_result(match)
    actual_draw = 1 if actual == "draw" else 0
    actual_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
    base_rank = top_scores(baseline, 3)
    cand_rank = top_scores(candidate, 3)
    base_log = base._joint_log(baseline, int(match.home_goals), int(match.away_goals))
    cand_log = base._joint_log(candidate, int(match.home_goals), int(match.away_goals))
    structural_actual = {
        "btts": 1 if match.home_goals > 0 and match.away_goals > 0 else 0,
        "home_zero": 1 if match.home_goals == 0 else 0,
        "away_zero": 1 if match.away_goals == 0 else 0,
        "margin2plus": 1 if abs(match.home_goals - match.away_goals) >= 2 else 0,
    }
    result = {
        "base_one_x_two_accuracy": 1 if max(base_m["1x2"], key=base_m["1x2"].get) == actual else 0,
        "cand_one_x_two_accuracy": 1 if max(cand_m["1x2"], key=cand_m["1x2"].get) == actual else 0,
        "base_one_x_two_brier": base._brier(base_m["1x2"], actual),
        "cand_one_x_two_brier": base._brier(cand_m["1x2"], actual),
        "base_one_x_two_rps": base._rps(base_m["1x2"], actual),
        "cand_one_x_two_rps": base._rps(cand_m["1x2"], actual),
        "base_draw_brier": (float(base_m["1x2"]["draw"]) - actual_draw) ** 2,
        "cand_draw_brier": (float(cand_m["1x2"]["draw"]) - actual_draw) ** 2,
        "base_draw_probability": float(base_m["1x2"]["draw"]),
        "cand_draw_probability": float(cand_m["1x2"]["draw"]),
        "actual_draw": actual_draw,
        "base_joint_log": base_log,
        "cand_joint_log": cand_log,
        "base_score_top1": 1 if base_rank and base_rank[0]["score"] == actual_score else 0,
        "cand_score_top1": 1 if cand_rank and cand_rank[0]["score"] == actual_score else 0,
        "base_score_top3": 1 if any(item["score"] == actual_score for item in base_rank) else 0,
        "cand_score_top3": 1 if any(item["score"] == actual_score for item in cand_rank) else 0,
    }
    for key, predicate in (
        ("btts", lambda h, a: h > 0 and a > 0),
        ("home_zero", lambda h, a: h == 0),
        ("away_zero", lambda h, a: a == 0),
        ("margin2plus", lambda h, a: abs(h - a) >= 2),
    ):
        actual_value = structural_actual[key]
        base_p = base._binary_probability(baseline, predicate)
        cand_p = base._binary_probability(candidate, predicate)
        result[f"base_{key}_brier"] = (base_p - actual_value) ** 2
        result[f"cand_{key}_brier"] = (cand_p - actual_value) ** 2
    return result


def _auc(scores: list[float], labels: list[int]) -> float | None:
    return base._auc(scores, labels)


def _blocks(rows: list[dict[str, Any]], size: int = BLOCK_SIZE) -> list[list[dict[str, Any]]]:
    return [rows[index:index + size] for index in range(0, len(rows), size)]


def _bootstrap_ci(rows: list[dict[str, Any]], diff_fn, seed: int) -> dict[str, Any]:
    blocks = _blocks(rows)
    if not blocks:
        return {"mean_difference": None, "ci95_lower": None, "ci95_upper": None, "blocks": 0, "draws": 0}
    point = mean(diff_fn(row) for row in rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled_rows = []
        for _ in range(len(blocks)):
            sampled_rows.extend(rng.choice(blocks))
        samples.append(mean(diff_fn(row) for row in sampled_rows))
    samples.sort()
    lo = samples[int(0.025 * (len(samples) - 1))]
    hi = samples[int(0.975 * (len(samples) - 1))]
    return {
        "mean_difference": point,
        "ci95_lower": lo,
        "ci95_upper": hi,
        "blocks": len(blocks),
        "block_size": BLOCK_SIZE,
        "draws": BOOTSTRAP_DRAWS,
    }


def _aggregate(rows: list[dict[str, Any]], seed_offset: int) -> dict[str, Any]:
    labels = [int(row["actual_draw"]) for row in rows]
    base_draw = [float(row["base_draw_probability"]) for row in rows]
    cand_draw = [float(row["cand_draw_probability"]) for row in rows]
    finite_log = [row for row in rows if row["base_joint_log"] is not None and row["cand_joint_log"] is not None]
    metrics = {
        "count": len(rows),
        "one_x_two_accuracy": {
            "baseline": mean(row["base_one_x_two_accuracy"] for row in rows),
            "candidate": mean(row["cand_one_x_two_accuracy"] for row in rows),
        },
        "one_x_two_brier": {
            "baseline": mean(row["base_one_x_two_brier"] for row in rows),
            "candidate": mean(row["cand_one_x_two_brier"] for row in rows),
        },
        "one_x_two_rps": {
            "baseline": mean(row["base_one_x_two_rps"] for row in rows),
            "candidate": mean(row["cand_one_x_two_rps"] for row in rows),
        },
        "draw_brier": {
            "baseline": mean(row["base_draw_brier"] for row in rows),
            "candidate": mean(row["cand_draw_brier"] for row in rows),
        },
        "draw_auc": {"baseline": _auc(base_draw, labels), "candidate": _auc(cand_draw, labels)},
        "joint_log": {
            "baseline": mean(row["base_joint_log"] for row in finite_log),
            "candidate": mean(row["cand_joint_log"] for row in finite_log),
            "count": len(finite_log),
        },
        "score_top1_accuracy": {
            "baseline": mean(row["base_score_top1"] for row in rows),
            "candidate": mean(row["cand_score_top1"] for row in rows),
        },
        "score_top3_accuracy": {
            "baseline": mean(row["base_score_top3"] for row in rows),
            "candidate": mean(row["cand_score_top3"] for row in rows),
        },
    }
    for key in ("one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "draw_brier", "draw_auc", "joint_log", "score_top1_accuracy", "score_top3_accuracy"):
        metrics[key]["candidate_minus_baseline"] = metrics[key]["candidate"] - metrics[key]["baseline"]
    structural = {}
    for key in ("btts", "home_zero", "away_zero", "margin2plus"):
        baseline = mean(row[f"base_{key}_brier"] for row in rows)
        candidate = mean(row[f"cand_{key}_brier"] for row in rows)
        structural[key] = {"baseline_brier": baseline, "candidate_brier": candidate, "candidate_minus_baseline": candidate - baseline}

    ci = {
        "draw_brier": _bootstrap_ci(rows, lambda row: row["cand_draw_brier"] - row["base_draw_brier"], RANDOM_SEED + seed_offset + 1),
        "one_x_two_brier": _bootstrap_ci(rows, lambda row: row["cand_one_x_two_brier"] - row["base_one_x_two_brier"], RANDOM_SEED + seed_offset + 2),
        "one_x_two_rps": _bootstrap_ci(rows, lambda row: row["cand_one_x_two_rps"] - row["base_one_x_two_rps"], RANDOM_SEED + seed_offset + 3),
        "joint_log": _bootstrap_ci(finite_log, lambda row: row["cand_joint_log"] - row["base_joint_log"], RANDOM_SEED + seed_offset + 4),
    }
    return {"metrics": metrics, "structural_brier": structural, "paired_block_bootstrap": ci}


def _target_outer_seasons(report: dict[str, Any]) -> list[str]:
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and season not in seasons:
            seasons.append(season)
    seasons.sort(key=_season_year)
    # Need at least one completed earlier outer season to train the residual model.
    return seasons[1:]


def validate_domain(cid: str, seed_offset: int) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    all_matches = read_processed_matches(cid)
    outer_reports = []
    pooled_rows = []
    max_total_residual = 0.0
    for target_season in _target_outer_seasons(report):
        target_year = _season_year(target_season)
        target_fold = _fold_for_season(report, target_season)
        target_params = target_fold.get("selected_parameters")
        if not isinstance(target_params, dict):
            continue
        train_x, train_y, training_seasons = strict_entry._strict_season_training_rows(cid, report, all_matches, target_season)
        if not train_x:
            continue
        if any(_season_year(season) >= target_year for season in training_seasons):
            raise PlatformError(f"future season leak in {cid} {target_season}")
        model = base._fit_logistic(train_x, train_y)
        temperature, calibration_mode = _target_season_temperature(cid, target_season)
        target_matches = sorted(
            [m for m in all_matches if str(m.season) == target_season],
            key=lambda m: (m.date, m.home_team, m.away_team),
        )
        season_rows = []
        for match in target_matches:
            try:
                _, history = current_season_history(all_matches, match.date, target_season)
                baseline = _predict_from_loaded_matches(
                    all_matches,
                    match.home_team,
                    match.away_team,
                    match.date,
                    target_season,
                    target_params,
                )
            except PlatformError:
                continue
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
            one = derive_score_marginals(baseline)["1x2"]
            features = base._venue_draw_features(
                history,
                match.home_team,
                match.away_team,
                one,
                baseline,
                float(target_params.get("team_prior_matches", 8.0)),
            )
            target_draw = base._predict_logistic(model, features)
            candidate, _, total_residual = strict_entry._safe_tilt_diagonal_to_target(baseline, target_draw)
            max_total_residual = max(max_total_residual, total_residual)
            row = _metric_row(baseline, candidate, match)
            row["target_season"] = target_season
            season_rows.append(row)
            pooled_rows.append(row)
        if not season_rows:
            continue
        season_summary = _aggregate(season_rows, seed_offset + len(outer_reports) * 100)
        outer_reports.append({
            "target_season": target_season,
            "training_seasons": training_seasons,
            "training_rows": len(train_x),
            "training_draw_rate": mean(train_y),
            "logistic_converged": model.get("converged"),
            "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
            **season_summary,
        })
    if not pooled_rows:
        raise PlatformError(f"no rolling OOF rows for {cid}")
    pooled = _aggregate(pooled_rows, seed_offset + 900)
    ci = pooled["paired_block_bootstrap"]
    seasons_with_draw_brier_improvement = sum(1 for item in outer_reports if item["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0)
    seasons_with_joint_log_nonworse = sum(1 for item in outer_reports if item["metrics"]["joint_log"]["candidate_minus_baseline"] <= 0.005)
    checks = {
        "multiple_outer_seasons": len(outer_reports) >= 2,
        "strict_prior_training_each_fold": all(all(_season_year(season) < _season_year(item["target_season"]) for season in item["training_seasons"]) for item in outer_reports),
        "draw_brier_mean_improves": pooled["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0,
        "draw_brier_ci_upper_below_zero": ci["draw_brier"]["ci95_upper"] < 0,
        "one_x_two_brier_ci_upper_noninferior": ci["one_x_two_brier"]["ci95_upper"] <= 0.001,
        "one_x_two_rps_ci_upper_noninferior": ci["one_x_two_rps"]["ci95_upper"] <= 0.001,
        "joint_log_ci_upper_noninferior": ci["joint_log"]["ci95_upper"] <= 0.005,
        "majority_seasons_draw_brier_improve": seasons_with_draw_brier_improvement >= math.ceil(len(outer_reports) / 2),
        "all_seasons_joint_log_noncatastrophic": seasons_with_joint_log_nonworse == len(outer_reports),
        "total_marginal_preserved": max_total_residual <= 1e-10,
    }
    status = "ROLLING_OOF_RESEARCH_CANDIDATE" if all(checks.values()) else "KEEP_FORMAL_WEIGHT_0"
    return {
        "competition_id": cid,
        "status": status,
        "outer_season_count": len(outer_reports),
        "pooled_prediction_count": len(pooled_rows),
        "max_total_marginal_residual": max_total_residual,
        "pooled": pooled,
        "outer_seasons": outer_reports,
        "checks": checks,
        "formal_weight": 0,
        "automatic_promotion": False,
        "probability_change": False,
        "governance_reason": "Unregistered research challenger. Rolling OOF evidence cannot create formal execution rights under CURRENT V4.7.0.",
    }


def main() -> int:
    reports = {}
    failures = {}
    for index, cid in enumerate(TARGETS):
        try:
            reports[cid] = validate_domain(cid, index * 10000)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    candidates = [cid for cid, report in reports.items() if report["status"] == "ROLLING_OOF_RESEARCH_CANDIDATE"]
    payload = {
        "schema_version": "V4.7.0-draw-residual-rolling-oof-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(TARGETS) and not failures else "PARTIAL",
        "competition_count_requested": len(TARGETS),
        "competition_count_completed": len(reports),
        "rolling_oof_research_candidates": candidates,
        "reports": reports,
        "failures": failures,
        "governance": {
            "registered_in_current": False,
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
            "formal_use_requires_complete_current_upgrade": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "rolling_oof_research_candidates": candidates, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
