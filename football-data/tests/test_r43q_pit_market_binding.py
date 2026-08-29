from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly.feature_assembler import FeatureAssembler, FeatureFamilyPolicy
from components.r43_native_matrix_components import R43QMarketScoreBaseline, dense_to_cells
from components.r43q_market_score_core import R43QMarketScoreCore
from identity.team_identity import TeamIdentityResolver
from pipeline.unified_inference import FeatureReadSpec, FixtureRequest, UnifiedInferenceEngine, matrix_hash
from pit.feature_store import PITFeatureRecord, PointInTimeFeatureStore


AS_OF = datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc)
SNAPSHOT = AS_OF - timedelta(minutes=30)
Q_PAYLOAD = {
    "one_x_two_odds": {"home": 2.05, "draw": 3.35, "away": 3.75},
    "asian_handicap": {"line": -0.25, "home": 1.97, "away": 1.93},
    "over_under": {"line": 2.5, "over": 1.95, "under": 1.95},
}


def resolver() -> TeamIdentityResolver:
    return TeamIdentityResolver([
        {
            "source_namespace": "test",
            "source_team_id": "home",
            "canonical_team_id": "HOME",
            "mapping_method": "test",
            "provenance_hash": "test",
        },
        {
            "source_namespace": "test",
            "source_team_id": "away",
            "canonical_team_id": "AWAY",
            "mapping_method": "test",
            "provenance_hash": "test",
        },
    ])


def request(fid: str = "fx") -> FixtureRequest:
    return FixtureRequest(
        fixture_id=fid,
        as_of=AS_OF,
        home_source_namespace="test",
        home_source_team_id="home",
        home_source_name=None,
        away_source_namespace="test",
        away_source_team_id="away",
        away_source_name=None,
    )


def market_record(
    fid: str = "fx",
    *,
    snapshot_at: datetime = SNAPSHOT,
    observed_at: datetime | None = None,
    known_at: datetime | None = None,
    effective_at: datetime | None = None,
    historical_use_allowed: bool = True,
    entity_type: str = "fixture_market",
    canonical_entity_id: str | None = None,
    payload: dict | None = None,
    record_id: str = "market-1",
) -> PITFeatureRecord:
    observed = observed_at or snapshot_at + timedelta(minutes=1)
    known = known_at or observed + timedelta(minutes=1)
    effective = effective_at or snapshot_at
    value = {"snapshot_timestamp_utc": snapshot_at.isoformat(), **(payload or Q_PAYLOAD)}
    return PITFeatureRecord(
        feature_family="market_1x2_ah_ou",
        entity_type=entity_type,
        canonical_entity_id=canonical_entity_id or fid,
        fixture_id=fid,
        value=value,
        source_name="test_market",
        source_record_id=record_id,
        source_hash=f"source:{record_id}",
        observed_at=observed,
        known_at=known,
        effective_at=effective,
        expires_at=AS_OF + timedelta(hours=1),
        leakage_class="prematch_market_snapshot",
        historical_use_allowed=historical_use_allowed,
        adapter_version="test-v1",
    )


def research_assembler() -> FeatureAssembler:
    return FeatureAssembler({
        "market_1x2_ah_ou": FeatureFamilyPolicy(True, False, True),
    })


def make_engine(store: PointInTimeFeatureStore, assembler: FeatureAssembler | None = None):
    baseline = R43QMarketScoreBaseline(store)
    engine = UnifiedInferenceEngine(
        resolver(),
        store,
        assembler or research_assembler(),
        baseline,
        (),
    )
    return engine, baseline


