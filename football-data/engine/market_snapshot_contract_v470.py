#!/usr/bin/env python3
"""V4.7 staged market-snapshot and LOMO evidence contract.

This module validates evidence only; it never downloads odds and never makes
formal EV available by itself. Historical/live acquisition must arrive through
an authorized source route and competition-specific LOMO validation remains a
separate hard gate.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_CONFIG = ROOT / "config" / "evidence_sources_v470.json"
REQUIRED_MARKETS = ("1X2", "AH", "OU")
REQUIRED_PRICE_COUNTS = {"1X2": 3, "AH": 2, "OU": 2}


def _configured_max_market_skew_seconds() -> int:
    """Read the single configured synchronization tolerance, fail-closed on errors."""
    try:
        data = json.loads(EVIDENCE_CONFIG.read_text(encoding="utf-8"))
        value = int(data["policies"]["market"]["max_market_skew_seconds"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        # Conservative fallback only for validator availability; the result is
        # exposed in the audit so callers can see which tolerance was used.
        value = 300
    return max(0, value)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite_price_count(prices: Any) -> int:
    if not isinstance(prices, dict):
        return 0
    count = 0
    for value in prices.values():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 1.0:
            count += 1
    return count


def _finite_line(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _market_check(name: str, item: Any) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    present = bool(item)
    timestamp = _parse_timestamp(item.get("timestamp_utc")) if present else None
    required_prices = REQUIRED_PRICE_COUNTS[name]
    valid_price_count = _finite_price_count(item.get("prices")) if present else 0
    prices_complete = valid_price_count >= required_prices
    line_required = name in {"AH", "OU"}
    line_complete = _finite_line(item.get("line")) if line_required and present else not line_required
    source = str(item.get("source") or "").strip() if present else ""
    source_present = bool(source)
    complete = bool(
        present
        and timestamp is not None
        and prices_complete
        and line_complete
        and source_present
    )
    return {
        "present": present,
        "timestamp_valid": timestamp is not None,
        "timestamp_utc": timestamp.isoformat() if timestamp is not None else None,
        "required_price_count": required_prices,
        "valid_price_count": valid_price_count,
        "prices_complete": prices_complete,
        "line_required": line_required,
        "line_complete": line_complete,
        "source_present": source_present,
        "source": source or None,
        "complete": complete,
        "_timestamp": timestamp,
    }


def grade_snapshot(snapshot: dict[str, Any], *, max_window_seconds: int | None = None) -> dict[str, Any]:
    """Grade one synchronized 1X2/AH/OU market snapshot.

    A requires all three markets to be individually complete, synchronized,
    tradable, and backed by verified original quote timestamps.
    B means at least two individually complete timestamped markets are present;
    it is useful as contextual evidence but is never eligible for formal market
    coordination or EV.
    """
    markets = snapshot.get("markets") or {}
    checks = {name: _market_check(name, markets.get(name)) for name in REQUIRED_MARKETS}
    tolerance = _configured_max_market_skew_seconds() if max_window_seconds is None else max(0, int(max_window_seconds))

    complete_names = [name for name, check in checks.items() if check["complete"]]
    timestamps = [checks[name]["_timestamp"] for name in complete_names if checks[name]["_timestamp"] is not None]
    synchronized = False
    market_skew_seconds = None
    if len(complete_names) == len(REQUIRED_MARKETS) and len(timestamps) == len(REQUIRED_MARKETS):
        market_skew_seconds = (max(timestamps) - min(timestamps)).total_seconds()
        synchronized = market_skew_seconds <= tolerance

    tradable = bool(snapshot.get("tradable", False))
    original_timestamp_verified = bool(snapshot.get("original_timestamp_verified", False))
    all_complete = len(complete_names) == len(REQUIRED_MARKETS)
    complete_prices = all(checks[name]["prices_complete"] for name in REQUIRED_MARKETS)
    complete_lines = all(checks[name]["line_complete"] for name in REQUIRED_MARKETS)
    identifiable_sources = [checks[name]["source"] for name in REQUIRED_MARKETS if checks[name]["source"]]

    if all_complete and synchronized and tradable and original_timestamp_verified:
        grade = "A"
    elif len(complete_names) >= 2:
        grade = "B"
    else:
        grade = "C"

    public_checks = {
        name: {key: value for key, value in check.items() if key != "_timestamp"}
        for name, check in checks.items()
    }
    return {
        "grade": grade,
        "markets_present": {name: checks[name]["present"] for name in REQUIRED_MARKETS},
        "market_checks": public_checks,
        "complete_market_count": len(complete_names),
        "complete_markets": complete_names,
        "synchronized": synchronized,
        "market_skew_seconds": market_skew_seconds,
        "max_window_seconds_used": tolerance,
        "complete_prices": complete_prices,
        "complete_lines": complete_lines,
        "tradable": tradable,
        "original_timestamp_verified": original_timestamp_verified,
        "source_count": len(set(identifiable_sources)),
        "formal_market_coordination_eligible": grade == "A",
        "formal_ev_eligible": False,
        "reason": "Independent competition-specific LOMO validation is still required for formal EV.",
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
        "status": "DESIGN_VALID" if excluded else "INVALID_TARGET_LEAKAGE",
    }
