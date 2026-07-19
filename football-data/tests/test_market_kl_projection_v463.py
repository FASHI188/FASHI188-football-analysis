from __future__ import annotations

import sys
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from market_kl_projection_v463 import project_market


class MarketKLProjectionTests(unittest.TestCase):
    def _matrix(self):
        cells = []
        for home in range(3):
            for away in range(3):
                cells.append({"home_goals": home, "away_goals": away, "probability": 1.0 / 9.0})
        return cells

    def test_one_x_two_projection_converges_and_preserves_probability(self):
        snapshot = {"one_x_two": {"home": 2.5, "draw": 3.2, "away": 2.8}}
        result = project_market(self._matrix(), snapshot, include=("1x2",))
        self.assertTrue(result["audit"]["converged"])
        self.assertLessEqual(result["audit"]["max_abs_constraint_residual"], 1e-8)
        self.assertAlmostEqual(result["audit"]["final_probability_sum"], 1.0, places=12)
        inverse = [1 / 2.5, 1 / 3.2, 1 / 2.8]
        total = sum(inverse)
        self.assertAlmostEqual(result["marginals"]["1x2"]["home"], inverse[0] / total, places=8)
        self.assertAlmostEqual(result["marginals"]["1x2"]["draw"], inverse[1] / total, places=8)


if __name__ == "__main__":
    unittest.main()
