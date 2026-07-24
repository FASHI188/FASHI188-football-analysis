#!/usr/bin/env python3
"""Research-only 100-selected-prediction block audit for V6.12.7.

V6.12.7 originally reported 100-all-match windows. Because selective coverage is only
~15%, those windows often contain just 10-20 actual selections and therefore have very
high binomial variance. This audit answers the user's operational question directly:
how does the rule perform over consecutive blocks of 100 predictions that were actually
selected?

The exact V6.12.7 nested calendar folds and threshold-selection procedure are reused.
No test fold is used to choose its own threshold. Historical odds remain retrospective
closing-price references, so formal_weight=0 and no CURRENT/runtime changes are allowed.
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
import validate_1x2_nested_walkforward_v6127 as nested

OUT = ROOT / "manifests" / "v6_1x2_selected100_blocks_v6129_status.json"
BLOCK_SIZE = 100
ROLLING_STEP = 50


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _selected_records(rows: list[dict[str, Any]], ht: float, at: float, fold: str) -> list[dict[str, Any]]:
    return [r for r in nested._records(rows, ht, at, fold) if r["accepted"]]


def _block(records: list[dict[str, Any]], start: int, stop: int) -> dict[str, Any]:
    chunk = records[start:stop]
    hits = sum(1 for r in chunk if r["correct"])
    n = len(chunk)
    by_pick = {}
    for d in ("home", "away"):
        sub = [r for r in chunk if r["pick"] == d]
        h = sum(1 for r in sub if r["correct"])
        by_pick[d] = {"count": len(sub), "hits": h, "accuracy": h / len(sub) if sub else None}
    return {
        "start_selected_index": start,
        "stop_selected_index_exclusive": stop,
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "first_date": chunk[0]["date"] if chunk else None,
        "last_date": chunk[-1]["date"] if chunk else None,
        "first_fold": chunk[0]["fold"] if chunk else None,
        "last_fold": chunk[-1]["fold"] if chunk else None,
        "by_pick": by_pick,
    }


def _q(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, math.ceil(q * len(s)) - 1)
    return s[idx]


def _audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda r: (r["date"], r["fold"]))
    full_nonoverlap = [
        _block(ordered, start, start + BLOCK_SIZE)
        for start in range(0, len(ordered) - BLOCK_SIZE + 1, BLOCK_SIZE)
    ]
    rolling = [
        _block(ordered, start, start + BLOCK_SIZE)
        for start in range(0, len(ordered) - BLOCK_SIZE + 1, ROLLING_STEP)
    ]
    remainder_start = (len(ordered) // BLOCK_SIZE) * BLOCK_SIZE
    remainder = _block(ordered, remainder_start, len(ordered)) if remainder_start < len(ordered) else None

    def summarize(blocks: list[dict[str, Any]]) -> dict[str, Any]:
        acc = [float(b["accuracy"]) for b in blocks if b["accuracy"] is not None]
        return {
            "block_count": len(blocks),
            "worst_accuracy": min(acc) if acc else None,
            "q10_accuracy": _q(acc, 0.10),
            "median_accuracy": statistics.median(acc) if acc else None,
            "mean_accuracy": statistics.mean(acc) if acc else None,
            "best_accuracy": max(acc) if acc else None,
            "blocks_ge_75pct": sum(1 for x in acc if x >= 0.75),
            "blocks_ge_70pct": sum(1 for x in acc if x >= 0.70),
            "blocks_lt_70pct": sum(1 for x in acc if x < 0.70),
            "blocks_lt_65pct": sum(1 for x in acc if x < 0.65),
            "blocks_lt_60pct": sum(1 for x in acc if x < 0.60),
        }

    return {
        "selected_count": len(ordered),
        "full_nonoverlap_100": full_nonoverlap,
        "nonoverlap_summary": summarize(full_nonoverlap),
        "rolling_100_step50": rolling,
        "rolling_summary": summarize(rolling),
        "remainder": remainder,
    }


def main() -> int:
    rows, providers = phase._read_rows()
    rows = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))

    adaptive: list[dict[str, Any]] = []
    fixed: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    fold_rules = []

    for start_s, end_s in nested.FOLDS:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        history = [r for r in rows if _d(r["date"]) < start]
        test = [r for r in rows if start <= _d(r["date"]) < end]
        tail_n = max(300, int(len(history) * nested.VALIDATION_TAIL_FRACTION))
        validation = history[-tail_n:]
        chosen, _ = nested._choose(validation)
        ht = float(chosen["home_threshold"])
        at = float(chosen["away_threshold"])
        fold_name = f"{start_s}_to_{end_s}"
        adaptive.extend(_selected_records(test, ht, at, fold_name))
        fixed.extend(_selected_records(test, nested.FIXED_HOME, nested.FIXED_AWAY, fold_name))
        baseline.extend(_selected_records(test, nested.BASELINE_HOME, nested.BASELINE_AWAY, fold_name))
        fold_rules.append({
            "fold": fold_name,
            "home_threshold": ht,
            "away_threshold": at,
            "validation_selected_count": chosen["count"],
            "validation_accuracy": chosen["accuracy"],
            "test_adaptive_selected_count": sum(1 for r in adaptive if r["fold"] == fold_name),
        })

    payload = {
        "schema_version": "V6.12.9-selected100-block-stability-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_MARKET_RESEARCH_STRICT_TIME_FORWARD_SPLITS",
        "design": {
            "source_method": "V6.12.7 nested calendar walk-forward",
            "block_unit": "100_consecutive_selected_predictions_not_100_all_matches",
            "block_size": BLOCK_SIZE,
            "rolling_step": ROLLING_STEP,
            "test_fold_used_for_threshold_selection": False,
        },
        "provider_counts": providers,
        "fold_rules": fold_rules,
        "adaptive": _audit(adaptive),
        "fixed_064_060": _audit(fixed),
        "baseline_062_062": _audit(baseline),
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "market_quotes_lack_original_timestamp": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "adaptive_selected_count": payload["adaptive"]["selected_count"],
        "adaptive_nonoverlap": payload["adaptive"]["nonoverlap_summary"],
        "adaptive_rolling": payload["adaptive"]["rolling_summary"],
        "fixed_nonoverlap": payload["fixed_064_060"]["nonoverlap_summary"],
        "baseline_nonoverlap": payload["baseline_062_062"]["nonoverlap_summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
