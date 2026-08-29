from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.outcome_mass_matrix_transport import lift_1x2_target
from components.r43_probability_matrix_adapters import (
    R43RScoreMatrixTransportComponent,
    R43YScoreMatrixTransportComponent,
)
from components.r43r_football_residual import residual_prob
from components.r43y_draw_calibration import calibrate
from pipeline.unified_inference import canonical_matrix, one_x_two


def sample_matrix():
    raw = [
        (0, 0, 0.10), (0, 1, 0.08), (0, 2, 0.03),
        (1, 0, 0.16), (1, 1, 0.13), (1, 2, 0.07),
        (2, 0, 0.12), (2, 1, 0.18), (2, 2, 0.13),
    ]
    return canonical_matrix([
        {"home_goals": h, "away_goals": a, "probability": p}
        for h, a, p in raw
    ])


def outcome_class(cell):
    h, a = cell["home_goals"], cell["away_goals"]
    return "home" if h > a else "draw" if h == a else "away"


class OutcomeMassMatrixTransportTests(unittest.TestCase):
    def test_identity_target_returns_same_probabilities(self):
        matrix = sample_matrix()
        out = lift_1x2_target(matrix, one_x_two(matrix))
        for before, after in zip(matrix, out):
            self.assertEqual(before["home_goals"], after["home_goals"])
            self.assertEqual(before["away_goals"], after["away_goals"])
            self.assertAlmostEqual(before["probability"], after["probability"], places=15)

    def test_target_masses_recovered_and_support_preserved(self):
        matrix = sample_matrix()
        target = {"home": 0.44, "draw": 0.34, "away": 0.22}
        out = lift_1x2_target(matrix, target)
        got = one_x_two(out)
        for k in target:
            self.assertAlmostEqual(got[k], target[k], places=15)
        self.assertEqual(
            [(c["home_goals"], c["away_goals"]) for c in matrix],
            [(c["home_goals"], c["away_goals"]) for c in out],
        )

    def test_conditional_score_shape_preserved_within_each_outcome(self):
        matrix = sample_matrix()
        out = lift_1x2_target(matrix, {"home": 0.44, "draw": 0.34, "away": 0.22})
        for klass in ("home", "draw", "away"):
            before = [c for c in matrix if outcome_class(c) == klass]
            after = [c for c in out if outcome_class(c) == klass]
            scale = after[0]["probability"] / before[0]["probability"]
            for b, a in zip(before, after):
                self.assertAlmostEqual(a["probability"] / b["probability"], scale, places=15)

    def test_positive_target_without_existing_class_support_fails_closed(self):
        matrix = canonical_matrix([
            {"home_goals": 0, "away_goals": 0, "probability": 0.5},
            {"home_goals": 1, "away_goals": 1, "probability": 0.5},
        ])
        with self.assertRaisesRegex(RuntimeError, "without existing score support"):
            lift_1x2_target(matrix, {"home": 0.1, "draw": 0.8, "away": 0.1})

    def test_r43r_wrapper_is_disabled_and_recovers_native_target(self):
        matrix = sample_matrix()
        market = one_x_two(matrix)
        football = {"home": 0.47, "draw": 0.31, "away": 0.22}
        beta = 0.08
        component = R43RScoreMatrixTransportComponent()
        self.assertFalse(component.enabled)
        out = component.apply(matrix, None, {
            "r43r_market_probabilities": market,
            "r43r_football_probabilities": football,
            "r43r_beta": beta,
        })
        target = residual_prob(market, football, beta)
        got = one_x_two(out)
        for k in target:
            self.assertAlmostEqual(got[k], target[k], places=15)

    def test_r43r_source_mismatch_fails_closed(self):
        matrix = sample_matrix()
        with self.assertRaisesRegex(RuntimeError, "source_1x2_mismatch"):
            R43RScoreMatrixTransportComponent(enabled=True).apply(matrix, None, {
                "r43r_market_probabilities": {"home": 0.50, "draw": 0.30, "away": 0.20},
                "r43r_football_probabilities": {"home": 0.45, "draw": 0.30, "away": 0.25},
                "r43r_beta": 0.05,
            })

    def test_r43y_wrapper_is_disabled_and_recovers_native_target(self):
        matrix = sample_matrix()
        source = one_x_two(matrix)
        component = R43YScoreMatrixTransportComponent()
        self.assertFalse(component.enabled)
        out = component.apply(matrix, None, {"r43y_source_r43u0_probabilities": source})
        target = calibrate(source)
        got = one_x_two(out)
        for k in target:
            self.assertAlmostEqual(got[k], target[k], places=15)

    def test_r43y_source_mismatch_fails_closed(self):
        matrix = sample_matrix()
        with self.assertRaisesRegex(RuntimeError, "source_1x2_mismatch"):
            R43YScoreMatrixTransportComponent(enabled=True).apply(matrix, None, {
                "r43y_source_r43u0_probabilities": {"home": 0.40, "draw": 0.40, "away": 0.20},
            })


if __name__ == "__main__":
    unittest.main()
