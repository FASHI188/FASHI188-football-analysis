#!/usr/bin/env python3
"""Negative tests for the zero-label single-candidate preregistration validator."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from validate_preregistration import PreregistrationError, validate  # noqa: E402


class PreregistrationMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = json.loads((HERE / "preregistration.json").read_text(encoding="utf-8"))

    def mutated(self) -> dict:
        return copy.deepcopy(self.base)

    def assert_mutation_fails(self, value: dict, pattern: str) -> None:
        with self.assertRaisesRegex(PreregistrationError, pattern):
            validate(value)

    def test_valid_contract_passes(self) -> None:
        receipt = validate(self.mutated())
        self.assertEqual(receipt["status"], "PASS_ZERO_LABEL_SINGLE_CANDIDATE_PREREGISTRATION_FROZEN")
        self.assertEqual(receipt["candidate_count"], 1)
        self.assertEqual(receipt["winner_labels_read"], 0)

    def test_baseline_formula_tamper_fails(self) -> None:
        value = self.mutated()
        value["probability_contract"]["baseline"]["formula"] = "qD_T90"
        self.assert_mutation_fails(value, "baseline formula changed")

    def test_candidate_formula_tamper_fails(self) -> None:
        value = self.mutated()
        value["probability_contract"]["fixed_candidate"]["formula"] = "clip(qD_T15 + 0.5 * (qD_T15 - qD_T60))"
        self.assert_mutation_fails(value, "unique candidate formula or coefficient changed")

    def test_candidate_coefficient_tamper_fails(self) -> None:
        value = self.mutated()
        value["probability_contract"]["fixed_candidate"]["coefficient"] = 0.6
        self.assert_mutation_fails(value, "unique candidate formula or coefficient changed")

    def test_staleness_threshold_tamper_fails(self) -> None:
        value = self.mutated()
        value["synchronization_and_staleness_contract"]["cutoffs"]["T90"]["maximum_single_runner_staleness_seconds"] = 1200
        self.assert_mutation_fails(value, "synchronization or staleness thresholds changed")

    def test_cross_runner_span_tamper_fails(self) -> None:
        value = self.mutated()
        value["synchronization_and_staleness_contract"]["cutoffs"]["T15"]["maximum_home_draw_away_observation_span_seconds"] = 180
        self.assert_mutation_fails(value, "synchronization or staleness thresholds changed")

    def test_average_precision_definition_tamper_fails(self) -> None:
        value = self.mutated()
        value["metric_definitions"]["average_precision"]["implementation"] = "trapezoidal_interpolation"
        self.assert_mutation_fails(value, "Average Precision definition changed")

    def test_roc_auc_tie_handling_tamper_fails(self) -> None:
        value = self.mutated()
        value["metric_definitions"]["roc_auc"]["tie_handling"] = "ties_contribute_zero"
        self.assert_mutation_fails(value, "ROC AUC definition changed")

    def test_bootstrap_seed_tamper_fails(self) -> None:
        value = self.mutated()
        value["bootstrap_contract"]["seed"] = 51104
        self.assert_mutation_fails(value, "bootstrap rules changed")

    def test_bootstrap_invalid_resample_rule_tamper_fails(self) -> None:
        value = self.mutated()
        value["bootstrap_contract"]["invalid_replicates_discarded_or_redrawn"] = True
        self.assert_mutation_fails(value, "bootstrap rules changed")

    def test_pass_condition_tamper_fails(self) -> None:
        value = self.mutated()
        value["sample_and_result_gates"]["research_pass_gate_all_required"]["average_precision_bootstrap_p05_strictly_greater_than"] = -0.01
        self.assert_mutation_fails(value, "research pass conditions changed")

    def test_label_access_order_tamper_fails(self) -> None:
        value = self.mutated()
        value["label_access_order_contract"]["identity_lock_must_be_persisted_and_verified_before_any_label_access"] = False
        self.assert_mutation_fails(value, "label access order changed")

    def test_one_time_only_tamper_fails(self) -> None:
        value = self.mutated()
        value["one_time_execution_contract"]["rerun_allowed"] = True
        self.assert_mutation_fails(value, "one-time-only execution restriction changed")

    def test_formal_weight_tamper_fails(self) -> None:
        value = self.mutated()
        value["hard_limits"]["formal_weight"] = 1
        self.assert_mutation_fails(value, "formal_weight, CURRENT, or formal asset mutation gate changed")

    def test_current_mutation_gate_tamper_fails(self) -> None:
        value = self.mutated()
        value["hard_limits"]["CURRENT_mutation_allowed"] = True
        self.assert_mutation_fails(value, "formal_weight, CURRENT, or formal asset mutation gate changed")

    def test_formal_asset_mutation_gate_tamper_fails(self) -> None:
        value = self.mutated()
        value["hard_limits"]["formal_model_mutation_allowed"] = True
        self.assert_mutation_fails(value, "formal_weight, CURRENT, or formal asset mutation gate changed")

    def test_second_candidate_tamper_fails(self) -> None:
        value = self.mutated()
        value["probability_contract"]["candidate_count"] = 2
        self.assert_mutation_fails(value, "candidate count changed")

    def test_policy_selection_section_reintroduced_fails(self) -> None:
        value = self.mutated()
        value["policy_selection_contract"] = {"selection_rule": "pick_best"}
        self.assert_mutation_fails(value, "legacy policy/confirmation or multi-candidate section remains")


if __name__ == "__main__":
    unittest.main(verbosity=2)
