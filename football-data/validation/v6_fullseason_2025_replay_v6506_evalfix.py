#!/usr/bin/env python3
"""Evaluation-only instrumentation for V6.50.6.

This wrapper changes no prediction, probability, selector, threshold, model parameter, or
training state. It monkey-patches only the 1X2 metric collector used by the existing
V6.50.6 replay, then post-processes its JSON receipt into explicit FULL_1X2 and
SELECTIVE_1X2 scorecards.

It is intentionally NOT executed by this correction commit. The V6.50.6 workflow is
manual-dispatch-only, so editing evaluation code cannot trigger the 5524-match replay.
"""
from __future__ import annotations

import json
from statistics import mean
from typing import Any

import v6_fullseason_2025_replay_v6506 as base

DIRECTIONS = base.DIRECTIONS
EPS = base.EPS
_ORIGINAL_X12 = base.x12_metrics


def _class_metrics(confusion: dict[str, dict[str, int]]) -> dict[str, Any]:
    precision: dict[str, float | None] = {}
    recall: dict[str, float | None] = {}
    f1: dict[str, float | None] = {}
    for label in DIRECTIONS:
        tp = int(confusion[label][label])
        pred_n = sum(int(confusion[label][actual]) for actual in DIRECTIONS)
        actual_n = sum(int(confusion[pred][label]) for pred in DIRECTIONS)
        p = tp / pred_n if pred_n else None
        r = tp / actual_n if actual_n else None
        if p is None or r is None:
            f = None
        elif p + r == 0.0:
            f = 0.0
        else:
            f = 2.0 * p * r / (p + r)
        precision[label] = p
        recall[label] = r
        f1[label] = f
    valid_f1 = [v for v in f1.values() if v is not None]
    valid_recall = [v for v in recall.values() if v is not None]
    return {
        "per_class_precision": precision,
        "per_class_recall": recall,
        "per_class_f1": f1,
        "macro_f1": mean(valid_f1) if valid_f1 else None,
        "balanced_accuracy": mean(valid_recall) if valid_recall else None,
    }


def _accuracy_coverage_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluation curve only; no threshold is selected from this curve."""
    scored = [
        r for r in rows
        if isinstance(r.get("selector"), dict)
        and r["selector"].get("reliability_score") is not None
    ]
    thresholds = sorted({float(r["selector"]["reliability_score"]) for r in scored})
    if 0.55 not in thresholds:
        thresholds.append(0.55)
        thresholds.sort()
    curve: list[dict[str, Any]] = []
    total = len(rows)
    for threshold in thresholds:
        selected = [r for r in scored if float(r["selector"]["reliability_score"]) >= threshold]
        n = len(selected)
        hits = sum(
            1
            for r in selected
            if str((r.get("selector") or {}).get("pick")) == str(r.get("actual"))
        )
        curve.append({
            "threshold": threshold,
            "executed_count": n,
            "total_count": total,
            "coverage": n / total if total else 0.0,
            "accuracy": hits / n if n else None,
        })
    return curve


def x12_metrics(rows: list[dict[str, Any]], key: str = "probabilities") -> dict[str, Any]:
    """Preserve existing scores and add auditable three-class classification metrics."""
    result = dict(_ORIGINAL_X12(rows, key))
    confusion = {
        pred: {actual: 0 for actual in DIRECTIONS}
        for pred in DIRECTIONS
    }
    for r in rows:
        p = {d: float(r[key][d]) for d in DIRECTIONS}
        actual = str(r["actual"])
        pick = max(DIRECTIONS, key=lambda d: p[d])
        if actual in DIRECTIONS:
            confusion[pick][actual] += 1
    result["confusion_matrix"] = confusion
    result.update(_class_metrics(confusion))
    if rows and any(isinstance(r.get("selector"), dict) for r in rows):
        result["accuracy_coverage_curve"] = _accuracy_coverage_curve(rows)
    return result


def _attach_scorecards(payload: dict[str, Any]) -> None:
    section = payload.get("f05_market_and_selector") or {}
    full = dict(section.get("market_all_available") or {})
    selective = dict(section.get("selector_selected") or {})
    market_n = int(full.get("count") or 0)
    executed_n = int(selective.get("count") or 0)

    full.update({
        "scorecard_type": "FULL_1X2",
        "abstain_allowed": False,
        "executed_count": market_n,
        "total_count": market_n,
        "coverage": 1.0 if market_n else 0.0,
    })
    selective.update({
        "scorecard_type": "SELECTIVE_1X2",
        "abstain_allowed": True,
        "executed_count": executed_n,
        "total_count": market_n,
        "coverage": executed_n / market_n if market_n else 0.0,
        "accuracy_coverage_curve": full.get("accuracy_coverage_curve", []),
    })

    section["FULL_1X2"] = full
    section["SELECTIVE_1X2"] = selective
    payload["f05_market_and_selector"] = section
    contract = payload.setdefault("target_contract", {})
    contract["evaluation_set_reuse_classification"] = "TIME_OUT_STYLE_REPEATED_RESEARCH_SET"
    contract["evaluation_set_label_zh"] = "时间外风格的重复研究集"
    contract["legacy_market_timestamp_classification"] = "RETROSPECTIVE_REFERENCE_ONLY"
    governance = payload.setdefault("governance", {})
    governance["full_and_selective_scorecards_must_not_be_mixed"] = True
    governance["live_fixed_time_reproducibility_proven"] = False


def main() -> int:
    base.x12_metrics = x12_metrics
    rc = base.main()
    payload = json.loads(base.OUT.read_text(encoding="utf-8"))
    _attach_scorecards(payload)
    base.OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
