from __future__ import annotations

import inspect
import unittest

import historical_pit_replay as core
import historical_pit_sharded_common as common
import historical_pit_sharded_predict as prediction
import historical_pit_sharded_source as source
from candidate_c_historical import monotonic_contract_holds


class HistoricalPITShardedTests(unittest.TestCase):
    def test_fixed_six_shards_exact_50(self):
        self.assertEqual(common.SHARD_N, 6)
        self.assertEqual(common.SHARD_SIZE, 50)
        got = [common.shard_bounds(i) for i in range(6)]
        self.assertEqual(got, [
            (0, 50, "shard-000-049"), (50, 100, "shard-050-099"),
            (100, 150, "shard-100-149"), (150, 200, "shard-150-199"),
            (200, 250, "shard-200-249"), (250, 300, "shard-250-299"),
        ])

    def test_old_timeout_is_technical_no_score_only(self):
        self.assertEqual(common.OLD_TIMEOUT_RUN_ID, 33485502884)
        self.assertEqual(common.OLD_TIMEOUT_HEAD, "f02f6780067bf076501bb173226c02795d68d8f0")
        self.assertEqual(common.OLD_TIMEOUT_STATUS, "TECHNICAL_TIMEOUT_NO_SCORE")

    def test_external_request_bounds(self):
        self.assertEqual(common.SOURCE_TIMEOUT_SECONDS, 15)
        self.assertEqual(common.SOURCE_ATTEMPTS, 2)
        self.assertLessEqual(common.MAX_SOURCE_WORKERS, 8)

    def test_offline_prediction_module_has_no_network_entrypoints(self):
        text = inspect.getsource(prediction).lower()
        for forbidden in ("urllib", "urlopen", "requests.", "sportsmole.co.uk", "web.archive.org", "kaggle.com", "bounded_fetch"):
            self.assertNotIn(forbidden, text)

    def test_original_scoring_contract_unchanged(self):
        self.assertEqual(core.LEAGUE, "ENG1")
        self.assertEqual(core.SEASON, "2023-24")
        self.assertEqual(core.FULL_SEASON_N, 380)
        self.assertEqual(core.COHORT_N, 300)
        self.assertEqual(core.T15_MINUTES, 15)
        self.assertEqual(core.RELEASE_HOURS, 3)
        self.assertEqual(core.BOOTSTRAP_N, 2000)
        self.assertEqual(core.BOOTSTRAP_SEED, 20260901)
        self.assertEqual(core.PROMOTION_MIN_ACTIVE_N, 60)
        self.assertAlmostEqual(core.PROMOTION_MIN_LL_IMPROVEMENT, 0.002)

    def test_uncertainty_contract_stays_monotonic(self):
        self.assertTrue(monotonic_contract_holds())

    def test_source_freeze_does_not_persist_web_body_contract(self):
        text = inspect.getsource(source.source_freeze)
        self.assertIn('"raw_webpage_body_persisted": False', text)
        self.assertIn("labels_read", text)

    def test_score_terminal_names_are_historical_only(self):
        text = inspect.getsource(core.score)
        self.assertIn("HISTORICAL_PIT_CANDIDATE_PASSED", text)
        self.assertIn("HISTORICAL_PIT_NOT_PROMOTED", text)
        self.assertNotIn("BLIND_TEST_PASSED", text)


if __name__ == "__main__":
    unittest.main()
