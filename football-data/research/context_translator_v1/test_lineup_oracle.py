from __future__ import annotations

import unittest

import lineup_oracle as o


class LineupOracleTests(unittest.TestCase):
    def test_required_research_tags(self):
        self.assertEqual(
            set(o.TAGS),
            {"HISTORICAL_LINEUP_ORACLE", "UPPER_BOUND_ONLY", "NOT_STRICT_PIT", "NOT_PROMOTION_ELIGIBLE", "POST_VIEW_RESEARCH"},
        )
        self.assertEqual(o.COHORT_SHA, "4663d6a534840e4b80975ee104e86bed0b5402cb332ee28d46c9cac4da2c9cba")

    def test_sanitized_packet_rejects_target_result_fields(self):
        o.assert_sanitized({"fixture_id": "x", "home": {"formation": "4-3-3"}})
        with self.assertRaises(o.OracleError):
            o.assert_sanitized({"fixture_id": "x", "score": "2-1"})
        with self.assertRaises(o.OracleError):
            o.assert_sanitized({"fixture_id": "x", "events": []})
        with self.assertRaises(o.OracleError):
            o.assert_sanitized({"fixture_id": "x", "rating": 8.2})

    def test_identity_mapping_exact_and_unknown_shrink(self):
        reg = {"team_x": {"joao pedro": "understat_player_1", "pape matar sarr": "understat_player_2"}}
        pid, reason = o.map_player({"source_player_id": "pl:10", "name": "João Pedro"}, "team_x", reg)
        self.assertEqual(pid, "understat_player_1")
        self.assertEqual(reason, "EXACT_NORMALIZED_NAME")
        pid, reason = o.map_player({"source_player_id": "pl:11", "name": "Completely New"}, "team_x", reg)
        self.assertEqual(pid, "oracle_pl_11")
        self.assertIn("SHRINK", reason)

    def test_same_kickoff_batch_never_enters_context_reference(self):
        target = {
            "fixture_id": "target", "pl_match_id": "2", "kickoff_utc": "2024-01-01T15:00:00Z",
            "home_team_id": "A", "away_team_id": "B",
            "home": {"manager": {"source_manager_id": "pl:m1"}, "formation": "4-3-3"},
            "away": {"manager": {"source_manager_id": "pl:m2"}, "formation": "4-4-2"},
        }
        same_batch = {
            "fixture_id": "same", "pl_match_id": "1", "kickoff_utc": "2024-01-01T15:00:00Z",
            "home_team_id": "A", "away_team_id": "C",
            "home": {"manager": {"source_manager_id": "pl:m1"}, "formation": "4-3-3",
                     "starting_xi": [{"source_player_id": f"pl:{i}", "name": f"P{i}", "position": "Midfielder"} for i in range(11)]},
            "away": {"manager": {"source_manager_id": "pl:m3"}, "formation": "4-4-2",
                     "starting_xi": [{"source_player_id": f"pl:x{i}", "name": f"X{i}", "position": "Midfielder"} for i in range(11)]},
        }
        usage, counts = o.prior_context_usage([same_batch, target], target, "2024-01-01T14:45:00Z", {}, "coach_formation")
        self.assertEqual(usage, {})
        self.assertEqual(counts, {"home": 0, "away": 0})

    def test_decision_threshold_is_non_micro(self):
        self.assertEqual(o.MIN_LL_IMPROVEMENT, 0.002)
        self.assertEqual(o.BOOTSTRAP_N, 2000)
        self.assertGreater(o.MIN_LL_IMPROVEMENT, 1e-6)


if __name__ == "__main__":
    unittest.main()
