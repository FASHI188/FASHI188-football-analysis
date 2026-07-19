from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import (
    beta_binomial_pmf,
    build_score_matrix,
    conditional_goal_difference_by_total,
    current_season_history,
    negative_binomial_pmf,
    predict_from_history,
)
from platform_core import MatchRow, derive_score_marginals


class FootballV460EngineTests(unittest.TestCase):
    def test_negative_binomial_conserves_probability(self):
        total = sum(negative_binomial_pmf(value, 2.8, 9.0) for value in range(100))
        self.assertAlmostEqual(total, 1.0, places=10)

    def test_beta_binomial_conserves_probability(self):
        total = sum(beta_binomial_pmf(value, 7, 5.0, 4.0) for value in range(8))
        self.assertAlmostEqual(total, 1.0, places=10)

    def test_score_matrix_and_marginals_conserve_probability(self):
        matrix = build_score_matrix(1.7, 1.1, 10.0, 14.0, 15)
        marginals = derive_score_marginals(matrix)
        self.assertAlmostEqual(marginals["probability_sum"], 1.0, places=10)
        self.assertAlmostEqual(sum(marginals["1x2"].values()), 1.0, places=10)
        self.assertAlmostEqual(sum(marginals["total_goals"].values()), 1.0, places=10)

    def test_low_score_adjustment_preserves_direct_nb_total_marginal(self):
        mean = 2.9
        k = 8.0
        matrix = build_score_matrix(1.8, 1.1, k, 12.0, 15, {(0, 0): 1.2, (1, 1): 0.8})
        marginals = derive_score_marginals(matrix)
        expected = {str(total): negative_binomial_pmf(total, mean, k) for total in range(7)}
        expected["7+"] = 1.0 - sum(negative_binomial_pmf(total, mean, k) for total in range(7))
        for key in expected:
            self.assertAlmostEqual(marginals["total_goals"][key], expected[key], places=10)

    def test_conditional_goal_difference_respects_total_parity(self):
        matrix = build_score_matrix(1.6, 1.0, 9.0, 14.0, 12)
        conditional = conditional_goal_difference_by_total(matrix)
        for total_text, distribution in conditional.items():
            total = int(total_text)
            self.assertAlmostEqual(sum(distribution.values()), 1.0, places=10)
            for difference_text in distribution:
                self.assertEqual((total - int(difference_text)) % 2, 0)

    def test_current_season_history_excludes_historical_seasons(self):
        cutoff = datetime(2026, 7, 10, tzinfo=timezone.utc)
        matches = [
            MatchRow("X", "2025", "regular", cutoff - timedelta(days=400), "A", "B", 1, 0, "old"),
            MatchRow("X", "2026", "regular", cutoff - timedelta(days=10), "A", "B", 2, 1, "new"),
        ]
        season, history = current_season_history(matches, cutoff)
        self.assertEqual(season, "2026")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].source_path, "new")

    def test_predict_from_history_produces_joint_distribution(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        teams = ["A", "B", "C", "D"]
        history = []
        for index in range(40):
            home = teams[index % 4]
            away = teams[(index + 1) % 4]
            history.append(
                MatchRow(
                    "TEST", "2026", "regular", start + timedelta(days=index),
                    home, away, (index * 3) % 4, (index * 5 + 1) % 3, f"row-{index}"
                )
            )
        prediction = predict_from_history(
            history, "TEST", "2026", "A", "B", start + timedelta(days=45)
        )
        probabilities = prediction["probabilities"]
        self.assertAlmostEqual(sum(probabilities["one_x_two"].values()), 1.0, places=8)
        self.assertAlmostEqual(sum(probabilities["total_goals"].values()), 1.0, places=8)
        self.assertAlmostEqual(sum(cell["probability"] for cell in probabilities["score_matrix"]), 1.0, places=8)
        self.assertTrue(math.isfinite(prediction["team_sample"]["ess"]))


if __name__ == "__main__":
    unittest.main()
