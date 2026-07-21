from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "validation" / "player_xi_matrix_projection_ita_v504.py"
    spec = importlib.util.spec_from_file_location("player_xi_matrix_projection_ita_v504", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load matrix projection module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_matrix():
    rows = [
        (0, 0, 0.15),
        (1, 0, 0.20),
        (0, 1, 0.15),
        (1, 1, 0.18),
        (2, 0, 0.12),
        (0, 2, 0.08),
        (2, 1, 0.07),
        (1, 2, 0.05),
    ]
    total = sum(probability for _, _, probability in rows)
    return [
        {
            "home_goals": home,
            "away_goals": away,
            "probability": probability / total,
        }
        for home, away, probability in rows
    ]


class PlayerXIMatrixProjectionTests(unittest.TestCase):
    def test_projection_preserves_total_marginal_and_probability(self) -> None:
        module = load_module()
        baseline = sample_matrix()
        candidate, audit = module.project_matrix(baseline, 0.20)
        self.assertAlmostEqual(module.probability_sum(candidate), 1.0, places=12)
        self.assertLessEqual(
            module.total_marginal_residual(baseline, candidate),
            1e-12,
        )
        self.assertLessEqual(abs(audit["target_margin_residual"]), 1e-8)
        self.assertLessEqual(abs(audit["home_mean_residual"]), 1e-8)

    def test_zero_adjustment_is_identity_within_precision(self) -> None:
        module = load_module()
        baseline = sample_matrix()
        candidate, audit = module.project_matrix(baseline, 0.0)
        baseline_map = {
            (row["home_goals"], row["away_goals"]): row["probability"]
            for row in baseline
        }
        candidate_map = {
            (row["home_goals"], row["away_goals"]): row["probability"]
            for row in candidate
        }
        self.assertEqual(set(baseline_map), set(candidate_map))
        for key in baseline_map:
            self.assertAlmostEqual(baseline_map[key], candidate_map[key], places=10)
        self.assertLessEqual(abs(audit["target_margin_residual"]), 1e-8)

    def test_margin_rps_is_finite(self) -> None:
        module = load_module()
        distribution = module.margin_distribution(sample_matrix())
        score = module.ordered_rps(distribution, 1)
        self.assertTrue(math.isfinite(score))
        self.assertGreaterEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()
