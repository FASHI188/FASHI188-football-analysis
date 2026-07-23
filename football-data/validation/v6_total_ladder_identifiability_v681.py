#!/usr/bin/env python3
"""V6.8.1 strict total-goals identifiability audit from full-time market ladders.

A single O/U line cannot identify P(T=0)..P(T=7+). Standard half-goal lines map directly to
CDF points: Under(k+0.5) = P(T<=k) after de-vigging. Exact 0..7+ is declared market-identified
only when every half-goal line 0.5..6.5 is present and the de-vigged CDF is monotone.
Otherwise only observed cumulative constraints are exposed; missing integer buckets are never
fabricated. This audit never smooths a non-monotone ladder into artificial probabilities.

The receipt also binds itself to the exact V6.8.0 source bundle count and generation timestamp.
That makes stale downstream receipts directly auditable instead of silently looking current.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LADDERS = ROOT / "evidence" / "market_ladders_v680" / "kambi_full_time_ladders.json"
OUT = ROOT / "manifests" / "v6_total_ladder_identifiability_v681_status.json"
REQUIRED = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5]
EPS = 1e-12


def devig_two(over: float, under: float) -> tuple[float, float]:
    io, iu = 1.0 / float(over), 1.0 / float(under)
    total = io + iu
    return io / total, iu / total


def half_line(value: float) -> bool:
    return abs((float(value) - 0.5) - round(float(value) - 0.5)) <= 1e-9


def cdf_from_bundle(bundle: dict[str, Any]) -> dict[float, float]:
    points: dict[float, float] = {}
    offers = [
        row
        for row in bundle.get("total_goal_ladder") or []
        if row.get("market_kind") == "total_goals" and half_line(float(row.get("line")))
    ]
    for row in offers:
        line = float(row["line"])
        _p_over, p_under = devig_two(float(row["over"]), float(row["under"]))
        existing = points.get(line)
        if existing is None or bool(row.get("main_line")):
            points[line] = p_under
    return dict(sorted(points.items()))


def exact_distribution(points: dict[float, float]) -> dict[str, float] | None:
    if any(line not in points for line in REQUIRED):
        return None
    cdf = [points[line] for line in REQUIRED]
    if any(cdf[i] > cdf[i + 1] + EPS for i in range(len(cdf) - 1)):
        return None
    probs = [cdf[0]] + [cdf[i] - cdf[i - 1] for i in range(1, 7)] + [1.0 - cdf[6]]
    if min(probs) < -EPS:
        return None
    probs = [max(0.0, value) for value in probs]
    total = sum(probs)
    if total <= 0:
        return None
    probs = [value / total for value in probs]
    return {key: probs[idx] for idx, key in enumerate(["0", "1", "2", "3", "4", "5", "6", "7+"])}


def analyze(bundle: dict[str, Any]) -> dict[str, Any]:
    points = cdf_from_bundle(bundle)
    lines = list(points)
    monotone = all(points[lines[i]] <= points[lines[i + 1]] + EPS for i in range(len(lines) - 1))
    distribution = exact_distribution(points)
    return {
        "event_id": bundle.get("event_id"),
        "home_team_source": bundle.get("home_team_source"),
        "away_team_source": bundle.get("away_team_source"),
        "kickoff_utc": bundle.get("kickoff_utc"),
        "observed_at_utc": bundle.get("observed_at_utc"),
        "half_goal_cdf_points": {str(line): points[line] for line in lines},
        "half_goal_line_count": len(lines),
        "monotone": monotone,
        "missing_required_lines": [line for line in REQUIRED if line not in points],
        "exact_0_7plus_market_identifiable": distribution is not None,
        "exact_0_7plus_distribution": distribution,
        "operational_output": "EXACT_0_7PLUS_AVAILABLE"
        if distribution is not None
        else ("PARTIAL_CDF_ONLY" if points and monotone else "LADDER_UNUSABLE"),
    }


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not LADDERS.exists():
        payload = {
            "schema_version": "V6.8.1-total-ladder-identifiability-r2",
            "status": "WAITING_FOR_V680_LADDERS",
            "generated_at_utc": now,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    source = json.loads(LADDERS.read_text(encoding="utf-8"))
    source_bundles = source.get("bundles") or []
    declared_source_bundle_count = int(source.get("bundle_count") or 0)
    actual_source_bundle_count = len(source_bundles)
    if declared_source_bundle_count != actual_source_bundle_count:
        payload = {
            "schema_version": "V6.8.1-total-ladder-identifiability-r2",
            "generated_at_utc": now,
            "status": "FAIL_SOURCE_BUNDLE_COUNT_MISMATCH",
            "source_ladder_generated_at_utc": source.get("generated_at_utc"),
            "source_declared_bundle_count": declared_source_bundle_count,
            "source_actual_bundle_count": actual_source_bundle_count,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    results = [analyze(bundle) for bundle in source_bundles]
    counts = Counter(row["operational_output"] for row in results)
    line_counts = Counter()
    monotone_failures = 0
    for row in results:
        monotone_failures += int(not row["monotone"])
        for line in row["half_goal_cdf_points"]:
            line_counts[line] += 1

    payload = {
        "schema_version": "V6.8.1-total-ladder-identifiability-r2",
        "generated_at_utc": now,
        "status": "PASS",
        "source_ladder_generated_at_utc": source.get("generated_at_utc"),
        "source_declared_bundle_count": declared_source_bundle_count,
        "source_actual_bundle_count": actual_source_bundle_count,
        "source_bundle_count_matches": declared_source_bundle_count == actual_source_bundle_count,
        "bundle_count": len(results),
        "operational_counts": dict(counts),
        "required_exact_lines": REQUIRED,
        "line_coverage_counts": dict(sorted(line_counts.items(), key=lambda item: float(item[0]))),
        "monotonicity_failure_count": monotone_failures,
        "results": results,
        "policy": {
            "single_ou_line_never_identifies_0_7plus": True,
            "missing_buckets_never_fabricated": True,
            "exact_total_distribution_requires_all_half_lines_0_5_to_6_5": True,
            "asian_quarter_lines_not_treated_as_binary_probabilities": True,
            "source_bundle_count_is_hard_bound": True,
            "research_only": True,
            "no_current_rule_change": True,
            "no_formal_probability_change": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "status",
                    "source_ladder_generated_at_utc",
                    "source_bundle_count_matches",
                    "bundle_count",
                    "operational_counts",
                    "line_coverage_counts",
                    "monotonicity_failure_count",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
