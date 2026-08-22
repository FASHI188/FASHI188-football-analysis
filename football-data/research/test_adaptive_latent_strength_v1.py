#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adaptive_latent_strength_v1 import (
    AdaptiveLatentConfig,
    AdaptiveLatentStrengthV1,
    LatentStrengthError,
)

HERE = Path(__file__).resolve().parent
UTC = timezone.utc
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class BehaviorTests(unittest.TestCase):
    def test_equal_unseen_priors_are_directionally_symmetric(self):
        model = AdaptiveLatentStrengthV1()
        out = model.compare("A", "B", at=T0)
        self.assertAlmostEqual(out["latent_margin"], 0.0, places=12)
        self.assertAlmostEqual(out["standardized_margin"], 0.0, places=12)

    def test_positive_attack_evidence_moves_attack_up(self):
        model = AdaptiveLatentStrengthV1()
        first = model.update_team("A", attack_observation=1.0, defence_observation=0.0, observed_at=T0)
        second = model.update_team("A", attack_observation=1.0, defence_observation=0.0, observed_at=T0 + timedelta(days=7))
        self.assertGreater(first["attack"]["mean"], 0.0)
        self.assertGreater(second["attack"]["mean"], first["attack"]["mean"])

    def test_measurement_reduces_same_time_uncertainty(self):
        model = AdaptiveLatentStrengthV1()
        prior = model.snapshot("A", at=T0)
        post = model.update_team("A", attack_observation=0.2, defence_observation=-0.1, observed_at=T0)
        self.assertLess(post["attack"]["variance"], prior["attack"]["variance"])
        self.assertLess(post["defence"]["variance"], prior["defence"]["variance"])

    def test_time_gap_increases_predictive_uncertainty_without_mutation(self):
        model = AdaptiveLatentStrengthV1()
        model.update_team("A", attack_observation=0.1, defence_observation=0.1, observed_at=T0)
        near = model.snapshot("A", at=T0 + timedelta(days=1))
        far = model.snapshot("A", at=T0 + timedelta(days=31))
        self.assertGreater(far["attack"]["variance"], near["attack"]["variance"])
        stored = model.export_state()["teams"]["A"]["attack"]["last_observed_at"]
        self.assertEqual(stored, T0.isoformat())

    def test_sustained_surprise_increases_future_process_variance(self):
        cfg = AdaptiveLatentConfig(surprise_decay=0.5, surprise_gain=3.0)
        model = AdaptiveLatentStrengthV1(cfg)
        model.update_team("A", attack_observation=0.0, defence_observation=0.0, observed_at=T0)
        baseline_q = model.snapshot("A", at=T0)["attack"]["process_variance_per_day"]
        for i in range(1, 5):
            model.update_team(
                "A",
                attack_observation=5.0,
                defence_observation=0.0,
                observed_at=T0 + timedelta(days=i),
                attack_observation_variance=0.05,
            )
        elevated = model.snapshot("A", at=T0 + timedelta(days=4))["attack"]["process_variance_per_day"]
        self.assertGreater(elevated, baseline_q)
        self.assertLessEqual(elevated, cfg.max_process_variance_per_day)

    def test_swap_teams_negates_latent_margin(self):
        model = AdaptiveLatentStrengthV1()
        model.update_team("A", attack_observation=0.7, defence_observation=-0.1, observed_at=T0)
        model.update_team("B", attack_observation=-0.2, defence_observation=0.3, observed_at=T0)
        ab = model.compare("A", "B", at=T0)
        ba = model.compare("B", "A", at=T0)
        self.assertAlmostEqual(ab["latent_margin"], -ba["latent_margin"], places=12)
        self.assertAlmostEqual(ab["latent_margin_variance"], ba["latent_margin_variance"], places=12)

    def test_comparison_variance_contains_all_four_latent_components(self):
        model = AdaptiveLatentStrengthV1()
        a = model.snapshot("A", at=T0)
        b = model.snapshot("B", at=T0)
        expected = a["attack"]["variance"] + b["defence"]["variance"] + b["attack"]["variance"] + a["defence"]["variance"]
        out = model.compare("A", "B", at=T0)
        self.assertAlmostEqual(out["latent_margin_variance"], expected, places=12)

    def test_more_precise_observation_moves_state_more(self):
        loose = AdaptiveLatentStrengthV1()
        tight = AdaptiveLatentStrengthV1()
        loose.update_team("A", attack_observation=1.0, defence_observation=0.0, observed_at=T0, attack_observation_variance=2.0)
        tight.update_team("A", attack_observation=1.0, defence_observation=0.0, observed_at=T0, attack_observation_variance=0.05)
        self.assertGreater(tight.snapshot("A", at=T0)["attack"]["mean"], loose.snapshot("A", at=T0)["attack"]["mean"])


