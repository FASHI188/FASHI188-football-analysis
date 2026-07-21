#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from statistics import pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _actual_result,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, read_processed_matches

CONFIG = ROOT / "config" / "predictability_gate_v516.json"


def _season_year(season: str) -> int:
    return int(str(season)[:4])


def _wilson(hits: int, n: int, z: float = 1.959963984540054) -> dict[str, float | None]:
    if n <= 0:
        return {"lower": None, "upper": None}
    p = hits / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin)}


def _norm_entropy(values: list[float]) -> float:
    vals = [max(0.0, float(v)) for v in values]
    total = sum(vals)
    if total <= 0 or len(vals) <= 1:
        return 0.0
    p = [v / total for v in vals if v > 0]
    h = -sum(v * math.log(v) for v in p)
    return h / math.log(len(vals))


def _completed_seasons(cid: str, report: dict[str, Any]) -> list[str]:
    max_year = _season_year(_requested_last_complete_season(cid))
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and _season_year(season) <= max_year and season not in seasons:
            seasons.append(season)
    seasons.sort(key=_season_year)
    return seasons


def _season_rows(cid: str, report: dict[str, Any], all_matches, season: str) -> list[dict[str, Any]]:
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"missing parameters {cid} {season}")
    temperature, _mode = _target_season_temperature(cid, season)
    matches = sorted(
        [m for m in all_matches if str(m.season) == season],
        key=lambda m: (m.date, m.home_team, m.away_team),
    )
    rows = []
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(
                all_matches, match.home_team, match.away_team, match.date, season, params
            )
        except PlatformError:
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        marg = derive_score_marginals(matrix)
        one = marg["1x2"]
        ranking = sorted(((k, float(one[k])) for k in ("home", "draw", "away")), key=lambda x: (-x[1], x[0]))
        score_probs = sorted((float(v) for v in marg["score_probabilities"].values()), reverse=True)
        total_probs = [float(marg["total_goals"][k]) for k in ("0", "1", "2", "3", "4", "5", "6", "7+")]
        total_ranked = sorted(total_probs, reverse=True)
        actual = _actual_result(int(match.home_goals), int(match.away_goals))
        rows.append({
            "top1_probability": ranking[0][1],
            "gap": ranking[0][1] - ranking[1][1],
            "result_entropy": _norm_entropy([float(one[k]) for k in ("home", "draw", "away")]),
            "score_top3": sum(score_probs[:3]),
            "score_gap": (score_probs[0] - score_probs[1]) if len(score_probs) >= 2 else 0.0,
            "total_top2": sum(total_ranked[:2]),
            "total_entropy": _norm_entropy(total_probs),
            "hit": int(ranking[0][0] == actual),
        })
    return rows


