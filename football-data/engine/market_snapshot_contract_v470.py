#!/usr/bin/env python3
"""V4.7.0 staged market-snapshot and LOMO evidence contract.

This is an evidence validator, not an odds downloader. Historical data acquisition
requires an authorized external route. Formal EV remains unavailable until
competition-specific LOMO validation is completed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

REQUIRED_MARKETS = ("1X2", "AH", "OU")


def grade_snapshot(snapshot: dict[str, Any], *, max_window_seconds: int = 300) -> dict[str, Any]:
    markets = snapshot.get("markets") or {}
    present = {name: isinstance(markets.get(name), dict) and bool(markets.get(name)) for name in REQUIRED_MARKETS}
    timestamps = []
    complete_prices = True
    sources = []
    for name in REQUIRED_MARKETS:
        item = markets.get(name) or {}
        ts = item.get("timestamp_utc")
        if ts:
            try:
                parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                timestamps.append(parsed.astimezone(timezone.utc))
            except ValueError:
                complete_prices = False
        else:
            complete_prices = False
        prices = item.get("prices")
        if not isinstance(prices, dict) or len(prices) < 2:
            complete_prices = False
        source = item.get("source")
        if source:
            sources.append(str(source))
    synchronized = False
    if len(timestamps) == len(REQUIRED_MARKETS):
        synchronized = (max(timestamps) - min(timestamps)).total_seconds() <= max_window_seconds
    tradable = bool(snapshot.get("tradable", False))
    original_timestamp_verified = bool(snapshot.get("original_timestamp_verified", False))

    if all(present.values()) and complete_prices and synchronized and tradable and original_timestamp_verified:
        grade = "A"
    elif sum(present.values()) >= 2 and complete_prices:
        grade = "B"
    else:
        grade = "C"
    return {
        "grade": grade,
        "markets_present": present,
        "synchronized": synchronized,
        "complete_prices": complete_prices,
        "tradable": tradable,
        "original_timestamp_verified": original_timestamp_verified,
        "source_count": len(set(sources)),
        "formal_market_coordination_eligible": grade == "A",
        "formal_ev_eligible": False,
        "reason": "Independent competition-specific LOMO validation is still required for formal EV."
    }


def validate_lomo_projection_constraints(
    *,
    target_market: str,
    markets_used_for_projection: list[str],
) -> dict[str, Any]:
    normalized = {str(item).upper() for item in markets_used_for_projection}
    target = str(target_market).upper()
    excluded = target not in normalized
    return {
        "target_market": target,
        "markets_used_for_projection": sorted(normalized),
        "target_excluded": excluded,
        "valid_for_lomo_design": excluded,
        "formal_ev_eligible": False,
        "status": "DESIGN_VALID" if excluded else "INVALID_TARGET_LEAKAGE"
    }
