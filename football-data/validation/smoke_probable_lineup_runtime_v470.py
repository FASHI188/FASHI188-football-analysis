#!/usr/bin/env python3
"""Smoke audit for the V4.7 auditable probable-lineup runtime layer."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, atomic_write_json
from probable_lineup_runtime_v470 import apply_probable_lineup_runtime

OUT = ROOT / "manifests" / "probable_lineup_runtime_v470_smoke.json"
PLAYERS = [f"P{i:02d}" for i in range(1, 14)]


def _context():
    return {
        "match_identity": {"freeze_time_utc": "2026-07-20T10:00:00+00:00"},
        "module_states": {"lineup_and_task": "部分通过"},
        "gates": {},
    }


def main() -> int:
    empty_probable = apply_probable_lineup_runtime(
        {"lineup_evidence": {"status": "probable", "sources": [{"name": "source-A"}]}},
        _context(),
    )

    external_xi = apply_probable_lineup_runtime(
        {
            "lineup_evidence": {
                "status": "probable",
                "sources": [{"name": "source-A"}],
                "predicted_starting_xi": PLAYERS[:11],
            }
        },
        _context(),
    )

    computed = apply_probable_lineup_runtime(
        {
            "lineup_evidence": {
                "status": "probable",
                "sources": [{"name": "source-A"}],
                "current_players": PLAYERS,
                "availability": {"P13": "out"},
                "observed_lineups": [
                    {"kickoff": "2026-07-01T10:00:00+00:00", "starting_xi": PLAYERS[:11]},
                    {"kickoff": "2026-07-10T10:00:00+00:00", "starting_xi": PLAYERS[1:12]},
                ],
            }
        },
        _context(),
    )

    official = apply_probable_lineup_runtime(
        {
            "lineup_evidence": {
                "status": "official",
                "sources": [{"name": "official-match-sheet"}],
                "starting_xi": PLAYERS[:11],
            }
        },
        _context(),
    )

    checks = {
        "bare_probable_status_not_treated_as_executed_projection": empty_probable["probable_lineup_v470_audit"]["status"] == "不可用" and not empty_probable["lineup_projection"]["starting_xi"],
        "bare_probable_status_downgrades_module_state": empty_probable["module_states"]["lineup_and_task"] == "警告",
        "external_predicted_xi_preserved_as_prediction": external_xi["probable_lineup_v470_audit"]["status"] == "部分通过" and external_xi["lineup_projection"]["observed_xi"] is False and len(external_xi["lineup_projection"]["starting_xi"]) == 11,
        "computed_probable_xi_uses_prior_same_season_lineups": computed["probable_lineup_v470_audit"]["projection_mode"] == "computed_same_season_probable_xi" and computed["probable_lineup_v470_audit"]["verified_same_season_lineups_used"] == 2 and len(computed["lineup_projection"]["starting_xi"]) == 11,
        "unavailable_player_not_selected_when_alternatives_exist": "P13" not in computed["lineup_projection"]["starting_xi"],
        "official_complete_xi_marked_observed": official["probable_lineup_v470_audit"]["status"] == "通过" and official["lineup_projection"]["observed_xi"] is True and len(official["lineup_projection"]["starting_xi"]) == 11,
        "numeric_lineup_effect_remains_zero": all(case["lineup_projection"]["formal_probability_effect_weight"] == 0 for case in (empty_probable, external_xi, computed, official)),
        "projection_layer_claims_no_probability_mutation": all(case["probable_lineup_v470_audit"]["probability_mutation"] is False for case in (empty_probable, external_xi, computed, official)),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "V4.7.0-probable-lineup-runtime-smoke-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "checks": checks,
        "empty_probable_audit": empty_probable["probable_lineup_v470_audit"],
        "external_predicted_audit": external_xi["probable_lineup_v470_audit"],
        "computed_audit": computed["probable_lineup_v470_audit"],
        "official_audit": official["probable_lineup_v470_audit"],
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Projection/evidence runtime only. Numeric lineup-to-score effects remain formal_weight=0 until competition-specific chronological OOF promotion.",
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
