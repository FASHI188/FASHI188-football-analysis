#!/usr/bin/env python3
import importlib.util, pathlib, unittest

HERE = pathlib.Path(__file__).resolve().parent
MOD_PATH = HERE / "score_fplcache_pit_availability_direction_consensus_v1.py"
spec = importlib.util.spec_from_file_location("scorer", MOD_PATH)
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


class TestDirectionConsensus(unittest.TestCase):
    def test_all_positive_activates(self):
        active, med, tilt = scorer.direction_consensus_tilt(0.10, 0.20, 0.30)
        self.assertTrue(active)
        self.assertAlmostEqual(med, 0.20, 12)
        self.assertAlmostEqual(tilt, 0.20 / 1.20, 12)

    def test_all_negative_activates(self):
        active, med, tilt = scorer.direction_consensus_tilt(-0.10, -0.20, -0.30)
        self.assertTrue(active)
        self.assertAlmostEqual(med, -0.20, 12)
        self.assertLess(tilt, 0.0)

    def test_zero_crossing_falls_back(self):
        for ds in ((0.10, 0.20, 0.0), (-0.10, 0.0, -0.20), (0.0, 0.0, 0.0)):
            active, med, tilt = scorer.direction_consensus_tilt(*ds)
            self.assertFalse(active)
            self.assertEqual(med, 0.0)
            self.assertEqual(tilt, 0.0)

    def test_mixed_sign_falls_back(self):
        active, med, tilt = scorer.direction_consensus_tilt(0.10, -0.20, 0.30)
        self.assertFalse(active)
        self.assertEqual((med, tilt), (0.0, 0.0))

    def test_zero_tilt_is_exact_identity(self):
        p = [0.5, 0.3, 0.2]
        self.assertEqual(scorer.transform_1x2(p, 0.0), p)


if __name__ == "__main__":
    unittest.main()
