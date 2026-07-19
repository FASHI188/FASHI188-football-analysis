import unittest
from datetime import timezone

from platform_core import (
    derive_score_marginals,
    parse_match_date,
    ranked_probability_score,
    settle_home_handicap,
    settle_over_total,
    split_quarter_line,
    validate_probability_vector,
)


class PlatformCoreTests(unittest.TestCase):
    def test_parse_cross_year_date_without_year(self):
        self.assertEqual(parse_match_date("Wed Sep 17", "2025/26").date().isoformat(), "2025-09-17")
        self.assertEqual(parse_match_date("Tue Jan 20", "2025/26").date().isoformat(), "2026-01-20")

    def test_quarter_line_settlement(self):
        self.assertEqual(split_quarter_line(-0.25), (-0.5, 0.0))
        self.assertEqual(settle_home_handicap(1, 1, -0.25), {"win": 0.0, "push": 0.5, "loss": 0.5})
        self.assertEqual(settle_over_total(1, 1, 2.25), {"win": 0.0, "push": 0.5, "loss": 0.5})

    def test_score_matrix_marginals(self):
        matrix = [
            {"home_goals": 1, "away_goals": 0, "probability": 0.4},
            {"home_goals": 0, "away_goals": 0, "probability": 0.2},
            {"home_goals": 0, "away_goals": 1, "probability": 0.4},
        ]
        derived = derive_score_marginals(matrix)
        self.assertAlmostEqual(derived["probability_sum"], 1.0)
        self.assertEqual(derived["1x2"], {"home": 0.4, "draw": 0.2, "away": 0.4})
        self.assertAlmostEqual(derived["total_goals"]["0"], 0.2)
        self.assertAlmostEqual(derived["total_goals"]["1"], 0.8)

    def test_probability_vector_and_rps(self):
        vector = validate_probability_vector({"H": 0.5, "D": 0.3, "A": 0.2}, ("H", "D", "A"), field="x")
        self.assertEqual(vector["H"], 0.5)
        self.assertGreaterEqual(ranked_probability_score([0.5, 0.3, 0.2], 0), 0.0)


if __name__ == "__main__":
    unittest.main()
