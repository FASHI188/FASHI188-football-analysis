from __future__ import annotations

import importlib.util
import math
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("formal_fusion_v2", HERE / "formal_fusion_v2.py")
ff = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ff
spec.loader.exec_module(ff)

hxg = ff._load_xg_module()
T0 = datetime(2025, 8, 10, 15, tzinfo=timezone.utc)


def fixture(fid="f1", comp="ENG_PremierLeague", season="2025/26", home="A", away="B", t=T0):
    return hxg.FixtureRow(fid, comp, season, t, home, away, home, away)


def seed_component(state, team, venue, comp, value=4.0, weight=8.0):
    target = state.venue_attack if comp == "attack" else state.venue_defence
    pooled = state.pooled_attack if comp == "attack" else state.pooled_defence
    when = T0 - timedelta(days=5)
    target[(team, venue)] = hxg.ResidualState(value, weight, when, "2025/26")
    pooled[team] = hxg.ResidualState(value, weight, when, "2025/26")


class FormalFusionV2WiringTests(unittest.TestCase):
    def test_01_contract_lock(self):
        c = ff.load_frozen_contract()
        self.assertEqual(ff.FUSION_WEIGHT, 0.75)
        self.assertFalse(ff.FORMAL_ENABLEMENT)
        self.assertEqual(c["identity"]["new_historical_confirmation"]["n"], 1752)
        self.assertTrue(c["identity"]["new_historical_confirmation"]["historical_completed_only"])
        self.assertFalse(c["identity"]["new_historical_confirmation"]["prospective_queue"])

    def test_02_no_xg_exact_v1_fallback(self):
        state = ff.new_candidate_state()
        f = fixture()
        raw_xg, raw_v1 = state.predict_batch([f], include_matrix=True)
        self.assertTrue(raw_xg[0]["dynamic"]["fallback_exact_v1"])
        result = ff.predict_formal_batch(ff.new_candidate_state(), [f])[0]
        self.assertTrue(result["audit"]["fallback_exact_v1"])
        self.assertEqual(result["audit"]["route"], "FROZEN_V1_EXACT_FALLBACK")
        self.assertEqual(result["prediction"], raw_v1[0])

    def test_03_active_full_matrix_and_1x2_identity(self):
        state = ff.new_candidate_state()
        for team, venue, comp, value in (
            ("A", "home", "attack", 3.0),
            ("A", "home", "defence", 1.0),
            ("B", "away", "attack", 1.0),
            ("B", "away", "defence", 0.0),
        ):
            seed_component(state, team, venue, comp, value, 8.0)
        f = fixture()
        xg_pred, v1_pred = state.predict_batch([f], include_matrix=True)
        self.assertFalse(xg_pred[0]["dynamic"]["fallback_exact_v1"])
        fused = ff.blend_active_predictions(v1_pred[0], xg_pred[0])
        self.assertAlmostEqual(sum(c["probability"] for c in fused["score_matrix"]), 1.0, 12)
        self.assertTrue(all(math.isfinite(c["probability"]) and c["probability"] >= 0 for c in fused["score_matrix"]))
        expected = [
            0.25 * v1_pred[0][key] + 0.75 * xg_pred[0][key]
            for key in ("p_home", "p_draw", "p_away")
        ]
        s = sum(expected)
        expected = [x / s for x in expected]
        self.assertAlmostEqual(fused["p_home"], expected[0], 12)
        self.assertAlmostEqual(fused["p_draw"], expected[1], 12)
        self.assertAlmostEqual(fused["p_away"], expected[2], 12)

    def test_04_same_kickoff_isolation_and_release_gate(self):
        state = ff.new_candidate_state()
        batch = [fixture("a", home="A", away="B"), fixture("b", home="C", away="D")]
        before = ff.predict_formal_batch(state, batch)
        self.assertTrue(all(row["audit"]["fallback_exact_v1"] for row in before))
        labels = {
            f.fixture_id: hxg.ReleasedLabel(1, 0, 1.4, 0.5, T0 + timedelta(hours=3))
            for f in batch
        }
        with self.assertRaises(hxg.XGError):
            ff.apply_completed_xg_batch(state, batch, labels, T0 + timedelta(hours=2, minutes=59))
        ff.apply_completed_xg_batch(state, batch, labels, T0 + timedelta(hours=3))

    def test_05_all_frozen_leagues_cold_start_fallback(self):
        contract = ff.load_frozen_contract()
        competitions = sorted(contract["lock"]["training_universe"]["competitions"])
        self.assertEqual(len(competitions), 16)
        for i, comp in enumerate(competitions):
            state = ff.new_candidate_state()
            f = fixture(f"cold-{i}", comp=comp, home=f"NEW_HOME_{i}", away=f"NEW_AWAY_{i}")
            result = ff.predict_formal_batch(state, [f])[0]
            self.assertTrue(result["audit"]["fallback_exact_v1"], comp)
            p = result["prediction"]
            self.assertAlmostEqual(p["p_home"] + p["p_draw"] + p["p_away"], 1.0, 12)
            self.assertAlmostEqual(sum(c["probability"] for c in p["score_matrix"]), 1.0, 12)

    def test_06_no_prospective_queue_surface(self):
        self.assertFalse(hasattr(ff, "create_queue"))
        self.assertFalse(hasattr(ff, "fetch_future"))
        self.assertFalse(hasattr(ff, "train"))
        self.assertFalse(hasattr(ff, "tune"))

    def test_07_matrix_support_fail_closed(self):
        state = ff.new_candidate_state()
        f = fixture()
        xg, v1 = state.predict_batch([f], include_matrix=True)
        bad = dict(xg[0])
        bad["score_matrix"] = xg[0]["score_matrix"][:-1]
        with self.assertRaises(ff.FormalFusionError):
            ff.blend_active_predictions(v1[0], bad)


if __name__ == "__main__":
    unittest.main()
