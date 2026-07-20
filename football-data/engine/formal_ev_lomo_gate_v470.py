#!/usr/bin/env python3
"""Fail-closed V4.7 formal-EV LOMO gate with separate coordination-candidate semantics.

A complete synchronized question-time 1X2/AH/OU snapshot is sufficient to RUN an
auditable market-coordination candidate.  It is not sufficient to:
- mutate the formal probability centre; or
- activate formal EV / execution.

Those formal actions still require a competition- and season-specific LOMO/OOS
receipt.  This separation prevents a missing LOMO receipt from making the market
coordination module look as if no algorithm ran at all.
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
        return str(path)


def _validate_receipt(competition_id: str, season: str) -> dict[str, Any]:
    path = _receipt_path(competition_id)
    display_path = _display_path(path)
    if not path.exists():
        return {
            "status": "不可用",
            "reason": "competition-specific LOMO validation receipt missing",
            "receipt_path": display_path,
            "receipt_valid": False,
            "formal_ev_enabled": False,
            "market_coordination_enabled": False,
        }
    try:
        receipt = load_json(path)
    except Exception as exc:
        return {
            "status": "失败",
            "reason": f"LOMO receipt unreadable: {exc}",
            "receipt_path": display_path,
            "receipt_valid": False,
            "formal_ev_enabled": False,
            "market_coordination_enabled": False,
        }
    base_checks = {
        "status_validated": receipt.get("status") == EXPECTED_STATUS,
        "competition_match": str(receipt.get("competition_id") or "") == competition_id,
        "target_season_match": str(receipt.get("target_season") or "") == season,
        "automatic_promotion_disabled": receipt.get("automatic_promotion") is False,
    }
    base_valid = all(base_checks.values())
    ev_enabled = bool(base_valid and receipt.get("formal_ev_enabled") is True)
    coordination_enabled = bool(base_valid and receipt.get("market_coordination_enabled") is True)
    checks = {
        **base_checks,
        "formal_ev_enabled": receipt.get("formal_ev_enabled") is True,
        "market_coordination_enabled": receipt.get("market_coordination_enabled") is True,
    }
    return {
        "status": "通过" if base_valid else "不可用",
        "reason": "validated competition-specific LOMO receipt" if base_valid else "LOMO receipt failed one or more hard checks",
        "receipt_path": display_path,
        "receipt_sha256": sha256_file(path),
        "receipt_valid": base_valid,
        "formal_ev_enabled": ev_enabled,
        "market_coordination_enabled": coordination_enabled,
        "checks": checks,
    }


def apply_formal_ev_lomo_gate(context: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(context)
    identity = output.get("match_identity") or {}
    competition_id = str(identity.get("competition_id") or "")
    season = str(identity.get("season") or "")
    market = output.setdefault("market_assessment", {})
    snapshot_complete = _snapshot_complete(market)
    receipt_audit = _validate_receipt(competition_id, season)

    coordination_candidate_eligible = snapshot_complete
    formal_coordination_eligible = bool(snapshot_complete and receipt_audit.get("market_coordination_enabled"))
    formal_ev_eligible = bool(snapshot_complete and receipt_audit.get("formal_ev_enabled"))

    market["snapshot_complete_gate"] = snapshot_complete
    market["lomo_validation_status"] = receipt_audit["status"]
    market["market_coordination_candidate_gate"] = coordination_candidate_eligible
    market["formal_market_coordination_gate"] = formal_coordination_eligible
    market["ev_gate"] = formal_ev_eligible
    market["formal_ev_gate_reason"] = (
        "question-time market snapshot complete and competition-specific LOMO receipt validated"
        if formal_ev_eligible
        else (
            "question-time market snapshot incomplete"
            if not snapshot_complete
            else "competition-specific LOMO/OOS validation not formally available"
        )
    )

    gates = output.setdefault("gates", {})
    gates["market_snapshot_complete"] = snapshot_complete
    gates["market_coordination_candidate_may_run"] = coordination_candidate_eligible
    # Backward-compatible name now means formal probability mutation eligibility.
    gates["market_coordination_may_run"] = formal_coordination_eligible
    gates["formal_market_coordination_may_apply"] = formal_coordination_eligible
    gates["ev_may_be_calculated"] = formal_ev_eligible

    states = output.setdefault("module_states", {})
    states["synchronized_market"] = "通过" if snapshot_complete else market.get("status", "降级")
    # The actual coordination runtime overwrites this after a genuine optimization.
    states["market_coordination"] = "未启用" if snapshot_complete else "不可用"
    states["price_ev_no_bet"] = "未启用" if not formal_ev_eligible else "通过"

    output["market_lomo_gate_audit"] = {
        "status": "通过" if formal_ev_eligible else ("部分通过" if snapshot_complete else "不可用"),
        "competition_id": competition_id,
        "target_season": season,
        "question_time_snapshot_complete": snapshot_complete,
        "market_coordination_candidate_eligible": coordination_candidate_eligible,
        "formal_market_coordination_eligible": formal_coordination_eligible,
        "formal_ev_eligible": formal_ev_eligible,
        "receipt": receipt_audit,
        "probability_mutation": False,
        "price_mutation": False,
        "policy": (
            "A complete synchronized current snapshot may run an auditable KL coordination candidate. "
            "Formal probability mutation and EV remain competition/season LOMO-gated."
        ),
    }
    return output
