from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
VALIDATION_DIR = Path(__file__).resolve().parents[1] / "validation"
for path in (ENGINE_DIR, VALIDATION_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import nested_backtest_v460 as backtest
from platform_core import MatchRow


class NestedBacktestTests(unittest.TestCase):
    def test_same_day_results_are_not_visible_to_each_other(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        matches = []
        teams = ["A", "B", "C", "D"]
        for index in range(34):
            date = start + timedelta(days=index)
            matches.append(MatchRow("TEST", "2026", "regular", date, teams[index % 4], teams[(index + 1) % 4], 1, 0, str(index)))
        same_day = start + timedelta(days=40)
        matches.extend([
            MatchRow("TEST", "2026", "regular", same_day, "A", "B", 2, 0, "same-1"),
            MatchRow("TEST", "2026", "regular", same_day, "C", "D", 0, 1, "same-2"),
        ])
        observed_history_lengths = []

        def fake_predict(history, competition_id, season, home, away, cutoff, params, use_team_effects=True):
            observed_history_lengths.append(len(history))
            matrix = [
                {"home_goals": 1, "away_goals": 0, "probability": 0.5},
                {"home_goals": 0, "away_goals": 1, "probability": 0.5},
            ]
            return {
                "probabilities": {
                    "one_x_two": {"home": 0.5, "draw": 0.0, "away": 0.5},
                    "total_goals": {"0": 0.0, "1": 1.0, "2": 0.0, "3": 0.0, "4": 0.0, "5": 0.0, "6": 0.0, "7+": 0.0},
                    "score_matrix": matrix,
                }
            }

        with patch.object(backtest, "predict_from_history", side_effect=fake_predict):
            records = backtest.evaluate_season("TEST", matches, {}, use_team_effects=True)
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(observed_history_lengths[-1], observed_history_lengths[-2])


if __name__ == "__main__":
    unittest.main()
