from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.r43u_fixed_diagonal import R43UFixedDiagonalInflationComponent
from identity.team_identity import TeamIdentityResolver
from pipeline.governed_configuration import (
    FORMAL_DEFAULT_PROFILE,
    R43Q_RESEARCH_PROFILE,
    build_formal_default_engine,
    build_r43q_research_candidate_engine,
)
from pipeline.unified_inference import FixtureRequest
from pipeline.v500_baseline_adapter import FROZEN_V500_BLOB_SHA
from pit.feature_store import PITFeatureRecord, PointInTimeFeatureStore


UTC = timezone.utc
AS_OF = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
Q_PAYLOAD = {
    "one_x_two_odds": {"home": 2.05, "draw": 3.35, "away": 3.75},
    "asian_handicap": {"line": -0.25, "home": 1.97, "away": 1.93},
    "over_under": {"line": 2.5, "over": 1.95, "under": 1.95},
}


def resolver() -> TeamIdentityResolver:
    return TeamIdentityResolver([
        {"source_namespace": "test", "source_team_id": "h", "canonical_team_id": "H", "mapping_method": "test", "provenance_hash": "test"},
        {"source_namespace": "test", "source_team_id": "a", "canonical_team_id": "A", "mapping_method": "test", "provenance_hash": "test"},
    ])


def request() -> FixtureRequest:
    return FixtureRequest(
        fixture_id="fx",
        as_of=AS_OF,
        home_source_namespace="test",
        home_source_team_id="h",
        home_source_name=None,
        away_source_namespace="test",
        away_source_team_id="a",
        away_source_name=None,
    )


def market_record() -> PITFeatureRecord:
    snap = AS_OF - timedelta(minutes=20)
    return PITFeatureRecord(
        feature_family="market_1x2_ah_ou",
        entity_type="fixture_market",
        canonical_entity_id="fx",
        fixture_id="fx",
        value={"snapshot_timestamp_utc": snap.isoformat(), **Q_PAYLOAD},
        source_name="m9_test_market",
        source_record_id="fx:market",
        source_hash="m9-market-source",
        observed_at=snap + timedelta(minutes=1),
        known_at=snap + timedelta(minutes=2),
        effective_at=snap,
        expires_at=AS_OF + timedelta(hours=1),
        leakage_class="prematch_market_snapshot",
        historical_use_allowed=True,
        adapter_version="m9-test-v1",
    )


def v500_payload():
    matrix = [
        {"home_goals": 0, "away_goals": 0, "probability": 0.25},
        {"home_goals": 1, "away_goals": 0, "probability": 0.35},
        {"home_goals": 0, "away_goals": 1, "probability": 0.20},
        {"home_goals": 1, "away_goals": 1, "probability": 0.10},
        {"home_goals": 2, "away_goals": 1, "probability": 0.10},
    ]
    return {"source_model_blob_sha": FROZEN_V500_BLOB_SHA, "score_matrix": matrix}


class GovernedConfigurationTests(unittest.TestCase):
    def test_formal_default_is_frozen_v500_and_all_research_numerics_off(self):
        governed = build_formal_default_engine(resolver(), PointInTimeFeatureStore([market_record()]))
        receipt = governed.receipt
        self.assertEqual(receipt.profile, FORMAL_DEFAULT_PROFILE)
        self.assertTrue(receipt.formal_default)
        self.assertFalse(receipt.research_only)
        self.assertEqual(receipt.baseline_component_id, "v500_frozen_score_matrix")
        self.assertFalse(receipt.market_numeric_effect_enabled)
        self.assertFalse(receipt.lineup_numeric_effect_enabled)
        self.assertFalse(receipt.player_technical_numeric_effect_enabled)
        self.assertFalse(receipt.head_coach_numeric_effect_enabled)
        self.assertFalse(receipt.availability_numeric_effect_enabled)
        self.assertEqual(receipt.enabled_research_components, ())
        result = governed.engine.predict("live", request(), v500_payload())
        self.assertEqual(result.component_chain[0]["component_id"], "v500_frozen_score_matrix")
        self.assertEqual(result.feature_activation_receipt["activations"], [])

    def test_research_market_profile_is_explicitly_nonformal_and_only_market_numeric(self):
        store = PointInTimeFeatureStore([market_record()])
        governed = build_r43q_research_candidate_engine(resolver(), store)
        receipt = governed.receipt
        self.assertEqual(receipt.profile, R43Q_RESEARCH_PROFILE)
        self.assertTrue(receipt.research_only)
        self.assertFalse(receipt.formal_default)
        self.assertEqual(receipt.baseline_component_id, "R43Q_market_score_baseline")
        self.assertTrue(receipt.market_numeric_effect_enabled)
        self.assertFalse(receipt.lineup_numeric_effect_enabled)
        self.assertFalse(receipt.player_technical_numeric_effect_enabled)
        self.assertFalse(receipt.head_coach_numeric_effect_enabled)
        self.assertFalse(receipt.availability_numeric_effect_enabled)
        self.assertEqual(receipt.enabled_research_components, ())
        result = governed.engine.predict("replay", request(), {})
        activation = result.feature_activation_receipt["activations"][0]
        self.assertEqual(activation["feature_family"], "market_1x2_ah_ou")
        self.assertTrue(activation["numeric_effect"])
        self.assertFalse(activation["experiment_passed"])

    def test_research_market_profile_fails_without_legal_market_snapshot(self):
        governed = build_r43q_research_candidate_engine(resolver(), PointInTimeFeatureStore())
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            governed.engine.predict("replay", request(), {})

    def test_enabled_r43_components_need_second_explicit_opt_in(self):
        store = PointInTimeFeatureStore([market_record()])
        u = R43UFixedDiagonalInflationComponent(enabled=True)
        with self.assertRaisesRegex(RuntimeError, "explicit opt-in"):
            build_r43q_research_candidate_engine(resolver(), store, (u,))
        governed = build_r43q_research_candidate_engine(
            resolver(), store, (u,), allow_enabled_research_components=True
        )
        self.assertEqual(governed.receipt.enabled_research_components, ("R43U_fixed_diagonal_inflation",))
        self.assertTrue(governed.receipt.research_only)
        self.assertFalse(governed.receipt.formal_default)

    def test_disabled_r43_component_does_not_require_extra_opt_in_or_change_output(self):
        store = PointInTimeFeatureStore([market_record()])
        governed = build_r43q_research_candidate_engine(
            resolver(), store, (R43UFixedDiagonalInflationComponent(),)
        )
        result = governed.engine.predict("replay", request(), {})
        self.assertEqual(governed.receipt.enabled_research_components, ())
        self.assertFalse(result.component_chain[1]["enabled"])
        self.assertEqual(result.component_chain[0]["output_matrix_hash"], result.component_chain[1]["output_matrix_hash"])

    def test_dataset_replay_live_share_same_research_configuration_and_numerics(self):
        outputs = []
        for mode in ("dataset", "replay", "live"):
            store = PointInTimeFeatureStore([market_record()])
            governed = build_r43q_research_candidate_engine(resolver(), store)
            result = governed.engine.predict(mode, request(), {})
            outputs.append((result.score_matrix_hash, tuple(sorted(result.probabilities.items()))))
        self.assertEqual(len(set(outputs)), 1)


if __name__ == "__main__":
    unittest.main()
