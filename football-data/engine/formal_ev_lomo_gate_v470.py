#!/usr/bin/env python3
"""Fail-closed V4.7 formal-EV and market-coordination LOMO gate.

A complete question-time 1X2/AH/OU snapshot is necessary but not sufficient for
formal EV.  The active evidence policy additionally requires competition-specific
LOMO/OOS validation.  Until an explicit signed receipt exists for the target
competition and season, this gate keeps formal EV and market coordination closed.

This module does not alter model probabilities or market prices.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from platform_core import ROOT, load_json, sha256_file

RECEIPT_ROOT = ROOT / "manifests" / "market_lomo"
EXPECTED_STATUS = "LOMO_FORMAL_EV_VALIDATED"


def _snapshot_complete(market: dict[str, Any]) -> bool:
    return bool(
        market.get("complete_1x2")
        and market.get("complete_asian_handicap")
        and market.get("complete_total_goals")
        and market.get("synchronized")
        and market.get("tradable_prices")
        and not market.get("error_codes")
    )


def _receipt_path(competition_id: str) -> Path:
    return RECEIPT_ROOT / f"{competition_id}.json"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        # Test harnesses may deliberately redirect RECEIPT_ROOT outside the
        # repository. Production receipts still live under ROOT.
        return str(path)


def _validate_receipt(competition_id: str, season: str) -> tuple[bool, dict[str, Any]]:
    path = _receipt_path(competition_id)
    display_path = _display_path(path)
    if not path.exists():
        return False, {
            "status": "不可用",
            "reason": "competition-specific LOMO validation receipt missing",
            "receipt_path": display_path,
        }
    try:
        receipt = load_json(path)
    except Exception as exc:
        return False, {
            "status": "失败",
            "reason": f"LOMO receipt unreadable: {exc}",
            "receipt_path": display_path,
        }
    checks = {
        "status_validated": receipt.get("status") == EXPECTED_STATUS,
        "competition_match": str(receipt.get("competition_id") or "") == competition_id,
        "target_season_match": str(receipt.get("target_season") or "") == season,
        "formal_ev_enabled": receipt.get("formal_ev_enabled") is True,
        "market_coordination_enabled": receipt.get("market_coordination_enabled") is True,
        "automatic_promotion_disabled": receipt.get("automatic_promotion") is False,
    }
    valid = all(checks.values())
    return valid, {
        "status": "通过" if valid else "不可用",
        "reason": "validated competition-specific LOMO receipt" if valid else "LOMO receipt failed one or more hard checks",
        "receipt_path": display_path,
        "receipt_sha256": sha256_file(path),
        "checks": checks,
    }


def apply_formal_ev_lomo_gate(context: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(context)
    identity = output.get("match_identity") or {}
    competition_id = str(identity.get("competition_id") or "")
    season = str(identity.get("season") or "")
    market = output.setdefault("market_assessment", {})
    snapshot_complete = _snapshot_complete(market)
    receipt_valid, receipt_audit = _validate_receipt(competition_id, season)
    formal_eligible = bool(snapshot_complete and receipt_valid)

    market["snapshot_complete_gate"] = snapshot_complete
    market["lomo_validation_status"] = receipt_audit["status"]
    market["formal_market_coordination_gate"] = formal_eligible
    market["ev_gate"] = formal_eligible
    market["formal_ev_gate_reason"] = (
        "question-time market snapshot complete and competition-specific LOMO receipt validated"
        if formal_eligible
        else (
            "question-time market snapshot incomplete"
            if not snapshot_complete
            else "competition-specific LOMO/OOS validation not formally available"
        )
    )

    gates = output.setdefault("gates", {})
    gates["market_snapshot_complete"] = snapshot_complete
    gates["market_coordination_may_run"] = formal_eligible
    gates["ev_may_be_calculated"] = formal_eligible

    states = output.setdefault("module_states", {})
    # Snapshot quality and formal market coordination are distinct states.
    states["synchronized_market"] = "通过" if snapshot_complete else market.get("status", "降级")
    states["market_coordination"] = "未启用" if not formal_eligible else "通过"
    states["price_ev_no_bet"] = "未启用" if not formal_eligible else "通过"

    output["market_lomo_gate_audit"] = {
        "status": "通过" if formal_eligible else "未启用",
        "competition_id": competition_id,
        "target_season": season,
        "question_time_snapshot_complete": snapshot_complete,
        "formal_market_coordination_eligible": formal_eligible,
        "formal_ev_eligible": formal_eligible,
        "receipt": receipt_audit,
        "probability_mutation": False,
        "price_mutation": False,
        "policy": "Complete current prices alone never activate formal EV; competition-specific LOMO/OOS receipt is mandatory.",
    }
    return output
