from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone

import nextgen_current_v2_foundation_v1 as ng

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def fake_prediction() -> dict:
    matrix = [
        {"home_goals": 0, "away_goals": 0, "probability": 0.20},
        {"home_goals": 1, "away_goals": 0, "probability": 0.30},
        {"home_goals": 0, "away_goals": 1, "probability": 0.10},
        {"home_goals": 1, "away_goals": 1, "probability": 0.15},
        {"home_goals": 2, "away_goals": 0, "probability": 0.10},
        {"home_goals": 0, "away_goals": 2, "probability": 0.05},
        {"home_goals": 2, "away_goals": 1, "probability": 0.05},
        {"home_goals": 1, "away_goals": 2, "probability": 0.05},
    ]
    return {
        "schema_version": "synthetic-current-v2",
        "score_matrix": matrix,
        "p_home": 0.45,
        "p_draw": 0.35,
        "p_away": 0.20,
    }


def fake_batch() -> list[dict]:
    return [{
        "fixture_id": "synthetic-1",
        "prediction": fake_prediction(),
        "audit": {
            "route": "FUSION_V2_ACTIVE",
            "xg_weight": 0.75,
            "v1_weight": 0.25,
            "fallback_exact_v1": False,
        },
    }]


class FoundationPureContractTests(unittest.TestCase):
    def test_01_all_empty_experts_are_not_available_zero_zero(self):
        rows = ng.empty_experts()
        self.assertEqual(tuple(x.name for x in rows), ng.EXPERT_NAMES)
        for row in rows:
            self.assertEqual(row.status, "NOT_AVAILABLE")
            self.assertEqual(row.weight, 0)
            self.assertEqual(row.matrix_delta, 0)

    def test_02_nonzero_weight_is_rejected(self):
        with self.assertRaises(ng.FoundationContractError):
            ng.ExpertInterface("market", weight=0.01)

    def test_03_nonzero_matrix_delta_is_rejected(self):
        with self.assertRaises(ng.FoundationContractError):
            ng.ExpertInterface("market", matrix_delta=1e-6)

    def test_04_non_not_available_status_is_rejected(self):
        with self.assertRaises(ng.FoundationContractError):
            ng.ExpertInterface("market", status="ACTIVE")

    def test_05_missing_is_not_interpreted_as_zero_evidence(self):
        row = ng.ExpertInterface("market")
        self.assertEqual(row.status, "NOT_AVAILABLE")
        self.assertEqual(row.evidence_quality, "NOT_AVAILABLE")
        self.assertEqual(row.rejection_reason, "NOT_ACTIVATED")

    def test_06_time_interface_enforces_pit(self):
        ng.PITTimeInterface(
            as_of=T0,
            cutoff=T0 - timedelta(minutes=15),
            observed_at=T0 - timedelta(hours=1),
            available_at=T0 - timedelta(minutes=30),
        )
        with self.assertRaises(ng.FoundationContractError):
            ng.PITTimeInterface(
                as_of=T0,
                cutoff=T0 - timedelta(minutes=15),
                observed_at=T0 - timedelta(hours=1),
                available_at=T0,
            )

    def test_07_default_capability_and_data_quality_are_not_available(self):
        cap = ng.CapabilityInterface()
        quality = ng.DataQualityInterface()
        self.assertTrue(all(getattr(cap, x) == "NOT_AVAILABLE" for x in ng.CAPABILITY_NAMES))
        self.assertTrue(all(v == "NOT_AVAILABLE" for v in quality.__dict__.values()))

    def test_08_unified_matrix_is_read_only_identity_check(self):
        prediction = fake_prediction()
        before = ng.canonical_bytes(prediction)
        cells = ng.validate_unified_score_matrix(prediction)
        self.assertTrue(cells)
        self.assertEqual(ng.canonical_bytes(prediction), before)

    def test_09_stage0_returns_exact_current_v2_object(self):
        baseline = fake_batch()
        calls = []

        def predictor(state, fixtures):
            calls.append((state, tuple(fixtures)))
            return baseline

        result = ng.run_with_current_v2(predictor, "STATE", ["FIXTURE"])
        self.assertIs(result, baseline)
        self.assertEqual(len(calls), 1)
        self.assertEqual(ng.canonical_bytes(result), ng.canonical_bytes(baseline))

    def test_10_derived_semantics_are_matrix_derived_only(self):
        prediction = fake_prediction()
        derived = ng.derived_distribution_semantics(prediction)
        self.assertEqual(derived["one_x_two_top1"]["selection"], "HOME")
        self.assertAlmostEqual(derived["over_2_5"], 0.10, 12)
        self.assertAlmostEqual(derived["btts_yes"], 0.25, 12)
        self.assertIsNone(derived["mu_home"])
        self.assertIsNone(derived["mu_away"])

    def test_11_receipt_fields_are_reserved_out_of_band_only(self):
        metadata = ng.research_metadata()
        self.assertEqual(metadata["research_status"], "RESEARCH_ONLY_FOUNDATION")
        self.assertEqual(metadata["receipt_reserved_fields"], list(ng.RECEIPT_RESERVED_FIELDS))
        self.assertFalse(metadata["formal_output_mutated"])
        self.assertFalse(metadata["second_loader_created"])
        self.assertFalse(metadata["second_provider_created"])
        self.assertFalse(metadata["production_chain_created"])
        self.assertFalse(metadata["training"])
        self.assertFalse(metadata["tuning"])
        self.assertFalse(metadata["new_target_labels"])

    def test_12_canonical_sha_semantics_are_stable(self):
        value = fake_batch()
        self.assertEqual(ng.canonical_sha256(value), hashlib.sha256(ng.canonical_bytes(value)).hexdigest())


