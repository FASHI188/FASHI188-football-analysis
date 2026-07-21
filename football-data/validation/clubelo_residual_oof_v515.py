#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from bayesian_dynamic_state_oof_v500 import _metric_row, _paired_summary
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows

CONFIG = ROOT / "config" / "clubelo_residual_challenger_v515.json"
EVIDENCE_ROOT = ROOT / "evidence" / "clubelo_v515"
HISTORY_PATH = EVIDENCE_ROOT / "club_histories.jsonl"
INGEST_STATUS = ROOT / "manifests" / "clubelo_history_ingest_v515_status.json"
OUT_ROOT = ROOT / "manifests" / "clubelo_residual_oof_v515"
AGGREGATE = ROOT / "manifests" / "clubelo_residual_oof_v515_status.json"
EPS = 1e-15
BOOTSTRAP_DRAWS = 1600
BLOCK_SIZE = 20
SEED = 5152026


def _season_year(season: str) -> int:
    return int(str(season)[:4])


def _completed_seasons(cid: str, report: dict[str, Any]) -> list[str]:
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and season not in seasons:
            seasons.append(season)
    seasons.sort(key=_season_year)
    return seasons


def _load_histories() -> dict[str, list[dict[str, Any]]]:
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with HISTORY_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            histories[str(row["clubelo_name"])].append(row)
    for name in histories:
        histories[name].sort(key=lambda row: row["from"])
    return dict(histories)


def _load_team_map(cid: str) -> dict[str, str]:
    payload = load_json(EVIDENCE_ROOT / f"{cid}_team_map.json")
    out = {}
    for team, item in (payload.get("mappings") or {}).items():
        if item.get("status") == "PASS" and item.get("clubelo_name"):
            out[str(team)] = str(item["clubelo_name"])
    return out


def _rating(histories: dict[str, list[dict[str, Any]]], name: str, date: datetime) -> float:
    token = date.date().isoformat()
    rows = histories.get(name) or []
    matches = [row for row in rows if str(row["from"]) <= token <= str(row["to"])]
    if len(matches) != 1:
        raise PlatformError(f"ClubElo interval lookup {name} {token} found {len(matches)} rows")
    return float(matches[0]["elo"])


def _total_marginals(matrix: list[dict[str, Any]]) -> dict[int, float]:
    result = defaultdict(float)
    for h, a, p in score_matrix_rows(matrix):
        result[h + a] += p
    return dict(result)


def _project(matrix: list[dict[str, Any]], elo_diff: float, beta: float, scale: float) -> tuple[list[dict[str, Any]], dict[str, float]]:
    grouped: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
    for h, a, p in score_matrix_rows(matrix):
        grouped[h + a].append((h, a, p))
    original_totals = _total_marginals(matrix)
    result = []
    signal = float(elo_diff) / float(scale)
    for total, cells in sorted(grouped.items()):
        mass = sum(p for _, _, p in cells)
        weighted = []
        for h, a, p in cells:
            exponent = float(beta) * signal * float(h - a)
            exponent = min(40.0, max(-40.0, exponent))
            weighted.append((h, a, p * math.exp(exponent)))
        denom = sum(w for _, _, w in weighted)
        if denom <= 0 or not math.isfinite(denom):
            raise PlatformError(f"ClubElo conditional KL normalization failed total={total}")
        for h, a, weight in weighted:
            result.append({"home_goals": h, "away_goals": a, "probability": mass * weight / denom})
    prob_sum = sum(float(cell["probability"]) for cell in result)
    if prob_sum <= 0 or not math.isfinite(prob_sum):
        raise PlatformError("ClubElo projected probability sum invalid")
    result = [{**cell, "probability": float(cell["probability"]) / prob_sum} for cell in result]
    new_totals = _total_marginals(result)
    max_total = max(abs(float(new_totals.get(t, 0.0)) - float(p)) for t, p in original_totals.items())
    return result, {
        "probability_sum_residual": abs(sum(float(cell["probability"]) for cell in result) - 1.0),
        "max_total_marginal_residual": max_total,
        "elo_difference": float(elo_diff),
        "elo_scaled_difference": signal,
        "beta": float(beta),
    }


