#!/usr/bin/env python3
"""Research-only rolling 100-match stability audit for selective market 1X2.

The primary threshold (market Top-1 probability >= 0.62) is inherited from the
validation-only 70% target gate in V6.10.3. This script does not re-fit that threshold
on the latest test outcomes. It asks whether the apparent high selective accuracy is
stable across chronological 100-match windows rather than only in aggregate.

Legacy historical market prices remain retrospective references because they do not
carry original quote timestamps. Therefore this audit has formal_weight=0 and cannot
change CURRENT, runtime probabilities, or promotion state.
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

import validate_1x2_selective_market_v6103 as base
from diagnose_1x2_market_anchor_v697 import _load_model_rows, _market_probs, _match_market

OUT = ROOT / "manifests" / "v6_1x2_rolling100_stability_v6122_status.json"
DIRECTIONS = ("home", "draw", "away")
PRIMARY_THRESHOLD = 0.62
DIAGNOSTIC_THRESHOLDS = (0.58, 0.60, 0.62, 0.65, 0.70)
WINDOW_SIZE = 100
ROLLING_STEP = 50
MIN_SELECTED_FOR_WINDOW = 12
Z90 = 1.6448536269514722


def _pick(row: dict[str, Any]) -> tuple[str, float]:
    p = _market_probs(row)
    order = sorted(DIRECTIONS, key=lambda k: p[k], reverse=True)
    return order[0], float(p[order[0]])


def _wilson_lower(hits: int, n: int, z: float = Z90) -> float | None:
    if n <= 0:
        return None
    phat = hits / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = phat + z2 / (2.0 * n)
    radius = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n)
    return (center - radius) / den


def _eval(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    selected = []
    for r in rows:
        direction, pmax = _pick(r)
        if pmax >= threshold:
            selected.append((r, direction, pmax))
    hits = sum(1 for r, direction, _ in selected if direction == r["actual"])
    n = len(selected)
    return {
        "all_market_count": len(rows),
        "selected_count": n,
        "coverage": n / len(rows) if rows else 0.0,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90_lower": _wilson_lower(hits, n),
        "direction_counts": {
            d: sum(1 for _, direction, _ in selected if direction == d) for d in DIRECTIONS
        },
    }


def _window_record(rows: list[dict[str, Any]], threshold: float, start: int, stop: int) -> dict[str, Any]:
    chunk = rows[start:stop]
    result = _eval(chunk, threshold)
    result.update(
        {
            "start_index": start,
            "stop_index_exclusive": stop,
            "first_date": chunk[0]["date"] if chunk else None,
            "last_date": chunk[-1]["date"] if chunk else None,
            "power_status": "ELIGIBLE" if result["selected_count"] >= MIN_SELECTED_FOR_WINDOW else "LOW_POWER",
        }
    )
    return result


def _summarize_windows(windows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [w for w in windows if w["power_status"] == "ELIGIBLE" and w["accuracy"] is not None]
    accuracies = [float(w["accuracy"]) for w in eligible]
    return {
        "window_count": len(windows),
        "eligible_window_count": len(eligible),
        "low_power_window_count": len(windows) - len(eligible),
        "minimum_selected_for_window": MIN_SELECTED_FOR_WINDOW,
        "worst_eligible_accuracy": min(accuracies) if accuracies else None,
        "median_eligible_accuracy": statistics.median(accuracies) if accuracies else None,
        "mean_eligible_accuracy": statistics.mean(accuracies) if accuracies else None,
        "best_eligible_accuracy": max(accuracies) if accuracies else None,
        "eligible_windows_ge_70pct": sum(1 for x in accuracies if x >= 0.70),
        "eligible_windows_ge_65pct": sum(1 for x in accuracies if x >= 0.65),
        "eligible_windows_lt_60pct": sum(1 for x in accuracies if x < 0.60),
    }


def main() -> int:
    matched, providers = _match_market(_load_model_rows())
    _, validation, latest_test = base._split(matched)
    latest_test = sorted(latest_test, key=lambda r: (r["date"], r["competition_id"], r["home_team"], r["away_team"]))

    if len(latest_test) < WINDOW_SIZE * 5:
        raise RuntimeError(f"insufficient latest-test rows for rolling stability audit: {len(latest_test)}")

    fixed = {}
    for threshold in DIAGNOSTIC_THRESHOLDS:
        nonoverlap = [
            _window_record(latest_test, threshold, start, start + WINDOW_SIZE)
            for start in range(0, len(latest_test) - WINDOW_SIZE + 1, WINDOW_SIZE)
        ]
        rolling = [
            _window_record(latest_test, threshold, start, start + WINDOW_SIZE)
            for start in range(0, len(latest_test) - WINDOW_SIZE + 1, ROLLING_STEP)
        ]
        fixed[f"{threshold:.2f}"] = {
            "aggregate_latest_test": _eval(latest_test, threshold),
            "nonoverlap_100": nonoverlap,
            "nonoverlap_summary": _summarize_windows(nonoverlap),
            "rolling_100_step50": rolling,
            "rolling_summary": _summarize_windows(rolling),
        }

    primary = fixed[f"{PRIMARY_THRESHOLD:.2f}"]
    primary_nonoverlap = primary["nonoverlap_summary"]
    stability_screen = {
        "threshold": PRIMARY_THRESHOLD,
        "pre_registered_source": "V6.10.3 validation-only target70 gate",
        "requires_at_least_7_eligible_nonoverlap_windows": primary_nonoverlap["eligible_window_count"] >= 7,
        "requires_no_eligible_window_below_60pct": primary_nonoverlap["eligible_windows_lt_60pct"] == 0,
        "requires_worst_eligible_accuracy_ge_65pct": (
            primary_nonoverlap["worst_eligible_accuracy"] is not None
            and primary_nonoverlap["worst_eligible_accuracy"] >= 0.65
        ),
    }
    stability_screen["passed"] = all(
        stability_screen[k]
        for k in (
            "requires_at_least_7_eligible_nonoverlap_windows",
            "requires_no_eligible_window_below_60pct",
            "requires_worst_eligible_accuracy_ge_65pct",
        )
    )

    payload = {
        "schema_version": "V6.12.2-selective-market-rolling100-stability-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "design": {
            "matched_rows": len(matched),
            "validation_rows": len(validation),
            "latest_test_rows": len(latest_test),
            "primary_threshold": PRIMARY_THRESHOLD,
            "threshold_not_refit_on_latest_test": True,
            "window_size_all_market_matches": WINDOW_SIZE,
            "nonoverlap_step": WINDOW_SIZE,
            "rolling_step": ROLLING_STEP,
            "minimum_selected_for_window": MIN_SELECTED_FOR_WINDOW,
        },
        "provider_counts": providers,
        "thresholds": fixed,
        "primary_stability_screen": stability_screen,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "test_outcomes_do_not_refit_primary_threshold": True,
            "diagnostic_threshold_comparison_not_promotion_evidence": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"primary_stability_screen": stability_screen, "primary": primary_nonoverlap}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
