#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from independent_latent_1x2_v1 import (
    Independent1X2Error,
    Independent1X2LinkConfig,
    probabilities_from_latent_comparison,
)
from latent_observation_adapter_v1 import (
    ObservationAdapterError,
    PITIntensityEvidence,
    adapt_completed_match_intensity,
)

HERE = Path(__file__).resolve().parent
UTC = timezone.utc
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
SHA = "a" * 64


def cmp(margin: float, variance: float = 0.6, interpretation: str = "synthetic_latent_comparison_v1"):
    return {
        "latent_margin": margin,
        "latent_margin_variance": variance,
        "interpretation": interpretation,
    }


CFG_SYM = Independent1X2LinkConfig(home_advantage=0.0, draw_boundary=0.35, match_noise_variance=1.0)


class AdapterBehaviorTests(unittest.TestCase):
    def evidence(self, **kw):
        base = dict(
            team="A", fixture_id="fixture:1", competition="SYNTH",
            source_kind="completed_match_xg", source_identity="synthetic-source",
            source_url="https://example.invalid/synthetic", payload_sha256=SHA, collector_run_id="run:1",
            attack_intensity=1.8, defence_intensity=0.9,
            attack_reference=1.5, defence_reference=1.5,
            attack_observation_variance=0.3, defence_observation_variance=0.4,
            event_completed_at=T0,
            source_published_at=T0 + timedelta(minutes=2), source_published_at_trusted=True,
            collector_first_observed_at=T0 + timedelta(minutes=5),
            retrieved_at=T0 + timedelta(minutes=6), ingested_at=T0 + timedelta(minutes=7),
            prediction_cutoff=T0 + timedelta(days=3),
        )
        base.update(kw)
        return PITIntensityEvidence(**base)

    def test_log1p_difference_semantics(self):
        out = adapt_completed_match_intensity(self.evidence())
        self.assertAlmostEqual(out["attack_observation"], math.log1p(1.8) - math.log1p(1.5))
        self.assertAlmostEqual(out["defence_observation"], math.log1p(0.9) - math.log1p(1.5))
        self.assertEqual(out["availability_basis"], "trusted_source_published_at")
        self.assertEqual(out["provable_available_at"], (T0 + timedelta(minutes=2)).isoformat())
        self.assertFalse(out["market_input_used"])
        self.assertEqual(out["formal_weight"], 0.0)

    def test_first_observed_fallback_when_source_time_not_trusted(self):
        out = adapt_completed_match_intensity(self.evidence(source_published_at=None, source_published_at_trusted=False))
        self.assertEqual(out["availability_basis"], "collector_first_observed_at")
        self.assertEqual(out["provable_available_at"], (T0 + timedelta(minutes=5)).isoformat())

    def test_exact_reference_is_zero_observation(self):
        out = adapt_completed_match_intensity(self.evidence(attack_intensity=1.5, defence_intensity=1.5))
        self.assertAlmostEqual(out["attack_observation"], 0.0)
        self.assertAlmostEqual(out["defence_observation"], 0.0)

    def test_better_defence_is_negative_concession_propensity(self):
        out = adapt_completed_match_intensity(self.evidence(defence_intensity=0.75, defence_reference=1.5))
        self.assertLess(out["defence_observation"], 0.0)

    def test_zero_observed_intensity_is_supported(self):
        out = adapt_completed_match_intensity(self.evidence(attack_intensity=0.0, defence_intensity=0.0))
        self.assertTrue(math.isfinite(out["attack_observation"]))
        self.assertTrue(math.isfinite(out["defence_observation"]))