def _rules(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    families = cfg["candidate_families"]
    out = []
    for gap in families["gap_only"]["gap_thresholds"]:
        out.append({"family": "gap_only", "gap": gap})
    for gap in families["gap_result_entropy"]["gap_thresholds"]:
        for entropy in families["gap_result_entropy"]["result_entropy_max"]:
            out.append({"family": "gap_result_entropy", "gap": gap, "result_entropy_max": entropy})
    for gap in families["gap_score_concentration"]["gap_thresholds"]:
        for value in families["gap_score_concentration"]["score_top3_min"]:
            out.append({"family": "gap_score_concentration", "gap": gap, "score_top3_min": value})
    for gap in families["gap_total_concentration"]["gap_thresholds"]:
        for value in families["gap_total_concentration"]["total_top2_min"]:
            out.append({"family": "gap_total_concentration", "gap": gap, "total_top2_min": value})
    for gap in families["gap_entropy_score"]["gap_thresholds"]:
        for entropy in families["gap_entropy_score"]["result_entropy_max"]:
            for value in families["gap_entropy_score"]["score_top3_min"]:
                out.append({"family": "gap_entropy_score", "gap": gap, "result_entropy_max": entropy, "score_top3_min": value})
    for idx, rule in enumerate(out):
        params = ",".join(f"{k}={rule[k]}" for k in sorted(rule) if k != "family")
        rule["rule_id"] = f"{rule['family']}|{params}"
        rule["complexity"] = {"gap_only": 1, "gap_result_entropy": 2, "gap_score_concentration": 2, "gap_total_concentration": 2, "gap_entropy_score": 3}[rule["family"]]
    return out


def _accept(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    if float(row["gap"]) < float(rule["gap"]):
        return False
    if "result_entropy_max" in rule and float(row["result_entropy"]) > float(rule["result_entropy_max"]):
        return False
    if "score_top3_min" in rule and float(row["score_top3"]) < float(rule["score_top3_min"]):
        return False
    if "total_top2_min" in rule and float(row["total_top2"]) < float(rule["total_top2_min"]):
        return False
    return True


def _stats(rows_by_season: dict[str, list[dict[str, Any]]], rule: dict[str, Any]) -> dict[str, Any]:
    seasons = []
    pooled_n = pooled_hits = total_eligible = 0
    for season, rows in rows_by_season.items():
        selected = [row for row in rows if _accept(row, rule)]
        n = len(selected)
        hits = sum(int(row["hit"]) for row in selected)
        total_eligible += len(rows)
        pooled_n += n
        pooled_hits += hits
        seasons.append({"season": season, "selected": n, "eligible": len(rows), "accuracy": hits / n if n else None})
    accuracies = [float(item["accuracy"]) for item in seasons if item["accuracy"] is not None]
    return {
        "rule_id": rule["rule_id"],
        "family": rule["family"],
        "rule": {k: v for k, v in rule.items() if k not in ("complexity",)},
        "selected": pooled_n,
        "eligible": total_eligible,
        "coverage": pooled_n / total_eligible if total_eligible else None,
        "hits": pooled_hits,
        "accuracy": pooled_hits / pooled_n if pooled_n else None,
        "min_season_selected": min((item["selected"] for item in seasons), default=0),
        "min_season_accuracy": min(accuracies) if accuracies else None,
        "season_accuracy_std": pstdev(accuracies) if len(accuracies) > 1 else 0.0 if accuracies else None,
        "season_stats": seasons,
        "complexity": int(rule["complexity"]),
    }


def _qualifies(stats: dict[str, Any], gate: dict[str, Any]) -> bool:
    return (
        stats["selected"] >= int(gate["pooled_selected_min"])
        and stats["min_season_selected"] >= int(gate["per_season_selected_min"])
        and stats["accuracy"] is not None
        and float(stats["accuracy"]) >= float(gate["pooled_accuracy_min"])
        and stats["min_season_accuracy"] is not None
        and float(stats["min_season_accuracy"]) >= float(gate["minimum_season_accuracy_min"])
        and stats["season_accuracy_std"] is not None
        and float(stats["season_accuracy_std"]) <= float(gate["season_accuracy_std_max"])
    )


def _select(prior_rows: dict[str, list[dict[str, Any]]], rules: list[dict[str, Any]], cfg: dict[str, Any], family: str | None = None):
    candidates = []
    for rule in rules:
        if family is not None and rule["family"] != family:
            continue
        stats = _stats(prior_rows, rule)
        if _qualifies(stats, cfg["prior_selection_gate"]):
            candidates.append(stats)
    if not candidates:
        return None
    candidates.sort(key=lambda s: (-int(s["selected"]), -float(s["accuracy"]), int(s["complexity"]), str(s["rule_id"])))
    return candidates[0]


def _evaluate(rows: list[dict[str, Any]], selected: dict[str, Any] | None) -> dict[str, Any]:
    if selected is None:
        return {"selection_status": "NO_PRIOR_QUALIFYING_RULE", "selected_count": 0, "hit_count": 0, "accuracy": None, "coverage": 0.0, "rule_id": None}
    rule = selected["rule"]
    selected_rows = [row for row in rows if _accept(row, rule)]
    n = len(selected_rows)
    hits = sum(int(row["hit"]) for row in selected_rows)
    return {
        "selection_status": "FROZEN_FROM_PRIOR_SEASONS",
        "rule_id": selected["rule_id"],
        "family": selected["family"],
        "rule": rule,
        "prior_selection_stats": {k: v for k, v in selected.items() if k != "season_stats"},
        "selected_count": n,
        "eligible_predictions": len(rows),
        "coverage": n / len(rows) if rows else None,
        "hit_count": hits,
        "accuracy": hits / n if n else None,
        "ci95_wilson": _wilson(hits, n),
    }


def validate_domain(cid: str, cfg: dict[str, Any]) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    seasons = _completed_seasons(cid, report)
    if len(seasons) < 3:
        raise PlatformError(f"need at least 3 completed seasons for {cid}")
    all_matches = read_processed_matches(cid)
    cache = {season: _season_rows(cid, report, all_matches, season) for season in seasons}
    rules = _rules(cfg)
    folds = []
    for idx in range(2, len(seasons)):
        target = seasons[idx]
        prior = seasons[:idx]
        prior_rows = {season: cache[season] for season in prior}
        selected = _select(prior_rows, rules, cfg)
        gap_only = _select(prior_rows, rules, cfg, family="gap_only")
        folds.append({
            "target_season": target,
            "training_seasons": prior,
            "multi_signal": _evaluate(cache[target], selected),
            "gap_only_comparator": _evaluate(cache[target], gap_only),
        })

    evaluated = [fold for fold in folds if fold["multi_signal"].get("accuracy") is not None]
    pooled_n = sum(int(fold["multi_signal"]["selected_count"]) for fold in evaluated)
    pooled_hits = sum(int(fold["multi_signal"]["hit_count"]) for fold in evaluated)
    accuracies = [float(fold["multi_signal"]["accuracy"]) for fold in evaluated]
    gap_eval = [fold for fold in folds if fold["gap_only_comparator"].get("accuracy") is not None]
    gap_n = sum(int(fold["gap_only_comparator"]["selected_count"]) for fold in gap_eval)
    gap_hits = sum(int(fold["gap_only_comparator"]["hit_count"]) for fold in gap_eval)

    gate = cfg["forward_candidate_gate"]
    checks = {
        "evaluated_forward_folds_min": len(evaluated) >= int(gate["evaluated_forward_folds_min"]),
        "pooled_selected_min": pooled_n >= int(gate["pooled_selected_min"]),
        "pooled_accuracy_min": pooled_n > 0 and pooled_hits / pooled_n >= float(gate["pooled_accuracy_min"]),
        "minimum_forward_season_accuracy_min": bool(accuracies) and min(accuracies) >= float(gate["minimum_forward_season_accuracy_min"]),
        "forward_accuracy_std_max": len(accuracies) > 1 and pstdev(accuracies) <= float(gate["forward_accuracy_std_max"]),
    }
    pooled_accuracy = pooled_hits / pooled_n if pooled_n else None
    gap_accuracy = gap_hits / gap_n if gap_n else None
    return {
        "competition_id": cid,
        "status": "PREDICTABILITY_RESEARCH_CANDIDATE" if all(checks.values()) else "KEEP_FORMAL_RUNTIME_UNCHANGED",
        "seasons": seasons,
        "feature_count": 7,
        "candidate_rule_count": len(rules),
        "forward_folds": folds,
        "evaluated_forward_fold_count": len(evaluated),
        "pooled_selected_count": pooled_n,
        "pooled_hit_count": pooled_hits,
        "pooled_accuracy": pooled_accuracy,
        "pooled_coverage": pooled_n / sum(len(cache[s]) for s in seasons[2:]) if seasons[2:] else None,
        "pooled_ci95_wilson": _wilson(pooled_hits, pooled_n),
        "forward_accuracy_min": min(accuracies) if accuracies else None,
        "forward_accuracy_std": pstdev(accuracies) if len(accuracies) > 1 else None,
        "gap_only_pooled_selected_count": gap_n,
        "gap_only_pooled_accuracy": gap_accuracy,
        "multi_signal_minus_gap_only_accuracy": (pooled_accuracy - gap_accuracy) if pooled_accuracy is not None and gap_accuracy is not None else None,
        "checks": checks,
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
    }
