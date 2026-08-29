from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.legacy_r43_registry import (
    SPECS,
    migration_candidates,
    native_probability_components,
    native_score_matrix_components,
    unresolved_sources,
)
from components.r43_native_matrix_components import R43TDynamicStateMatrixComponent
from components.r43_probability_matrix_adapters import R43RScoreMatrixTransportComponent, R43YScoreMatrixTransportComponent
from components.r43u_fixed_diagonal import DIAGONAL_FACTOR, R43UFixedDiagonalInflationComponent
from components.r43y_draw_calibration import DRAW_LOGIT_INTERCEPT
from governance.feature_integration_truth import FEATURE_TRUTH, numeric_enabled_families
from identity.team_identity import TeamIdentityResolver
from pipeline.governed_configuration import (
    FORMAL_DEFAULT_PROFILE,
    R43Q_RESEARCH_PROFILE,
    build_formal_default_engine,
    build_r43q_research_candidate_engine,
)
from pipeline.v500_baseline_adapter import FROZEN_V500_BLOB_SHA
from pit.feature_store import PointInTimeFeatureStore


EXPECTED_SOURCE_BLOBS = {
    "R43Q": "299b86ed07e49af0b9ec5c7632f519e91e836158",
    "R43R": "8748e795bb92780c47af934c3187db14c254a415",
    "R43T": "f6db4f0e6c0f544c058b15a7279731f55c5f6570",
    "R43U": "4ad46cca4acb618068f6db2601cf96bad4109698",
    "R43Y": "a342138bef97eb4acb0bcba015dea251a3280fdf",
}
EXPECTED_V500_BLOB = "9c302506c49aa1847e60cd7896fc1a80f3b6b457"
EXPECTED_Y_INTERCEPT = 0.1322913820792354


def resolver() -> TeamIdentityResolver:
    return TeamIdentityResolver([
        {"source_namespace": "test", "source_team_id": "h", "canonical_team_id": "H", "mapping_method": "test", "provenance_hash": "test"},
        {"source_namespace": "test", "source_team_id": "a", "canonical_team_id": "A", "mapping_method": "test", "provenance_hash": "test"},
    ])


class FinalRebuildGovernanceTests(unittest.TestCase):
    def test_frozen_v500_identity_is_unchanged(self):
        self.assertEqual(FROZEN_V500_BLOB_SHA, EXPECTED_V500_BLOB)

    def test_all_r43_sources_are_resolved_migrated_and_disabled_by_registry(self):
        self.assertEqual(set(SPECS), set(EXPECTED_SOURCE_BLOBS))
        self.assertEqual(unresolved_sources(), ())
        self.assertEqual(migration_candidates(), ())
        for key, sha in EXPECTED_SOURCE_BLOBS.items():
            spec = SPECS[key]
            self.assertEqual(spec.source_blob_sha, sha)
            self.assertTrue(spec.source_resolved)
            self.assertTrue(spec.implementation_migrated)
            self.assertFalse(spec.enabled_by_default)
        self.assertEqual(native_score_matrix_components(), ("R43Q", "R43T", "R43U"))
        self.assertEqual(native_probability_components(), ("R43R", "R43Y"))

    def test_fixed_r43_numerical_constants_are_not_drifted(self):
        self.assertEqual(DIAGONAL_FACTOR, 1.25)
        self.assertAlmostEqual(DRAW_LOGIT_INTERCEPT, EXPECTED_Y_INTERCEPT, places=15)

    def test_research_components_remain_disabled_by_default(self):
        self.assertFalse(R43TDynamicStateMatrixComponent().enabled)
        self.assertFalse(R43UFixedDiagonalInflationComponent().enabled)
        self.assertFalse(R43RScoreMatrixTransportComponent().enabled)
        self.assertFalse(R43YScoreMatrixTransportComponent().enabled)

    def test_default_feature_truth_enables_no_numeric_feature_family(self):
        self.assertEqual(numeric_enabled_families(), ())
        self.assertTrue(FEATURE_TRUTH["lineup_pstart"].historical_mechanism_gate_passed)
        self.assertFalse(FEATURE_TRUTH["lineup_pstart"].numeric_effect_enabled)
        self.assertFalse(FEATURE_TRUTH["player_technical"].formal_promotion_allowed)
        self.assertFalse(FEATURE_TRUTH["head_coach"].formal_promotion_allowed)
        market = FEATURE_TRUTH["market_1x2_ah_ou"]
        self.assertTrue(market.currently_pit_bound_to_consumer)
        self.assertFalse(market.historical_mechanism_gate_passed)
        self.assertFalse(market.numeric_effect_enabled)
        self.assertFalse(market.formal_promotion_allowed)

    def test_formal_and_research_profiles_cannot_be_confused(self):
        store = PointInTimeFeatureStore()
        formal = build_formal_default_engine(resolver(), store)
        research = build_r43q_research_candidate_engine(resolver(), store)
        self.assertEqual(formal.receipt.profile, FORMAL_DEFAULT_PROFILE)
        self.assertTrue(formal.receipt.formal_default)
        self.assertFalse(formal.receipt.research_only)
        self.assertEqual(formal.receipt.baseline_component_id, "v500_frozen_score_matrix")
        self.assertFalse(formal.receipt.market_numeric_effect_enabled)
        self.assertEqual(research.receipt.profile, R43Q_RESEARCH_PROFILE)
        self.assertFalse(research.receipt.formal_default)
        self.assertTrue(research.receipt.research_only)
        self.assertEqual(research.receipt.baseline_component_id, "R43Q_market_score_baseline")
        self.assertTrue(research.receipt.market_numeric_effect_enabled)
        self.assertFalse(research.receipt.lineup_numeric_effect_enabled)
        self.assertFalse(research.receipt.player_technical_numeric_effect_enabled)
        self.assertFalse(research.receipt.head_coach_numeric_effect_enabled)
        self.assertFalse(research.receipt.availability_numeric_effect_enabled)


if __name__ == "__main__":
    unittest.main()
