from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_v554 import build, validate_consensus
from prospective_market_snapshot_v523 import canonical_sha256


def _snapshot(
    group: str,
    name: str,
    home: float,
    draw: float,
    away: float,
    minute: int = 0,
    *,
    main_ou_line: float = 2.5,
    ou25_reference: tuple[float, float] | None = None,
):
    ts = f"2026-08-15T15:0{minute}:00+00:00"
    row = {
        "competition_id": "GER_Bundesliga",
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
        "provider_name": name,
        "provider_group": group,
        "one_x_two": {"home": home, "draw": draw, "away": away},
        "asian_handicap": {"line": -0.5, "home": 1.95, "away": 1.95},
        "over_under": {"line": main_ou_line, "over": 1.91, "under": 1.99},
    }
    if ou25_reference is not None:
        row["research_reference_surfaces"] = {
            "over_under_2_5": {
                "line": 2.5,
                "over": ou25_reference[0],
                "under": ou25_reference[1],
                "observed_at_utc": ts,
                "role": "fixed_research_reference_surface",
            }
        }
    row["raw_snapshot_sha256"] = canonical_sha256(row)
    return row


def test_consensus_uses_arithmetic_mean_decimal_prices_and_unique_groups():
    payload = build([
        _snapshot("book_a", "Book A", 1.80, 3.70, 4.60, 0),
        _snapshot("book_b", "Book B", 1.90, 3.50, 4.40, 1),
    ])
    assert validate_consensus(payload)["passed"] is True
    assert payload["provider_count"] == 2
    assert payload["one_x_two"]["home"] == 1.85
    assert payload["one_x_two"]["draw"] == 3.60
    assert payload["one_x_two"]["away"] == 4.50
    assert payload["surface_consensus_eligibility"]["over_under_2_5"] is True
    assert payload["over_under_2_5"]["line"] == 2.5
    assert payload["promotion_evidence_eligible"] is True


def test_main_ou275_can_coexist_with_fixed_ou25_research_consensus():
    payload = build([
        _snapshot("book_a", "Book A", 1.80, 3.70, 4.60, 0, main_ou_line=2.75, ou25_reference=(1.72, 2.15)),
        _snapshot("book_b", "Book B", 1.90, 3.50, 4.40, 1, main_ou_line=2.75, ou25_reference=(1.76, 2.08)),
    ])
    assert validate_consensus(payload)["passed"] is True
    assert payload["over_under"]["line"] == 2.75
    assert payload["over_under_2_5"]["line"] == 2.5
    assert payload["over_under_2_5"]["over"] == 1.74
    assert payload["over_under_2_5"]["under"] == 2.115
    assert payload["surface_consensus_eligibility"]["over_under_2_5"] is True


def test_main_ou275_without_fixed_reference_is_not_ou25_eligible():
    payload = build([
        _snapshot("book_a", "Book A", 1.80, 3.70, 4.60, 0, main_ou_line=2.75),
        _snapshot("book_b", "Book B", 1.90, 3.50, 4.40, 1, main_ou_line=2.75),
    ])
    assert validate_consensus(payload)["passed"] is True
    assert payload["over_under_2_5"] is None
    assert payload["surface_consensus_eligibility"]["over_under_2_5"] is False


def test_duplicate_provider_group_fails_closed():
    try:
        build([
            _snapshot("same_group", "Book A", 1.80, 3.70, 4.60, 0),
            _snapshot("same_group", "Book B", 1.90, 3.50, 4.40, 1),
        ])
    except ValueError as exc:
        assert "provider_group" in str(exc)
    else:
        raise AssertionError("duplicate provider groups must be rejected")


def test_cross_provider_observation_skew_over_five_minutes_fails():
    late = _snapshot("book_b", "Book B", 1.90, 3.50, 4.40, 0)
    late_ts = "2026-08-15T15:06:00+00:00"
    late["freeze_utc"] = late_ts
    late["accessed_at_utc"] = late_ts
    late["source_observed_at_utc"] = late_ts
    late["surface_observed_at_utc"] = {"one_x_two": late_ts, "asian_handicap": late_ts, "over_under": late_ts}
    late["raw_snapshot_sha256"] = canonical_sha256(late)
    try:
        build([_snapshot("book_a", "Book A", 1.80, 3.70, 4.60, 0), late])
    except ValueError as exc:
        assert "synchronization window" in str(exc)
    else:
        raise AssertionError("cross-provider skew over five minutes must be rejected")
