from __future__ import annotations

import importlib.util
import json
import unittest
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "historical_pit_xi_replay.py"
CONTRACT = HERE / "HISTORICAL_PIT_XI_REPLAY_CONTRACT.json"

spec = importlib.util.spec_from_file_location("xi_replay", RUNNER)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class HistoricalPitXiReplayTests(unittest.TestCase):
    def test_frozen_constants(self):
        self.assertEqual(m.DECAY, 0.78)
        self.assertEqual(m.SMOOTH, 0.50)
        self.assertEqual(m.LOOKBACK_POOL, 20)
        self.assertEqual(m.FEATURE_LOOKBACK, 8)
        self.assertEqual(m.MAX_CANDIDATES, 32)
        self.assertEqual(m.TARGET_SUM, 11.0)
        self.assertEqual(m.TARGET_SEASONS, (2020, 2021, 2022))
        self.assertEqual(m.EXPECTED_TARGET_N, 5478)
        self.assertEqual(m.EXPECTED_PER_SEASON, 1826)

    def test_contract_governance(self):
        c = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(c["status"], "FROZEN_BEFORE_BATCH_TARGET_XI_SCORING")
        self.assertEqual(c["parent"]["head"], "a90762a97515f3edd564e8ad204db0d0d4231494")
        self.assertEqual(c["source"]["database_sha256"], m.EXPECTED_DB_SHA256)
        self.assertEqual(c["target_cohort"]["expected_fixture_n"], 5478)
        self.assertTrue(c["target_cohort"]["consumed_history"])
        self.assertTrue(c["mechanism"]["no_parameter_search"])
        self.assertTrue(c["mechanism"]["no_training_or_refit"])
        self.assertTrue(c["mechanism"]["no_injury_backfill"])
        self.assertTrue(c["label_policy"]["target_confirmed_xi_allowed_only_after_prediction_fixed"])
        self.assertFalse(c["label_policy"]["target_result_or_score_access"])
        for key in ("open_or_use_3504", "retune_on_2020_2022_labels", "use_2023_as_fresh_confirmation", "change_formal_v2", "change_frozen_v3_1_1", "change_CURRENT", "change_production_pointer", "change_formal_weights"):
            self.assertTrue(c["forbidden"][key])

    def test_projection_sum_and_order(self):
        raw = [0.85] * 8 + [0.55] * 7 + [0.15] * 9
        q = m.project_sum(raw)
        self.assertAlmostEqual(sum(q), 11.0, places=7)
        self.assertGreater(q[0], q[8])
        self.assertGreater(q[8], q[15])
        self.assertTrue(all(0 < x < 1 for x in q))

    def test_probability_uses_only_prior_history(self):
        h = deque(maxlen=m.LOOKBACK_POOL)
        h.append({"day": "2020-01-01", "players": {7: {"start": True, "minutes": 90.0}}})
        h.append({"day": "2020-01-08", "players": {7: {"start": False, "minutes": 20.0}}})
        p = m.raw_probability(h, 7)
        expected = (m.DECAY * 1.0 + 0.0 + m.SMOOTH) / (m.DECAY + 1.0 + 2.0 * m.SMOOTH)
        self.assertAlmostEqual(p, expected, places=12)

    def test_starter_label_rule(self):
        self.assertTrue(m.is_starter("GK"))
        self.assertTrue(m.is_starter("FW"))
        self.assertFalse(m.is_starter("Sub"))
        self.assertFalse(m.is_starter(" sub "))
        self.assertFalse(m.is_starter(None))

    def test_runner_has_no_result_or_market_feature_reads(self):
        src = RUNNER.read_text(encoding="utf-8")
        forbidden_feature_tokens = ["h_goals", "a_goals", "h_w", "h_d", "h_l", "odds", "home_xg", "away_xg"]
        for token in forbidden_feature_tokens:
            self.assertNotIn(token, src)
        self.assertIn('"market_access": False', src)
        self.assertIn("Phase A: fix every target prediction", src)
        self.assertIn("Phase B: only after today's target predictions are fixed", src)
        self.assertIn("same_date_matches_are_not_prior", CONTRACT.read_text(encoding="utf-8"))

    def test_final_status_is_research_only(self):
        src = RUNNER.read_text(encoding="utf-8")
        for literal in ("\"promotion_allowed\": False", "\"2023_opened_by_this_replay\": False", "\"3504_opened\": False", "\"formal_v2_changed\": False", "\"frozen_v3_1_1_changed\": False", "\"CURRENT_changed\": False"):
            self.assertIn(literal, src)


if __name__ == "__main__":
    unittest.main()