class ContractTests(unittest.TestCase):
    def test_contract_is_zero_label_research_only(self):
        contract = json.loads((HERE / "adaptive_latent_strength_v1_contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["status"], "RESEARCH_ONLY_ZERO_LABEL_ENGINEERING")
        self.assertEqual(contract["formal_weight"], 0.0)
        self.assertFalse(contract["real_label_access"])
        self.assertFalse(contract["real_match_row_access"])
        self.assertFalse(contract["training"])
        self.assertFalse(contract["real_scoring"])
        self.assertFalse(contract["market_input"])

    def test_contract_freezes_scientific_gate_before_label_read(self):
        contract = json.loads((HERE / "adaptive_latent_strength_v1_contract.json").read_text(encoding="utf-8"))
        gate = contract["scientific_next_gate_before_any_real_label_read"]
        self.assertTrue(gate)
        self.assertTrue(all(gate.values()))

    def test_public_comparison_does_not_claim_probability(self):
        model = AdaptiveLatentStrengthV1()
        out = model.compare("A", "B", at=T0)
        self.assertEqual(out["interpretation"], "research_only_latent_direction_not_1x2_probability")
        self.assertNotIn("home_probability", out)
        self.assertNotIn("draw_probability", out)
        self.assertNotIn("away_probability", out)


class FailClosedTests(unittest.TestCase):
    def test_naive_timestamp_rejected(self):
        model = AdaptiveLatentStrengthV1()
        with self.assertRaises(LatentStrengthError):
            model.snapshot("A", at=datetime(2026, 1, 1, 12, 0))

    def test_backward_timestamp_rejected(self):
        model = AdaptiveLatentStrengthV1()
        model.update_team("A", attack_observation=0.0, defence_observation=0.0, observed_at=T0 + timedelta(days=1))
        with self.assertRaises(LatentStrengthError):
            model.snapshot("A", at=T0)

    def test_nonfinite_observation_rejected(self):
        model = AdaptiveLatentStrengthV1()
        with self.assertRaises(LatentStrengthError):
            model.update_team("A", attack_observation=float("nan"), defence_observation=0.0, observed_at=T0)

    def test_nonpositive_observation_variance_rejected(self):
        model = AdaptiveLatentStrengthV1()
        with self.assertRaises(LatentStrengthError):
            model.update_team("A", attack_observation=0.0, defence_observation=0.0, observed_at=T0, attack_observation_variance=0.0)

    def test_blank_team_rejected(self):
        model = AdaptiveLatentStrengthV1()
        with self.assertRaises(LatentStrengthError):
            model.snapshot("   ", at=T0)

    def test_failed_update_has_no_state_side_effect(self):
        model = AdaptiveLatentStrengthV1()
        before = model.export_state()
        with self.assertRaises(LatentStrengthError):
            model.update_team("Ghost", attack_observation=float("nan"), defence_observation=0.0, observed_at=T0)
        self.assertEqual(model.export_state(), before)

    def test_same_home_and_away_team_rejected(self):
        model = AdaptiveLatentStrengthV1()
        with self.assertRaises(LatentStrengthError):
            model.compare("A", "A", at=T0)

    def test_invalid_process_bounds_rejected(self):
        with self.assertRaises(LatentStrengthError):
            AdaptiveLatentStrengthV1(AdaptiveLatentConfig(base_process_variance_per_day=0.1, max_process_variance_per_day=0.05))

    def test_invalid_surprise_decay_rejected(self):
        with self.assertRaises(LatentStrengthError):
            AdaptiveLatentStrengthV1(AdaptiveLatentConfig(surprise_decay=1.0))


class IndependentSyntheticProbes(unittest.TestCase):
    def test_updating_one_team_does_not_mutate_another(self):
        model = AdaptiveLatentStrengthV1()
        before = model.snapshot("B", at=T0)
        model.update_team("A", attack_observation=2.0, defence_observation=-1.0, observed_at=T0)
        after = model.snapshot("B", at=T0)
        self.assertEqual(before, after)

    def test_identical_evidence_produces_identical_states_regardless_of_team_name(self):
        model = AdaptiveLatentStrengthV1()
        model.update_team("Alpha", attack_observation=0.4, defence_observation=-0.2, observed_at=T0)
        model.update_team("Omega", attack_observation=0.4, defence_observation=-0.2, observed_at=T0)
        a = model.snapshot("Alpha", at=T0)
        o = model.snapshot("Omega", at=T0)
        for side in ("attack", "defence"):
            for key in ("mean", "variance", "process_variance_per_day", "surprise_ewma"):
                self.assertAlmostEqual(a[side][key], o[side][key], places=12)

    def test_extreme_surprise_is_bounded(self):
        cfg = AdaptiveLatentConfig(max_process_variance_per_day=0.01, max_abs_state=2.0)
        model = AdaptiveLatentStrengthV1(cfg)
        model.update_team("A", attack_observation=1e9, defence_observation=-1e9, observed_at=T0, attack_observation_variance=0.01, defence_observation_variance=0.01)
        snap = model.snapshot("A", at=T0)
        self.assertLessEqual(abs(snap["attack"]["mean"]), cfg.max_abs_state)
        self.assertLessEqual(abs(snap["defence"]["mean"]), cfg.max_abs_state)
        self.assertLessEqual(snap["attack"]["process_variance_per_day"], cfg.max_process_variance_per_day)

    def test_uncertainty_not_top1_is_the_native_fragility_signal(self):
        model = AdaptiveLatentStrengthV1()
        model.update_team("A", attack_observation=0.4, defence_observation=0.0, observed_at=T0)
        model.update_team("B", attack_observation=0.0, defence_observation=0.1, observed_at=T0)
        now = model.compare("A", "B", at=T0)
        later = model.compare("A", "B", at=T0 + timedelta(days=120))
        self.assertAlmostEqual(now["latent_margin"], later["latent_margin"], places=12)
        self.assertGreater(later["latent_margin_sd"], now["latent_margin_sd"])
        self.assertLess(abs(later["standardized_margin"]), abs(now["standardized_margin"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
