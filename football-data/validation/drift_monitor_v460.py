#!/usr/bin/env python3
"""Sequential post-match drift monitor for the football formal core.

The monitor reconstructs recent forecast vectors from immutable freezes and
pairs them with post-match audits. It can suspend A eligibility on coverage or
1X2 calibration deterioration. Relative-log-score suspension requires a frozen
baseline forecast; when no baseline is available the monitor reports that gate
as unavailable rather than fabricating a comparison.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

import sys

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, PlatformError, load_json, utc_now  # noqa: E402

FREEZE_ROOT = ROOT / "prediction_freezes"
AUDIT_ROOT = ROOT / "postmatch_audits"
RECENT_WINDOW = 60


def _find_freezes(competition_id: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for path in FREEZE_ROOT.glob(f"*/{competition_id}/*.json"):
        item = load_json(path)
        if item.get("freeze_id"):
            output[str(item["freeze_id"])] = item
    return output


def _ece(records: list[dict[str, Any]], outcome: str, bins: int = 10) -> float | None:
    if not records:
        return None
    field = {"home": "p_home", "draw": "p_draw", "away": "p_away"}[outcome]
    error = 0.0
    used = 0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        subset = [r for r in records if low <= r[field] < high or (index == bins - 1 and r[field] == 1.0)]
        if not subset:
            continue
        forecast = mean(r[field] for r in subset)
        observed = mean(float(r["actual_outcome"] == outcome) for r in subset)
        error += len(subset) / len(records) * abs(forecast - observed)
        used += len(subset)
    return error if used else None


def monitor_competition(competition_id: str, window: int = RECENT_WINDOW) -> dict[str, Any]:
    freezes = _find_freezes(competition_id)
    paired: list[dict[str, Any]] = []
    for path in AUDIT_ROOT.glob(f"*/{competition_id}/*.json"):
        audit = load_json(path)
        freeze = freezes.get(str(audit.get("freeze_id")))
        if not freeze:
            continue
        probs = freeze.get("calculation_output", {}).get("probabilities", {})
        one = probs.get("one_x_two") or {}
        result = audit.get("result") or {}
        try:
            hg, ag = int(result["home_goals"]), int(result["away_goals"])
        except (KeyError, TypeError, ValueError):
            continue
        outcome = "home" if hg > ag else "draw" if hg == ag else "away"
        actual_score = f"{hg}-{ag}"
        set80 = freeze.get("calculation_output", {}).get("conclusions", {}).get("score_set_80", {}).get("scores", [])
        set90 = freeze.get("calculation_output", {}).get("conclusions", {}).get("score_set_90", {}).get("scores", [])
        paired.append({
            "audited_at_utc": audit.get("audited_at_utc", ""),
            "actual_outcome": outcome,
            "p_home": float(one.get("home", 0.0)),
            "p_draw": float(one.get("draw", 0.0)),
            "p_away": float(one.get("away", 0.0)),
            "joint_log_score": float(audit.get("scores", {}).get("exact_score", {}).get("log_score", 0.0)),
            "set80_covered": actual_score in {str(item.get("score")) for item in set80 if isinstance(item, dict)},
            "set90_covered": actual_score in {str(item.get("score")) for item in set90 if isinstance(item, dict)},
        })
    paired.sort(key=lambda item: item["audited_at_utc"])
    recent = paired[-max(1, int(window)):]
    if not recent:
        return {
            "schema_version": "V4.6.2",
            "competition_id": competition_id,
            "monitored_at_utc": utc_now(),
            "status": "INSUFFICIENT_RECENT_AUDITS",
            "recent_count": 0,
            "suspend_a": False,
            "reason": "No immutable freeze/post-match audit pairs are available.",
        }

    cover80 = mean(float(r["set80_covered"]) for r in recent)
    cover90 = mean(float(r["set90_covered"]) for r in recent)
    eces = {key: _ece(recent, key) for key in ("home", "draw", "away")}
    max_ece = max((value for value in eces.values() if value is not None), default=None)
    coverage_outside = not (0.76 <= cover80 <= 0.84 and 0.86 <= cover90 <= 0.94)
    ece_trigger = max_ece is not None and max_ece > 0.08
    suspend = bool(coverage_outside or ece_trigger)
    return {
        "schema_version": "V4.6.2",
        "competition_id": competition_id,
        "monitored_at_utc": utc_now(),
        "window": int(window),
        "recent_count": len(recent),
        "mean_joint_log_score": mean(r["joint_log_score"] for r in recent),
        "relative_log_score_vs_baseline": None,
        "relative_log_score_gate": "不可用：冻结产物尚未保存独立基准预测，禁止伪造相对基准漂移值",
        "score_set_80_coverage": cover80,
        "score_set_90_coverage": cover90,
        "one_x_two_ece": eces,
        "max_one_x_two_ece": max_ece,
        "coverage_trigger": coverage_outside,
        "ece_trigger": ece_trigger,
        "suspend_a": suspend,
        "status": "SUSPEND_A" if suspend else "MONITORING_NO_BASELINE_GATE",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--window", type=int, default=RECENT_WINDOW)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = monitor_competition(args.competition, args.window)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("suspend_a") else 0


if __name__ == "__main__":
    raise SystemExit(main())