def _simulate_season(cid: str, season: str, all_matches, report: dict[str, Any], histories, team_map, cfg) -> dict[str, Any]:
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"missing frozen parameters {cid} {season}")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    target = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    beta_rows = {str(beta): [] for beta in cfg["beta_profiles"]}
    baseline_rows = []
    skipped_baseline = skipped_identity = skipped_elo = 0
    max_prob = max_total = 0.0
    for match in target:
        try:
            baseline = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
        except PlatformError:
            skipped_baseline += 1
            continue
        home_name = team_map.get(match.home_team)
        away_name = team_map.get(match.away_team)
        if not home_name or not away_name:
            skipped_identity += 1
            continue
        rating_date = match.date - timedelta(days=1)
        try:
            home_elo = _rating(histories, home_name, rating_date)
            away_elo = _rating(histories, away_name, rating_date)
        except PlatformError:
            skipped_elo += 1
            continue
        key = f"{cid}:{season}:{match.date.date().isoformat()}:{match.home_team}:{match.away_team}"
        base_metrics = _metric_row(baseline, match)
        baseline_rows.append({"match_key": key, "season": season, "date": match.date.date().isoformat(), **base_metrics})
        elo_diff = home_elo - away_elo
        for beta in cfg["beta_profiles"]:
            candidate, audit = _project(baseline, elo_diff, float(beta), float(cfg["rating_scale"]))
            metrics = _metric_row(candidate, match)
            max_prob = max(max_prob, float(audit["probability_sum_residual"]), float(metrics["probability_sum_residual"]))
            max_total = max(max_total, float(audit["max_total_marginal_residual"]))
            beta_rows[str(beta)].append({
                "match_key": key, "season": season, "date": match.date.date().isoformat(),
                "beta": float(beta), **metrics, "audit": audit
            })
    return {
        "season": season,
        "baseline": baseline_rows,
        "beta_rows": beta_rows,
        "target_match_count": len(target),
        "eligible_count": len(baseline_rows),
        "skipped_baseline": skipped_baseline,
        "skipped_identity": skipped_identity,
        "skipped_elo": skipped_elo,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "max_probability_sum_residual": max_prob,
        "max_total_marginal_residual": max_total,
    }


def _selection_objective(rows: list[dict[str, Any]]) -> float:
    return (
        mean(float(row["one_x_two_rps"]) for row in rows)
        + 0.25 * mean(float(row["one_x_two_brier"]) for row in rows)
        + 0.02 * mean(float(row["joint_log"]) for row in rows)
    )


def _blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (row["season"], row["date"], row["match_key"]))
    return [ordered[i:i + BLOCK_SIZE] for i in range(0, len(ordered), BLOCK_SIZE)]


def _bootstrap(rows: list[dict[str, Any]], candidate_key: str, baseline_key: str, seed: int) -> dict[str, Any]:
    blocks = _blocks(rows)
    point = mean(float(row[candidate_key]) - float(row[baseline_key]) for row in rows)
    rng = random.Random(seed)
    values = []
    for _ in range(BOOTSTRAP_DRAWS):
        sample = []
        for _ in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        values.append(mean(float(row[candidate_key]) - float(row[baseline_key]) for row in sample))
    values.sort()
    return {
        "mean_difference": point,
        "ci95_lower": values[int(0.025 * (len(values) - 1))],
        "ci95_upper": values[int(0.975 * (len(values) - 1))],
        "blocks": len(blocks),
        "draws": BOOTSTRAP_DRAWS,
    }


