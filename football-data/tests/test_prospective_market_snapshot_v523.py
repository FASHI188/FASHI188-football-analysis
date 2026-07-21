from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import canonical_sha256, validate


def _valid_snapshot():
    snapshot = {
        "competition_id": "ENG_PremierLeague",
        "season": "2026/27",
        "home_team": "Arsenal",
        "away_team": "Liverpool",
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
        "provider_group": "example_independent_group",
        "one_x_two": {"home": 2.20, "draw": 3.50, "away": 3.20},
        "asian_handicap": {"line": -0.25, "home": 1.95, "away": 1.95},
        "over_under": {"line": 2.75, "over": 1.93, "under": 1.97},
    }
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    return snapshot


def test_valid_snapshot_passes():
    result = validate(_valid_snapshot())
    assert result["passed"] is True
    assert result["formal_pit_eligible"] is True
    assert result["surface_timestamp_spread_seconds"] == 10.0


def test_post_kickoff_freeze_fails():
    snapshot = _valid_snapshot()
    snapshot["freeze_utc"] = "2026-08-15T16:31:00+00:00"
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    result = validate(snapshot)
    assert result["passed"] is False
    assert any("freeze_utc must precede" in error for error in result["errors"])


def test_surface_spread_over_five_minutes_fails():
    snapshot = _valid_snapshot()
    snapshot["surface_observed_at_utc"]["over_under"] = "2026-08-15T15:06:00+00:00"
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    result = validate(snapshot)
    assert result["passed"] is False
    assert any("timestamp spread" in error for error in result["errors"])


def test_missing_two_sided_price_fails():
    snapshot = _valid_snapshot()
    snapshot["asian_handicap"].pop("away")
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    result = validate(snapshot)
    assert result["passed"] is False
    assert any("asian_handicap.away" in error for error in result["errors"])


def test_hash_mismatch_fails():
    snapshot = _valid_snapshot()
    snapshot["one_x_two"]["home"] = 2.30
    result = validate(snapshot)
    assert result["passed"] is False
    assert any("raw_snapshot_sha256 does not match" in error for error in result["errors"])
