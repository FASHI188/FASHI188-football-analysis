from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import validate
from question_time_market_snapshot_writer_v553 import build


def _args(**overrides):
    values = dict(
        competition_id="GER_Bundesliga",
        season="2026/27",
        home_team="Home",
        away_team="Away",
        kickoff_utc="2026-08-15T16:30:00+00:00",
        freeze_utc="2026-08-15T15:00:00+00:00",
        accessed_at_utc="2026-08-15T15:00:10+00:00",
        source_observed_at_utc="2026-08-15T14:59:55+00:00",
        one_x_two_observed_at_utc="2026-08-15T14:59:50+00:00",
        asian_handicap_observed_at_utc="2026-08-15T14:59:55+00:00",
        over_under_observed_at_utc="2026-08-15T15:00:00+00:00",
        source_url="https://example.invalid/market",
        provider_name="Example Bookmaker",
        provider_group="example_group",
        home_odds=1.85,
        draw_odds=3.70,
        away_odds=4.50,
        ah_line=-0.5,
        ah_home_odds=1.95,
        ah_away_odds=1.95,
        ou_line=2.5,
        over_odds=1.91,
        under_odds=1.99,
        out=None,
    )
    values.update(overrides)
    return Namespace(**values)


def test_writer_builds_a_valid_hashed_pit_snapshot():
    payload = build(_args())
    result = validate(payload)
    assert result["passed"] is True
    assert len(payload["raw_snapshot_sha256"]) == 64
    assert payload["observation_semantics"]["retrospective_backfill"] is False


def test_writer_rejects_surface_skew_over_five_minutes():
    try:
        build(_args(over_under_observed_at_utc="2026-08-15T15:06:00+00:00"))
    except ValueError as exc:
        assert "hard gate failed" in str(exc)
    else:
        raise AssertionError("writer must reject >5 minute surface skew")


def test_writer_rejects_post_kickoff_freeze():
    try:
        build(_args(freeze_utc="2026-08-15T16:31:00+00:00"))
    except ValueError as exc:
        assert "hard gate failed" in str(exc)
    else:
        raise AssertionError("writer must reject post-kickoff freeze")
