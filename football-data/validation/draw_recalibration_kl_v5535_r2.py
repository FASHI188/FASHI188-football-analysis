#!/usr/bin/env python3
"""V5.5.35-r2 scope correction.

The r1 experiment accidentally allowed current partial calendar-year seasons into the
final holdout. This wrapper binds every competition to the same last-complete-season
policy used by the formal V4.7.0 baseline, then reruns the unchanged calibration and
KL projection logic. The r1 receipt is superseded for research conclusions.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import draw_recalibration_kl_v5535 as base
from backtest_last_complete_season_all_domains_v470 import _requested_last_complete_season
from platform_core import atomic_write_json, load_json

OUT = ROOT / "manifests" / "draw_recalibration_kl_v5535_r2_status.json"


def _completed_outer_seasons_last_complete_only(report: dict[str, Any]) -> list[str]:
    competition_id = str(report.get("competition_id") or "")
    if not competition_id:
        raise base.PlatformError("formal-core report missing competition_id")
    target = _requested_last_complete_season(competition_id)
    target_key = base._season_key(target)
    seasons: list[str] = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and base._season_key(season) <= target_key and season not in seasons:
            seasons.append(season)
    seasons.sort(key=base._season_key)
    if target not in seasons:
        raise base.PlatformError(
            f"last-complete-season fold missing for {competition_id}: expected {target}, got {seasons}"
        )
    return seasons


def main() -> int:
    base._completed_outer_seasons = _completed_outer_seasons_last_complete_only
    base.OUT = OUT
    rc = base.main()
    payload = load_json(OUT)
    payload["schema_version"] = "V5.5.35-draw-recalibration-kl-r2-last-complete-season"
    payload["generated_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload["scope_correction"] = {
        "r1_receipt_superseded": True,
        "r1_error": "current partial calendar-year seasons were admitted as untouched holdout",
        "corrected_policy": "only outer folds up to each domain's formal last-complete-season target",
        "expected_holdout_prediction_count": 4786,
        "current_partial_season_used": False,
    }
    actual = int((payload.get("row_counts") or {}).get("untouched_holdout", -1))
    if actual != 4786:
        payload["status"] = "FAIL_HOLDOUT_SCOPE_MISMATCH"
        payload.setdefault("failures", {})["holdout_scope"] = f"expected 4786, got {actual}"
        rc = 1
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload.get("status"),
        "holdout_count": actual,
        "result_status": (payload.get("result") or {}).get("status"),
        "baseline_accuracy": ((payload.get("baseline") or {}).get("untouched_holdout") or {}).get("accuracy"),
        "calibrated_accuracy": (((payload.get("result") or {}).get("holdout") or {}).get("accuracy")),
        "challenge_gate_passed": (payload.get("result") or {}).get("challenge_gate_passed"),
    }, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