class CurrentV2ExactIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from historical_xg_challenger_v1 import historical_xg_challenger as hxg
            from new_engine_v1 import formal_fusion_v2 as formal_v2
        except Exception as exc:
            raise unittest.SkipTest(f"repository current-V2 imports unavailable: {exc}")
        cls.hxg = hxg
        cls.formal_v2 = formal_v2
        cls.fixture = hxg.FixtureRow(
            "nextgen-stage0-fallback",
            "ENG_PremierLeague",
            "2025/26",
            datetime(2025, 8, 10, 15, tzinfo=timezone.utc),
            "A",
            "B",
            "A",
            "B",
        )

    def _fresh_active_state(self):
        ff = self.formal_v2
        hxg = self.hxg
        state = ff.new_candidate_state()
        when = self.fixture.kickoff - timedelta(days=5)
        for team, venue, component, value in (
            ("A", "home", "attack", 3.0),
            ("A", "home", "defence", 1.0),
            ("B", "away", "attack", 1.0),
            ("B", "away", "defence", 0.0),
        ):
            target = state.venue_attack if component == "attack" else state.venue_defence
            pooled = state.pooled_attack if component == "attack" else state.pooled_defence
            target[(team, venue)] = hxg.ResidualState(value, 8.0, when, "2025/26")
            pooled[team] = hxg.ResidualState(value, 8.0, when, "2025/26")
        return state

    def _assert_exact_semantics(self, baseline, candidate):
        self.assertEqual(ng.canonical_bytes(candidate), ng.canonical_bytes(baseline))
        self.assertEqual(ng.canonical_sha256(candidate), ng.canonical_sha256(baseline))
        self.assertEqual(candidate[0]["audit"]["route"], baseline[0]["audit"]["route"])
        self.assertEqual(candidate[0]["audit"], baseline[0]["audit"])
        self.assertEqual(candidate[0]["prediction"]["score_matrix"], baseline[0]["prediction"]["score_matrix"])
        self.assertEqual(
            ng.derived_distribution_semantics(candidate[0]["prediction"]),
            ng.derived_distribution_semantics(baseline[0]["prediction"]),
        )

    def test_13_current_v2_exact_fallback_is_byte_equivalent(self):
        ff = self.formal_v2
        baseline = ff.predict_formal_batch(ff.new_candidate_state(), [self.fixture])
        candidate = ng.run_with_current_v2(ff.predict_formal_batch, ff.new_candidate_state(), [self.fixture])
        self.assertEqual(baseline[0]["audit"]["route"], "FROZEN_V1_EXACT_FALLBACK")
        self._assert_exact_semantics(baseline, candidate)

    def test_14_current_v2_active_fusion_is_byte_equivalent(self):
        ff = self.formal_v2
        baseline = ff.predict_formal_batch(self._fresh_active_state(), [self.fixture])
        candidate = ng.run_with_current_v2(ff.predict_formal_batch, self._fresh_active_state(), [self.fixture])
        self.assertEqual(baseline[0]["audit"]["route"], "FUSION_V2_ACTIVE")
        self._assert_exact_semantics(baseline, candidate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