class AdapterFailClosedTests(unittest.TestCase):
    def valid(self):
        return AdapterBehaviorTests().evidence()

    def mutate(self, **kw):
        e = self.valid()
        return PITIntensityEvidence(**{**e.__dict__, **kw})

    def test_unknown_source_kind_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(source_kind="market_odds"))

    def test_post_cutoff_evidence_rejected(self):
        cutoff = self.valid().prediction_cutoff
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(source_published_at=cutoff, collector_first_observed_at=cutoff))

    def test_event_after_availability_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(event_completed_at=T0 + timedelta(minutes=3)))

    def test_timezone_naive_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(prediction_cutoff=datetime(2026, 1, 2, 12, 0)))

    def test_negative_intensity_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(attack_intensity=-0.1))

    def test_zero_reference_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(attack_reference=0.0))

    def test_invalid_variance_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(attack_observation_variance=float("nan")))

    def test_zero_variance_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(attack_observation_variance=0.0))

    def test_bad_payload_hash_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(payload_sha256="abcd"))

    def test_trusted_source_time_requires_timestamp(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(source_published_at=None, source_published_at_trusted=True))

    def test_trusted_source_time_after_first_observation_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(source_published_at=T0 + timedelta(minutes=8)))

    def test_first_observed_after_retrieved_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(retrieved_at=T0 + timedelta(minutes=4)))

    def test_retrieved_after_ingested_rejected(self):
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(self.mutate(retrieved_at=T0 + timedelta(minutes=9), ingested_at=T0 + timedelta(minutes=8)))


class Independent1X2BehaviorTests(unittest.TestCase):
    def test_probability_conservation(self):
        out = probabilities_from_latent_comparison(cmp(0.2), config=CFG_SYM)
        self.assertAlmostEqual(out["home"] + out["draw"] + out["away"], 1.0, places=12)
        self.assertTrue(all(0.0 <= out[k] <= 1.0 for k in ("home", "draw", "away")))

    def test_stage1_compare_schema_is_accepted(self):
        out = probabilities_from_latent_comparison(
            cmp(0.2, interpretation="research_only_latent_direction_not_1x2_probability"), config=CFG_SYM
        )
        self.assertAlmostEqual(sum(out[k] for k in ("home", "draw", "away")), 1.0, places=12)

    def test_equal_strength_is_home_away_symmetric_without_home_advantage(self):
        out = probabilities_from_latent_comparison(cmp(0.0), config=CFG_SYM)
        self.assertAlmostEqual(out["home"], out["away"], places=12)
        self.assertGreater(out["draw"], 0.0)

    def test_sign_flip_swaps_home_away(self):
        pos = probabilities_from_latent_comparison(cmp(0.8), config=CFG_SYM)
        neg = probabilities_from_latent_comparison(cmp(-0.8), config=CFG_SYM)
        self.assertAlmostEqual(pos["home"], neg["away"], places=12)
        self.assertAlmostEqual(pos["away"], neg["home"], places=12)
        self.assertAlmostEqual(pos["draw"], neg["draw"], places=12)

    def test_positive_margin_monotonically_increases_home_probability(self):
        vals = [probabilities_from_latent_comparison(cmp(m), config=CFG_SYM)["home"] for m in (-1.0, 0.0, 1.0)]
        self.assertLess(vals[0], vals[1])
        self.assertLess(vals[1], vals[2])

    def test_draw_is_naturally_highest_near_zero_for_fixed_variance(self):
        near = probabilities_from_latent_comparison(cmp(0.0), config=CFG_SYM)["draw"]
        far = probabilities_from_latent_comparison(cmp(2.0), config=CFG_SYM)["draw"]
        self.assertGreater(near, far)

    def test_home_advantage_shifts_mass_without_manual_draw_boost(self):
        neutral = probabilities_from_latent_comparison(cmp(0.0), config=CFG_SYM)
        home_cfg = Independent1X2LinkConfig(home_advantage=0.4, draw_boundary=0.35, match_noise_variance=1.0)
        shifted = probabilities_from_latent_comparison(cmp(0.0), config=home_cfg)
        self.assertGreater(shifted["home"], neutral["home"])
        self.assertLess(shifted["away"], neutral["away"])

    def test_larger_draw_boundary_increases_draw_probability(self):
        narrow = probabilities_from_latent_comparison(
            cmp(0.0), config=Independent1X2LinkConfig(home_advantage=0.0, draw_boundary=0.2, match_noise_variance=1.0)
        )
        wide = probabilities_from_latent_comparison(
            cmp(0.0), config=Independent1X2LinkConfig(home_advantage=0.0, draw_boundary=0.6, match_noise_variance=1.0)
        )
        self.assertGreater(wide["draw"], narrow["draw"])

    def test_no_market_or_score_matrix_usage_claim(self):
        out = probabilities_from_latent_comparison(cmp(0.1), config=CFG_SYM)
        self.assertFalse(out["market_input_used"])
        self.assertFalse(out["score_matrix_used"])
        self.assertEqual(out["formal_weight"], 0.0)


