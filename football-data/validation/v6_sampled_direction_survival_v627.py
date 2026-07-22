#!/usr/bin/env python3
"""V6.2.7 calibration-only direction survival gate.

Consumes the frozen V6.2.5 r3 scored cache and the V6.2.6 thresholds.
A direction survives only when its V6.2.6 calibration subset itself reaches the 65% target.
The newer 850 rows are never consulted when deciding which direction survives.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "manifests" / "v6_sampled_17domain_scored_cache_v625_r3.json"
V626 = ROOT / "manifests" / "v6_sampled_asymmetric_65_gate_v626_status.json"
OUT = ROOT / "manifests" / "v6_sampled_direction_survival_v627_status.json"
TARGET = 0.65


def _wilson_lower(hits: int, count: int, z: float = 1.6448536269514722) -> float | None:
    import math
    if count <= 0:
        return None
    p = hits / count
    denom = 1.0 + z * z / count
    centre = p + z * z / (2.0 * count)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * count)) / count)
    return (centre - radius) / denom


def _metric(rows: list[dict[str, Any]], denominator: int = 850) -> dict[str, Any]:
    count = len(rows)
    hits = sum(int(bool(r["hit"])) for r in rows)
    return {
        "count": count,
        "hits": hits,
        "accuracy": hits / count if count else None,
        "wilson90_lower": _wilson_lower(hits, count),
        "coverage_of_850": count / denominator,
        "predicted_direction_counts": dict(Counter(str(r["pick"]) for r in rows)),
    }


def _selected(rows: list[dict[str, Any]], direction: str, threshold: float) -> list[dict[str, Any]]:
    return [
        r for r in rows
        if bool(r.get("eligible_prior_selective"))
        and r.get("pick") == direction
        and float(r.get("confidence", 0.0)) >= threshold
    ]


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    v626 = json.loads(V626.read_text(encoding="utf-8"))
    if cache.get("schema_version") != "V6.2.5-fixed-sampled-scored-cache-r3":
        raise SystemExit("unexpected scored-cache schema")
    if cache.get("count") != 1700:
        raise SystemExit("scored-cache count mismatch")
    rule = v626.get("calibration_selected_rule") or {}
    home_threshold = float(rule["home_threshold"])
    away_threshold = float(rule["away_threshold"])

    older = [r for r in cache["rows"] if r["role"] == "older"]
    newer = [r for r in cache["rows"] if r["role"] == "newer"]
    if len(older) != 850 or len(newer) != 850:
        raise SystemExit("role counts mismatch")

    calibration_by_direction: dict[str, Any] = {}
    survived: list[str] = []
    thresholds = {"home": home_threshold, "away": away_threshold}
    for direction in ("home", "away"):
        rows = _selected(older, direction, thresholds[direction])
        metric = _metric(rows)
        survives = bool(rows) and float(metric["accuracy"]) >= TARGET
        calibration_by_direction[direction] = {
            "threshold": thresholds[direction],
            **metric,
            "survives_65_calibration_gate": survives,
        }
        if survives:
            survived.append(direction)

    test_rows: list[dict[str, Any]] = []
    test_by_direction: dict[str, Any] = {}
    for direction in survived:
        rows = _selected(newer, direction, thresholds[direction])
        test_by_direction[direction] = {"threshold": thresholds[direction], **_metric(rows)}
        test_rows.extend(rows)
    test_metric = _metric(test_rows)

    payload = {
        "schema_version": "V6.2.7-sampled-direction-survival-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "design": {
            "source_panel": "V6.2.5 r3 fixed scored cache",
            "source_thresholds": "V6.2.6 calibration-selected asymmetric thresholds",
            "direction_survival_rule": "keep direction iff its older-850 selected subset raw accuracy >=65%",
            "newer_850_used_for_survival_decision": False,
            "target_accuracy": TARGET,
        },
        "calibration_by_direction": calibration_by_direction,
        "survived_directions": survived,
        "newer_850_by_survived_direction": test_by_direction,
        "newer_850_combined": {
            **test_metric,
            "target_65_raw_accuracy_met": bool(test_rows) and float(test_metric["accuracy"]) >= TARGET,
        },
        "comparison": {
            "v625_pooled_accuracy": 0.6391304347826087,
            "v625_pooled_coverage": 0.27058823529411763,
            "v626_asymmetric_accuracy": float((v626.get("newer_850_test") or {}).get("accuracy")),
            "v626_asymmetric_coverage": float((v626.get("newer_850_test") or {}).get("coverage_of_850")),
            "v627_accuracy": test_metric["accuracy"],
            "v627_coverage": test_metric["coverage_of_850"],
        },
        "governance": {
            "fast_development_gate_only": True,
            "calibration_only_direction_survival": True,
            "not_pristine_promotion_evidence": True,
            "current_rule_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "automatic_promotion": False,
            "v610_v613_pristine_forward_untouched": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
