#!/usr/bin/env python3
"""Research-only sequential regime gate for selective 1X2.

Static market-confidence thresholds raise average hit rate but fail in some 100-match
regimes. This audit adds a strictly backward-looking circuit breaker: before each date,
check the hit rate of the previous K base-eligible picks of the same direction. When
that trailing accuracy falls below a development-selected cutoff, abstain for that
direction. Same-day outcomes are never used to gate other matches on the same date.

Parameters are selected only on older development seasons. Latest seasons are scored
sequentially after selection; their earlier resolved outcomes may inform later holdout
dates, which is forward-executable. Historical odds themselves lack original quote
timestamps, therefore this remains retrospective research with formal_weight=0.
"""
from __future__ import annotations

import json
import math
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

import validate_1x2_crossseason_phase_v6123 as phase

OUT = ROOT / "manifests" / "v6_1x2_dynamic_regime_gate_v6126_status.json"
HOME_THRESHOLD = 0.64
AWAY_THRESHOLD = 0.60
LOOKBACK_GRID = (20, 30, 50, 80, 100)
CUTOFF_GRID = (0.55, 0.60, 0.65, 0.70)
MODE_GRID = ("home", "away", "both")
WINDOW_SIZE = 100
MIN_SELECTED_WINDOW = 10
MIN_COVERAGE_RETENTION = 0.80
MIN_ELIGIBLE_WINDOW_SHARE = 0.80
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


def _is_base_candidate(r: dict[str, Any]) -> bool:
    p = float(r["pmax"])
    return (r["pick"] == "home" and p >= HOME_THRESHOLD) or (r["pick"] == "away" and p >= AWAY_THRESHOLD)


def _tag_rows(rows: list[dict[str, Any]], split_meta: dict[str, Any]) -> list[dict[str, Any]]:
    comp_meta = split_meta["competitions"]
    out = []
    for r in rows:
        meta = comp_meta.get(r["competition_id"])
        if not meta:
            continue
        season = r["season"]
        if season in set(meta["development_seasons"]):
            split = "development"
        elif season == meta["holdout_season"]:
            split = "holdout"
        else:
            continue
        x = dict(r)
        x["split"] = split
        out.append(x)
    return out


def _gate_applies(mode: str, direction: str) -> bool:
    return mode == "both" or mode == direction


def _simulate(
    history_stream: list[dict[str, Any]],
    score_split: str,
    lookback: int | None,
    cutoff: float | None,
    mode: str | None,
) -> list[dict[str, Any]]:
    ordered = sorted(history_stream, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    histories: dict[str, list[int]] = defaultdict(list)
    records: list[dict[str, Any]] = []
    i = 0
    while i < len(ordered):
        day = ordered[i]["date"]
        j = i
        while j < len(ordered) and ordered[j]["date"] == day:
            j += 1
        batch = ordered[i:j]

        # Decide all same-day matches before adding any same-day result to history.
        for r in batch:
            if r["split"] != score_split:
                continue
            base = _is_base_candidate(r)
            accepted = False
            trailing_accuracy = None
            if base:
                direction = r["pick"]
                if lookback is None or cutoff is None or mode is None or not _gate_applies(mode, direction):
                    accepted = True
                else:
                    hist = histories[direction]
                    if len(hist) < lookback:
                        accepted = True
                    else:
                        recent = hist[-lookback:]
                        trailing_accuracy = sum(recent) / lookback
                        accepted = trailing_accuracy >= cutoff
            records.append({
                "date": r["date"],
                "competition_id": r["competition_id"],
                "season": r["season"],
                "row_index": r["row_index"],
                "pick": r["pick"],
                "actual": r["actual"],
                "pmax": r["pmax"],
                "base_candidate": base,
                "accepted": accepted,
                "correct": bool(accepted and r["pick"] == r["actual"]),
                "trailing_accuracy_before_date": trailing_accuracy,
            })

        # All base-candidate outcomes become known after the date, even for abstained picks.
        for r in batch:
            if _is_base_candidate(r):
                histories[r["pick"]].append(1 if r["pick"] == r["actual"] else 0)
        i = j
    return records


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [r for r in records if r["accepted"]]
    hits = sum(1 for r in selected if r["correct"])
    n = len(selected)
    base_n = sum(1 for r in records if r["base_candidate"])
    by_direction = {}
    for d in ("home", "away"):
        sub = [r for r in selected if r["pick"] == d]
        h = sum(1 for r in sub if r["correct"])
        by_direction[d] = {"count": len(sub), "hits": h, "accuracy": h / len(sub) if sub else None}
    return {
        "base_candidate_count": base_n,
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90_lower": _wilson_lower(hits, n),
        "coverage_retention_vs_static_base": n / base_n if base_n else 0.0,
        "by_direction": by_direction,
    }


def _q10(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.10 * len(s)) - 1)]


def _window_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    windows = []
    for start in range(0, len(records) - WINDOW_SIZE + 1, WINDOW_SIZE):
        chunk = records[start:start + WINDOW_SIZE]
        s = _summary(chunk)
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
            "low_power_window_count": len(windows) - len(eligible),
            "worst_accuracy": min(acc) if acc else None,
            "q10_accuracy": _q10(acc),
            "median_accuracy": statistics.median(acc) if acc else None,
            "mean_accuracy": statistics.mean(acc) if acc else None,
            "windows_ge_70pct": sum(1 for x in acc if x >= 0.70),
            "windows_lt_65pct": sum(1 for x in acc if x < 0.65),
            "windows_lt_60pct": sum(1 for x in acc if x < 0.60),
        },
    }


