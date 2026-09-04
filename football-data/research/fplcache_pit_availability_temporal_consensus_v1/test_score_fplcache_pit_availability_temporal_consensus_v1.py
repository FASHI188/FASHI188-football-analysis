#!/usr/bin/env python3
import importlib.util, pathlib, unittest

HERE = pathlib.Path(__file__).resolve().parent
MOD_PATH = HERE / "score_fplcache_pit_availability_temporal_consensus_v1.py"
spec = importlib.util.spec_from_file_location("scorer", MOD_PATH)
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)

class TestScorer(unittest.TestCase):
    def test_outcome_idx(self):
        self.assertEqual(scorer.outcome_idx(2, 1), 0)
        self.assertEqual(scorer.outcome_idx(1, 1), 1)
        self.assertEqual(scorer.outcome_idx(0, 1), 2)

    def test_iprojection_hits_target(self):
        m = [[0.20, 0.10, 0.05], [0.15, 0.20, 0.05], [0.10, 0.05, 0.10]]
        s = sum(map(sum, m))
        m = [[v / s for v in r] for r in m]
        target = [0.45, 0.30, 0.25]
        q = scorer.iproject(m, target)
        got = scorer.integrate(q)
        for a, b in zip(got, target):
            self.assertAlmostEqual(a, b, 12)

    def test_team_impairment(self):
        snap = {"teams": [{"id": 1, "name": "Man Utd"}], "players": [
            {"team": 1, "minutes": 900, "status": "a"},
            {"team": 1, "minutes": 900, "status": "i"},
            {"team": 1, "minutes": 0, "status": "u"},
        ]}
        self.assertAlmostEqual(
            scorer.team_impairment(snap, "Man United", {"Man United": "Man Utd"}), 0.5, 12)

    def test_temporal_consensus_median(self):
        med, tilt = scorer.temporal_consensus_tilt(0.1, 0.4, 0.2)
        self.assertAlmostEqual(med, 0.2, 12)
        self.assertAlmostEqual(tilt, 0.2 / 1.2, 12)

    def test_temporal_consensus_bounds(self):
        for d in (-1.0, -0.5, 0.0, 0.5, 1.0):
            med, tilt = scorer.temporal_consensus_tilt(d, d, d)
            self.assertLessEqual(abs(tilt), 0.5 + 1e-15)
            self.assertEqual(0 if med == 0 else (1 if med > 0 else -1),
                             0 if tilt == 0 else (1 if tilt > 0 else -1))

    def test_transform_normalizes_and_direction(self):
        p = [0.4, 0.3, 0.3]
        q = scorer.transform_1x2(p, 0.2)
        self.assertAlmostEqual(sum(q), 1.0, 12)
        self.assertGreater(q[0], p[0])
        self.assertLess(q[2], p[2])

    def test_metric_top1_correct(self):
        rows = [{"y": 0, "home_goals": 1, "away_goals": 0,
                 "p": [0.6, 0.2, 0.2], "m": [[0.4, 0.1], [0.2, 0.3]]}]
        x = scorer.metric(rows, "p", "m")
        self.assertEqual(x["top1_correct"], 1)

if __name__ == "__main__":
    unittest.main()
