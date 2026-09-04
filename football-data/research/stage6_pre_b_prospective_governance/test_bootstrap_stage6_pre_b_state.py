from __future__ import annotations

import math
import unittest

from bootstrap_stage6_pre_b_state import BState, b_alpha, b_values_from_history, b_values_from_ratio


class BootstrapStage6PreBStateTests(unittest.TestCase):
    def test_frozen_alpha(self):
        expected = 1.0 - math.exp(math.log(0.5) / 16.0)
        self.assertAlmostEqual(b_alpha(), expected, places=15)

    def test_transforms(self):
        d, p = b_values_from_ratio(9, 8.5)
        self.assertAlmostEqual(d, math.log1p(9.0))
        self.assertAlmostEqual(p, -math.log(8.5))

    def test_history_ppda_semantics(self):
        d, p = b_values_from_history({"deep": 4, "ppda": {"att": 102, "def": 12}})
        self.assertAlmostEqual(d, math.log1p(4.0))
        self.assertAlmostEqual(p, -math.log(8.5))

    def test_invalid_history_fails_for_caller_to_skip(self):
        with self.assertRaises(ValueError):
            b_values_from_history({"deep": 4, "ppda": {"att": 102, "def": 0}})
        with self.assertRaises(ValueError):
            b_values_from_history({"deep": 4})

    def test_state_update_matches_development_rule(self):
        alpha = b_alpha()
        s = BState()
        s.update(1.0, 2.0, alpha)
        self.assertEqual(s.n, 1)
        self.assertEqual(s.deep, 1.0)
        self.assertEqual(s.press, 2.0)
        s.update(3.0, 4.0, alpha)
        self.assertEqual(s.n, 2)
        self.assertAlmostEqual(s.deep, (1 - alpha) * 1.0 + alpha * 3.0)
        self.assertAlmostEqual(s.press, (1 - alpha) * 2.0 + alpha * 4.0)


if __name__ == "__main__":
    unittest.main()
