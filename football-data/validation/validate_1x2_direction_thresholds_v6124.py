#!/usr/bin/env python3
"""Research-only cross-season direction-specific selective 1X2 threshold audit.

Uses the multi-season split from V6.12.3. Development seasons choose separate market
Top-1 probability thresholds for home and away favourites; the latest season per
competition remains untouched until final evaluation. Draw cannot pass these high
Top-1 thresholds in the observed data and is not force-created.

Historical odds lack original quote timestamps, so this is retrospective research only,
formal_weight=0, with no CURRENT/runtime/promotion changes.
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

OUT = ROOT / "manifests" / "v6_1x2_direction_thresholds_v6124_status.json"
GRID = (0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72)
PHASE_EXCLUDE = 0.05
TARGET_ACCURACY = 0.75
MIN_COVERAGE_RETENTION = 0.80
MIN_DEV_HOME = 1200
MIN_DEV_AWAY = 300
WINDOW_SIZE = 100
MIN_SELECTED_WINDOW = 10
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


def _eval(rows: list[dict[str, Any]], home_threshold: float, away_threshold: float) -> dict[str, Any]:
    max_progress = 1.0 - PHASE_EXCLUDE
    selected = []
    for r in rows:
        if float(r["season_progress"]) > max_progress:
            continue
        pick = r["pick"]
        if pick == "home" and float(r["pmax"]) >= home_threshold:
            selected.append(r)
        elif pick == "away" and float(r["pmax"]) >= away_threshold:
            selected.append(r)
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


def _choose(development: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline = _eval(development, 0.62, 0.62)
    candidates = []
    for ht in GRID:
        for at in GRID:
            e = _eval(development, ht, at)
            home_n = e["by_direction"]["home"]["count"]
            away_n = e["by_direction"]["away"]["count"]
            retention = e["count"] / baseline["count"] if baseline["count"] else 0.0
            e["coverage_retention_vs_062"] = retention
            e["admissible"] = (
                e["accuracy"] is not None
                and e["accuracy"] >= TARGET_ACCURACY
                and retention >= MIN_COVERAGE_RETENTION
                and home_n >= MIN_DEV_HOME
                and away_n >= MIN_DEV_AWAY
            )
            candidates.append(e)
    admissible = [c for c in candidates if c["admissible"]]
    if not admissible:
        return baseline, candidates
    # Among rules that reach target accuracy, keep as much coverage as possible.
    admissible.sort(key=lambda c: (c["count"], c["accuracy"], c["wilson90_lower"] or 0.0), reverse=True)
    return admissible[0], candidates


def _windows(rows: list[dict[str, Any]], ht: float, at: float) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    out = []
    for start in range(0, len(ordered) - WINDOW_SIZE + 1, WINDOW_SIZE):
        chunk = ordered[start:start + WINDOW_SIZE]
        e = _eval(chunk, ht, at)
        e.update({
            "start": start,
            "stop": start + WINDOW_SIZE,
            "first_date": chunk[0]["date"],
            "last_date": chunk[-1]["date"],
            "power_status": "ELIGIBLE" if e["count"] >= MIN_SELECTED_WINDOW else "LOW_POWER",
        })
        out.append(e)
    eligible = [w for w in out if w["power_status"] == "ELIGIBLE" and w["accuracy"] is not None]
    acc = [float(w["accuracy"]) for w in eligible]
    return {
        "windows": out,
        "summary": {
            "window_count": len(out),
            "eligible_window_count": len(eligible),
            "worst_accuracy": min(acc) if acc else None,
            "median_accuracy": statistics.median(acc) if acc else None,
            "mean_accuracy": statistics.mean(acc) if acc else None,
            "windows_ge_70pct": sum(1 for x in acc if x >= 0.70),
            "windows_lt_60pct": sum(1 for x in acc if x < 0.60),
        },
    }


def main() -> int:
    rows, providers = phase._read_rows()
    development, holdout, split_meta = phase._prepare_seasons(rows)
    if not development or not holdout:
        raise RuntimeError("insufficient cross-season rows")

    chosen, candidates = _choose(development)
    baseline_dev = _eval(development, 0.62, 0.62)
    baseline_holdout = _eval(holdout, 0.62, 0.62)
    chosen_holdout = _eval(holdout, float(chosen["home_threshold"]), float(chosen["away_threshold"]))

    payload = {
        "schema_version": "V6.12.4-direction-specific-thresholds-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "design": {
            "development_rows": len(development),
            "holdout_rows": len(holdout),
            "latest_season_per_competition_untouched_for_threshold_selection": True,
            "phase_exclude_final_fraction_frozen_from_v6123": PHASE_EXCLUDE,
            "grid": list(GRID),
            "target_accuracy": TARGET_ACCURACY,
            "minimum_coverage_retention": MIN_COVERAGE_RETENTION,
            "minimum_development_home_selected": MIN_DEV_HOME,
            "minimum_development_away_selected": MIN_DEV_AWAY,
        },
        "provider_counts": providers,
        "split": split_meta,
        "development_baseline_062": baseline_dev,
        "development_selected_rule": chosen,
        "development_admissible_candidates": [c for c in candidates if c["admissible"]],
        "holdout_baseline_062": baseline_holdout,
        "holdout_selected_rule": chosen_holdout,
        "holdout_accuracy_difference": (
            chosen_holdout["accuracy"] - baseline_holdout["accuracy"]
            if chosen_holdout["accuracy"] is not None and baseline_holdout["accuracy"] is not None
            else None
        ),
        "holdout_baseline_windows": _windows(holdout, 0.62, 0.62),
        "holdout_selected_windows": _windows(holdout, float(chosen["home_threshold"]), float(chosen["away_threshold"])),
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "holdout_not_used_for_threshold_selection": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "development_baseline_062": baseline_dev,
        "development_selected_rule": chosen,
        "holdout_baseline_062": baseline_holdout,
        "holdout_selected_rule": chosen_holdout,
        "holdout_accuracy_difference": payload["holdout_accuracy_difference"],
        "baseline_window_summary": payload["holdout_baseline_windows"]["summary"],
        "selected_window_summary": payload["holdout_selected_windows"]["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
