from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_matrix_shadow_v531 import evaluate
from prospective_market_snapshot_v523 import canonical_sha256


def _matrix():
    raw = {
        (0, 0): 0.10, (1, 0): 0.14, (0, 1): 0.10, (1, 1): 0.16,
        (2, 0): 0.10, (0, 2): 0.08, (2, 1): 0.12, (1, 2): 0.08,
        (2, 2): 0.07, (3, 1): 0.05,
    }
    return [{"home_goals": h, "away_goals": a, "probability": p} for (h, a), p in raw.items()]


def _snapshot(line=2.5):
    s = {
        "competition_id": "POR_PrimeiraLiga",
        "season": "2026/27",
        "home_team": "Home",
        "away_team": "Away",
        "kickoff_utc": "2026-08-15T19:15:00+00:00",
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": "2026-08-15T18:00:00+00:00",
        "accessed_at_utc": "2026-08-15T18:00:10+00:00",
        "source_observed_at_utc": "2026-08-15T17:59:55+00:00",
        "surface_observed_at_utc": {
            "one_x_two": "2026-08-15T17:59:50+00:00",
            "asian_handicap": "2026-08-15T17:59:55+00:00",
            "over_under": "2026-08-15T18:00:00+00:00",
        },
        "source_url": "https://example.invalid/market",
        "provider_name": "Example Market",
        "provider_group": "example_group",
        "one_x_two": {"home": 1.90, "draw": 3.60, "away": 4.20},
        "asian_handicap": {"line": -0.5, "home": 1.95, "away": 1.95},
        "over_under": {"line": line, "over": 1.91, "under": 1.99},
    }
    s["raw_snapshot_sha256"] = canonical_sha256(s)
    return s


def test_por_is_registered_dual_surface_candidate():
    result = evaluate(_snapshot(), _matrix())
    assert result["shadow_status"] == "SHADOW_MARKET_MATRIX_READY"
    assert result["audit"]["converged"] is True
    assert result["audit"]["max_constraint_residual"] <= 1e-10
    assert result["audit"]["probability_sum_residual"] <= 1e-10
    assert result["formal_matrix_override"] is False
    assert result["formal_probability_mutation"] is False


def test_por_wrong_ou_line_fails_closed():
    result = evaluate(_snapshot(2.75), _matrix())
    assert result["shadow_status"] == "OU25_REFERENCE_REQUIRED_FOR_FROZEN_PROFILE"
