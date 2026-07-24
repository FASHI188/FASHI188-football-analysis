#!/usr/bin/env python3
"""Research-only nested calendar walk-forward audit for selective 1X2.

This is the robustness test after V6.12.2-V6.12.6. Each annual fold selects separate
home/away market-confidence thresholds using only matches available before the fold.
Within pre-fold history, only the most recent 25% is used for threshold selection so the
procedure can adapt without seeing the test year. The next 12 months are untouched.

Historical market odds are closing-price references without original quote timestamps,
so the audit is research-only (formal_weight=0) even though the split logic is strictly
time-forward.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_1x2_crossseason_phase_v6123 as phase

OUT = ROOT / "manifests" / "v6_1x2_nested_walkforward_v6127_status.json"
GRID = (0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72)
FOLDS = (
    ("2022-07-01", "2023-07-01"),
    ("2023-07-01", "2024-07-01"),
    ("2024-07-01", "2025-07-01"),
    ("2025-07-01", "2026-07-01"),
)
VALIDATION_TAIL_FRACTION = 0.25
BASELINE_HOME = 0.62
BASELINE_AWAY = 0.62
FIXED_HOME = 0.64
FIXED_AWAY = 0.60
MIN_VALIDATION_SELECTED = 150
MIN_VALIDATION_HOME = 100
MIN_VALIDATION_AWAY = 25
MIN_COVERAGE_RETENTION = 0.80
WINDOW_SIZE = 100
MIN_SELECTED_WINDOW = 10
Z90 = 1.6448536269514722


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _wilson_lower(hits: int, n: int, z: float = Z90) -> float | None:
    if n <= 0:
        return None
    p = hits / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (center - radius) / den


def _accept(r: dict[str, Any], ht: float, at: float) -> bool:
    p = float(r["pmax"])
    return (r["pick"] == "home" and p >= ht) or (r["pick"] == "away" and p >= at)


def _eval(rows: list[dict[str, Any]], ht: float, at: float) -> dict[str, Any]:
    selected = [r for r in rows if _accept(r, ht, at)]
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
        "home_threshold": ht,
        "away_threshold": at,
        "by_direction": by_direction,
    }


def _choose(validation_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline = _eval(validation_rows, BASELINE_HOME, BASELINE_AWAY)
    candidates = []
    for ht in GRID:
        for at in GRID:
            e = _eval(validation_rows, ht, at)
            retention = e["count"] / baseline["count"] if baseline["count"] else 0.0
            e["coverage_retention_vs_062"] = retention
            e["admissible"] = (
                e["wilson90_lower"] is not None
                and e["count"] >= MIN_VALIDATION_SELECTED
                and e["by_direction"]["home"]["count"] >= MIN_VALIDATION_HOME
                and e["by_direction"]["away"]["count"] >= MIN_VALIDATION_AWAY
                and retention >= MIN_COVERAGE_RETENTION
            )
            candidates.append(e)
    admissible = [c for c in candidates if c["admissible"]]
    if not admissible:
        return baseline, candidates
    # Confidence-bound first to avoid chasing a raw hit-rate spike; then keep coverage.
    admissible.sort(
        key=lambda c: (c["wilson90_lower"], c["accuracy"], c["count"]),
        reverse=True,
    )
    return admissible[0], candidates


def _records(rows: list[dict[str, Any]], ht: float, at: float, fold: str) -> list[dict[str, Any]]:
    out = []
    for r in sorted(rows, key=lambda x: (x["date"], x["competition_id"], x["season"], x["row_index"])):
        accepted = _accept(r, ht, at)
        out.append({
            "fold": fold,
            "date": r["date"],
            "competition_id": r["competition_id"],
            "season": r["season"],
            "row_index": r["row_index"],
            "pick": r["pick"],
            "actual": r["actual"],
            "pmax": r["pmax"],
            "accepted": accepted,
            "correct": bool(accepted and r["pick"] == r["actual"]),
        })
    return out


def _record_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [r for r in records if r["accepted"]]
    hits = sum(1 for r in selected if r["correct"])
    n = len(selected)
    return {
        "all_match_count": len(records),
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90_lower": _wilson_lower(hits, n),
        "coverage": n / len(records) if records else 0.0,
    }


def _q10(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.10 * len(s)) - 1)]


def _windows(records: list[dict[str, Any]]) -> dict[str, Any]:
    windows = []
    for start in range(0, len(records) - WINDOW_SIZE + 1, WINDOW_SIZE):
        chunk = records[start:start + WINDOW_SIZE]
        s = _record_summary(chunk)
        s.update({
            "start": start,
            "stop": start + WINDOW_SIZE,
            "first_date": chunk[0]["date"],
            "last_date": chunk[-1]["date"],
            "power_status": "ELIGIBLE" if s["count"] >= MIN_SELECTED_WINDOW else "LOW_POWER",
        })
        windows.append(s)
    eligible = [w for w in windows if w["power_status"] == "ELIGIBLE" and w["accuracy"] is not None]
    acc = [float(w["accuracy"]) for w in eligible]
    return {
        "windows": windows,
        "summary": {
            "window_count": len(windows),
            "eligible_window_count": len(eligible),
            "eligible_window_share": len(eligible) / len(windows) if windows else 0.0,
            "worst_accuracy": min(acc) if acc else None,
            "q10_accuracy": _q10(acc),
            "median_accuracy": statistics.median(acc) if acc else None,
            "mean_accuracy": statistics.mean(acc) if acc else None,
            "windows_ge_70pct": sum(1 for x in acc if x >= 0.70),
            "windows_lt_65pct": sum(1 for x in acc if x < 0.65),
            "windows_lt_60pct": sum(1 for x in acc if x < 0.60),
        },
    }


def main() -> int:
    rows, providers = phase._read_rows()
    rows = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))

    fold_results = []
    adaptive_records: list[dict[str, Any]] = []
    fixed_records: list[dict[str, Any]] = []
    baseline_records: list[dict[str, Any]] = []

    for start_s, end_s in FOLDS:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        history = [r for r in rows if _d(r["date"]) < start]
        test = [r for r in rows if start <= _d(r["date"]) < end]
        if len(history) < 500 or len(test) < 300:
            raise RuntimeError(f"insufficient rows for fold {start_s}: history={len(history)} test={len(test)}")

        tail_n = max(300, int(len(history) * VALIDATION_TAIL_FRACTION))
        validation = history[-tail_n:]
        chosen, candidates = _choose(validation)
        ht = float(chosen["home_threshold"])
        at = float(chosen["away_threshold"])
        fold_name = f"{start_s}_to_{end_s}"

        adaptive_test = _eval(test, ht, at)
        fixed_test = _eval(test, FIXED_HOME, FIXED_AWAY)
        baseline_test = _eval(test, BASELINE_HOME, BASELINE_AWAY)
        fold_results.append({
            "fold": fold_name,
            "history_rows": len(history),
            "selection_validation_rows": len(validation),
            "test_rows": len(test),
            "selected_rule": chosen,
            "validation_admissible_count": sum(1 for c in candidates if c["admissible"]),
            "test_adaptive": adaptive_test,
            "test_fixed_064_060": fixed_test,
            "test_baseline_062_062": baseline_test,
        })
        adaptive_records.extend(_records(test, ht, at, fold_name))
        fixed_records.extend(_records(test, FIXED_HOME, FIXED_AWAY, fold_name))
        baseline_records.extend(_records(test, BASELINE_HOME, BASELINE_AWAY, fold_name))

    adaptive_summary = _record_summary(adaptive_records)
    fixed_summary = _record_summary(fixed_records)
    baseline_summary = _record_summary(baseline_records)
    adaptive_windows = _windows(adaptive_records)
    fixed_windows = _windows(fixed_records)
    baseline_windows = _windows(baseline_records)

    payload = {
        "schema_version": "V6.12.7-nested-calendar-walkforward-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_MARKET_RESEARCH_STRICT_TIME_FORWARD_SPLITS",
        "design": {
            "folds": [{"start": s, "end": e} for s, e in FOLDS],
            "validation_tail_fraction": VALIDATION_TAIL_FRACTION,
            "threshold_grid": list(GRID),
            "selection_objective": "maximize validation Wilson90 lower bound subject to >=80% retention and direction sample floors",
            "test_year_never_used_for_threshold_selection": True,
            "fixed_benchmark": {"home_threshold": FIXED_HOME, "away_threshold": FIXED_AWAY},
            "baseline_benchmark": {"home_threshold": BASELINE_HOME, "away_threshold": BASELINE_AWAY},
            "window_size": WINDOW_SIZE,
        },
        "provider_counts": providers,
        "fold_results": fold_results,
        "aggregate_adaptive": adaptive_summary,
        "aggregate_fixed_064_060": fixed_summary,
        "aggregate_baseline_062_062": baseline_summary,
        "aggregate_accuracy_diff_adaptive_vs_fixed": (
            adaptive_summary["accuracy"] - fixed_summary["accuracy"]
            if adaptive_summary["accuracy"] is not None and fixed_summary["accuracy"] is not None else None
        ),
        "aggregate_accuracy_diff_adaptive_vs_baseline": (
            adaptive_summary["accuracy"] - baseline_summary["accuracy"]
            if adaptive_summary["accuracy"] is not None and baseline_summary["accuracy"] is not None else None
        ),
        "adaptive_windows": adaptive_windows,
        "fixed_windows": fixed_windows,
        "baseline_windows": baseline_windows,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "strict_calendar_time_forward": True,
            "market_quotes_lack_original_timestamp": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "fold_results": fold_results,
        "aggregate_adaptive": adaptive_summary,
        "aggregate_fixed_064_060": fixed_summary,
        "aggregate_baseline_062_062": baseline_summary,
        "adaptive_windows": adaptive_windows["summary"],
        "fixed_windows": fixed_windows["summary"],
        "baseline_windows": baseline_windows["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
