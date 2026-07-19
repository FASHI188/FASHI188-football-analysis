from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
VALIDATION_DIR = Path(__file__).resolve().parents[1] / "validation"
for path in (ENGINE_DIR, VALIDATION_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals


class OOFMatrixCalibrationTests(unittest.TestCase):
    def test_temperature_one_is_identity(self):
        matrix = [
            {"home_goals": 0, "away_goals": 0, "probability": 0.2},
            {"home_goals": 1, "away_goals": 0, "probability": 0.5},
            {"home_goals": 0, "away_goals": 1, "probability": 0.3},
        ]
        calibrated = temperature_scale_matrix(matrix, 1.0)
        for raw, changed in zip(matrix, calibrated):
            self.assertAlmostEqual(raw["probability"], changed["probability"], places=12)

    def test_temperature_scaling_conserves_probability_and_coherence(self):
        matrix = [
            {"home_goals": 0, "away_goals": 0, "probability": 0.10},
            {"home_goals": 1, "away_goals": 0, "probability": 0.40},
            {"home_goals": 0, "away_goals": 1, "probability": 0.20},
            {"home_goals": 1, "away_goals": 1, "probability": 0.30},
        ]
        calibrated = temperature_scale_matrix(matrix, 1.5)
        marginals = derive_score_marginals(calibrated)
        self.assertTrue(math.isfinite(marginals["probability_sum"]))
        self.assertAlmostEqual(marginals["probability_sum"], 1.0, places=12)
        self.assertAlmostEqual(sum(marginals["1x2"].values()), 1.0, places=12)
        self.assertAlmostEqual(sum(marginals["total_goals"].values()), 1.0, places=12)

    def test_higher_temperature_flattens_distribution(self):
        matrix = [
            {"home_goals": 0, "away_goals": 0, "probability": 0.05},
            {"home_goals": 1, "away_goals": 0, "probability": 0.80},
            {"home_goals": 0, "away_goals": 1, "probability": 0.15},
        ]
        calibrated = temperature_scale_matrix(matrix, 1.8)
        self.assertLess(calibrated[1]["probability"], 0.80)
        self.assertGreater(calibrated[0]["probability"], 0.05)


if __name__ == "__main__":
    unittest.main()