class R43QPITMarketBindingTests(unittest.TestCase):
    def test_valid_atomic_pit_snapshot_exactly_matches_r43q_core_and_receipt_proves_numeric_effect(self):
        record = market_record()
        store = PointInTimeFeatureStore([record])
        engine, baseline = make_engine(store)
        result = engine.predict("replay", request(), {})
        expected = R43QMarketScoreCore.build(
            Q_PAYLOAD["one_x_two_odds"], Q_PAYLOAD["asian_handicap"], Q_PAYLOAD["over_under"]
        )
        self.assertEqual(result.score_matrix_hash, matrix_hash(dense_to_cells(expected["score_matrix"])))
        self.assertTrue(baseline.pit_bound_market)
        acts = result.feature_activation_receipt["activations"]
        self.assertEqual(len(acts), 1)
        act = acts[0]
        self.assertEqual(act["feature_family"], "market_1x2_ah_ou")
        self.assertTrue(act["recognized"])
        self.assertTrue(act["pit_legal"])
        self.assertTrue(act["assembled"])
        self.assertTrue(act["numeric_effect"])
        self.assertFalse(act["experiment_passed"])
        self.assertTrue(act["numeric_effect_enabled"])
        self.assertEqual(act["source_record_count"], 1)
        self.assertEqual(act["source_record_hashes"], [record.record_hash])
        self.assertNotEqual(act["component_input_hash"], act["component_output_hash"])

    def test_default_governance_policy_blocks_r43q_before_numeric_execution(self):
        store = PointInTimeFeatureStore([market_record()])
        baseline = R43QMarketScoreBaseline(store)
        engine = UnifiedInferenceEngine(resolver(), store, FeatureAssembler(), baseline, ())
        with self.assertRaisesRegex(RuntimeError, "disabled by governance policy"):
            engine.predict("replay", request(), {})
        self.assertEqual(baseline.numerical_feature_evidence(), ())

    def test_direct_market_payload_is_forbidden(self):
        store = PointInTimeFeatureStore([market_record()])
        engine, _ = make_engine(store)
        with self.assertRaisesRegex(RuntimeError, "forbids direct payload"):
            engine.predict("replay", request(), Q_PAYLOAD)

    def test_known_after_asof_fails_closed(self):
        late = market_record(
            snapshot_at=AS_OF - timedelta(minutes=5),
            observed_at=AS_OF + timedelta(minutes=1),
            known_at=AS_OF + timedelta(minutes=2),
        )
        engine, _ = make_engine(PointInTimeFeatureStore([late]))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            engine.predict("replay", request(), {})

    def test_effective_after_asof_fails_closed(self):
        future_effective = market_record(effective_at=AS_OF + timedelta(minutes=1))
        engine, _ = make_engine(PointInTimeFeatureStore([future_effective]))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            engine.predict("replay", request(), {})

    def test_historical_use_permission_is_required_even_for_live_candidate(self):
        blocked = market_record(historical_use_allowed=False)
        engine, _ = make_engine(PointInTimeFeatureStore([blocked]))
        for mode in ("dataset", "replay", "live"):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                engine.predict(mode, request(), {})

    def test_snapshot_timestamp_after_observed_at_fails_closed(self):
        invalid = market_record(
            snapshot_at=SNAPSHOT,
            observed_at=SNAPSHOT - timedelta(seconds=1),
            known_at=SNAPSHOT + timedelta(seconds=1),
        )
        engine, _ = make_engine(PointInTimeFeatureStore([invalid]))
        with self.assertRaisesRegex(RuntimeError, "later than observed_at"):
            engine.predict("replay", request(), {})

    def test_wrong_market_entity_type_fails_closed(self):
        invalid = market_record(entity_type="team")
        engine, _ = make_engine(PointInTimeFeatureStore([invalid]))
        with self.assertRaisesRegex(RuntimeError, "entity_type"):
            engine.predict("replay", request(), {})

    def test_foreign_fixture_market_record_is_not_read(self):
        foreign = market_record(canonical_entity_id="other-fixture")
        engine, _ = make_engine(PointInTimeFeatureStore([foreign]))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            engine.predict("replay", request(), {})

    def test_dataset_replay_live_use_identical_market_snapshot_numerics(self):
        hashes = []
        probs = []
        for mode in ("dataset", "replay", "live"):
            store = PointInTimeFeatureStore([market_record()])
            engine, _ = make_engine(store)
            result = engine.predict(mode, request(), {})
            hashes.append(result.score_matrix_hash)
            probs.append(tuple(sorted(result.probabilities.items())))
        self.assertEqual(len(set(hashes)), 1)
        self.assertEqual(len(set(probs)), 1)

    def test_latest_legal_atomic_snapshot_is_selected(self):
        older_payload = {
            "one_x_two_odds": {"home": 2.20, "draw": 3.20, "away": 3.55},
            "asian_handicap": {"line": 0.0, "home": 1.90, "away": 2.00},
            "over_under": {"line": 2.25, "over": 1.92, "under": 1.98},
        }
        older = market_record(
            snapshot_at=SNAPSHOT - timedelta(minutes=10),
            payload=older_payload,
            record_id="older",
        )
        newer = market_record(record_id="newer")
        store = PointInTimeFeatureStore([older, newer])
        engine, _ = make_engine(store)
        result = engine.predict("replay", request(), {})
        expected = R43QMarketScoreCore.build(
            Q_PAYLOAD["one_x_two_odds"], Q_PAYLOAD["asian_handicap"], Q_PAYLOAD["over_under"]
        )
        self.assertEqual(result.score_matrix_hash, matrix_hash(dense_to_cells(expected["score_matrix"])))
        acts = result.feature_activation_receipt["activations"]
        self.assertEqual(acts[0]["source_record_hashes"], [newer.record_hash])

    def test_manual_market_activation_cannot_replace_consumer_attestation(self):
        store = PointInTimeFeatureStore([market_record()])
        engine, _ = make_engine(store)
        fake = FeatureReadSpec(
            feature_family="market_1x2_ah_ou",
            numerical_values={"fake": 1},
            numerical_feature_names=("fake",),
            component_input_hash="a",
            component_output_hash="b",
        )
        with self.assertRaisesRegex(RuntimeError, "may not be supplied manually"):
            engine.predict("replay", request(), {}, feature_specs=(fake,))

    def test_baseline_and_unified_engine_cannot_use_different_pit_stores(self):
        baseline_store = PointInTimeFeatureStore([market_record()])
        engine_store = PointInTimeFeatureStore([market_record()])
        baseline = R43QMarketScoreBaseline(baseline_store)
        with self.assertRaisesRegex(ValueError, "baseline PIT store must be the unified engine PIT store"):
            UnifiedInferenceEngine(resolver(), engine_store, research_assembler(), baseline, ())


if __name__ == "__main__":
    unittest.main()
