from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_v554 import build
from prospective_market_matrix_outcome_v548 import score as score_matrix
from prospective_market_selective_outcome_v552 import score as score_selective
from prospective_market_snapshot_v523 import canonical_sha256


def _matrix():
    raw = {
        (0, 0): 0.10, (1, 0): 0.14, (0, 1): 0.10, (1, 1): 0.16,
        (2, 0): 0.10, (0, 2): 0.08, (2, 1): 0.12, (1, 2): 0.08,
        (2, 2): 0.07, (3, 1): 0.05,
    }
    return [{"home_goals": h, "away_goals": a, "probability": p} for (h, a), p in raw.items()]


def _snapshot(cid: str, group: str, minute: int):
    ts = f"2026-08-15T15:0{minute}:00+00:00"
    row = {
        "competition_id": cid,
        "season": "2026/27",
        "home_team": "Home",
        "away_team": "Away",
        "kickoff_utc": "2026-08-15T16:30:00+00:00",
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": ts,
        "accessed_at_utc": ts,
        "source_observed_at_utc": ts,
        "surface_observed_at_utc": {"one_x_two": ts, "asian_handicap": ts, "over_under": ts},
        "source_url": f"https://example.invalid/{group}",
        "provider_name": group,
        "provider_group": group,
        "one_x_two": {"home": 1.35, "draw": 5.0, "away": 8.0},
        "asian_handicap": {"line": -1.0, "home": 1.95, "away": 1.95},
        "over_under": {"line": 2.5, "over": 1.91, "under": 1.99},
    }
    row["raw_snapshot_sha256"] = canonical_sha256(row)
    return row


def _consensus(cid: str):
    return build([_snapshot(cid, "book_a", 0), _snapshot(cid, "book_b", 1)])


def test_ger_matrix_consensus_outcome_is_promotion_eligible():
    row = score_matrix(_consensus("GER_Bundesliga"), _matrix(), 2, 0)
    assert row["status"] == "SCORED_SHADOW_ROW"
    assert row["market_input_kind"] == "INDEPENDENT_PROVIDER_CONSENSUS"
    assert row["promotion_evidence_eligible"] is True
    assert row["provider_count"] == 2
    assert row["profile"] == "minimum_KL_IPF_1x2_plus_ou25"


def test_single_provider_matrix_outcome_is_diagnostic_only():
    row = score_matrix(_snapshot("GER_Bundesliga", "book_a", 0), _matrix(), 2, 0)
    assert row["status"] == "SCORED_SHADOW_ROW"
    assert row["market_input_kind"] == "SINGLE_PROVIDER_SNAPSHOT_DIAGNOSTIC"
    assert row["promotion_evidence_eligible"] is False


def test_esp_selective_consensus_outcome_is_promotion_eligible_and_selected():
    row = score_selective(_consensus("ESP_LaLiga"), {"home": 0.62, "draw": 0.22, "away": 0.16}, "home")
    assert row["status"] == "SCORED_SELECTIVE_ROW"
    assert row["market_input_kind"] == "INDEPENDENT_PROVIDER_CONSENSUS"
    assert row["promotion_evidence_eligible"] is True
    assert row["selected_by_shadow_gate"] is True
    assert row["market_direction"] == "home"


def test_single_provider_selective_outcome_is_diagnostic_only():
    row = score_selective(_snapshot("ESP_LaLiga", "book_a", 0), {"home": 0.62, "draw": 0.22, "away": 0.16}, "home")
    assert row["status"] == "SCORED_SELECTIVE_ROW"
    assert row["promotion_evidence_eligible"] is False
