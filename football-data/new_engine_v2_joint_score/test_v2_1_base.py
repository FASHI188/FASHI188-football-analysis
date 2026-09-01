from __future__ import annotations

import json
import math
import unittest
from datetime import datetime, timedelta, timezone

import v2_1_base as v

T0 = datetime(2023, 8, 12, 15, tzinfo=timezone.utc)

def fx(fid="f1", home="A", away="B", season="2023-24", kickoff=T0, comp="ENG1"):
    return v.Fixture(fid, comp, season, kickoff, home, away, 1)

class V21BaseTests(unittest.TestCase):
    def test_01_average_team_home_direction(self):
        p = v.EngineState(v.Parameters()).predict_batch([fx()])[0]
        self.assertGreater(p["mu_home"], p["mu_away"])

    def test_02_average_team_recovers_comp_intercepts(self):
        p = v.EngineState(v.Parameters()).predict_batch([fx()])[0]
        self.assertAlmostEqual(p["mu_home"], p["components"]["competition_home_rate"], places=14)
        self.assertAlmostEqual(p["mu_away"], p["components"]["competition_away_rate"], places=14)
        self.assertAlmostEqual(math.log(p["mu_home"]), p["components"]["competition_home_intercept"], places=14)
        self.assertAlmostEqual(math.log(p["mu_away"]), p["components"]["competition_away_intercept"], places=14)

    def test_03_attack_monotonic_own_mu_only(self):
        a, b = v.EngineState(v.Parameters()), v.EngineState(v.Parameters())
        b.teams["ENG1|A"] = v.TeamState(4.0, 0.0, 4.0, (T0-timedelta(days=7)).isoformat(), "2023-24")
        pa, pb = a.predict_batch([fx("fa")])[0], b.predict_batch([fx("fb")])[0]
        self.assertGreater(pb["mu_home"], pa["mu_home"]); self.assertAlmostEqual(pb["mu_away"], pa["mu_away"], places=14)

    def test_04_defence_monotonic_opponent_mu_only(self):
        a, b = v.EngineState(v.Parameters()), v.EngineState(v.Parameters())
        b.teams["ENG1|B"] = v.TeamState(0.0, 4.0, 4.0, (T0-timedelta(days=7)).isoformat(), "2023-24")
        pa, pb = a.predict_batch([fx("fa")])[0], b.predict_batch([fx("fb")])[0]
        self.assertLess(pb["mu_home"], pa["mu_home"]); self.assertAlmostEqual(pb["mu_away"], pa["mu_away"], places=14)

    def test_05_home_away_swap_only_team_roles_not_strength_scale(self):
        s = v.EngineState(v.Parameters())
        s.teams["ENG1|A"] = v.TeamState(3.0, 1.0, 5.0, (T0-timedelta(days=5)).isoformat(), "2023-24")
        s.teams["ENG1|B"] = v.TeamState(-1.0, 2.5, 4.0, (T0-timedelta(days=6)).isoformat(), "2023-24")
        clone = v.EngineState.deserialize(s.serialize())
        ab = s.predict_batch([fx("ab", "A", "B")])[0]["components"]
        ba = clone.predict_batch([fx("ba", "B", "A")])[0]["components"]
        self.assertAlmostEqual(ab["home_attack"], ba["away_attack"], places=14)
        self.assertAlmostEqual(ab["home_defence"], ba["away_defence"], places=14)
        self.assertAlmostEqual(ab["away_attack"], ba["home_attack"], places=14)
        self.assertAlmostEqual(ab["away_defence"], ba["home_defence"], places=14)
        self.assertAlmostEqual(ab["competition_home_rate"], ba["competition_home_rate"], places=14)
        self.assertAlmostEqual(ab["competition_away_rate"], ba["competition_away_rate"], places=14)
        self.assertEqual(ab["team_venue_bias_home"], 0.0); self.assertEqual(ba["team_venue_bias_home"], 0.0)

    def test_06_home_advantage_applied_exactly_once(self):
        p = v.EngineState(v.Parameters()).predict_batch([fx()])[0]
        ch, ca = p["components"]["competition_home_rate"], p["components"]["competition_away_rate"]
        self.assertAlmostEqual(p["mu_home"]/p["mu_away"], ch/ca, places=14)
        self.assertEqual(p["components"]["team_venue_bias_home"], 0.0); self.assertEqual(p["components"]["team_venue_bias_away"], 0.0)

    def test_07_equal_strength_cannot_reverse_home_advantage(self):
        s = v.EngineState(v.Parameters())
        for team in ("A", "B"):
            s.teams[f"ENG1|{team}"] = v.TeamState(2.0, 1.5, 6.0, (T0-timedelta(days=5)).isoformat(), "2023-24")
        self.assertGreater(s.predict_batch([fx()])[0]["mu_home"], s.pending_predictions["f1"]["mu_away"])

    def test_08_same_goal_performance_adjusts_for_opponent_strength(self):
        weak, strong = v.EngineState(v.Parameters()), v.EngineState(v.Parameters())
        weak.teams["ENG1|B"] = v.TeamState(0.0, -4.0, 5.0, (T0-timedelta(days=5)).isoformat(), "2023-24")
        strong.teams["ENG1|B"] = v.TeamState(0.0, 4.0, 5.0, (T0-timedelta(days=5)).isoformat(), "2023-24")
        pw, ps = weak.predict_batch([fx("w")])[0], strong.predict_batch([fx("s")])[0]
        self.assertGreater(2.0 - ps["mu_home"], 2.0 - pw["mu_home"])

    def test_09_same_kickoff_predict_before_update(self):
        s = v.EngineState(v.Parameters()); batch = [fx("f1", "A", "B"), fx("f2", "C", "D")]
        before = json.loads(s.serialize()); self.assertEqual(len(s.predict_batch(batch)), 2); after = json.loads(s.serialize())
        self.assertEqual(after["teams"], before["teams"]); self.assertEqual(after["competitions"], before["competitions"])
        labels = {"f1": (2, 0, T0+timedelta(hours=3)), "f2": (1, 1, T0+timedelta(hours=3))}
        with self.assertRaises(v.V21Error): s.apply_batch(batch, labels, as_of=T0)
        s.apply_batch(batch, labels, as_of=T0+timedelta(hours=3)); self.assertEqual(s.seen_fixtures, {"f1", "f2"})

    def test_10_fail_closed_future_cutoff_duplicate_identity_and_labels(self):
        s = v.EngineState(v.Parameters())
        with self.assertRaises(v.V21Error): s.predict_batch([fx("a", "A", "B"), fx("b", "A", "C")])
        with self.assertRaises(v.V21Error): s.predict_batch([fx("x"), fx("x", "C", "D")])
        s.predict_batch([fx("ok")])
        with self.assertRaises(v.V21Error): s.predict_batch([fx("past", "C", "D", kickoff=T0-timedelta(days=1))])
        with self.assertRaises(v.V21Error): s.apply_batch([fx("ok")], {"ok": (-1, 0, T0+timedelta(hours=3))}, as_of=T0+timedelta(hours=3))
        with self.assertRaises(v.V21Error): s.apply_batch([fx("ok")], {"ok": (1.0, 0, T0+timedelta(hours=3))}, as_of=T0+timedelta(hours=3))
        with self.assertRaises(v.V21Error): s.apply_batch([fx("ok")], {"wrong": (1, 0, T0+timedelta(hours=3))}, as_of=T0+timedelta(hours=3))

    def test_11_cross_season_shrink_exactly_once_and_monotone(self):
        s = v.EngineState(v.Parameters(cross_season_shrink=0.40))
        s.teams["ENG1|A"] = v.TeamState(5.0, -3.0, 8.0, (T0-timedelta(days=30)).isoformat(), "2022-23")
        s.predict_batch([fx("n1", "A", "B", season="2023-24")]); once = s.teams["ENG1|A"]
        self.assertAlmostEqual(once.attack_residual_sum, 2.0); self.assertAlmostEqual(once.defence_residual_sum, -1.2)
        self.assertAlmostEqual(once.evidence, 3.2); self.assertEqual(once.season_transition_count, 1)
        s.predict_batch([fx("n2", "A", "C", season="2023-24", kickoff=T0+timedelta(days=1))]); twice = s.teams["ENG1|A"]
        self.assertAlmostEqual(twice.attack_residual_sum, 2.0); self.assertEqual(twice.season_transition_count, 1)

    def test_12_serialization_restore_prediction_bytes_identical(self):
        s = v.EngineState(v.Parameters()); s.teams["ENG1|A"] = v.TeamState(2.0, 1.0, 3.0, (T0-timedelta(days=10)).isoformat(), "2023-24")
        restored = v.EngineState.deserialize(s.serialize()); f = fx("z", "C", "D", kickoff=T0+timedelta(days=3))
        a, b = s.predict_batch([f])[0], restored.predict_batch([f])[0]
        self.assertEqual(v.canonical_bytes(a), v.canonical_bytes(b)); self.assertEqual(s.serialize(), restored.serialize())

    def test_13_matrix_nonnegative_finite_normalized_and_1x2_same_matrix(self):
        m = v.independent_poisson_matrix(1.71, 1.09, 14)
        self.assertAlmostEqual(sum(sum(r) for r in m), 1.0, places=12); self.assertTrue(all(math.isfinite(x) and x >= 0.0 for r in m for x in r))
        p = v.matrix_1x2(m); direct = {"home": 0.0, "draw": 0.0, "away": 0.0}
        for i, row in enumerate(m):
            for j, q in enumerate(row): direct["home" if i > j else "draw" if i == j else "away"] += q
        for k in direct: self.assertAlmostEqual(p[k], direct[k], places=14)

if __name__ == "__main__":
    unittest.main()
