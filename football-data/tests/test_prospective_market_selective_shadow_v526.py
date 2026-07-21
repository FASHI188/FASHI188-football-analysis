from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import canonical_sha256
from prospective_market_selective_shadow_v526 import evaluate


def _snapshot(competition_id: str, odds: tuple[float, float, float]):
    snapshot = {
        "competition_id": competition_id,
        "season": "2026/27",
        "home_team": "Home",
        "away_team": "Away",
        "kickoff_utc": "2026-08-15T16:30:00+00:00",
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": "2026-08-15T15:00:00+00:00",
        "accessed_at_utc": "2026-08-15T15:00:10+00:00",
        "source_observed_at_utc": "2026-08-15T14:59:55+00:00",
        "surface_observed_at_utc": {
            "one_x_two": "2026-08-15T14:59:50+00:00",
            "asian_handicap": "2026-08-15T14:59:55+00:00",
            "over_under": "2026-08-15T15:00:00+00:00"
        },
        "source_url": "https://example.invalid/market",
        "provider_name": "Example Market",
        "provider_group": "example_group",
        "one_x_two": {"home": odds[0], "draw": odds[1], "away": odds[2]},
        "asian_handicap": {"line": -0.25, "home": 1.95, "away": 1.95},
        "over_under": {"line": 2.75, "over": 1.93, "under": 1.97}
    }
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    return snapshot


def test_registered_domain_high_gap_emits_shadow_direction_only():
    result = evaluate(_snapshot("ESP_LaLiga", (1.35, 5.0, 8.0)))
    assert result["snapshot_contract_passed"] is True
    assert result["shadow_status"] == "SHADOW_MARKET_HIGH_CONFIDENCE_DIRECTION"
    assert result["shadow_direction"] == "home"
    assert result["formal_direction_override"] is False
    assert result["probability_mutation"] is False
    assert result["timing_robust_point_gate"] is True


def test_registered_domain_low_gap_does_not_emit_direction():
    result = evaluate(_snapshot("ESP_LaLiga", (2.20, 3.40, 3.20)))
    assert result["snapshot_contract_passed"] is True
    assert result["shadow_status"] == "SHADOW_GATE_NOT_MET"


def test_non_registered_domain_never_emits_shadow_direction():
    result = evaluate(_snapshot("ENG_PremierLeague", (1.20, 7.0, 12.0)))
    assert result["snapshot_contract_passed"] is True
    assert result["shadow_status"] == "DOMAIN_NOT_REGISTERED_FOR_MARKET_SELECTIVE_SHADOW"


def test_nor_and_sco_closing_only_hypotheses_are_not_runtime_candidates():
    for cid in ("NOR_Eliteserien", "SCO_Premiership"):
        result = evaluate(_snapshot(cid, (1.15, 8.0, 17.0)))
        assert result["snapshot_contract_passed"] is True
        assert result["registered_candidate_domain"] is False
        assert result["shadow_status"] == "DOMAIN_NOT_REGISTERED_FOR_MARKET_SELECTIVE_SHADOW"


def test_invalid_snapshot_fails_closed():
    snapshot = _snapshot("GER_Bundesliga", (1.35, 5.0, 8.0))
    snapshot["freeze_utc"] = "2026-08-15T17:00:00+00:00"
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    result = evaluate(snapshot)
    assert result["snapshot_contract_passed"] is False
    assert result["shadow_status"] == "SNAPSHOT_INVALID_FAIL_CLOSED"