def _candidate(dev_stream: list[dict[str, Any]], lookback: int, cutoff: float, mode: str) -> dict[str, Any]:
    records = _simulate(dev_stream, "development", lookback, cutoff, mode)
    overall = _summary(records)
    windows = _window_summary(records)["summary"]
    admissible = (
        overall["accuracy"] is not None
        and overall["coverage_retention_vs_static_base"] >= MIN_COVERAGE_RETENTION
        and windows["eligible_window_share"] >= MIN_ELIGIBLE_WINDOW_SHARE
        and windows["q10_accuracy"] is not None
    )
    return {
        "lookback": lookback,
        "cutoff": cutoff,
        "mode": mode,
        "overall": overall,
        "windows": windows,
        "admissible": admissible,
    }


def _choose(dev_stream: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = [_candidate(dev_stream, k, c, m) for k in LOOKBACK_GRID for c in CUTOFF_GRID for m in MODE_GRID]
    admissible = [x for x in candidates if x["admissible"]]
    if not admissible:
        raise RuntimeError("no admissible dynamic gate")
    # Stability first, using development only. Prefer high q10, then fewer collapses,
    # then worst-window accuracy, aggregate accuracy, and retained coverage.
    admissible.sort(
        key=lambda x: (
            x["windows"]["q10_accuracy"],
            -x["windows"]["windows_lt_60pct"],
            x["windows"]["worst_accuracy"],
            x["overall"]["accuracy"],
            x["overall"]["coverage_retention_vs_static_base"],
        ),
        reverse=True,
    )
    return admissible[0], candidates


def main() -> int:
    rows, providers = phase._read_rows()
    _, _, split_meta = phase._prepare_seasons(rows)
    tagged = _tag_rows(rows, split_meta)
    dev_stream = [r for r in tagged if r["split"] == "development"]
    if not dev_stream:
        raise RuntimeError("empty development stream")

    static_dev_records = _simulate(dev_stream, "development", None, None, None)
    static_dev = _summary(static_dev_records)
    static_dev_windows = _window_summary(static_dev_records)
    chosen, candidates = _choose(dev_stream)

    lookback = int(chosen["lookback"])
    cutoff = float(chosen["cutoff"])
    mode = str(chosen["mode"])

    # Holdout is replayed through the full eligible chronology. Earlier resolved holdout
    # results can inform later holdout dates; no same-day result can do so.
    static_holdout_records = _simulate(tagged, "holdout", None, None, None)
    dynamic_holdout_records = _simulate(tagged, "holdout", lookback, cutoff, mode)
    static_holdout = _summary(static_holdout_records)
    dynamic_holdout = _summary(dynamic_holdout_records)
    static_holdout_windows = _window_summary(static_holdout_records)
    dynamic_holdout_windows = _window_summary(dynamic_holdout_records)

    payload = {
        "schema_version": "V6.12.6-sequential-dynamic-regime-gate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "design": {
            "base_home_threshold": HOME_THRESHOLD,
            "base_away_threshold": AWAY_THRESHOLD,
            "lookback_grid": list(LOOKBACK_GRID),
            "cutoff_grid": list(CUTOFF_GRID),
            "mode_grid": list(MODE_GRID),
            "same_day_outcomes_used_for_same_day_gate": False,
            "history_updates_include_abstained_base_candidates_after_resolution": True,
            "holdout_used_for_gate_parameter_selection": False,
            "window_size": WINDOW_SIZE,
            "minimum_coverage_retention": MIN_COVERAGE_RETENTION,
            "minimum_eligible_window_share": MIN_ELIGIBLE_WINDOW_SHARE,
        },
        "provider_counts": providers,
        "split": split_meta,
        "development_static_base": static_dev,
        "development_static_windows": static_dev_windows,
        "development_selected_gate": chosen,
        "development_admissible_candidates": [x for x in candidates if x["admissible"]],
        "holdout_static_base": static_holdout,
        "holdout_dynamic_gate": dynamic_holdout,
        "holdout_accuracy_difference": (
            dynamic_holdout["accuracy"] - static_holdout["accuracy"]
            if dynamic_holdout["accuracy"] is not None and static_holdout["accuracy"] is not None else None
        ),
        "holdout_static_windows": static_holdout_windows,
        "holdout_dynamic_windows": dynamic_holdout_windows,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "sequential_gate_logic_is_past_only": True,
            "market_quotes_lack_original_timestamp": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "development_static_base": static_dev,
        "development_static_windows": static_dev_windows["summary"],
        "development_selected_gate": chosen,
        "holdout_static_base": static_holdout,
        "holdout_dynamic_gate": dynamic_holdout,
        "holdout_static_windows": static_holdout_windows["summary"],
        "holdout_dynamic_windows": dynamic_holdout_windows["summary"],
        "holdout_accuracy_difference": payload["holdout_accuracy_difference"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
