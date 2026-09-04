from __future__ import annotations

import unittest
from datetime import datetime, timezone

import enroll_stage6_pre_b_receipts as m


class EnrollmentTests(unittest.TestCase):
    def test_b_fallback_is_exact(self):
        base = [0.45, 0.30, 0.25]
        got, active, edge = m.b_predict(base, None, 0.2)
        self.assertFalse(active)
        self.assertIsNone(edge)
        self.assertEqual(got, base)

    def test_b_active_preserves_draw_and_probability_mass(self):
        base = [0.45, 0.30, 0.25]
        got, active, edge = m.b_predict(base, 1.0, -1.0, 0.10)
        self.assertTrue(active)
        self.assertAlmostEqual(edge, 2.0, 14)
        self.assertAlmostEqual(got[1], base[1], 14)
        self.assertAlmostEqual(sum(got), 1.0, 14)
        self.assertGreater(got[0], base[0])

    def test_b_edge_clip_bounds_effect(self):
        base = [0.45, 0.30, 0.25]
        a, _, _ = m.b_predict(base, 100.0, -100.0, 0.10)
        b, _, _ = m.b_predict(base, 3.0, 0.0, 0.10)
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, 14)

    def test_b_process_scores_use_population_standardization(self):
        pack = {"leagues": {"EPL": {
            "1": {"deep": 0.0, "press": 0.0, "n": 1},
            "2": {"deep": 2.0, "press": 2.0, "n": 1},
        }}}
        s = m.b_process_scores(pack, "EPL")
        self.assertAlmostEqual(s["1"], -1.0, 14)
        self.assertAlmostEqual(s["2"], 1.0, 14)

    def test_atomic_groups_keep_same_kickoff_together(self):
        t1 = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 5, 15, tzinfo=timezone.utc)
        rows = [
            ({"fixture_identity_sha256": "a"}, {"kickoff": t1, "mid": 1}),
            ({"fixture_identity_sha256": "b"}, {"kickoff": t2, "mid": 2}),
            ({"fixture_identity_sha256": "c"}, {"kickoff": t2, "mid": 3}),
        ]
        g = m.atomic_groups(rows)
        self.assertEqual([len(x) for x in g], [1, 2])

    def test_frozen_constants(self):
        self.assertEqual(m.REQUIRED_N, 1335)
        self.assertEqual(m.QUEUE_SHA, "6cfcaba8e2f82af0996a404eb3fc5bb477174aebd09c9b10c7434d95e59c8dfc")
        self.assertEqual(m.CUTOFF.isoformat(), "2026-09-04T11:00:00+00:00")
        self.assertEqual(m.V311_HEAD, "a90762a97515f3edd564e8ad204db0d0d4231494")


if __name__ == "__main__":
    unittest.main()
