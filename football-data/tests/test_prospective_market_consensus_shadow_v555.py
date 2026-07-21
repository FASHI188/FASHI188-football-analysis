from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_v554 import build
from prospective_market_consensus_shadow_v555 import evaluate_matrix, evaluate_selective
from prospective_market_snapshot_v523 import canonical_sha256


def _matrix():
    raw = {
        (0, 0): 0.10, (1, 0): 0.14, (0, 1): 0.10, (1, 1): 0.16,
        (2, 0): 0.10, (0, 2): 0.08, (2, 1): 0.12, (1, 2): 0.08,
        (2, 2): 0.07, (3, 1): 0.05,
    }
    return [{"home_goals": h, "away_goals": a, "probability": p} for (h, a), p in raw.items()]


def _snapshot(cid: str, group: str, minute: int, *, ou_line: float = 2.5):
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
        "over_under": {"line": ou_line, "over": 1.91, "under": 1.99},
    }
    row["raw_snapshot_sha256"] = canonical_sha256(row)
    return row


def _consensus(cid: str, *, ou_lines=(2.5, 2.5)):
    return build([
        _snapshot(cid, "book_a", 0, ou_line=ou_lines[0]),
        _snapshot(cid, "book_b", 1, ou_line=ou_lines[1]),
    ])


def test_ger_consensus_dual_surface_matrix_is_ready():
    result = evaluate_matrix(_consensus("GER_Bundesliga"), _matrix())
    assert result["shadow_status"] == "SHADOW_MARKET_MATRIX_READY"
    assert result["market_input_kind"] == "INDEPENDENT_PROVIDER_CONSENSUS"
    assert result["provider_count"] == 2
    assert result["audit"]["converged"] is True
    assert result["audit"]["max_constraint_residual"] <= 1e-10


def test_por_consensus_routes_to_1x2_only_even_when_ou_is_not_25():
    result = evaluate_matrix(_consensus("POR_PrimeiraLiga", ou_lines=(2.75, 2.75)), _matrix())
    assert result["shadow_status"] == "SHADOW_MARKET_MATRIX_READY"
    assert result["audit"]["method"] == "minimum_KL_partition_projection_1X2"


def test_ger_requires_consensus_ou25_for_frozen_profile():
    result = evaluate_matrix(_consensus("GER_Bundesliga", ou_lines=(2.5, 2.75)), _matrix())
    assert result["shadow_status"] == "OU25_CONSENSUS_REQUIRED_FOR_FROZEN_PROFILE"


def test_timing_robust_selective_domain_uses_consensus_gap():
    result = evaluate_selective(_consensus("ESP_LaLiga"))
    assert result["shadow_status"] == "SHADOW_MARKET_HIGH_CONFIDENCE_DIRECTION"
    assert result["shadow_direction"] == "home"
    assert result["provider_count"] == 2
    assert result["promotion_evidence_eligible"] is True


def test_sco_is_not_consensus_selective_runtime_candidate():
    result = evaluate_selective(_consensus("SCO_Premiership"))
    assert result["shadow_status"] == "DOMAIN_NOT_REGISTERED_FOR_MARKET_SELECTIVE_SHADOW"
