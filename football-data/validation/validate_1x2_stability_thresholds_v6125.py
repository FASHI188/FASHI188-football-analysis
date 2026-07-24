#!/usr/bin/env python3
"""Research-only cross-season stability-optimized selective 1X2 gate.

This follows V6.12.4 but changes the development-only objective: instead of maximizing
aggregate coverage after clearing an accuracy target, choose separate home/away market
Top-1 thresholds that improve the lower tail of chronological 100-match window
accuracy. The latest season per competition remains untouched until final evaluation.

All market prices are retrospective references without original quote timestamps.
formal_weight=0; this script cannot change CURRENT, runtime probabilities, or promotion.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_1x2_crossseason_phase_v6123 as phase

OUT = ROOT / "manifests" / "v6_1x2_stability_thresholds_v6125_status.json"
GRID = (0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72, 0.74, 0.76)
PHASE_EXCLUDE = 0.05
WINDOW_SIZE = 100
MIN_SELECTED_WINDOW = 10
MIN_ELIGIBLE_WINDOW_SHARE = 0.80
MIN_COVERAGE_RETENTION = 0.70
MIN_OVERALL_DEV_ACCURACY = 0.74
MIN_DEV_HOME = 1000
MIN_DEV_AWAY = 250
BASELINE_HOME_THRESHOLD = 0.62
BASELINE_AWAY_THRESHOLD = 0.62
V6124_HOME_THRESHOLD = 0.64
V6124_AWAY_THRESHOLD = 0.60
Z90 = 1.6448536269514722


def _wilson_lower(hits: int, n: int, z: float = Z90) -> float | None:
    if n <= 0:
        return None
    p = hits / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (center - radius) / den


def _select(rows: list[dict[str, Any]], home_threshold: float, away_threshold: float) -> list[dict[str, Any]]:
    max_progress = 1.0 - PHASE_EXCLUDE
    selected = []
    for r in rows:
        if float(r["season_progress"]) > max_progress:
            continue
        pick = r["pick"]
        pmax = float(r["pmax"])
        if pick == "home" and pmax >= home_threshold:
            selected.append(r)
        elif pick == "away" and pmax >= away_threshold:
            selected.append(r)
    return selected


def _eval(rows: list[dict[str, Any]], home_threshold: float, away_threshold: float) -> dict[str, Any]:
    selected = _select(rows, home_threshold, away_threshold)
    hits = sum(1 for r in selected if r["pick"] == r["actual"])
    n = len(selected)
    by_direction = {}
    for d in ("home", "away"):
        sub = [r for r in selected if r["pick"] == d]
        h = sum(1 for r in sub if r["pick"] == r["actual"])
        by_direction[d] = {"count": len(sub), "hits": h, "accuracy": h / len(sub) if sub else None}
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90_lower": _wilson_lower(hits, n),
        "coverage": n / len(rows) if rows else 0.0,
        "home_threshold": home_threshold,
        "away_threshold": away_threshold,
        "exclude_final_fraction": PHASE_EXCLUDE,
        "by_direction": by_direction,
    }


def _q10(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, math.ceil(0.10 * len(s)) - 1)
    return s[idx]


def _window_eval(rows: list[dict[str, Any]], home_threshold: float, away_threshold: float) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    windows = []
    for start in range(0, len(ordered) - WINDOW_SIZE + 1, WINDOW_SIZE):
        chunk = ordered[start:start + WINDOW_SIZE]
        e = _eval(chunk, home_threshold, away_threshold)
        e.update({
            "start": start,
            "stop": start + WINDOW_SIZE,
            "first_date": chunk[0]["date"],
            "last_date": chunk[-1]["date"],
            "power_status": "ELIGIBLE" if e["count"] >= MIN_SELECTED_WINDOW else "LOW_POWER",
        })
        windows.append(e)
    eligible = [w for w in windows if w["power_status"] == "ELIGIBLE" and w["accuracy"] is not None]
    acc = [float(w["accuracy"]) for w in eligible]
    summary = {
        "window_count": len(windows),
        "eligible_window_count": len(eligible),
        "eligible_window_share": len(eligible) / len(windows) if windows else 0.0,
        "low_power_window_count": len(windows) - len(eligible),
        "worst_accuracy": min(acc) if acc else None,
        "q10_accuracy": _q10(acc),
        "median_accuracy": statistics.median(acc) if acc else None,
        "mean_accuracy": statistics.mean(acc) if acc else None,
        "windows_ge_70pct": sum(1 for x in acc if x >= 0.70),
        "windows_lt_65pct": sum(1 for x in acc if x < 0.65),
        "windows_lt_60pct": sum(1 for x in acc if x < 0.60),
    }
    return {"windows": windows, "summary": summary}


def _candidate(development: list[dict[str, Any]], baseline_count: int, ht: float, at: float) -> dict[str, Any]:
    overall = _eval(development, ht, at)
    windows = _window_eval(development, ht, at)["summary"]
    retention = overall["count"] / baseline_count if baseline_count else 0.0
    home_n = overall["by_direction"]["home"]["count"]
    away_n = overall["by_direction"]["away"]["count"]
    admissible = (
        overall["accuracy"] is not None
        and overall["accuracy"] >= MIN_OVERALL_DEV_ACCURACY
        and retention >= MIN_COVERAGE_RETENTION
        and home_n >= MIN_DEV_HOME
        and away_n >= MIN_DEV_AWAY
        and windows["eligible_window_share"] >= MIN_ELIGIBLE_WINDOW_SHARE
        and windows["q10_accuracy"] is not None
    )
    return {
        "home_threshold": ht,
        "away_threshold": at,
        "overall": overall,
        "windows": windows,
        "coverage_retention_vs_062": retention,
        "admissible": admissible,
    }


def _choose(development: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline = _eval(development, BASELINE_HOME_THRESHOLD, BASELINE_AWAY_THRESHOLD)
    candidates = [_candidate(development, baseline["count"], ht, at) for ht in GRID for at in GRID]
    admissible = [c for c in candidates if c["admissible"]]
    if not admissible:
        return _candidate(development, baseline["count"], BASELINE_HOME_THRESHOLD, BASELINE_AWAY_THRESHOLD), candidates
    # Stability-first development-only objective: maximize 10th-percentile window accuracy,
    # then worst window, then fewer <60 windows, aggregate accuracy, and coverage.
    admissible.sort(
        key=lambda c: (
            c["windows"]["q10_accuracy"],
            c["windows"]["worst_accuracy"],
            -c["windows"]["windows_lt_60pct"],
            c["overall"]["accuracy"],
            c["overall"]["count"],
        ),
        reverse=True,
    )
    return admissible[0], candidates


def main() -> int:
    rows, providers = phase._read_rows()
    development, holdout, split_meta = phase._prepare_seasons(rows)
    if not development or not holdout:
        raise RuntimeError("insufficient cross-season rows")

    chosen, candidates = _choose(development)
    chosen_ht = float(chosen["home_threshold"])
    chosen_at = float(chosen["away_threshold"])

    baseline_dev = _eval(development, BASELINE_HOME_THRESHOLD, BASELINE_AWAY_THRESHOLD)
    baseline_dev_windows = _window_eval(development, BASELINE_HOME_THRESHOLD, BASELINE_AWAY_THRESHOLD)
    chosen_dev_windows = _window_eval(development, chosen_ht, chosen_at)

    baseline_holdout = _eval(holdout, BASELINE_HOME_THRESHOLD, BASELINE_AWAY_THRESHOLD)
    v6124_holdout = _eval(holdout, V6124_HOME_THRESHOLD, V6124_AWAY_THRESHOLD)
    chosen_holdout = _eval(holdout, chosen_ht, chosen_at)
    baseline_holdout_windows = _window_eval(holdout, BASELINE_HOME_THRESHOLD, BASELINE_AWAY_THRESHOLD)
    v6124_holdout_windows = _window_eval(holdout, V6124_HOME_THRESHOLD, V6124_AWAY_THRESHOLD)
    chosen_holdout_windows = _window_eval(holdout, chosen_ht, chosen_at)

    payload = {
        "schema_version": "V6.12.5-stability-optimized-direction-thresholds-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "design": {
            "development_rows": len(development),
            "holdout_rows": len(holdout),
            "latest_season_per_competition_untouched_for_gate_selection": True,
            "phase_exclude_final_fraction_frozen_from_v6123": PHASE_EXCLUDE,
            "threshold_grid": list(GRID),
            "window_size": WINDOW_SIZE,
            "minimum_selected_window": MIN_SELECTED_WINDOW,
            "minimum_eligible_window_share": MIN_ELIGIBLE_WINDOW_SHARE,
            "minimum_coverage_retention": MIN_COVERAGE_RETENTION,
            "minimum_overall_dev_accuracy": MIN_OVERALL_DEV_ACCURACY,
            "selection_objective": "maximize development q10 100-match-window accuracy, then worst window, fewer sub-60 windows, overall accuracy, coverage",
        },
        "provider_counts": providers,
        "split": split_meta,
        "development_baseline_062": baseline_dev,
        "development_baseline_windows": baseline_dev_windows,
        "development_selected_rule": chosen,
        "development_selected_windows": chosen_dev_windows,
        "development_admissible_candidates": [c for c in candidates if c["admissible"]],
        "holdout_baseline_062": baseline_holdout,
        "holdout_v6124_rule": v6124_holdout,
        "holdout_selected_rule": chosen_holdout,
        "holdout_accuracy_difference_vs_062": (
            chosen_holdout["accuracy"] - baseline_holdout["accuracy"]
            if chosen_holdout["accuracy"] is not None and baseline_holdout["accuracy"] is not None else None
        ),
        "holdout_accuracy_difference_vs_v6124": (
            chosen_holdout["accuracy"] - v6124_holdout["accuracy"]
            if chosen_holdout["accuracy"] is not None and v6124_holdout["accuracy"] is not None else None
        ),
        "holdout_baseline_windows": baseline_holdout_windows,
        "holdout_v6124_windows": v6124_holdout_windows,
        "holdout_selected_windows": chosen_holdout_windows,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "holdout_not_used_for_gate_selection": True,
            "no_original_quote_timestamps": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "development_selected_rule": chosen,
        "holdout_baseline_062": baseline_holdout,
        "holdout_v6124_rule": v6124_holdout,
        "holdout_selected_rule": chosen_holdout,
        "baseline_holdout_windows": baseline_holdout_windows["summary"],
        "v6124_holdout_windows": v6124_holdout_windows["summary"],
        "selected_holdout_windows": chosen_holdout_windows["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