def validate_domain(cid: str) -> dict[str, Any]:
    cfg = load_json(CONFIG)
    ingest = load_json(INGEST_STATUS)
    domain_ingest = (ingest.get("domain_reports") or {}).get(cid)
    if not domain_ingest or float(domain_ingest.get("coverage") or 0.0) < 0.95:
        raise PlatformError(f"ClubElo ingest coverage below gate for {cid}")
    report = load_json(REPORT_ROOT / f"{cid}.json")
    seasons = _completed_seasons(cid, report)
    if len(seasons) < 3:
        raise PlatformError(f"need at least three outer seasons for {cid}")
    all_matches = read_processed_matches(cid)
    histories = _load_histories()
    team_map = _load_team_map(cid)
    simulations = {s: _simulate_season(cid, s, all_matches, report, histories, team_map, cfg) for s in seasons}
    folds = []
    pooled_rows = []
    for idx in range(2, len(seasons)):
        target = seasons[idx]
        prior = seasons[:idx]
        scored = []
        for beta in cfg["beta_profiles"]:
            rows = [row for season in prior for row in simulations[season]["beta_rows"][str(beta)]]
            if len(rows) < int(cfg["selection"]["minimum_prior_prediction_rows"]):
                continue
            scored.append({"beta": float(beta), "objective": _selection_objective(rows), "selection_rows": len(rows)})
        if not scored:
            folds.append({"target_season": target, "status": "NO_PRIOR_BETA_SELECTION", "prior_seasons": prior})
            continue
        scored.sort(key=lambda item: (item["objective"], abs(item["beta"]), item["beta"]))
        selected = scored[0]
        beta = float(selected["beta"])
        base_map = {row["match_key"]: row for row in simulations[target]["baseline"]}
        cand_map = {row["match_key"]: row for row in simulations[target]["beta_rows"][str(beta)]}
        keys = sorted(set(base_map) & set(cand_map))
        season_rows = []
        for key in keys:
            base = base_map[key]
            cand = cand_map[key]
            row = {"match_key": key, "season": target, "date": base["date"], "selected_beta": beta}
            for metric in ("one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"):
                row[f"baseline_{metric}"] = base[metric]
                row[f"candidate_{metric}"] = cand[metric]
            season_rows.append(row)
            pooled_rows.append(row)
        folds.append({
            "target_season": target,
            "status": "EVALUATED_FORWARD_FROZEN_BETA",
            "prior_seasons": prior,
            "selected_beta": beta,
            "selection_objective": selected["objective"],
            "selection_rows": selected["selection_rows"],
            "outer_predictions": len(season_rows),
            "metrics": _paired_summary(season_rows) if season_rows else None,
        })
    if not pooled_rows:
        raise PlatformError(f"no ClubElo forward rows for {cid}")
    pooled = _paired_summary(pooled_rows)
    ci = {
        "one_x_two_brier": _bootstrap(pooled_rows, "candidate_one_x_two_brier", "baseline_one_x_two_brier", SEED + 1),
        "one_x_two_rps": _bootstrap(pooled_rows, "candidate_one_x_two_rps", "baseline_one_x_two_rps", SEED + 2),
        "joint_log": _bootstrap(pooled_rows, "candidate_joint_log", "baseline_joint_log", SEED + 3),
    }
    evaluated = [fold for fold in folds if fold.get("status") == "EVALUATED_FORWARD_FROZEN_BETA"]
    last_two = evaluated[-2:]
    max_prob = max(float(simulations[s]["max_probability_sum_residual"]) for s in seasons)
    max_total = max(float(simulations[s]["max_total_marginal_residual"]) for s in seasons)
    proper_ci_improves = ci["one_x_two_brier"]["ci95_upper"] < 0.0 or ci["one_x_two_rps"]["ci95_upper"] < 0.0
    other_ci_noninferior = (
        (ci["one_x_two_brier"]["ci95_upper"] < 0.0 and ci["one_x_two_rps"]["ci95_upper"] <= 0.001)
        or (ci["one_x_two_rps"]["ci95_upper"] < 0.0 and ci["one_x_two_brier"]["ci95_upper"] <= 0.001)
        or (ci["one_x_two_brier"]["ci95_upper"] < 0.0 and ci["one_x_two_rps"]["ci95_upper"] < 0.0)
    )
    checks = {
        "minimum_evaluated_forward_folds": len(evaluated) >= int(cfg["forward_gate"]["minimum_evaluated_forward_folds"]),
        "minimum_pooled_predictions": len(pooled_rows) >= int(cfg["forward_gate"]["minimum_pooled_predictions"]),
        "nonzero_beta_selected_in_each_last_two_forward_folds": len(last_two) == 2 and all(abs(float(fold["selected_beta"])) > 1e-12 for fold in last_two),
        "at_least_one_one_x_two_proper_score_ci_improves": proper_ci_improves,
        "other_one_x_two_proper_score_ci_noninferior": other_ci_noninferior,
        "one_x_two_accuracy_nonworse": pooled["one_x_two_accuracy"]["candidate"] + 1e-12 >= pooled["one_x_two_accuracy"]["baseline"],
        "one_x_two_brier_nonworse": pooled["one_x_two_brier"]["candidate"] <= pooled["one_x_two_brier"]["baseline"] + 1e-12,
        "one_x_two_rps_nonworse": pooled["one_x_two_rps"]["candidate"] <= pooled["one_x_two_rps"]["baseline"] + 1e-12,
        "joint_log_nonworse": pooled["joint_log"]["candidate"] <= pooled["joint_log"]["baseline"] + 1e-12,
        "score_top1_nonworse": pooled["score_top1"]["candidate"] + 1e-12 >= pooled["score_top1"]["baseline"],
        "score_top3_nonworse": pooled["score_top3"]["candidate"] + 1e-12 >= pooled["score_top3"]["baseline"],
        "total_top1_exactly_preserved": abs(pooled["total_top1"]["candidate_minus_baseline"]) <= 1e-12,
        "total_top2_exactly_preserved": abs(pooled["total_top2"]["candidate_minus_baseline"]) <= 1e-12,
        "total_rps_exactly_preserved": abs(pooled["total_rps"]["candidate_minus_baseline"]) <= 1e-12,
        "probability_conservation": max_prob <= float(cfg["forward_gate"]["probability_sum_tolerance"]),
        "total_marginal_conservation": max_total <= float(cfg["forward_gate"]["total_marginal_tolerance"]),
    }
    passed = all(checks.values())
    return {
        "schema_version": "V5.1.5-clubelo-residual-oof-domain-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": cid,
        "status": "CLUBELO_RESIDUAL_SIGNAL_PASS_SHADOW_ONLY" if passed else "REJECT_KEEP_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "seasons": seasons,
        "beta_profiles": cfg["beta_profiles"],
        "forward_prediction_count": len(pooled_rows),
        "folds": folds,
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "max_probability_sum_residual": max_prob,
        "max_total_marginal_residual": max_total,
        "checks": checks,
        "policy": "ClubElo rating is looked up on target calendar date minus one day. Beta is selected only from strictly earlier completed seasons. Projection is minimum-KL within each fixed total-goal slice, so total-goal marginals are structurally preserved."
    }
