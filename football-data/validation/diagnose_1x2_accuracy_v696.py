#!/usr/bin/env python3
"""Repeated 1X2 hit-rate diagnostics on strict PIT last-complete-season replays.

Purpose:
- stop treating data-coverage work as the objective;
- measure 1X2 Top-1 hit rate repeatedly on 100-match samples;
- discover a simple decision-layer correction on development samples;
- verify that correction on a permanently untouched 100-match audit sample;
- separately measure selective/abstention accuracy and coverage.

This is RESEARCH ONLY. It does not mutate the formal engine, CURRENT rules, weights,
or production probabilities. Historical odds are not injected because the repository's
full timestamped historical market backfill is not complete.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from football_v460_engine import (
    _merge_parameters,
    build_score_matrix,
    current_season_history,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from oof_matrix_calibration import load_oof_matrix_calibrator, temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, read_processed_matches

FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
OUT = ROOT / "manifests" / "v6_1x2_accuracy_diagnostic_v696_status.json"
CACHE = ROOT / "validation" / "cache" / "v696_last_complete_season_1x2_rows.json"

CALENDAR_YEAR_DOMAINS = {
    "SWE_Allsvenskan", "NOR_Eliteserien", "JPN_J1", "KOR_KLeague1",
    "BRA_SerieA", "ARG_Primera", "USA_MLS",
}
DIRECTIONS = ("home", "draw", "away")
SEED = 20260724
AUDIT_N = 100
DEV_N = 100
TEST_N = 100
REPEATS = 30


def _requested_last_complete_season(competition_id: str) -> str:
    return "2025" if competition_id in CALENDAR_YEAR_DOMAINS else "2025/26"


def _actual_result(hg: int, ag: int) -> str:
    return "home" if hg > ag else "draw" if hg == ag else "away"


def _fold_for_season(report: dict[str, Any], season: str) -> dict[str, Any]:
    folds = [f for f in (report.get("folds") or []) if str(f.get("outer_season")) == season]
    if len(folds) != 1:
        raise PlatformError(f"expected exactly one outer fold for {season}; got {len(folds)}")
    return folds[0]


def _temperature(competition_id: str, season: str) -> float:
    loaded = load_oof_matrix_calibrator(competition_id)
    if loaded is None:
        return 1.0
    _, artifact = loaded
    item = (artifact.get("season_calibrators") or {}).get(season)
    if not isinstance(item, dict):
        return 1.0
    return float(item.get("temperature", 1.0))


def _predict_matrix(all_matches, home: str, away: str, cutoff, season: str, selected: dict[str, Any]):
    config = load_config()
    params = _merge_parameters(config, selected)
    _, history = current_season_history(all_matches, cutoff, season)
    state = fit_current_season_state(history, cutoff, params, config)
    means = expected_goals(state, home, away, params, config)
    factors = low_score_factors(state, params)
    return build_score_matrix(
        float(means["mu_home"]), float(means["mu_away"]),
        float(state["nb_dispersion_k"]), float(params["beta_binomial_concentration"]),
        int(config["max_total_goals_exact"]), factors,
    )


def _row_key(row: dict[str, Any]) -> str:
    return "|".join((row["competition_id"], row["date"], row["home_team"], row["away_team"]))


def _generate_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    rows: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    skipped_by_comp: dict[str, int] = {}

    for cid in competitions:
        try:
            season = _requested_last_complete_season(cid)
            report = load_json(REPORT_ROOT / f"{cid}.json")
            fold = _fold_for_season(report, season)
            selected = fold.get("selected_parameters")
            if not isinstance(selected, dict):
                raise PlatformError("selected_parameters missing")
            all_matches = read_processed_matches(cid)
            matches = sorted(
                [m for m in all_matches if str(m.season) == season],
                key=lambda m: (m.date, m.home_team, m.away_team),
            )
            temp = _temperature(cid, season)
            skipped = 0
            for match in matches:
                try:
                    matrix = _predict_matrix(
                        all_matches, match.home_team, match.away_team, match.date, season, selected
                    )
                except PlatformError:
                    skipped += 1
                    continue
                if abs(temp - 1.0) > 1e-15:
                    matrix = temperature_scale_matrix(matrix, temp)
                one = derive_score_marginals(matrix)["1x2"]
                probs = {k: float(one[k]) for k in DIRECTIONS}
                order = sorted(DIRECTIONS, key=lambda k: probs[k], reverse=True)
                rows.append({
                    "competition_id": cid,
                    "season": season,
                    "date": match.date.isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "actual": _actual_result(int(match.home_goals), int(match.away_goals)),
                    "p_home": probs["home"],
                    "p_draw": probs["draw"],
                    "p_away": probs["away"],
                    "baseline_pick": order[0],
                    "baseline_max_probability": probs[order[0]],
                    "baseline_margin": probs[order[0]] - probs[order[1]],
                })
            skipped_by_comp[cid] = skipped
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        raise PlatformError(f"row generation failures: {failures}")
    return rows, {"competition_count": len(competitions), "skipped_by_competition": skipped_by_comp}


def _load_or_generate_rows() -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if CACHE.exists():
        cached = load_json(CACHE)
        if cached.get("schema_version") == "V6.9.6-1x2-row-cache-r1" and isinstance(cached.get("rows"), list):
            return cached["rows"], cached.get("generation") or {}, "cache"
    rows, generation = _generate_rows()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({
        "schema_version": "V6.9.6-1x2-row-cache-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "historical_odds_used": False,
        "generation": generation,
        "rows": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows, generation, "generated"


def _baseline_pick(row: dict[str, Any]) -> str:
    return str(row["baseline_pick"])


def _weighted_probs(row: dict[str, Any], weights: tuple[float, float, float]) -> dict[str, float]:
    raw = {
        "home": float(row["p_home"]) * weights[0],
        "draw": float(row["p_draw"]) * weights[1],
        "away": float(row["p_away"]) * weights[2],
    }
    total = sum(raw.values())
    return {k: raw[k] / total for k in DIRECTIONS}


def _weighted_pick(row: dict[str, Any], weights: tuple[float, float, float]) -> str:
    p = _weighted_probs(row, weights)
    return max(DIRECTIONS, key=lambda k: p[k])


def _accuracy(rows: list[dict[str, Any]], picker) -> tuple[int, int, float]:
    hits = sum(1 for r in rows if picker(r) == r["actual"])
    n = len(rows)
    return hits, n, hits / n if n else float("nan")


def _candidate_weights() -> list[tuple[float, float, float]]:
    homes = (0.85, 0.90, 0.95, 1.00, 1.05)
    draws = (1.00, 1.10, 1.20, 1.30, 1.40, 1.50, 1.60)
    aways = (0.90, 0.95, 1.00, 1.05, 1.10)
    return [(h, d, a) for h in homes for d in draws for a in aways]


def _fit_weights(dev: list[dict[str, Any]]) -> tuple[float, float, float]:
    best = (1.0, 1.0, 1.0)
    best_hits = -1
    best_distance = float("inf")
    for weights in _candidate_weights():
        hits = sum(1 for r in dev if _weighted_pick(r, weights) == r["actual"])
        distance = sum(abs(x - 1.0) for x in weights)
        if hits > best_hits or (hits == best_hits and distance < best_distance):
            best, best_hits, best_distance = weights, hits, distance
    return best


def _direction_counts(rows: list[dict[str, Any]], picker) -> dict[str, int]:
    return dict(Counter(picker(r) for r in rows))


def _actual_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(r["actual"]) for r in rows))


def _fit_selective_gate(
    rows: list[dict[str, Any]],
    weights: tuple[float, float, float],
    target_accuracy: float,
) -> dict[str, Any] | None:
    candidates = []
    for min_p_i in range(34, 71, 2):
        min_p = min_p_i / 100.0
        for min_margin_i in range(0, 31, 2):
            min_margin = min_margin_i / 100.0
            selected = []
            for row in rows:
                p = _weighted_probs(row, weights)
                order = sorted(DIRECTIONS, key=lambda k: p[k], reverse=True)
                if p[order[0]] >= min_p and (p[order[0]] - p[order[1]]) >= min_margin:
                    selected.append((row, order[0]))
            if len(selected) < 50:
                continue
            hits = sum(1 for row, pick in selected if pick == row["actual"])
            acc = hits / len(selected)
            if acc >= target_accuracy:
                candidates.append({
                    "min_probability": min_p,
                    "min_margin": min_margin,
                    "count": len(selected),
                    "coverage": len(selected) / len(rows),
                    "accuracy": acc,
                })
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x["coverage"], x["accuracy"], -x["min_probability"], -x["min_margin"]), reverse=True)
    return candidates[0]


def _eval_selective_gate(
    rows: list[dict[str, Any]], weights: tuple[float, float, float], gate: dict[str, Any] | None
) -> dict[str, Any]:
    if gate is None:
        return {"available": False, "count": 0, "coverage": 0.0, "accuracy": None}
    selected = []
    for row in rows:
        p = _weighted_probs(row, weights)
        order = sorted(DIRECTIONS, key=lambda k: p[k], reverse=True)
        if p[order[0]] >= float(gate["min_probability"]) and (p[order[0]] - p[order[1]]) >= float(gate["min_margin"]):
            selected.append((row, order[0]))
    hits = sum(1 for row, pick in selected if pick == row["actual"])
    return {
        "available": True,
        "count": len(selected),
        "hits": hits,
        "coverage": len(selected) / len(rows) if rows else 0.0,
        "accuracy": hits / len(selected) if selected else None,
        "gate": gate,
    }


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def main() -> int:
    rows, generation, cache_mode = _load_or_generate_rows()
    if len(rows) < AUDIT_N + DEV_N + TEST_N:
        raise PlatformError(f"not enough eligible rows: {len(rows)}")

    # Untouched 100-match audit sample is selected first and excluded from every tuning repeat.
    master_rng = random.Random(SEED)
    audit_indices = set(master_rng.sample(range(len(rows)), AUDIT_N))
    audit_rows = [r for i, r in enumerate(rows) if i in audit_indices]
    research_rows = [r for i, r in enumerate(rows) if i not in audit_indices]

    overall_baseline = _accuracy(rows, _baseline_pick)
    repeated = []
    fitted_rules: list[tuple[float, float, float]] = []
    for repeat in range(REPEATS):
        rng = random.Random(SEED + 1000 + repeat)
        sample = rng.sample(research_rows, DEV_N + TEST_N)
        dev, test = sample[:DEV_N], sample[DEV_N:]
        weights = _fit_weights(dev)
        fitted_rules.append(weights)
        base_hits, _, base_acc = _accuracy(test, _baseline_pick)
        tuned_hits, _, tuned_acc = _accuracy(test, lambda r, w=weights: _weighted_pick(r, w))
        repeated.append({
            "repeat": repeat + 1,
            "dev_n": DEV_N,
            "test_n": TEST_N,
            "weights": {"home": weights[0], "draw": weights[1], "away": weights[2]},
            "baseline_hits": base_hits,
            "baseline_accuracy": base_acc,
            "tuned_hits": tuned_hits,
            "tuned_accuracy": tuned_acc,
            "uplift_pp": (tuned_acc - base_acc) * 100.0,
        })

    rule_counts = Counter(fitted_rules)
    consensus_weights, consensus_frequency = sorted(
        rule_counts.items(), key=lambda kv: (kv[1], -sum(abs(x - 1.0) for x in kv[0])), reverse=True
    )[0]

    audit_base_hits, _, audit_base_acc = _accuracy(audit_rows, _baseline_pick)
    audit_tuned_hits, _, audit_tuned_acc = _accuracy(
        audit_rows, lambda r: _weighted_pick(r, consensus_weights)
    )

    selective = {}
    for target in (0.60, 0.65, 0.70):
        gate = _fit_selective_gate(research_rows, consensus_weights, target)
        selective[f"target_{int(target*100)}"] = {
            "research_fit": gate,
            "untouched_100_audit": _eval_selective_gate(audit_rows, consensus_weights, gate),
        }

    baseline_accs = [r["baseline_accuracy"] for r in repeated]
    tuned_accs = [r["tuned_accuracy"] for r in repeated]
    uplifts = [r["uplift_pp"] for r in repeated]
    comparison = Counter(
        "win" if r["tuned_accuracy"] > r["baseline_accuracy"] else
        "tie" if r["tuned_accuracy"] == r["baseline_accuracy"] else "loss"
        for r in repeated
    )

    payload = {
        "schema_version": "V6.9.6-repeated-1x2-accuracy-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "objective": "raise and repeatedly verify 1X2 Top-1 hit rate before further data-coverage work",
        "sample_contract": {
            "eligible_row_count": len(rows),
            "untouched_audit_n": AUDIT_N,
            "repeat_count": REPEATS,
            "development_n_per_repeat": DEV_N,
            "test_n_per_repeat": TEST_N,
            "seed": SEED,
            "historical_odds_used": False,
            "lineup_hindsight_used": False,
            "postmatch_features_used": False,
            "cache_mode": cache_mode,
        },
        "generation": generation,
        "overall_baseline": {
            "hits": overall_baseline[0],
            "count": overall_baseline[1],
            "accuracy": overall_baseline[2],
            "predicted_direction_counts": _direction_counts(rows, _baseline_pick),
            "actual_direction_counts": _actual_counts(rows),
        },
        "repeated_100_match_tests": {
            "baseline_accuracy": _summary(baseline_accs),
            "tuned_accuracy": _summary(tuned_accs),
            "uplift_pp": _summary(uplifts),
            "tuned_vs_baseline": dict(comparison),
            "consensus_weight_rule": {
                "home": consensus_weights[0],
                "draw": consensus_weights[1],
                "away": consensus_weights[2],
                "frequency_across_repeats": consensus_frequency,
                "repeat_count": REPEATS,
            },
            "runs": repeated,
        },
        "untouched_random_100_audit": {
            "baseline_hits": audit_base_hits,
            "baseline_accuracy": audit_base_acc,
            "tuned_hits": audit_tuned_hits,
            "tuned_accuracy": audit_tuned_acc,
            "uplift_pp": (audit_tuned_acc - audit_base_acc) * 100.0,
            "baseline_direction_counts": _direction_counts(audit_rows, _baseline_pick),
            "tuned_direction_counts": _direction_counts(audit_rows, lambda r: _weighted_pick(r, consensus_weights)),
            "actual_direction_counts": _actual_counts(audit_rows),
        },
        "selective_accuracy": selective,
        "interpretation_rules": {
            "full_coverage_gain_requires_untouched_audit_improvement": True,
            "selective_accuracy_must_always_report_coverage": True,
            "no_rule_promoted_from_development_hit_rate_alone": True,
            "historical_diagnostic_is_not_formal_forward_promotion": True,
        },
        "governance": {
            "research_only": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "overall_baseline": payload["overall_baseline"],
        "repeated_summary": payload["repeated_100_match_tests"],
        "untouched_random_100_audit": payload["untouched_random_100_audit"],
        "selective_accuracy": payload["selective_accuracy"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
