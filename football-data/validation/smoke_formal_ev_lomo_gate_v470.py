#!/usr/bin/env python3
"""Smoke audit for the fail-closed V4.7 formal EV/LOMO runtime gate."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import formal_ev_lomo_gate_v470 as gate
from platform_core import ROOT, atomic_write_json

OUT = ROOT / "manifests" / "formal_ev_lomo_gate_v470_smoke.json"


def _context(season: str = "2026"):
    return {
        "match_identity": {"competition_id": "USA_MLS", "season": season},
        "market_assessment": {
            "status": "通过",
            "error_codes": [],
            "complete_1x2": True,
            "complete_asian_handicap": True,
            "complete_total_goals": True,
            "synchronized": True,
            "tradable_prices": True,
            "ev_gate": True,
        },
        "module_states": {
            "synchronized_market": "通过",
            "market_coordination": "未启用",
            "price_ev_no_bet": "降级",
        },
        "gates": {"ev_may_be_calculated": True},
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        original_root = gate.RECEIPT_ROOT
        gate.RECEIPT_ROOT = Path(tmp)
        try:
            no_receipt = gate.apply_formal_ev_lomo_gate(_context("2026"))

            receipt = {
                "status": "LOMO_FORMAL_EV_VALIDATED",
                "competition_id": "USA_MLS",
                "target_season": "2026",
                "formal_ev_enabled": True,
                "market_coordination_enabled": True,
                "automatic_promotion": False,
            }
            (Path(tmp) / "USA_MLS.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            valid_receipt = gate.apply_formal_ev_lomo_gate(_context("2026"))
            wrong_season = gate.apply_formal_ev_lomo_gate(_context("2027"))
        finally:
            gate.RECEIPT_ROOT = original_root

    checks = {
        "complete_snapshot_without_lomo_keeps_ev_closed": no_receipt["gates"]["market_snapshot_complete"] is True and no_receipt["gates"]["ev_may_be_calculated"] is False,
        "complete_snapshot_without_lomo_allows_coordination_candidate": no_receipt["gates"]["market_coordination_candidate_may_run"] is True,
        "complete_snapshot_without_lomo_keeps_formal_coordination_closed": no_receipt["gates"]["formal_market_coordination_may_apply"] is False and no_receipt["gates"]["market_coordination_may_run"] is False,
        "valid_competition_season_receipt_opens_ev": valid_receipt["gates"]["ev_may_be_calculated"] is True,
        "valid_competition_season_receipt_opens_formal_market_coordination": valid_receipt["gates"]["formal_market_coordination_may_apply"] is True,
        "wrong_season_receipt_keeps_ev_closed": wrong_season["gates"]["ev_may_be_calculated"] is False,
        "wrong_season_still_allows_candidate_when_snapshot_complete": wrong_season["gates"]["market_coordination_candidate_may_run"] is True and wrong_season["gates"]["formal_market_coordination_may_apply"] is False,
        "no_probability_or_price_mutation_claimed": no_receipt["market_lomo_gate_audit"]["probability_mutation"] is False and no_receipt["market_lomo_gate_audit"]["price_mutation"] is False,
        "snapshot_quality_remains_distinct_from_ev": no_receipt["module_states"]["synchronized_market"] == "通过" and no_receipt["module_states"]["price_ev_no_bet"] == "未启用",
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "V4.7.0-formal-ev-lomo-gate-smoke-r2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "checks": checks,
        "no_receipt_audit": no_receipt["market_lomo_gate_audit"],
        "valid_receipt_audit": valid_receipt["market_lomo_gate_audit"],
        "wrong_season_audit": wrong_season["market_lomo_gate_audit"],
        "formal_weight_change": False,
        "production_receipt_created": False,
        "policy": "Smoke uses an isolated temporary receipt root. Coordination candidates may run without LOMO, but no production LOMO receipt or formal EV activation is created.",
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
