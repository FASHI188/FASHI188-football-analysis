from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import (  # noqa: E402
    _select_point_in_time_parameters,
    current_season_history,
    expected_goals,
)
from platform_core import MatchRow  # noqa: E402


class V462BottomInvariantTests(unittest.TestCase):
    def _state(self):
        return {
            "league_home_goals": 1.5,
            "league_away_goals": 1.2,
            "mean_total_goals": 2.7,
            "team": {
                "home": {
                    "raw_matches": 10,
                    "home_raw_matches": 5,
                    "away_raw_matches": 5,
                    "effective_matches": 8.0,
                    "home_matches": 4.0,
                    "away_matches": 4.0,
                    "home_gf": 8.0,
                    "home_ga": 4.0,
                    "away_gf": 4.0,
                    "away_ga": 6.0,
                },
                "away": {
                    "raw_matches": 10,
                    "home_raw_matches": 5,
                    "away_raw_matches": 5,
                    "effective_matches": 8.0,
                    "home_matches": 4.0,
                    "away_matches": 4.0,
                    "home_gf": 6.0,
                    "home_ga": 5.0,
                    "away_gf": 4.0,
                    "away_ga": 8.0,
                },
            },
        }

    def test_same_day_rows_are_conservatively_excluded_from_live_history(self):
        cutoff = datetime(2026, 7, 18, 18, 0, tzinfo=timezone.utc)
        matches = [
            MatchRow("X", "2026", "regular", cutoff - timedelta(days=1), "A", "B", 1, 0, "prior"),
            MatchRow("X", "2026", "regular", cutoff.replace(hour=0), "C", "D", 2, 1, "same-day"),
        ]
        season, history = current_season_history(matches, cutoff, "2026")
        self.assertEqual(season, "2026")
        self.assertEqual([row.source_path for row in history], ["prior"])

    def test_direct_total_track_uses_total_goal_rates_not_sum_of_score_signals(self):
        base_state = self._state()
        params = {"team_prior_matches": 8.0, "minimum_goal_mean": 0.15, "maximum_goal_mean": 4.5}
        config = {"minimum_team_raw_matches": 2}
        first = expected_goals(base_state, "home", "away", params, config)

        changed = {**base_state, "team": {key: dict(value) for key, value in base_state["team"].items()}}
        # Preserve each venue-specific total-goal rate but radically change GF/GA composition.
        changed["team"]["home"].update({"home_gf": 4.0, "home_ga": 8.0})
        changed["team"]["away"].update({"away_gf": 8.0, "away_ga": 4.0})
        second = expected_goals(changed, "home", "away", params, config)

        self.assertAlmostEqual(first["mu_total"], second["mu_total"], places=12)
        self.assertNotAlmostEqual(first["allocation_home_share"], second["allocation_home_share"], places=6)
        self.assertAlmostEqual(first["mu_home"] + first["mu_away"], first["mu_total"], places=12)
        self.assertEqual(first["direct_total_method"], "nested_oos_shrunk_geometric_venue_total_rates")
        self.assertAlmostEqual(first["direct_total_signal_weight"], 1.0, places=12)

    def test_relevant_venue_ess_and_raw_counts_drive_sample_gate(self):
        state = self._state()
        state["team"]["home"].update({"raw_matches": 20, "home_raw_matches": 2, "home_matches": 1.7})
        state["team"]["away"].update({"raw_matches": 20, "away_raw_matches": 3, "away_matches": 2.4})
        params = {"team_prior_matches": 8.0, "minimum_goal_mean": 0.15, "maximum_goal_mean": 4.5}
        result = expected_goals(state, "home", "away", params, {"minimum_team_raw_matches": 2})
        self.assertEqual(result["home_raw_matches"], 2.0)
        self.assertEqual(result["away_raw_matches"], 3.0)
        self.assertAlmostEqual(result["ess"], 1.7, places=12)

        state["team"]["home"]["home_raw_matches"] = 1
        with self.assertRaises(Exception):
            expected_goals(state, "home", "away", params, {"minimum_team_raw_matches": 2})

    def test_point_in_time_parameter_map_refuses_unknown_historical_season(self):
        artifact = {
            "point_in_time_parameters": {
                "2025": {"half_life_days": 180.0},
                "2026": {"half_life_days": 120.0},
            }
        }
        self.assertEqual(_select_point_in_time_parameters(artifact, "2025")["half_life_days"], 180.0)
        with self.assertRaises(Exception):
            _select_point_in_time_parameters(artifact, "2024")


if __name__ == "__main__":
    unittest.main()
