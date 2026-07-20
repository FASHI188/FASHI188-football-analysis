#!/usr/bin/env python3
"""Smoke audit for the hardened V4.7 synchronized market snapshot contract."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from market_snapshot_contract_v470 import grade_snapshot, validate_lomo_projection_constraints
from platform_core import ROOT, atomic_write_json

OUT = ROOT / "manifests" / "market_snapshot_contract_v470_smoke.json"


def _snapshot():
    return {
        "tradable": True,
        "original_timestamp_verified": True,
        "markets": {
            "1X2": {
                "timestamp_utc": "2026-07-20T10:00:00Z",
                "source": "bookmaker-A",
                "prices": {"home": 1.91, "draw": 3.55, "away": 4.20},
            },
            "AH": {
                "timestamp_utc": "2026-07-20T10:05:00Z",
                "source": "bookmaker-A",
                "line": -0.5,
                "prices": {"home": 1.95, "away": 1.95},
            },
            "OU": {
                "timestamp_utc": "2026-07-20T10:10:00Z",
                "source": "bookmaker-A",
                "line": 2.5,
                "prices": {"over": 1.90, "under": 2.00},
            },
        },
    }


def main() -> int:
    base = _snapshot()
    configured = grade_snapshot(base)

    missing_draw = _snapshot()
    missing_draw["markets"]["1X2"]["prices"].pop("draw")
    missing_draw_result = grade_snapshot(missing_draw)

    missing_ah_line = _snapshot()
    missing_ah_line["markets"]["AH"].pop("line")
    missing_ah_line_result = grade_snapshot(missing_ah_line)

    strict_300 = grade_snapshot(_snapshot(), max_window_seconds=300)
    lomo_valid = validate_lomo_projection_constraints(target_market="AH", markets_used_for_projection=["1X2", "OU"])
    lomo_leak = validate_lomo_projection_constraints(target_market="AH", markets_used_for_projection=["1X2", "AH", "OU"])

    checks = {
        "configured_900_second_window_is_A": configured["grade"] == "A" and configured["max_window_seconds_used"] == 900,
        "configured_skew_recorded_as_600": configured["market_skew_seconds"] == 600.0,
        "one_x_two_requires_three_prices": missing_draw_result["market_checks"]["1X2"]["prices_complete"] is False and missing_draw_result["grade"] != "A",
        "ah_requires_explicit_line": missing_ah_line_result["market_checks"]["AH"]["line_complete"] is False and missing_ah_line_result["grade"] != "A",
        "explicit_300_second_window_rejects_600_skew": strict_300["grade"] != "A" and strict_300["synchronized"] is False,
        "lomo_target_exclusion_valid": lomo_valid["status"] == "DESIGN_VALID" and lomo_valid["target_excluded"] is True,
        "lomo_target_leakage_rejected": lomo_leak["status"] == "INVALID_TARGET_LEAKAGE" and lomo_leak["target_excluded"] is False,
        "snapshot_never_enables_ev_without_lomo": all(result["formal_ev_eligible"] is False for result in (configured, missing_draw_result, missing_ah_line_result, strict_300)),
        "lomo_design_never_enables_ev_by_itself": lomo_valid["formal_ev_eligible"] is False and lomo_leak["formal_ev_eligible"] is False,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "V4.7.0-market-snapshot-contract-smoke-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "checks": checks,
        "configured_A_case": configured,
        "missing_1x2_price_case": missing_draw_result,
        "missing_ah_line_case": missing_ah_line_result,
        "strict_300_case": strict_300,
        "lomo_valid_case": lomo_valid,
        "lomo_leak_case": lomo_leak,
        "formal_weight_change": False,
        "formal_ev_available": False,
        "policy": "Validator hardening only; no market data were acquired and no formal EV or market weight was activated.",
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
