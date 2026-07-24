#!/usr/bin/env python3
"""Research-only cross-season audit of late-season selective 1X2 stability.

Purpose: test whether the end-of-season collapse seen in V6.12.2 is reproducible across
older seasons, without using the latest season to choose a phase rule. For each
competition, all eligible seasons except the latest form development; the latest season
is held out. The market probability threshold remains fixed at 0.62. Development may
choose only how much of the final season phase (0/5/10/15/20%) to exclude, subject to a
coverage-retention constraint. The chosen phase rule is then frozen and evaluated on
latest seasons.

All historical odds are retrospective references without original quote timestamps.
formal_weight=0; no CURRENT/runtime/promotion changes are permitted.
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from diagnose_1x2_market_anchor_v697 import _extract_odds
from platform_core import parse_match_date

OUT = ROOT / "manifests" / "v6_1x2_crossseason_phase_v6123_status.json"
THRESHOLD = 0.62
EXCLUDE_FINAL_FRACTIONS = (0.00, 0.05, 0.10, 0.15, 0.20)
MIN_SEASON_ROWS = 100
MIN_DEVELOPMENT_SELECTED = 300
MIN_COVERAGE_RETENTION = 0.80
WINDOW_SIZE = 100
MIN_SELECTED_WINDOW = 10
Z90 = 1.6448536269514722
DIRECTIONS = ("home", "draw", "away")


def _wilson_lower(hits: int, n: int, z: float = Z90) -> float | None:
    if n <= 0:
        return None
    p = hits / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (center - radius) / den


def _actual(raw: dict[str, str]) -> str | None:
    ftr = str(raw.get("FTR") or raw.get("Result") or "").strip().upper()
    if ftr in {"H", "HOME"}:
        return "home"
    if ftr in {"D", "DRAW"}:
        return "draw"
    if ftr in {"A", "AWAY"}:
        return "away"
    try:
        hg = int(float(str(raw.get("FTHG") or raw.get("HG") or "")))
        ag = int(float(str(raw.get("FTAG") or raw.get("AG") or "")))
    except (TypeError, ValueError):
        return None
    return "home" if hg > ag else "away" if ag > hg else "draw"


def _season_label(raw: dict[str, str], path: Path) -> str:
    return str(raw.get("season") or raw.get("Season") or path.stem).strip()


def _season_sort_key(label: str, first_date: str) -> tuple[int, str]:
    years = re.findall(r"(?:19|20)\d{2}", label)
    if years:
        return int(years[0]), first_date
    try:
        return int(first_date[:4]), first_date
    except Exception:
        return 0, first_date


def _read_rows() -> tuple[list[dict[str, Any]], dict[str, int]]:
    processed = ROOT / "processed"
    rows: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = defaultdict(int)
    for comp_dir in sorted(p for p in processed.iterdir() if p.is_dir()):
        cid = comp_dir.name
        for path in sorted(comp_dir.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row_index, raw0 in enumerate(csv.DictReader(handle)):
                    raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                    actual = _actual(raw)
                    extracted = _extract_odds(raw)
                    if actual is None or extracted is None:
                        continue
                    market, provider = extracted
                    season = _season_label(raw, path)
                    date_raw = str(raw.get("Date") or "").strip()
                    if not date_raw:
                        continue
                    try:
                        date_iso = parse_match_date(date_raw, season).isoformat()
                    except Exception:
                        continue
                    top = max(DIRECTIONS, key=lambda d: market[d])
                    rows.append(
                        {
                            "competition_id": cid,
                            "season": season,
                            "date": date_iso,
                            "row_index": row_index,
                            "actual": actual,
                            "pick": top,
                            "pmax": float(market[top]),
                            "provider": provider,
                        }
                    )
                    provider_counts[provider] += 1
    return rows, dict(provider_counts)


def _prepare_seasons(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["competition_id"], r["season"])].append(r)

    eligible: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    dropped_small = 0
    for (cid, season), group in groups.items():
        g = sorted(group, key=lambda r: (r["date"], r["row_index"]))
        if len(g) < MIN_SEASON_ROWS:
            dropped_small += 1
            continue
        n = len(g)
        for i, r in enumerate(g):
            r["season_progress"] = (i + 1) / n
        eligible[cid].append((season, g))

    development: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    split_meta: dict[str, Any] = {}
    for cid, seasons in sorted(eligible.items()):
        if len(seasons) < 2:
            continue
        seasons.sort(key=lambda item: _season_sort_key(item[0], item[1][0]["date"]))
        for season, g in seasons[:-1]:
            development.extend(g)
        latest_season, latest_rows = seasons[-1]
        holdout.extend(latest_rows)
        split_meta[cid] = {
            "development_seasons": [s for s, _ in seasons[:-1]],
            "holdout_season": latest_season,
            "development_rows": sum(len(g) for _, g in seasons[:-1]),
            "holdout_rows": len(latest_rows),
        }
    return development, holdout, {"competitions": split_meta, "dropped_small_season_groups": dropped_small}


def _eval(rows: list[dict[str, Any]], exclude_final_fraction: float) -> dict[str, Any]:
    max_progress = 1.0 - exclude_final_fraction
    selected = [
        r for r in rows
        if r["pmax"] >= THRESHOLD and float(r["season_progress"]) <= max_progress
    ]
    hits = sum(1 for r in selected if r["pick"] == r["actual"])
    n = len(selected)
    by_direction = {}
    for d in DIRECTIONS:
        sub = [r for r in selected if r["pick"] == d]
        h = sum(1 for r in sub if r["pick"] == r["actual"])
        by_direction[d] = {"count": len(sub), "hits": h, "accuracy": h / len(sub) if sub else None}
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90_lower": _wilson_lower(hits, n),
        "coverage": n / len(rows) if rows else 0.0,
        "exclude_final_fraction": exclude_final_fraction,
        "max_season_progress": max_progress,
        "by_direction": by_direction,
    }


def _choose_on_development(development: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = [_eval(development, x) for x in EXCLUDE_FINAL_FRACTIONS]
    baseline_count = candidates[0]["count"]
    admissible = [
        c for c in candidates
        if c["count"] >= MIN_DEVELOPMENT_SELECTED
        and (c["count"] / baseline_count if baseline_count else 0.0) >= MIN_COVERAGE_RETENTION
        and c["accuracy"] is not None
    ]
    if not admissible:
        return candidates[0], candidates
    # Accuracy first; ties retain more coverage and exclude less of the season.
    admissible.sort(key=lambda c: (c["accuracy"], c["count"], -c["exclude_final_fraction"]), reverse=True)
    return admissible[0], candidates


def _window_eval(rows: list[dict[str, Any]], exclude_final_fraction: float) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    windows = []
    for start in range(0, len(ordered) - WINDOW_SIZE + 1, WINDOW_SIZE):
        chunk = ordered[start:start + WINDOW_SIZE]
        result = _eval(chunk, exclude_final_fraction)
        result.update(
            {
                "start": start,
                "stop": start + WINDOW_SIZE,
                "first_date": chunk[0]["date"],
                "last_date": chunk[-1]["date"],
                "power_status": "ELIGIBLE" if result["count"] >= MIN_SELECTED_WINDOW else "LOW_POWER",
            }
        )
        windows.append(result)
    eligible = [w for w in windows if w["power_status"] == "ELIGIBLE" and w["accuracy"] is not None]
    acc = [float(w["accuracy"]) for w in eligible]
    return {
        "windows": windows,
        "summary": {
            "window_count": len(windows),
            "eligible_window_count": len(eligible),
            "worst_accuracy": min(acc) if acc else None,
            "median_accuracy": statistics.median(acc) if acc else None,
            "mean_accuracy": statistics.mean(acc) if acc else None,
            "windows_ge_70pct": sum(1 for x in acc if x >= 0.70),
            "windows_lt_60pct": sum(1 for x in acc if x < 0.60),
        },
    }


def main() -> int:
    rows, providers = _read_rows()
    development, holdout, split_meta = _prepare_seasons(rows)
    if not development or not holdout:
        raise RuntimeError("insufficient multi-season market rows for cross-season phase audit")

    chosen, development_grid = _choose_on_development(development)
    chosen_exclude = float(chosen["exclude_final_fraction"])
    holdout_baseline = _eval(holdout, 0.0)
    holdout_chosen = _eval(holdout, chosen_exclude)
    baseline_windows = _window_eval(holdout, 0.0)
    chosen_windows = _window_eval(holdout, chosen_exclude)

    payload = {
        "schema_version": "V6.12.3-crossseason-phase-stability-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "design": {
            "market_threshold_fixed": THRESHOLD,
            "development_rows": len(development),
            "holdout_rows": len(holdout),
            "latest_season_per_competition_untouched_for_phase_selection": True,
            "candidate_exclude_final_fractions": list(EXCLUDE_FINAL_FRACTIONS),
            "minimum_development_selected": MIN_DEVELOPMENT_SELECTED,
            "minimum_coverage_retention": MIN_COVERAGE_RETENTION,
            "window_size": WINDOW_SIZE,
        },
        "provider_counts": providers,
        "split": split_meta,
        "development_grid": development_grid,
        "development_selected_rule": chosen,
        "holdout_baseline": holdout_baseline,
        "holdout_selected_rule": holdout_chosen,
        "holdout_accuracy_difference": (
            holdout_chosen["accuracy"] - holdout_baseline["accuracy"]
            if holdout_chosen["accuracy"] is not None and holdout_baseline["accuracy"] is not None
            else None
        ),
        "holdout_baseline_windows": baseline_windows,
        "holdout_selected_rule_windows": chosen_windows,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "holdout_not_used_for_phase_rule_selection": True,
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
        "holdout_baseline": holdout_baseline,
        "holdout_selected_rule": holdout_chosen,
        "holdout_accuracy_difference": payload["holdout_accuracy_difference"],
        "baseline_window_summary": baseline_windows["summary"],
        "selected_window_summary": chosen_windows["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
