#!/usr/bin/env python3
import importlib.util, math, pathlib, unittest

HERE = pathlib.Path(__file__).resolve().parent
MOD_PATH = HERE / 'score_top1_macro_micro_geometry_v1.py'
spec = importlib.util.spec_from_file_location('scorer', MOD_PATH)
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)

class TestGeometryDecision(unittest.TestCase):
    def test_outcome_idx(self):
        self.assertEqual(scorer.outcome_idx(2, 1), 0)
        self.assertEqual(scorer.outcome_idx(1, 1), 1)
        self.assertEqual(scorer.outcome_idx(0, 1), 2)

    def test_integrate(self):
        m = [[0.20, 0.10], [0.30, 0.40]]
        got = scorer.integrate(m)
        for a, b in zip(got, [0.30, 0.60, 0.10]):
            self.assertAlmostEqual(a, b, 12)

    def test_region_peaks(self):
        m = [[0.20, 0.10, 0.05], [0.30, 0.15, 0.05], [0.05, 0.05, 0.05]]
        s = sum(map(sum, m)); m = [[v/s for v in row] for row in m]
        p = scorer.region_peaks(m)
        self.assertAlmostEqual(p[0], 0.30/s, 12)
        self.assertAlmostEqual(p[1], 0.20/s, 12)
        self.assertAlmostEqual(p[2], 0.10/s, 12)

    def test_majority_protection(self):
        m = [[0.25, 0.12], [0.53, 0.10]]
        p = scorer.integrate(m)
        self.assertGreater(p[0], 0.5)
        c, regime, _, evidence = scorer.geometry_decision(p, m)
        self.assertEqual(c, 0)
        self.assertEqual(regime, 'DOMINANT_MAJORITY')
        self.assertEqual(evidence, [None, None, None])

    def test_balanced_draw_can_win_geometry(self):
        m = [
            [0.20, 0.10, 0.05],
            [0.20, 0.15, 0.05],
            [0.10, 0.05, 0.10],
        ]
        s = sum(map(sum, m)); m = [[v/s for v in row] for row in m]
        p = scorer.integrate(m)
        c, regime, _, evidence = scorer.geometry_decision(p, m)
        self.assertEqual(regime, 'BALANCED_GEOMETRY')
        self.assertEqual(c, max(range(3), key=lambda k: (evidence[k], p[k], -k)))

    def test_baseline_tie_break_is_deterministic(self):
        self.assertEqual(scorer.baseline_top1([0.4, 0.4, 0.2]), 0)

if __name__ == '__main__':
    unittest.main()
