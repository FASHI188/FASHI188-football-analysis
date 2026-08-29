from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly.feature_assembler import FeatureAssembler
from identity.team_identity import TeamIdentityResolver
from pipeline.unified_inference import FixtureRequest, UnifiedInferenceEngine, matrix_hash, one_x_two
from pipeline.v500_baseline_adapter import FROZEN_V500_BLOB_SHA, FrozenV500MatrixBaseline
from pit.feature_store import PointInTimeFeatureStore
import validation.bayesian_dynamic_state_oof_v500 as v500


COMPETITION = "NOR_Eliteserien"
SEASON = "2024"
TARGET_DATE = "2024-04-27"
TARGET_HOME = "Rosenborg"
TARGET_AWAY = "Bodo/Glimt"
SELECTED_PROFILE = "medium_balanced"
EXPECTED_R43AA0_CANDIDATE_1X2 = {
    "home": 0.25217858285118316,
    "draw": 0.25810955952640713,
    "away": 0.48971185762240926,
}


def reconstruct_consumed_v500_matrix() -> list[dict]:
    """Compatibility-only reconstruction of one already-consumed R43AA0 row.

    The frozen V500 season simulator is called unchanged. A temporary metric spy
    captures matrices for the target fixture; the selected profile is fixed from
    the already-consumed R43AA0 artifact, never tuned here.
    """
    report = v500.load_json(v500.REPORT_ROOT / f"{COMPETITION}.json")
    all_matches = v500.read_processed_matches(COMPETITION)
    captured: list[list[dict]] = []
    original = v500._metric_row

    def spy(matrix, match):
        if (
            str(match.season) == SEASON
            and match.date.date().isoformat() == TARGET_DATE
            and match.home_team == TARGET_HOME
            and match.away_team == TARGET_AWAY
        ):
            captured.append(deepcopy(matrix))
        return original(matrix, match)

    v500._metric_row = spy
    try:
        v500._simulate_season(COMPETITION, SEASON, all_matches, report)
    finally:
        v500._metric_row = original

    # Per eligible fixture: baseline first, then one candidate matrix per PROFILES order.
    if len(captured) != 1 + len(v500.PROFILES):
        raise AssertionError(f"unexpected captured matrix count: {len(captured)}")
    profile_index = next(i for i, profile in enumerate(v500.PROFILES) if profile["id"] == SELECTED_PROFILE)
    return captured[1 + profile_index]


class UnifiedInferenceV500CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v500_matrix = reconstruct_consumed_v500_matrix()

    def _engine_and_request(self):
        resolver = TeamIdentityResolver([
            {
                "source_namespace": "legacy_v500",
                "source_team_id": TARGET_HOME,
                "canonical_team_id": "team:rosenborg",
                "mapping_method": "compat_exact",
                "provenance_hash": FROZEN_V500_BLOB_SHA,
            },
            {
                "source_namespace": "legacy_v500",
                "source_team_id": TARGET_AWAY,
                "canonical_team_id": "team:bodo-glimt",
                "mapping_method": "compat_exact",
                "provenance_hash": FROZEN_V500_BLOB_SHA,
            },
        ])
        engine = UnifiedInferenceEngine(
            resolver,
            PointInTimeFeatureStore(),
            FeatureAssembler(),
            FrozenV500MatrixBaseline(),
        )
        request = FixtureRequest(
            fixture_id=f"{COMPETITION}:{SEASON}:{TARGET_DATE}:{TARGET_HOME}:{TARGET_AWAY}",
            as_of=datetime(2024, 4, 27, 12, 0, tzinfo=timezone.utc),
            home_source_namespace="legacy_v500",
            home_source_team_id=TARGET_HOME,
            home_source_name=TARGET_HOME,
            away_source_namespace="legacy_v500",
            away_source_team_id=TARGET_AWAY,
            away_source_name=TARGET_AWAY,
        )
        return engine, request

    def test_reconstructed_matrix_matches_consumed_r43aa0_candidate_1x2(self):
        actual = one_x_two(self.v500_matrix)
        for key, expected in EXPECTED_R43AA0_CANDIDATE_1X2.items():
            self.assertAlmostEqual(actual[key], expected, places=12)

    def test_shared_path_preserves_v500_matrix_hash_in_all_modes(self):
        engine, request = self._engine_and_request()
        expected_hash = matrix_hash(self.v500_matrix)
        payload = {
            "source_model_blob_sha": FROZEN_V500_BLOB_SHA,
            "score_matrix": self.v500_matrix,
            "score_matrix_hash": expected_hash,
        }
        results = [engine.predict(mode, request, payload) for mode in ("dataset", "replay", "live")]
        self.assertEqual({result.score_matrix_hash for result in results}, {expected_hash})
        self.assertEqual({result.top1 for result in results}, {"away"})
        for result in results:
            for key, expected in EXPECTED_R43AA0_CANDIDATE_1X2.items():
                self.assertAlmostEqual(result.probabilities[key], expected, places=12)
            self.assertEqual(result.component_chain[0]["component_id"], "v500_frozen_score_matrix")
            self.assertEqual(result.component_chain[0]["output_matrix_hash"], expected_hash)

    def test_wrong_v500_lineage_fails_closed(self):
        engine, request = self._engine_and_request()
        with self.assertRaisesRegex(ValueError, "source_model_blob_sha mismatch"):
            engine.predict(
                "replay",
                request,
                {"source_model_blob_sha": "wrong", "score_matrix": self.v500_matrix},
            )

    def test_wrong_declared_matrix_hash_fails_closed(self):
        engine, request = self._engine_and_request()
        with self.assertRaisesRegex(ValueError, "score_matrix_hash mismatch"):
            engine.predict(
                "replay",
                request,
                {
                    "source_model_blob_sha": FROZEN_V500_BLOB_SHA,
                    "score_matrix": self.v500_matrix,
                    "score_matrix_hash": "wrong",
                },
            )


if __name__ == "__main__":
    unittest.main()
