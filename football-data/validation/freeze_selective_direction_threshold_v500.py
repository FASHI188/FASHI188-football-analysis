#!/usr/bin/env python3
"""Freeze a V5.0 selective 1X2 direction threshold for ESP_LaLiga 2026/27.

The target-season threshold is chosen only from completed seasons strictly before
2026/27. The unseen target season contributes zero outcomes. The result is a
selection receipt only; a separate promotion/activation receipt is required before
runtime enforcement.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from one_x_two_70pct_forward_threshold_oof_v470 import (  # noqa: E402
    REPORT_ROOT,
    _completed_seasons,
    _season_rows,
    _select_threshold,
)
from platform_core import (  # noqa: E402
    PlatformError,
    atomic_write_json,
    load_json,
    read_processed_matches,
    sha256_file,
)

COMPETITION_ID = "ESP_LaLiga"
TARGET_SEASON = "2026/27"
FORWARD_RECEIPT = ROOT / "manifests" / "one_x_two_70pct_forward_threshold_oof_v470_status.json"
OUT = ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_selection.json"


def main() -> int:
    report = load_json(REPORT_ROOT / f"{COMPETITION_ID}.json")
    all_matches = read_processed_matches(COMPETITION_ID)
    completed = _completed_seasons(COMPETITION_ID, report)
    if len(completed) < 4:
        raise PlatformError(f"need at least four completed seasons, got {completed}")

    rows_by_season = {season: _season_rows(COMPETITION_ID, report, all_matches, season) for season in completed}
    selected = _select_threshold(rows_by_season)
    if selected is None:
        raise PlatformError("no threshold qualifies using strictly prior completed seasons")

    forward = load_json(FORWARD_RECEIPT)
    domain = (forward.get("reports") or {}).get(COMPETITION_ID) or {}
    forward_checks = domain.get("checks") or {}
    forward_pass = (
        forward.get("status") == "PASS"
        and domain.get("status") == "FORWARD_OOF_RESEARCH_CANDIDATE"
        and all(bool(forward_checks.get(key)) for key in (
            "at_least_two_forward_folds",
            "pooled_selected_at_least_60",
            "pooled_accuracy_at_least_70pct",
            "minimum_forward_season_accuracy_at_least_60pct",
            "forward_accuracy_std_at_most_10pp",
        ))
    )
    if not forward_pass:
        raise PlatformError("prior forward-frozen OOF evidence does not satisfy the V5 selection prerequisite")

    payload = {
        "schema_version": "V5.0.0-selective-direction-threshold-selection-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_id": COMPETITION_ID,
        "target_season": TARGET_SEASON,
        "selection_status": "FROZEN_FROM_STRICTLY_PRIOR_COMPLETED_SEASONS",
        "training_seasons": completed,
        "selected_threshold": float(selected["threshold"]),
        "prior_selection_stats": selected,
        "forward_oof_evidence": {
            "source": str(FORWARD_RECEIPT.relative_to(ROOT)),
            "source_sha256": sha256_file(FORWARD_RECEIPT),
            "evaluated_forward_fold_count": domain.get("evaluated_forward_fold_count"),
            "pooled_selected_count": domain.get("pooled_selected_count"),
            "pooled_accuracy": domain.get("pooled_accuracy"),
            "pooled_ci95_wilson": domain.get("pooled_ci95_wilson"),
            "forward_accuracy_min": domain.get("forward_accuracy_min"),
            "forward_accuracy_std": domain.get("forward_accuracy_std"),
        },
        "runtime_semantics": {
            "probability_mutation": False,
            "market_mutation": False,
            "direction_gate_only": True,
            "allow_formal_direction_when_top1_minus_top2_gap_at_least_threshold": True,
            "otherwise": "ABSTAIN",
        },
        "formal_promotion": False,
        "automatic_activation": False,
        "policy": "This receipt freezes a target-season threshold without target-season outcomes. It is not itself a formal runtime promotion."
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload["status"],
        "competition_id": COMPETITION_ID,
        "target_season": TARGET_SEASON,
        "selected_threshold": payload["selected_threshold"],
        "training_seasons": completed,
        "forward_pooled_accuracy": payload["forward_oof_evidence"]["pooled_accuracy"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