class Independent1X2FailClosedTests(unittest.TestCase):
    def test_config_has_no_zero_or_negative_draw_boundary(self):
        with self.assertRaises(Independent1X2Error):
            probabilities_from_latent_comparison(cmp(0.0), config=Independent1X2LinkConfig(0.0, 0.0, 1.0))

    def test_nonpositive_latent_variance_rejected(self):
        with self.assertRaises(Independent1X2Error):
            probabilities_from_latent_comparison(cmp(0.0, 0.0), config=CFG_SYM)

    def test_nan_margin_rejected(self):
        with self.assertRaises(Independent1X2Error):
            probabilities_from_latent_comparison(cmp(float("nan")), config=CFG_SYM)

    def test_unapproved_input_interpretation_rejected(self):
        with self.assertRaises(Independent1X2Error):
            probabilities_from_latent_comparison(cmp(0.0, interpretation="market_anchored"), config=CFG_SYM)

    def test_extreme_but_bounded_calculation_stays_conservative(self):
        out = probabilities_from_latent_comparison(cmp(8.0, 25.0), config=CFG_SYM)
        self.assertAlmostEqual(sum(out[k] for k in ("home", "draw", "away")), 1.0, places=12)


class ContractTests(unittest.TestCase):
    def test_contract_stays_zero_label_and_no_activation(self):
        c = json.loads((HERE / "adaptive_latent_direct_1x2_prelabel_contract_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(c["formal_weight"], 0.0)
        self.assertFalse(c["production_activation"])
        self.assertFalse(c["training"])
        self.assertFalse(c["tuning"])
        self.assertFalse(c["real_scoring"])
        self.assertFalse(c["target_access"]["authorized"])
        self.assertEqual(c["target_access"]["real_target_rows_read"], 0)
        self.assertTrue(c["target_access"]["stop_before_real_labels"])
        self.assertEqual(c["future_oos_gate"]["primary_metric"], "LogLoss")
        self.assertEqual(c["future_oos_gate"]["top1_role"], "DIAGNOSTIC_ONLY")
        self.assertFalse(c["candidate_market_input"])
        self.assertFalse(c["candidate_score_matrix_input"])


class IndependentAdversarialProbes(unittest.TestCase):
    def test_string_numeric_inputs_are_normalized_but_nonfinite_blocked(self):
        out = probabilities_from_latent_comparison(cmp("0.25", "0.5"), config=CFG_SYM)
        self.assertAlmostEqual(sum(out[k] for k in ("home", "draw", "away")), 1.0, places=12)
        with self.assertRaises(Independent1X2Error):
            probabilities_from_latent_comparison(cmp("inf", "0.5"), config=CFG_SYM)

    def test_unknown_dict_shape_cannot_silently_default(self):
        with self.assertRaises(Independent1X2Error):
            probabilities_from_latent_comparison({"interpretation": "synthetic_latent_comparison_v1"}, config=CFG_SYM)

    def test_future_evidence_cannot_be_smuggled_by_equal_cutoff_timestamp(self):
        base = AdapterBehaviorTests().evidence(source_published_at=None, source_published_at_trusted=False)
        bad = PITIntensityEvidence(**{**base.__dict__, "collector_first_observed_at": base.prediction_cutoff})
        with self.assertRaises(ObservationAdapterError):
            adapt_completed_match_intensity(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
