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
    cells = []
    # Positive support across home/draw/away x under/over intersections.
    raw = {
        (0, 0): 0.10,
        (1, 0): 0.14,
        (0, 1): 0.10,
        (1, 1): 0.16,
        (2, 0): 0.10,
        (0, 2): 0.08,
        (2, 1): 0.12,
        (1, 2): 0.08,
        (2, 2): 0.07,
        (3, 1): 0.05,
    }
    for (h, a), p in raw.items():
        cells.append({"home_goals": h, "away_goals": a, "probability": p})
    return cells


def _snapshot(cid="GER_Bundesliga", line=2.5):
    s = {
        "competition_id": cid,
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
            "over_under": "2026-08-15T15:00:00+00:00",
        },
        "source_url": "https://example.invalid/market",
        "provider_name": "Example Market",
        "provider_group": "example_group",
        "one_x_two": {"home": 1.80, "draw": 3.80, "away": 4.60},
        "asian_handicap": {"line": -0.5, "home": 1.95, "away": 1.95},
        "over_under": {"line": line, "over": 1.90, "under": 2.00},
    }
    s["raw_snapshot_sha256"] = canonical_sha256(s)
    return s


def test_registered_domain_exactly_fits_1x2_and_ou25():
    result = evaluate(_snapshot(), _matrix())
    assert result["shadow_status"] == "SHADOW_MARKET_MATRIX_READY"
    audit = result["audit"]
    assert audit["converged"] is True
    assert audit["max_constraint_residual"] <= 1e-10
    assert audit["probability_sum_residual"] <= 1e-10
    assert result["formal_matrix_override"] is False


def test_france_is_registered_primary_candidate():
    result = evaluate(_snapshot("FRA_Ligue1"), _matrix())
    assert result["shadow_status"] == "SHADOW_MARKET_MATRIX_READY"


def test_england_is_not_primary_candidate_after_total_top2_failure():
    result = evaluate(_snapshot("ENG_PremierLeague"), _matrix())
    assert result["shadow_status"] == "DOMAIN_NOT_REGISTERED_PRIMARY_MATRIX_CANDIDATE"


def test_non_25_total_line_does_not_reuse_frozen_profile():
    result = evaluate(_snapshot("GER_Bundesliga", 2.75), _matrix())
    assert result["shadow_status"] == "OU25_REFERENCE_REQUIRED_FOR_FROZEN_PROFILE"
